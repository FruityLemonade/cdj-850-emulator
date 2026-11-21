/* SPDX-License-Identifier: GPL-2.0-or-later */
/*
 * cdj850_cache: a QEMU TCG plugin that models the SH7263 caches of the
 * CDJ-850 MAIN CPU next to the emulator, which has none.
 *
 * QEMU runs the firmware as if memory were coherent: every load sees the
 * last store, whoever made it. The real chip has an 8 KB instruction cache
 * and an 8 KB write-back operand cache that software has to keep coherent
 * itself (SH7263 Hardware Manual R01UH0459EJ0400 rev 4.00, section 8). This
 * plugin runs the real cache rules on the side and keeps a second copy of
 * SDRAM holding what the real memory chips would hold. Whenever the real
 * chip would hand the CPU different bytes than QEMU just did, that is a
 * coherency bug the emulator cannot show, and it is reported.
 *
 * Cache (section 8.1, 8.3): instruction and operand caches of 4 ways x 128
 * entries x 16-byte lines, LRU, entry = address bits 10-4, tag = physical
 * bits 28-11. Cache-enabled space is 0x00000000-0x1FFFFFFF; the same
 * addresses + 0x20000000 bypass the cache. CCR1 (0xFFFC1000): ICF bit 11,
 * ICE bit 8, OCF bit 3, WT bit 1, OCE bit 0; a flush clears V/U/LRU with no
 * write-back. CCR2 (0xFFFC1004): LE bit 16, W3LOAD/W3LOCK bits 9/8,
 * W2LOAD/W2LOCK bits 1/0. Address arrays at 0xF0000000 (I) / 0xF0800000
 * (O): way = bits 12-11, entry = bits 10-4, A = bit 3; data tag = bits
 * 28-11, LRU = bits 9-4, U = bit 1 (O only), V = bit 0. Associative writes
 * update V/U of the way whose tag matches; any operand line leaving the
 * cache with U = 1 is written back (8.4.1, 8.4.3). Data arrays at
 * 0xF1000000 / 0xF1800000: way = bits 12-11, entry = bits 10-4, longword =
 * bits 3-2. The DMAC only ever sees memory (8.4.4 note 3).
 *
 * Memory model: the shadow ("real") SDRAM is copied from the guest at the
 * first instruction and from then on changes only the way the chip's would:
 * through uncached stores, write-through stores, write-backs of dirty lines
 * and DMA. QEMU's memory is read back with qemu_plugin_read_memory_vaddr
 * where a comparison needs it. Flash (CS0, below 0x04000000) is a device
 * whose array the CPU does not write directly, so its "real" bytes are
 * always QEMU's; a cached flash line still goes stale when flash is
 * reprogrammed. Other cache-enabled space is tracked for hits and misses
 * only.
 *
 * DMA is inferred from the CPU's own DMAC register accesses (0xFFFE1000):
 * a CHCR store with DE = 1 arms a channel with its SAR/DAR/DMATCR/TS/SM/DM;
 * a memory source range is checked against the shadow at that point (the
 * DMAC reads real memory, so a source still dirty in the cache sends stale
 * data); a memory destination range is copied from QEMU into the shadow
 * once the CPU sees the transfer finish (a CHCR read with TE = 1, a DMATCR
 * read of 0, or the channel being re-armed).
 *
 * After a report the shadow and the cache line are brought back in line
 * with QEMU, so each root cause is reported once instead of cascading.
 *
 * Reports (first occurrence per kind and pc goes to stderr at once; all of
 * them with counts go to <out>-report.txt at exit):
 *   stale-read        cached load hit a line older than memory (someone
 *                     wrote memory around the cache after the line filled)
 *   diverged-fill     cached load missed and filled from real memory that
 *                     differs from QEMU's (a dirty line was discarded, a
 *                     write-back clobbered newer data, or DMA not seen)
 *   uncached-stale    uncached load of data still sitting dirty in the cache
 *   writeback-clobber eviction/flush wrote back a dirty line over data that
 *                     was written around the cache after it filled
 *   dirty-discard     OCF flush threw away dirty data never written back
 *   stale-code        instruction fetch hit an instruction cache line older
 *                     than the code QEMU executes (code changed, no ICF or
 *                     address-array invalidate)
 *   code-not-in-mem   instruction fetch from memory that does not hold the
 *                     code yet (it is still dirty in the operand cache)
 *   dma-read-stale    DMA armed to read memory whose newest bytes are still
 *                     dirty in the operand cache
 *
 * Profile (<out>-profile.csv, one row per pc that did anything): fetches
 * and instruction-cache misses, operand reads/writes with misses, dirty
 * write-backs caused, uncached accesses, prefetches. The Ghidra script
 * scripts/ghidra/apply_cache_profile.py rolls these up per function.
 *
 * Arguments: out=<path prefix> (default cdj850-cache), check=on|off,
 * profile=on|off, quiet=on (no live stderr reports).
 *
 * Needs a QEMU with patches/target_sh4_helper.c.patch (SH-2A exception
 * entry reported to plugins) and the board's plugin register feature
 * (cdj850_plugin_regs_init); without them the report warns. See
 * docs/cdj850-cache-model.md.
 */
#include <glib.h>
#include <inttypes.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include <qemu-plugin.h>

QEMU_PLUGIN_EXPORT int qemu_plugin_version = QEMU_PLUGIN_VERSION;

#define LINE_SIZE       16
#define N_SETS          128
#define N_WAYS          4

#define SDRAM_BASE      0x0C000000u
#define SDRAM_SIZE      (32u << 20)
#define FLASH_END       0x04000000u          /* CS0 */
#define CACHED_END      0x20000000u
#define UNCACHED_END    0x40000000u

#define CCR1_ADDR       0xFFFC1000u
#define CCR2_ADDR       0xFFFC1004u
#define CCR1_ICF        0x800u
#define CCR1_ICE        0x100u
#define CCR1_OCF        0x008u
#define CCR1_WT         0x002u
#define CCR1_OCE        0x001u
#define CCR2_LE         0x10000u
#define CCR2_W3LOAD     0x200u
#define CCR2_W3LOCK     0x100u
#define CCR2_W2LOAD     0x002u
#define CCR2_W2LOCK     0x001u

#define DMAC_BASE       0xFFFE1000u
#define DMAC_CHANS      8

/* ---- reports ------------------------------------------------------------ */

enum kind {
    K_STALE_READ,
    K_DIVERGED_FILL,
    K_UNCACHED_STALE,
    K_WB_CLOBBER,
    K_DIRTY_DISCARD,
    K_STALE_CODE,
    K_CODE_NOT_IN_MEM,
    K_DMA_READ_STALE,
    K_COUNT
};

static const char *const kind_name[K_COUNT] = {
    "stale-read", "diverged-fill", "uncached-stale", "writeback-clobber",
    "dirty-discard", "stale-code", "code-not-in-mem", "dma-read-stale",
};

typedef struct {
    enum kind kind;
    uint32_t pc;
    uint32_t addr;          /* first address seen */
    uint64_t count;
    uint8_t hw[LINE_SIZE];  /* what the chip would see (first occurrence) */
    uint8_t emu[LINE_SIZE]; /* what QEMU had */
    unsigned len;
    uint32_t writer;        /* last writer around the cache, see writer_str */
} Report;

/* ---- profile ------------------------------------------------------------ */

typedef struct {
    uint32_t pc;
    uint64_t fetch, ic_miss;
    uint64_t rd, rd_miss, wr, wr_miss;
    uint64_t writeback;
    uint64_t unc_rd, unc_wr;
    uint64_t pref, pref_miss;
} PcStat;

typedef struct {
    uint32_t pc;
    uint8_t size;
    uint8_t bytes[4];
    int8_t pref_reg;        /* n of pref @Rn, else -1 */
    bool rte;
    PcStat *stat;
} Insn;

/* ---- cache -------------------------------------------------------------- */

typedef struct {
    uint32_t line;          /* physical address >> 4 (tag and entry) */
    bool v, u;
    uint8_t data[LINE_SIZE];
} Line;

typedef struct {
    Line way[N_WAYS];
    uint8_t rank[N_WAYS];   /* 0 = most recently used, 3 = least */
} Set;

typedef struct {
    Set set[N_SETS];
    bool data_array_dirty;  /* software wrote the data array directly */
} Cache;

/* ---- state -------------------------------------------------------------- */

static Cache ic, oc;
static uint32_t ccr1, ccr2;
static uint8_t *real;               /* shadow SDRAM */
static uint32_t *writer;            /* per line: last write around the cache */
static bool started;
static bool opt_check = true, opt_profile = true, opt_quiet;
static char *opt_out;

static GHashTable *reports;         /* (kind << 32 | pc) -> Report */
static GHashTable *pcstats;         /* pc -> PcStat */
static GHashTable *insns;           /* pc -> Insn (latest translation) */
static GHashTable *maint;           /* pc -> count of CCR1/array writes */
static GPtrArray *all_insns;
static struct qemu_plugin_register *reg_r[16];
static bool have_regs;
static uint64_t pref_unmodelled;

static struct {
    uint64_t fetch, ic_hit, ic_miss;
    uint64_t rd, rd_hit, rd_miss, wr, wr_hit, wr_miss;
    uint64_t writeback, unc_rd, unc_wr, pref, pref_miss;
    uint64_t ccr1_writes, icf, ocf, aa_writes, da_writes;
    uint64_t dma_arm, dma_done, other_cached;
    uint64_t entries, entries_unmodelled, rte;
} g;

typedef struct {
    uint32_t sar, dar, tcr, chcr;
    bool pending;           /* destination range not yet copied in */
    uint32_t dst, len;
} Chan;
static Chan dmac[DMAC_CHANS];

/* writer encoding: 0 none, pc of an uncached/write-through store, or
 * 0xD0000000 | channel for DMA */
#define WRITER_DMA      0xD0000000u

/* Live messages go to stderr: qemu_plugin_outs() only prints under
 * "-d plugin". */
static void live(const char *msg)
{
    fputs(msg, stderr);
    fflush(stderr);
}

static const char *writer_str(uint32_t w, char *buf, size_t n)
{
    if (!w) {
        snprintf(buf, n, "none seen");
    } else if ((w & 0xFFFFFFF0u) == WRITER_DMA) {
        snprintf(buf, n, "DMA channel %u", w & 0xF);
    } else {
        snprintf(buf, n, "store at pc 0x%08x", w);
    }
    return buf;
}

static inline bool in_sdram(uint32_t phys)
{
    return phys - SDRAM_BASE < SDRAM_SIZE;
}

static inline uint8_t *real_at(uint32_t phys)
{
    return real + (phys - SDRAM_BASE);
}

static inline uint32_t *writer_at(uint32_t phys)
{
    return &writer[(phys - SDRAM_BASE) / LINE_SIZE];
}

/* QEMU's current bytes. With no MMU the guest virtual address is the
 * physical one, and the uncached alias folds onto it. */
static bool emu_read(uint32_t phys, uint8_t *dst, size_t len)
{
    GByteArray *b = g_byte_array_sized_new(len);
    bool ok = qemu_plugin_read_memory_vaddr(phys, b, len);

    if (ok && b->len == len) {
        memcpy(dst, b->data, len);
    } else {
        ok = false;
    }
    g_byte_array_free(b, TRUE);
    return ok;
}

/* The chip's memory: shadow SDRAM, live flash, nothing elsewhere. */
static bool real_read(uint32_t phys, uint8_t *dst, size_t len)
{
    if (in_sdram(phys)) {
        memcpy(dst, real_at(phys), len);
        return true;
    }
    if (phys < FLASH_END) {
        return emu_read(phys, dst, len);
    }
    return false;
}

static void real_write(uint32_t phys, const uint8_t *src, size_t len)
{
    if (in_sdram(phys)) {
        memcpy(real_at(phys), src, len);
    }
}

static PcStat *pc_stat(uint32_t pc)
{
    PcStat *s = g_hash_table_lookup(pcstats, GUINT_TO_POINTER(pc));

    if (!s) {
        s = g_new0(PcStat, 1);
        s->pc = pc;
        g_hash_table_insert(pcstats, GUINT_TO_POINTER(pc), s);
    }
    return s;
}

static void hex(GString *s, const uint8_t *p, unsigned n)
{
    for (unsigned i = 0; i < n; i++) {
        g_string_append_printf(s, "%02x", p[i]);
    }
}

static void report(enum kind k, uint32_t pc, uint32_t addr,
                   const uint8_t *hw, const uint8_t *emu, unsigned len)
{
    uint64_t key = ((uint64_t)k << 32) | pc;
    Report *r;
    char wb[48];

    if (!opt_check) {
        return;
    }
    if (len > LINE_SIZE) {
        len = LINE_SIZE;
    }
    r = g_hash_table_lookup(reports, &key);
    if (r) {
        r->count++;
        return;
    }
    r = g_new0(Report, 1);
    r->kind = k;
    r->pc = pc;
    r->addr = addr;
    r->count = 1;
    r->len = len;
    if (hw) {
        memcpy(r->hw, hw, len);
    }
    if (emu) {
        memcpy(r->emu, emu, len);
    }
    r->writer = in_sdram(addr & 0x1FFFFFFFu) ? *writer_at(addr & 0x1FFFFFFFu) : 0;
    {
        uint64_t *kp = g_new(uint64_t, 1);
        *kp = key;
        g_hash_table_insert(reports, kp, r);
    }
    if (!opt_quiet) {
        GString *s = g_string_new(NULL);

        g_string_append_printf(s, "cdj850-cache: %s pc=0x%08x addr=0x%08x",
                               kind_name[k], pc, addr);
        if (hw && emu) {
            g_string_append(s, " chip=");
            hex(s, hw, len);
            g_string_append(s, " qemu=");
            hex(s, emu, len);
        }
        g_string_append_printf(s, " last-write-around-cache=%s\n",
                               writer_str(r->writer, wb, sizeof(wb)));
        live(s->str);
        g_string_free(s, TRUE);
    }
}

/* ---- LRU ----------------------------------------------------------------- */

static void lru_reset(Set *s)
{
    /* LRU bits 000000: way 3 is replaced first (table 8.1). */
    for (int w = 0; w < N_WAYS; w++) {
        s->rank[w] = w;
    }
}

static void lru_touch(Set *s, int w)
{
    for (int i = 0; i < N_WAYS; i++) {
        if (s->rank[i] < s->rank[w]) {
            s->rank[i]++;
        }
    }
    s->rank[w] = 0;
}

/* Address-array LRU bits: bit 5 ways 0/1, 4 0/2, 3 0/3, 2 1/2, 1 1/3,
 * 0 2/3; a 1 means the lower way is the older one (tables 8.1, 8.5-8.7). */
static void lru_set_bits(Set *s, unsigned bits)
{
    static const int pair[6][2] = {
        { 2, 3 }, { 1, 3 }, { 1, 2 }, { 0, 3 }, { 0, 2 }, { 0, 1 },
    };
    int older[N_WAYS] = { 0 };

    for (int b = 0; b < 6; b++) {
        int i = pair[b][0], j = pair[b][1];

        if (bits & (1u << b)) {
            older[i]++;          /* i older than j: j is newer than i */
        } else {
            older[j]++;
        }
    }
    /* rank = how many ways are newer; legal patterns give 0..3 once each */
    for (int w = 0; w < N_WAYS; w++) {
        s->rank[w] = older[w];
    }
}

/* Way to replace: the least recently used way the CCR2 lock allows
 * (tables 8.3, 8.4). */
static int victim(Cache *c, Set *s, bool pref)
{
    bool allowed[N_WAYS] = { true, true, true, true };
    int best = -1;

    if (c == &oc && (ccr2 & CCR2_LE)) {
        if (pref && (ccr2 & CCR2_W3LOAD) && (ccr2 & CCR2_W3LOCK)) {
            return 3;
        }
        if (pref && (ccr2 & CCR2_W2LOAD) && (ccr2 & CCR2_W2LOCK)) {
            return 2;
        }
        if (ccr2 & CCR2_W2LOCK) {
            allowed[2] = false;
        }
        if (ccr2 & CCR2_W3LOCK) {
            allowed[3] = false;
        }
    }
    for (int w = 0; w < N_WAYS; w++) {
        if (allowed[w] && (best < 0 || s->rank[w] > s->rank[best])) {
            best = w;
        }
    }
    return best;
}

static int lookup(Cache *c, uint32_t line, Set **sp)
{
    Set *s = &c->set[line & (N_SETS - 1)];

    *sp = s;
    for (int w = 0; w < N_WAYS; w++) {
        if (s->way[w].v && s->way[w].line == line) {
            return w;
        }
    }
    return -1;
}

/* A dirty operand line goes back to memory: eviction, address-array
 * write, associative invalidate. */
static void write_back(Line *l, uint32_t pc, PcStat *st)
{
    uint32_t phys = l->line << 4;
    uint8_t emu[LINE_SIZE];

    g.writeback++;
    if (st) {
        st->writeback++;
    }
    if (!in_sdram(phys)) {
        return;
    }
    /* All the CPU's stores reached both the line and QEMU's memory, so a
     * difference here was written around the cache after the line filled:
     * the write-back destroys it on the chip. */
    if (opt_check && emu_read(phys, emu, LINE_SIZE) &&
        memcmp(emu, l->data, LINE_SIZE) != 0) {
        report(K_WB_CLOBBER, pc, phys, l->data, emu, LINE_SIZE);
        real_write(phys, emu, LINE_SIZE);
        memcpy(l->data, emu, LINE_SIZE);
        return;
    }
    real_write(phys, l->data, LINE_SIZE);
}

static void evict(Cache *c, Line *l, uint32_t pc, PcStat *st)
{
    if (c == &oc && l->v && l->u) {
        write_back(l, pc, st);
    }
    l->v = l->u = false;
}

/* Fill a line from the chip's memory. Returns false where there is no
 * data model (device space): the line is tracked for hit/miss only. */
static bool fill(Line *l, uint32_t line)
{
    l->line = line;
    l->v = true;
    l->u = false;
    return real_read(line << 4, l->data, LINE_SIZE);
}

static inline bool has_data(uint32_t phys)
{
    return in_sdram(phys) || phys < FLASH_END;
}

/* After a report: the whole 16-byte line (shadow memory, and the cache line
 * if given) takes QEMU's bytes, so the rest of a stale line is not reported
 * again word by word. */
static void resync(uint32_t phys, Line *l)
{
    uint8_t emu[LINE_SIZE];
    uint32_t base = phys & ~(LINE_SIZE - 1);

    if (!emu_read(base, emu, LINE_SIZE)) {
        return;
    }
    real_write(base, emu, LINE_SIZE);
    if (l) {
        memcpy(l->data, emu, LINE_SIZE);
    }
}

/* ---- operand accesses ---------------------------------------------------- */

static void value_bytes(qemu_plugin_meminfo_t info, uint8_t *out, unsigned size)
{
    qemu_plugin_mem_value v = qemu_plugin_mem_get_value(info);
    uint64_t x;

    switch (v.type) {
    case QEMU_PLUGIN_MEM_VALUE_U8:  x = v.data.u8; break;
    case QEMU_PLUGIN_MEM_VALUE_U16: x = v.data.u16; break;
    case QEMU_PLUGIN_MEM_VALUE_U32: x = v.data.u32; break;
    case QEMU_PLUGIN_MEM_VALUE_U64: x = v.data.u64; break;
    default:                        x = v.data.u128.low; break;
    }
    /* guest memory is big-endian */
    for (unsigned i = 0; i < size; i++) {
        out[i] = x >> (8 * (size - 1 - i));
    }
}

static void cached_access(uint32_t phys, const uint8_t *val, unsigned size,
                          bool store, uint32_t pc, PcStat *st)
{
    uint32_t off = phys & (LINE_SIZE - 1);
    uint32_t line = phys >> 4;
    bool data = has_data(phys);
    Set *s;
    int w = lookup(&oc, line, &s);
    Line *l;

    if (!data) {
        g.other_cached++;
    }
    if (!store) {
        g.rd++;
        st->rd++;
        if (w >= 0) {
            g.rd_hit++;
            l = &s->way[w];
            lru_touch(s, w);
            if (data && memcmp(l->data + off, val, size) != 0) {
                report(K_STALE_READ, pc, phys, l->data + off, val, size);
                resync(phys, l);
            }
            return;
        }
        g.rd_miss++;
        st->rd_miss++;
        w = victim(&oc, s, false);
        l = &s->way[w];
        evict(&oc, l, pc, st);
        if (fill(l, line) && memcmp(l->data + off, val, size) != 0) {
            report(K_DIVERGED_FILL, pc, phys, l->data + off, val, size);
            resync(phys, l);
        }
        lru_touch(s, w);
        return;
    }

    g.wr++;
    st->wr++;
    if (ccr1 & CCR1_WT) {
        /* write-through: memory always, the line only on a hit */
        if (w >= 0) {
            g.wr_hit++;
            memcpy(s->way[w].data + off, val, size);
            lru_touch(s, w);
        } else {
            g.wr_miss++;
            st->wr_miss++;
        }
        real_write(phys, val, size);
        if (in_sdram(phys)) {
            *writer_at(phys) = pc;
        }
        return;
    }
    if (w >= 0) {
        g.wr_hit++;
    } else {
        g.wr_miss++;
        st->wr_miss++;
        w = victim(&oc, s, false);
        evict(&oc, &s->way[w], pc, st);
        fill(&s->way[w], line);
    }
    l = &s->way[w];
    memcpy(l->data + off, val, size);
    l->u = true;
    lru_touch(s, w);
}

static void uncached_access(uint32_t phys, const uint8_t *val, unsigned size,
                            bool store, uint32_t pc, PcStat *st)
{
    if (store) {
        g.unc_wr++;
        st->unc_wr++;
        if (in_sdram(phys)) {
            real_write(phys, val, size);
            *writer_at(phys) = pc;
        }
        return;
    }
    g.unc_rd++;
    st->unc_rd++;
    if (in_sdram(phys) && memcmp(real_at(phys), val, size) != 0) {
        report(K_UNCACHED_STALE, pc, phys, real_at(phys), val, size);
        resync(phys, NULL);
    }
}

/* ---- control registers and memory-mapped cache --------------------------- */

static void count_maint(uint32_t pc)
{
    gpointer k = GUINT_TO_POINTER(pc);

    g_hash_table_insert(maint, k, GSIZE_TO_POINTER(
                        GPOINTER_TO_SIZE(g_hash_table_lookup(maint, k)) + 1));
}

static void flush(Cache *c, uint32_t pc)
{
    for (int i = 0; i < N_SETS; i++) {
        Set *s = &c->set[i];

        for (int w = 0; w < N_WAYS; w++) {
            Line *l = &s->way[w];
            uint32_t phys = l->line << 4;

            /* OCF does not write back (8.2.1): dirty data is lost. */
            if (c == &oc && l->v && l->u && in_sdram(phys) &&
                memcmp(real_at(phys), l->data, LINE_SIZE) != 0) {
                report(K_DIRTY_DISCARD, pc, phys, real_at(phys), l->data,
                       LINE_SIZE);
                real_write(phys, l->data, LINE_SIZE);
            }
            l->v = l->u = false;
        }
        lru_reset(s);
    }
}

static void ccr1_write(uint32_t v, uint32_t pc)
{
    g.ccr1_writes++;
    count_maint(pc);
    if (v & CCR1_ICF) {
        g.icf++;
        flush(&ic, pc);
    }
    if (v & CCR1_OCF) {
        g.ocf++;
        flush(&oc, pc);
    }
    ccr1 = v & (CCR1_ICE | CCR1_WT | CCR1_OCE);
}

static void address_array_write(uint32_t a, uint32_t v, uint32_t pc, PcStat *st)
{
    Cache *c = (a & 0x00800000u) ? &oc : &ic;
    unsigned entry = (a >> 4) & (N_SETS - 1);
    int way = (a >> 11) & 3;
    bool assoc = a & 8;
    bool nv = v & 1, nu = c == &oc && (v & 2);
    uint32_t line = ((v & 0x1FFFF800u) | (entry << 4)) >> 4;
    Set *s = &c->set[entry];

    g.aa_writes++;
    count_maint(pc);
    if (assoc) {
        for (int w = 0; w < N_WAYS; w++) {
            Line *l = &s->way[w];

            if (l->v && l->line == line) {
                if (c == &oc && l->u && !(nv && nu)) {
                    write_back(l, pc, st);
                }
                l->v = nv;
                l->u = nv && nu;
                return;
            }
        }
        return;
    }
    {
        Line *l = &s->way[way];

        if (c == &oc && l->v && l->u) {
            write_back(l, pc, st);
        }
        l->line = line;
        l->v = nv;
        l->u = nv && nu;
        lru_set_bits(s, (v >> 4) & 0x3F);
        /* The data array keeps whatever it held: a line made valid this
         * way holds data the model only knows if it was written through
         * the data array. Reload it from memory so a later compare is
         * meaningful unless software filled it itself. */
        if (nv && !c->data_array_dirty) {
            real_read(line << 4, l->data, LINE_SIZE);
        }
    }
}

static void data_array_write(uint32_t a, uint32_t v)
{
    Cache *c = (a & 0x00800000u) ? &oc : &ic;
    Line *l = &c->set[(a >> 4) & (N_SETS - 1)].way[(a >> 11) & 3];
    unsigned lw = (a >> 2) & 3;

    g.da_writes++;
    c->data_array_dirty = true;
    l->data[lw * 4 + 0] = v >> 24;
    l->data[lw * 4 + 1] = v >> 16;
    l->data[lw * 4 + 2] = v >> 8;
    l->data[lw * 4 + 3] = v;
}

/* ---- DMAC ---------------------------------------------------------------- */

static const unsigned dma_unit[4] = { 1, 2, 4, 16 };

/* Memory range of one side of a transfer, or false if that side is a
 * fixed address (a FIFO) or not SDRAM. */
static bool dma_range(uint32_t addr, unsigned mode, uint32_t len,
                      uint32_t *start)
{
    uint32_t phys = addr & 0x1FFFFFFFu;

    if (mode == 0 || mode == 3 || len == 0) {
        return false;
    }
    if (mode == 2) {                    /* decrement */
        phys = phys + 1 - len;
    }
    if (!in_sdram(phys) || !in_sdram(phys + len - 1)) {
        return false;
    }
    *start = phys;
    return true;
}

static void dma_done(int n)
{
    Chan *ch = &dmac[n];
    uint8_t *buf;

    if (!ch->pending) {
        return;
    }
    ch->pending = false;
    g.dma_done++;
    buf = g_malloc(ch->len);
    if (emu_read(ch->dst, buf, ch->len)) {
        real_write(ch->dst, buf, ch->len);
        for (uint32_t p = ch->dst & ~(LINE_SIZE - 1); p < ch->dst + ch->len;
             p += LINE_SIZE) {
            *writer_at(p) = WRITER_DMA | n;
        }
    }
    g_free(buf);
}

static void dma_arm(int n, uint32_t pc)
{
    Chan *ch = &dmac[n];
    unsigned unit = dma_unit[(ch->chcr >> 3) & 3];
    unsigned dm = (ch->chcr >> 14) & 3, sm = (ch->chcr >> 12) & 3;
    uint32_t len = (ch->tcr & 0xFFFFFF) * unit;
    uint32_t start;

    dma_done(n);                        /* a re-arm ends the last transfer */
    g.dma_arm++;
    if (len == 0) {
        len = 0x1000000 * unit;         /* DMATCR 0 = 2^24 transfers */
    }
    if (dma_range(ch->sar, sm, len, &start)) {
        uint8_t *emu = g_malloc(len);

        /* The DMAC reads real memory: anything newer only in the operand
         * cache goes out stale. */
        if (emu_read(start, emu, len)) {
            for (uint32_t i = 0; i < len; i++) {
                if (emu[i] != real_at(start)[i]) {
                    uint32_t lo = i & ~(LINE_SIZE - 1);
                    report(K_DMA_READ_STALE, pc, start + lo, real_at(start + lo),
                           emu + lo, MIN(LINE_SIZE, len - lo));
                    break;
                }
            }
            real_write(start, emu, len);
        }
        g_free(emu);
    }
    if (dma_range(ch->dar, dm, len, &start)) {
        ch->pending = true;
        ch->dst = start;
        ch->len = len;
    }
}

static void dmac_access(uint32_t a, const uint8_t *val, unsigned size,
                        bool store, uint32_t pc)
{
    uint32_t off = a - DMAC_BASE;
    int n = off >> 4;
    uint32_t reg = off & 0xC, x = 0;
    uint32_t *r;

    if (off >= DMAC_CHANS * 0x10 || size > 4) {
        return;
    }
    r = reg == 0 ? &dmac[n].sar : reg == 4 ? &dmac[n].dar :
        reg == 8 ? &dmac[n].tcr : &dmac[n].chcr;
    for (unsigned i = 0; i < size; i++) {
        x = (x << 8) | val[i];
    }
    /* merge a narrower access into the longword */
    {
        unsigned shift = (4 - size - (off & 3)) * 8;
        uint32_t mask = (size == 4 ? 0xFFFFFFFFu : ((1u << (8 * size)) - 1)) << shift;

        x = (*r & ~mask) | ((x << shift) & mask);
    }
    if (!store) {
        if ((reg == 0xC && (x & 2)) || (reg == 8 && (x & 0xFFFFFF) == 0)) {
            dma_done(n);
        }
        return;
    }
    *r = x;
    if (reg == 0xC && (x & 1) && !(x & 2)) {
        dma_arm(n, pc);
    }
}

/* ---- callbacks ----------------------------------------------------------- */

static void find_registers(void);

static void start(void)
{
    GByteArray *b = g_byte_array_sized_new(SDRAM_SIZE);

    started = true;
    find_registers();
    real = g_malloc0(SDRAM_SIZE);
    writer = g_malloc0(SDRAM_SIZE / LINE_SIZE * sizeof(uint32_t));
    if (qemu_plugin_read_memory_vaddr(SDRAM_BASE, b, SDRAM_SIZE) &&
        b->len == SDRAM_SIZE) {
        memcpy(real, b->data, SDRAM_SIZE);
    } else {
        live("cdj850-cache: could not read SDRAM at start; "
                         "shadow starts zeroed\n");
    }
    g_byte_array_free(b, TRUE);
    for (int i = 0; i < N_SETS; i++) {
        lru_reset(&ic.set[i]);
        lru_reset(&oc.set[i]);
    }
}

static void fetch_line(const Insn *in, uint32_t phys, const uint8_t *code,
                       unsigned n)
{
    uint32_t off = phys & (LINE_SIZE - 1);
    Set *s;
    int w;
    Line *l;

    if (!(ccr1 & CCR1_ICE)) {
        if (in_sdram(phys) && memcmp(real_at(phys), code, n) != 0) {
            report(K_CODE_NOT_IN_MEM, in->pc, phys, real_at(phys), code, n);
            resync(phys, NULL);
        }
        return;
    }
    w = lookup(&ic, phys >> 4, &s);
    if (w >= 0) {
        g.ic_hit++;
        l = &s->way[w];
        lru_touch(s, w);
        if (has_data(phys) && memcmp(l->data + off, code, n) != 0) {
            report(K_STALE_CODE, in->pc, phys, l->data + off, code, n);
            resync(phys, l);
        }
        return;
    }
    g.ic_miss++;
    in->stat->ic_miss++;
    w = victim(&ic, s, false);
    l = &s->way[w];
    l->v = false;
    if (fill(l, phys >> 4) && memcmp(l->data + off, code, n) != 0) {
        report(K_CODE_NOT_IN_MEM, in->pc, phys, l->data + off, code, n);
        resync(phys, l);
    }
    lru_touch(s, w);
}

static void prefetch(uint32_t addr, uint32_t pc, PcStat *st)
{
    Set *s;
    int w;

    g.pref++;
    st->pref++;
    if (addr >= CACHED_END || !(ccr1 & CCR1_OCE)) {
        return;
    }
    w = lookup(&oc, addr >> 4, &s);
    if (w >= 0) {
        lru_touch(s, w);
        return;
    }
    g.pref_miss++;
    st->pref_miss++;
    w = victim(&oc, s, true);
    evict(&oc, &s->way[w], pc, st);
    fill(&s->way[w], addr >> 4);
    lru_touch(s, w);
}

static bool read_reg(struct qemu_plugin_register *reg, uint32_t *out)
{
    GByteArray *b = g_byte_array_new();
    bool ok = reg && qemu_plugin_read_register(reg, b) && b->len >= 4;

    if (ok) {
        /* gdb register layout: target (big-endian) byte order */
        *out = (uint32_t)b->data[0] << 24 | b->data[1] << 16 |
               b->data[2] << 8 | b->data[3];
    }
    g_byte_array_free(b, TRUE);
    return ok;
}

static void insn_exec(unsigned int vcpu, void *udata)
{
    Insn *in = udata;
    uint32_t pc = in->pc;

    if (!started) {
        start();
    }
    g.fetch++;
    in->stat->fetch++;
    if (pc < CACHED_END) {
        uint32_t first = LINE_SIZE - (pc & (LINE_SIZE - 1));

        if (first >= in->size) {
            fetch_line(in, pc, in->bytes, in->size);
        } else {                        /* a 32-bit insn across two lines */
            fetch_line(in, pc, in->bytes, first);
            fetch_line(in, pc + first, in->bytes + first, in->size - first);
        }
    } else if (pc < UNCACHED_END) {
        uint32_t phys = pc & 0x1FFFFFFFu;

        if (in_sdram(phys) && memcmp(real_at(phys), in->bytes, in->size) != 0) {
            report(K_CODE_NOT_IN_MEM, pc, phys, real_at(phys), in->bytes,
                   in->size);
            resync(phys, NULL);
        }
    }
    if (in->rte) {
        g.rte++;
    }
    if (in->pref_reg >= 0) {
        uint32_t a;

        if (have_regs && read_reg(reg_r[in->pref_reg], &a)) {
            prefetch(a, pc, in->stat);
        } else {
            pref_unmodelled++;
        }
    }
}

/* A CPU data access to external memory space; false for anything else
 * (on-chip RAM, peripherals, the cache's own windows). */
static bool data_access(uint32_t a, const uint8_t *val, unsigned size,
                        bool store, uint32_t pc, PcStat *st)
{
    if (a < CACHED_END) {
        if (ccr1 & CCR1_OCE) {
            uint32_t first = LINE_SIZE - (a & (LINE_SIZE - 1));

            if (first >= size) {
                cached_access(a, val, size, store, pc, st);
            } else {
                cached_access(a, val, first, store, pc, st);
                cached_access(a + first, val + first, size - first, store,
                              pc, st);
            }
        } else {
            uncached_access(a, val, size, store, pc, st);
        }
        return true;
    }
    if (a < UNCACHED_END) {
        uncached_access(a & 0x1FFFFFFFu, val, size, store, pc, st);
        return true;
    }
    return false;
}

/*
 * SH-2A exception and interrupt entry: R15 -= 4, SR stored; R15 -= 4, PC
 * stored; PC loaded from the vector table. QEMU does it inside a helper,
 * which no memory callback sees, so the push is replayed here as the two
 * CPU stores the chip makes (through the operand cache, like any store).
 * The handler's RTE and the RTOS read the frame back with ordinary loads.
 * The vector read is not replayed: its slot is not known here.
 */
static void entry(unsigned int vcpu, enum qemu_plugin_discon_type type,
                  uint64_t from, uint64_t to, void *udata)
{
    uint32_t sp;
    uint8_t frame[8];

    if (!started) {
        start();
    }
    g.entries++;
    if (!read_reg(reg_r[15], &sp)) {
        g.entries_unmodelled++;
        return;
    }
    if (!emu_read(sp, frame, sizeof(frame))) {
        g.entries_unmodelled++;
        return;
    }
    data_access(sp + 4, frame + 4, 4, true, to, pc_stat(to));  /* SR */
    data_access(sp, frame, 4, true, to, pc_stat(to));          /* PC */
}

static void mem_access(unsigned int vcpu, qemu_plugin_meminfo_t info,
                       uint64_t vaddr, void *udata)
{
    Insn *in = udata;
    uint32_t a = vaddr;
    unsigned size = 1u << qemu_plugin_mem_size_shift(info);
    bool store = qemu_plugin_mem_is_store(info);
    uint8_t val[16];

    if (size > sizeof(val)) {
        return;
    }
    value_bytes(info, val, size);

    if (data_access(a, val, size, store, in->pc, in->stat)) {
        return;
    }
    if (!store) {
        if (a - DMAC_BASE < 0x400) {
            dmac_access(a, val, size, false, in->pc);
        }
        return;
    }
    if (a == CCR1_ADDR && size == 4) {
        ccr1_write((uint32_t)val[0] << 24 | val[1] << 16 | val[2] << 8 | val[3],
                   in->pc);
    } else if (a == CCR2_ADDR && size == 4) {
        ccr2 = (uint32_t)val[0] << 24 | val[1] << 16 | val[2] << 8 | val[3];
    } else if ((a >> 24) == 0xF0 && size == 4) {
        address_array_write(a, (uint32_t)val[0] << 24 | val[1] << 16 |
                            val[2] << 8 | val[3], in->pc, in->stat);
    } else if ((a >> 24) == 0xF1 && size == 4) {
        data_array_write(a, (uint32_t)val[0] << 24 | val[1] << 16 |
                         val[2] << 8 | val[3]);
    } else if (a - DMAC_BASE < 0x400) {
        dmac_access(a, val, size, true, in->pc);
    }
}

static Insn *insn_record(struct qemu_plugin_insn *qi)
{
    uint32_t pc = qemu_plugin_insn_vaddr(qi);
    size_t size = qemu_plugin_insn_size(qi);
    uint8_t bytes[4] = { 0 };
    Insn *in;

    if (size > 4) {
        size = 4;
    }
    qemu_plugin_insn_data(qi, bytes, size);
    in = g_hash_table_lookup(insns, GUINT_TO_POINTER(pc));
    if (in && in->size == size && memcmp(in->bytes, bytes, size) == 0) {
        return in;
    }
    /* New or changed code at this pc: a new record (the old one may still
     * be referenced by translations QEMU has not dropped yet). */
    in = g_new0(Insn, 1);
    in->pc = pc;
    in->size = size;
    memcpy(in->bytes, bytes, size);
    in->stat = pc_stat(pc);
    in->pref_reg = -1;
    if (size == 2 && (bytes[0] & 0xF0) == 0x00 && bytes[1] == 0x83) {
        in->pref_reg = bytes[0] & 0x0F;
    }
    in->rte = size == 2 && bytes[0] == 0x00 && bytes[1] == 0x2B;
    g_hash_table_insert(insns, GUINT_TO_POINTER(pc), in);
    g_ptr_array_add(all_insns, in);
    return in;
}

static void tb_trans(struct qemu_plugin_tb *tb, void *udata)
{
    size_t n = qemu_plugin_tb_n_insns(tb);

    for (size_t i = 0; i < n; i++) {
        struct qemu_plugin_insn *qi = qemu_plugin_tb_get_insn(tb, i);
        Insn *in = insn_record(qi);

        qemu_plugin_register_vcpu_insn_exec_cb(
            qi, insn_exec,
            in->pref_reg >= 0 ? QEMU_PLUGIN_CB_R_REGS : QEMU_PLUGIN_CB_NO_REGS,
            in);
        qemu_plugin_register_vcpu_mem_cb(qi, mem_access, QEMU_PLUGIN_CB_NO_REGS,
                                         QEMU_PLUGIN_MEM_RW, in);
    }
}

static void find_registers(void)
{
    GArray *regs = qemu_plugin_get_registers();
    int found = 0;

    for (guint i = 0; regs && i < regs->len; i++) {
        qemu_plugin_reg_descriptor *d =
            &g_array_index(regs, qemu_plugin_reg_descriptor, i);
        int n;

        if (d->name[0] == 'r' && sscanf(d->name + 1, "%d", &n) == 1 &&
            n >= 0 && n < 16 && !reg_r[n]) {
            char check[8];

            snprintf(check, sizeof(check), "r%d", n);
            if (strcmp(check, d->name) == 0) {
                reg_r[n] = d->handle;
                found++;
            }
        }
    }
    have_regs = found == 16;
    if (!have_regs) {
        live("cdj850-cache: CPU registers not readable by plugins (a QEMU "
             "without the cdj850 plugin register feature): pref @Rn and the "
             "exception-entry push are not modelled\n");
    }
    if (regs) {
        g_array_free(regs, TRUE);
    }
}

/* ---- output -------------------------------------------------------------- */

static gint maint_order(gconstpointer a, gconstpointer b)
{
    uint32_t x = GPOINTER_TO_UINT(a), y = GPOINTER_TO_UINT(b);

    return x < y ? -1 : x > y;
}

static gint report_order(gconstpointer a, gconstpointer b)
{
    const Report *x = *(Report * const *)a, *y = *(Report * const *)b;

    if (x->kind != y->kind) {
        return x->kind < y->kind ? -1 : 1;
    }
    return x->count > y->count ? -1 : x->count < y->count ? 1 :
           x->pc < y->pc ? -1 : x->pc > y->pc;
}

static gint stat_order(gconstpointer a, gconstpointer b)
{
    const PcStat *x = *(PcStat * const *)a, *y = *(PcStat * const *)b;

    return x->pc < y->pc ? -1 : x->pc > y->pc;
}

static double pct(uint64_t part, uint64_t whole)
{
    return whole ? 100.0 * part / whole : 0.0;
}

static void write_outputs(void)
{
    GString *path = g_string_new(NULL);
    FILE *f;

    g_string_printf(path, "%s-report.txt", opt_out);
    f = fopen(path->str, "w");
    if (f) {
        GPtrArray *rs = g_ptr_array_new();
        GHashTableIter it;
        gpointer k, v;
        uint64_t per_kind[K_COUNT] = { 0 };
        char wb[48];

        fprintf(f, "cdj850-cache: SH7263 cache model (8 KB I + 8 KB O, 4-way, "
                "16-byte lines)\n\n");
        fprintf(f, "final CCR1 0x%08x (ICE %d OCE %d WT %d), CCR2 0x%08x\n",
                ccr1, !!(ccr1 & CCR1_ICE), !!(ccr1 & CCR1_OCE),
                !!(ccr1 & CCR1_WT), ccr2);
        fprintf(f, "CCR1 writes %" PRIu64 " (ICF %" PRIu64 ", OCF %" PRIu64
                "), address-array writes %" PRIu64 ", data-array writes %"
                PRIu64 "\n", g.ccr1_writes, g.icf, g.ocf, g.aa_writes,
                g.da_writes);
        fprintf(f, "fetches %" PRIu64 ": I-cache hits %" PRIu64 ", misses %"
                PRIu64 " (%.3f%% of cached fetches)\n", g.fetch, g.ic_hit,
                g.ic_miss, pct(g.ic_miss, g.ic_hit + g.ic_miss));
        fprintf(f, "cached reads %" PRIu64 ": misses %" PRIu64 " (%.3f%%)\n",
                g.rd, g.rd_miss, pct(g.rd_miss, g.rd));
        fprintf(f, "cached writes %" PRIu64 ": misses %" PRIu64 " (%.3f%%)\n",
                g.wr, g.wr_miss, pct(g.wr_miss, g.wr));
        fprintf(f, "dirty write-backs %" PRIu64 "\n", g.writeback);
        fprintf(f, "uncached reads %" PRIu64 ", writes %" PRIu64 "\n",
                g.unc_rd, g.unc_wr);
        fprintf(f, "prefetches %" PRIu64 " (misses %" PRIu64 ", not modelled %"
                PRIu64 ")\n", g.pref, g.pref_miss, pref_unmodelled);
        fprintf(f, "cached accesses outside SDRAM/flash %" PRIu64 "\n",
                g.other_cached);
        fprintf(f, "DMA transfers armed %" PRIu64 ", completions seen %" PRIu64
                "\n", g.dma_arm, g.dma_done);
        fprintf(f, "exception/interrupt entries %" PRIu64 " (push not modelled %"
                PRIu64 "), RTE executed %" PRIu64 "\n", g.entries,
                g.entries_unmodelled, g.rte);
        if (g.rte && !g.entries) {
            fprintf(f, "WARNING: RTE ran but no exception entry was seen: this "
                    "QEMU lacks the SH-2A plugin hook (patches/"
                    "target_sh4_helper.c.patch), so stack frames look stale and "
                    "findings on stacks are not real\n");
        } else if (g.entries_unmodelled) {
            fprintf(f, "WARNING: exception-entry pushes not modelled (no "
                    "plugin-readable r15: this QEMU lacks the cdj850 plugin "
                    "register feature), so findings on stacks are not real\n");
        }
        {
            GList *keys = g_hash_table_get_keys(maint);

            keys = g_list_sort(keys, (GCompareFunc)maint_order);
            fprintf(f, "cache maintenance (CCR1 and address-array writes) by pc:");
            for (GList *l = keys; l; l = l->next) {
                fprintf(f, " 0x%08x x%zu", GPOINTER_TO_UINT(l->data),
                        GPOINTER_TO_SIZE(g_hash_table_lookup(maint, l->data)));
            }
            fputc('\n', f);
            g_list_free(keys);
        }
        fputc('\n', f);

        g_hash_table_iter_init(&it, reports);
        while (g_hash_table_iter_next(&it, &k, &v)) {
            g_ptr_array_add(rs, v);
            per_kind[((Report *)v)->kind] += ((Report *)v)->count;
        }
        g_ptr_array_sort(rs, report_order);
        fprintf(f, "coherency findings: %u distinct (kind, pc)%s\n",
                rs->len, opt_check ? "" : " (check=off)");
        for (int i = 0; i < K_COUNT; i++) {
            if (per_kind[i]) {
                fprintf(f, "  %-18s %" PRIu64 " events\n", kind_name[i],
                        per_kind[i]);
            }
        }
        fprintf(f, "\nkind,pc,addr,count,chip,qemu,last_write_around_cache\n");
        for (guint i = 0; i < rs->len; i++) {
            Report *r = rs->pdata[i];
            GString *s = g_string_new(NULL);

            g_string_append_printf(s, "%s,0x%08x,0x%08x,%" PRIu64 ",",
                                   kind_name[r->kind], r->pc, r->addr, r->count);
            hex(s, r->hw, r->len);
            g_string_append_c(s, ',');
            hex(s, r->emu, r->len);
            g_string_append_printf(s, ",%s\n",
                                   writer_str(r->writer, wb, sizeof(wb)));
            fputs(s->str, f);
            g_string_free(s, TRUE);
        }
        fclose(f);
        g_ptr_array_free(rs, TRUE);
    }

    if (opt_profile) {
        g_string_printf(path, "%s-profile.csv", opt_out);
        f = fopen(path->str, "w");
        if (f) {
            GPtrArray *ss = g_ptr_array_new();
            GHashTableIter it;
            gpointer k, v;

            g_hash_table_iter_init(&it, pcstats);
            while (g_hash_table_iter_next(&it, &k, &v)) {
                g_ptr_array_add(ss, v);
            }
            g_ptr_array_sort(ss, stat_order);
            fprintf(f, "pc,fetch,ic_miss,rd,rd_miss,wr,wr_miss,writeback,"
                    "unc_rd,unc_wr,pref,pref_miss\n");
            for (guint i = 0; i < ss->len; i++) {
                PcStat *s = ss->pdata[i];

                fprintf(f, "0x%08x,%" PRIu64 ",%" PRIu64 ",%" PRIu64 ",%"
                        PRIu64 ",%" PRIu64 ",%" PRIu64 ",%" PRIu64 ",%" PRIu64
                        ",%" PRIu64 ",%" PRIu64 ",%" PRIu64 "\n", s->pc,
                        s->fetch, s->ic_miss, s->rd, s->rd_miss, s->wr,
                        s->wr_miss, s->writeback, s->unc_rd, s->unc_wr,
                        s->pref, s->pref_miss);
            }
            fclose(f);
            g_ptr_array_free(ss, TRUE);
        }
    }
    g_string_free(path, TRUE);
}

static void plugin_exit(void *udata)
{
    write_outputs();
}

static bool parse_bool_arg(const char *name, const char *val, bool *out)
{
    if (!qemu_plugin_bool_parse(name, val, out)) {
        fprintf(stderr, "cdj850-cache: bad value for %s: %s\n", name, val);
        return false;
    }
    return true;
}

/* Emulator debug/acceleration knobs that store to guest memory outside any
 * guest store instruction (target/sh4/op_helper.c). */
static const char *const hidden_writers[] = {
    "CDJ_NATIVE_COPY", "CDJ_PPOKE", "CDJ_PSTORE", "CDJ_RPOKE",
};

QEMU_PLUGIN_EXPORT int qemu_plugin_install(qemu_plugin_id_t id,
                                           const qemu_info_t *info,
                                           int argc, char **argv)
{
    opt_out = g_strdup("cdj850-cache");
    for (int i = 0; i < argc; i++) {
        char **kv = g_strsplit(argv[i], "=", 2);
        bool ok = kv[0] && kv[1];

        if (ok && g_strcmp0(kv[0], "out") == 0) {
            g_free(opt_out);
            opt_out = g_strdup(kv[1]);
        } else if (ok && g_strcmp0(kv[0], "check") == 0) {
            ok = parse_bool_arg(kv[0], kv[1], &opt_check);
        } else if (ok && g_strcmp0(kv[0], "profile") == 0) {
            ok = parse_bool_arg(kv[0], kv[1], &opt_profile);
        } else if (ok && g_strcmp0(kv[0], "quiet") == 0) {
            ok = parse_bool_arg(kv[0], kv[1], &opt_quiet);
        } else {
            fprintf(stderr, "cdj850-cache: unknown argument %s\n", argv[i]);
            ok = false;
        }
        g_strfreev(kv);
        if (!ok) {
            return -1;
        }
    }
    if (strcmp(info->target_name, "sh4eb") != 0 &&
        strcmp(info->target_name, "sh4") != 0) {
        fprintf(stderr, "cdj850-cache: built for the SH7263 board (sh4eb), "
                "not %s\n", info->target_name);
        return -1;
    }
    reports = g_hash_table_new_full(g_int64_hash, g_int64_equal, g_free,
                                    g_free);
    pcstats = g_hash_table_new_full(NULL, NULL, NULL, g_free);
    insns = g_hash_table_new(NULL, NULL);
    maint = g_hash_table_new(NULL, NULL);
    all_insns = g_ptr_array_new_with_free_func(g_free);
    qemu_plugin_register_vcpu_discon_cb(id, QEMU_PLUGIN_DISCON_INTERRUPT |
                                        QEMU_PLUGIN_DISCON_EXCEPTION, entry,
                                        NULL);
    for (size_t i = 0; i < G_N_ELEMENTS(hidden_writers); i++) {
        const char *v = getenv(hidden_writers[i]);

        if (v && *v && strcmp(v, "0") != 0) {
            fprintf(stderr, "cdj850-cache: %s is set: it writes guest memory "
                    "from a helper the model cannot see, so findings near "
                    "those writes are not real\n", hidden_writers[i]);
        }
    }
    qemu_plugin_register_vcpu_tb_trans_cb(id, tb_trans, NULL);
    qemu_plugin_register_atexit_cb(id, plugin_exit, NULL);
    return 0;
}
