/* SPDX-License-Identifier: GPL-2.0-or-later */
#include "cdj_common.h"
#include "cmt.h"
#include "intc.h"
/*
 * Pioneer CDJ-850 interrupt + compare-match timer core (Renesas SH7263).
 *
 * The MAIN firmware programs CMT channel 0 as its RTOS tick (CMCOR 0x1045,
 * CKS 0 = Pphi/8, CMSTR 1) and then reads CMSTR back: with the channel still
 * unmodelled the read returns 0, the firmware concludes the timer did not
 * start and halts in its init error loop. This file gives it a real tick.
 *
 *   +0x00 CMSTR    bit0 = start channel 0 (bit1 = channel 1, stored only)
 *   +0x02 CMCSR_0  bit6 CMIE, bit7 CMF, bits1:0 CKS (pclk/8,32,128,512)
 *   +0x04 CMCNT_0  up-counter (synthesised from the time remaining)
 *   +0x06 CMCOR_0  compare value
 *   +0x08..0x0C    channel 1 (plain storage; the firmware never uses it)
 *
 * Interrupt delivery carries one vector in env.sh2a_irq_vector (there is no
 * INTC model to arbitrate, so sources queue here and a pump presents them
 * one at a time). CMI0 is vector 142 (SH7263 Hardware Manual Table 6.4;
 * IPR08 in that table is USB|LCDC|CMT0|CMT1, and the firmware arms CMT0
 * there). The firmware's own vector table (VBR=0xFFF80000) confirms it:
 * [VBR+142*4] holds its tick trampoline, while the previously used 188
 * (an IIC3 slot) holds the default-ignore ISR, so the RTOS tick never ran.
 *
 * CMI0 takes its priority from INTC IPR08[7:4]. Stock MAIN sets it to
 * 7, so IRQ7 (vector 71) wins a tie against CMI0 (vector 142).
 */

#define CDJ850_CMT_BASE     0xFFFEC000
#define CDJ850_CMT_SIZE     0x10
/* Pphi feeds the timers; 66 MHz bus / 2 is the working assumption. */
#define CDJ850_CMT_PCLK     33333333
#define CDJ850_CMI0_VECTOR  142
#define CDJ850_CMT_CMIE     0x40
#define CDJ850_CMT_CMF      0x80

/* The queue is per-board (one MAIN CPU); no vmstate versioning beyond v1. */
#define CDJ850_IRQ_QUEUE_LEN  32

typedef struct {
    uint16_t vec;
    uint8_t level;
    bool used;
} Cdj850Irq;

static struct Cdj850IrqQueue {
    Cdj850Irq q[CDJ850_IRQ_QUEUE_LEN];
    unsigned head, tail;
    QEMUTimer *pump;
    SuperHCPU *cpu;
    unsigned dropped;
    uint16_t cur_vec;        /* vector last handed to the CPU */
    uint8_t cur_level;
    bool cur_valid;          /* ... and not yet taken or cancelled */
    unsigned stolen;         /* foreign acknowledgements refused */
    unsigned presented;
    int64_t present_ns;
    int64_t warn_ns;
    unsigned warned_stuck;
    bool debug;
} cdj850_irq;

static void cdj850_irq_pump(void *opaque);

/*
 * Acknowledge one source. A peripheral clearing its request must only clear
 * CPU_INTERRUPT_HARD if the vector on the pin is its own; otherwise it would
 * destroy another source's pending interrupt.
 */
static void cdj850_ack(SuperHCPU *cpu, uint16_t vec)
{
    CPUState *cs = CPU(cpu);

    if (!(cs->interrupt_request & CPU_INTERRUPT_HARD)) {
        return;                 /* already taken -- nothing to withdraw */
    }
    if (cdj850_irq.cur_valid && cdj850_irq.cur_vec != vec) {
        /* Another source's request is standing. */
        if (cdj850_irq.stolen++ < 8) {
            info_report("cdj850: refused ack of vector %u by %u",
                        cdj850_irq.cur_vec, vec);
        }
        return;
    }
    cdj850_irq.cur_valid = false;
    cpu_reset_interrupt(cs, CPU_INTERRUPT_HARD);
    cdj850_irq_pump(NULL);        /* let anything queued behind it through */
}

/* SH7263 Table 6.4: at the same IPR level, the lower vector number wins. */
static inline bool cdj850_irq_higher(uint8_t la, uint16_t va,
                                     uint8_t lb, uint16_t vb)
{
    return la > lb || (la == lb && va < vb);
}

static void cdj850_irq_pump(void *opaque)
{
    CPUState *cs;
    unsigned i, best = CDJ850_IRQ_QUEUE_LEN;

    if (!cdj850_irq.cpu) {
        return;
    }
    cs = CPU(cdj850_irq.cpu);

    if (!(cs->interrupt_request & CPU_INTERRUPT_HARD)) {
        cdj850_irq.cur_valid = false;         /* the CPU took the last one */
    }

    /*
     * Present the highest-level pending request, not the oldest. SH-2A only
     * accepts a request above SR.IMASK, so strict FIFO would let a masked
     * source block every tick queued behind it. A higher-level raise
     * preempts a standing request, which goes back in the queue.
     */
    for (i = cdj850_irq.head; i != cdj850_irq.tail;
         i = (i + 1) % CDJ850_IRQ_QUEUE_LEN) {
        if (!cdj850_irq.q[i].used) {
            continue;
        }
        if (best == CDJ850_IRQ_QUEUE_LEN ||
            cdj850_irq_higher(cdj850_irq.q[i].level, cdj850_irq.q[i].vec,
                              cdj850_irq.q[best].level,
                              cdj850_irq.q[best].vec)) {
            best = i;
        }
    }

    if (best != CDJ850_IRQ_QUEUE_LEN &&
        (cs->interrupt_request & CPU_INTERRUPT_HARD) &&
        cdj850_irq.cur_valid &&
        cdj850_irq_higher(cdj850_irq.q[best].level, cdj850_irq.q[best].vec,
                          cdj850_irq.cur_level, cdj850_irq.cur_vec)) {
        /* Preempt: the standing request goes back into the queue. */
        unsigned n = (cdj850_irq.tail + 1) % CDJ850_IRQ_QUEUE_LEN;

        if (n != cdj850_irq.head) {
            cdj850_irq.q[cdj850_irq.tail] = (Cdj850Irq){
                .vec = cdj850_irq.cur_vec,
                .level = cdj850_irq.cur_level,
                .used = true };
            cdj850_irq.tail = n;
            cdj850_irq.cur_valid = false;
            cpu_reset_interrupt(cs, CPU_INTERRUPT_HARD);
        }
    }

    if (best != CDJ850_IRQ_QUEUE_LEN &&
        !(cs->interrupt_request & CPU_INTERRUPT_HARD)) {
        Cdj850Irq *e = &cdj850_irq.q[best];

        e->used = false;
        while (cdj850_irq.head != cdj850_irq.tail &&
               !cdj850_irq.q[cdj850_irq.head].used) {
            cdj850_irq.head = (cdj850_irq.head + 1) % CDJ850_IRQ_QUEUE_LEN;
        }
        cdj850_irq.cpu->env.sh2a_irq_vector = e->vec;
        cdj850_irq.cpu->env.sh2a_irq_level = e->level;
        cdj850_irq.cur_vec = e->vec;
        cdj850_irq.cur_level = e->level;
        cdj850_irq.cur_valid = true;
        cdj850_irq.presented++;
        cdj850_irq.present_ns = qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL);
        cpu_interrupt(cs, CPU_INTERRUPT_HARD);
        if (cdj850_irq.debug) {
            info_report("cdj850: present vector %u level %u at %" PRId64 " ms",
                        e->vec, e->level,
                        cdj850_irq.present_ns / 1000000);
        }
    } else if (opaque && cdj850_irq.cur_valid &&
               (cs->interrupt_request & CPU_INTERRUPT_HARD)) {
        /* cpu_interrupt() already signals TCG. Only retry from the timer:
         * a full kick from an IRQ handler would exit the vCPU loop even
         * though the next interrupt is already presented for its RTE. */
        int64_t now = qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL);

        qemu_cpu_kick(cs);
        if (now - cdj850_irq.present_ns > 1000000000LL &&
            now - cdj850_irq.warn_ns > 1000000000LL &&
            cdj850_irq.warned_stuck < 6) {
            CPUSH4State *env = &cdj850_irq.cpu->env;

            cdj850_irq.warned_stuck++;
            cdj850_irq.warn_ns = now;
            warn_report("cdj850: vector %u level %u presented at %" PRId64
                        " ms still not taken at %" PRId64 " ms"
                        " -- sr=0x%08x imask=%u bl=%u pc=0x%08x",
                        cdj850_irq.cur_vec, cdj850_irq.cur_level,
                        cdj850_irq.present_ns / 1000000, now / 1000000,
                        env->sr, (env->sr >> 4) & 0xf,
                        (env->sr >> SR_BL) & 1, env->pc);
        }
    }

    /* Keep pumping while anything is queued or a presented vector is not
     * yet taken (10 us; at 100 us the GUI board's queue overflowed). */
    if (cdj850_irq.pump && !timer_pending(cdj850_irq.pump) &&
        (cdj850_irq.head != cdj850_irq.tail ||
         (cdj850_irq.cur_valid &&
          (cs->interrupt_request & CPU_INTERRUPT_HARD)))) {
        timer_mod(cdj850_irq.pump,
                  qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL) + 10000);
    }
}

/*
 * Called by the CPU right after it accepts an interrupt (SR.IMASK is now
 * raised). Present the next pending vector immediately: it stands while the
 * handler runs and is taken on its RTE. Without this, a request that lost
 * to a higher level waited for the pump timer, which only fires between TCG
 * slices (~1 ms): the IRQ7 (DSPDREQ) audio feed lost to the CMT tick for
 * several milliseconds per block and the DSP starved.
 */
static void cdj850_irq_taken(CPUSH4State *env)
{
    (void)env;
    cdj850_irq.cur_valid = false;
    cdj850_irq_pump(NULL);
}

static void cdj850_raise(SuperHCPU *cpu, uint16_t vec, uint8_t level)
{
    unsigned next, i;

    cdj850_irq.cpu = cpu;
    cpu->env.sh2a_irq_taken = cdj850_irq_taken;

    /* A request already presented to the CPU is still pending. */
    if (cdj850_irq_pending(vec)) {
        return;
    }

    next = (cdj850_irq.tail + 1) % CDJ850_IRQ_QUEUE_LEN;
    if (next == cdj850_irq.head) {
        /* Entries taken or withdrawn out of order leave holes that only
         * close when the head passes them; squeeze them out first. */
        Cdj850Irq live[CDJ850_IRQ_QUEUE_LEN];
        unsigned n = 0;

        for (i = cdj850_irq.head; i != cdj850_irq.tail;
             i = (i + 1) % CDJ850_IRQ_QUEUE_LEN) {
            if (cdj850_irq.q[i].used) {
                live[n++] = cdj850_irq.q[i];
            }
        }
        memset(cdj850_irq.q, 0, sizeof(cdj850_irq.q));
        memcpy(cdj850_irq.q, live, n * sizeof(live[0]));
        cdj850_irq.head = 0;
        cdj850_irq.tail = n;
        next = (cdj850_irq.tail + 1) % CDJ850_IRQ_QUEUE_LEN;
    }
    if (next == cdj850_irq.head) {
        if (cdj850_irq.dropped++ < 8) {
            warn_report("cdj850: interrupt queue full, dropping vector %u",
                        vec);
        }
        return;
    }
    cdj850_irq.q[cdj850_irq.tail] = (Cdj850Irq){ .vec = vec, .level = level,
                                                 .used = true };
    cdj850_irq.tail = next;

    if (!cdj850_irq.pump) {
        cdj850_irq.pump = timer_new_ns(QEMU_CLOCK_VIRTUAL, cdj850_irq_pump,
                                      &cdj850_irq);
    }
    cdj850_irq_pump(NULL);
}

/* Entry point for the other interrupt sources (xc/intc.c). */
void cdj850_irq_raise(SuperHCPU *cpu, uint16_t vec, uint8_t level)
{
    cdj850_raise(cpu, vec, level);
}

/*
 * For level-sensitive sources (xc/usb.c): is a request for vec queued, or
 * presented to the CPU and not yet taken?
 */
bool cdj850_irq_pending(uint16_t vec)
{
    unsigned i;

    for (i = cdj850_irq.head; i != cdj850_irq.tail;
         i = (i + 1) % CDJ850_IRQ_QUEUE_LEN) {
        if (cdj850_irq.q[i].used && cdj850_irq.q[i].vec == vec) {
            return true;
        }
    }
    return cdj850_irq.cpu && cdj850_irq.cur_valid &&
           cdj850_irq.cur_vec == vec &&
           (CPU(cdj850_irq.cpu)->interrupt_request & CPU_INTERRUPT_HARD);
}

/*
 * A level-sensitive source whose request line dropped: remove its queued
 * request and withdraw it from the CPU if presented but not yet taken.
 */
void cdj850_irq_withdraw(SuperHCPU *cpu, uint16_t vec)
{
    unsigned i;

    for (i = cdj850_irq.head; i != cdj850_irq.tail;
         i = (i + 1) % CDJ850_IRQ_QUEUE_LEN) {
        if (cdj850_irq.q[i].used && cdj850_irq.q[i].vec == vec) {
            cdj850_irq.q[i].used = false;
        }
    }
    while (cdj850_irq.head != cdj850_irq.tail &&
           !cdj850_irq.q[cdj850_irq.head].used) {
        cdj850_irq.head = (cdj850_irq.head + 1) % CDJ850_IRQ_QUEUE_LEN;
    }
    if (cdj850_irq.cur_valid && cdj850_irq.cur_vec == vec) {
        cdj850_ack(cpu, vec);
    }
}

static const VMStateDescription vmstate_cdj850_irq = {
    .name = "cdj850-irq",
    .version_id = 1,
    .minimum_version_id = 1,
    .fields = (const VMStateField[]) {
        VMSTATE_BUFFER_UNSAFE(q, struct Cdj850IrqQueue, 0,
                              sizeof(cdj850_irq.q)),
        VMSTATE_UINT32(head, struct Cdj850IrqQueue),
        VMSTATE_UINT32(tail, struct Cdj850IrqQueue),
        VMSTATE_UINT16(cur_vec, struct Cdj850IrqQueue),
        VMSTATE_UINT8(cur_level, struct Cdj850IrqQueue),
        VMSTATE_BOOL(cur_valid, struct Cdj850IrqQueue),
        VMSTATE_END_OF_LIST()
    }
};

typedef struct {
    MemoryRegion iomem;
    QEMUTimer *timer;
    SuperHCPU *cpu;
    uint16_t cmstr, cmcsr0, cmcnt0, cmcor0;
    uint16_t cmcsr1, cmcnt1, cmcor1;   /* channel 1: storage only */
    uint64_t period_ns;
    uint64_t next_ns;
    uint32_t missed;         /* compare matches while CMF was still set */
} Cdj850Cmt;

static Cdj850Cmt *cdj850_cmt;
static const int cmt_div[4] = { 8, 32, 128, 512 };

static void cmt_update_irq(Cdj850Cmt *s)
{
    unsigned level = (cdj850_intc_reg16(CDJ850_INTC_IPR08)
                      & CDJ850_IPR08_CMT0_MASK) >> 4;
    unsigned enabled = CDJ850_CMT_CMIE | CDJ850_CMT_CMF;

    if (!level || (s->cmcsr0 & enabled) != enabled) {
        cdj850_irq_withdraw(s->cpu, CDJ850_CMI0_VECTOR);
    } else if (!cdj850_irq_pending(CDJ850_CMI0_VECTOR)) {
        cdj850_raise(s->cpu, CDJ850_CMI0_VECTOR, level);
    }
}

void cdj850_cmt_priority_changed(void)
{
    Cdj850Cmt *s = cdj850_cmt;

    if (s) {
        /* Re-arbitrate any outstanding compare match at its new priority. */
        cdj850_irq_withdraw(s->cpu, CDJ850_CMI0_VECTOR);
        cmt_update_irq(s);
    }
}

static void cmt_rearm(Cdj850Cmt *s)
{
    uint64_t ticks;

    if (!(s->cmstr & 1) || s->cmcor0 == 0) {
        timer_del(s->timer);
        s->period_ns = 0;
        return;
    }

    ticks = (uint64_t)s->cmcor0 + 1;
    s->period_ns = ticks * cmt_div[s->cmcsr0 & 3] * 1000000000ULL /
                   CDJ850_CMT_PCLK;
    /*
     * CDJ850_CMT_SPEED=<percent> (default 100): run the RTOS tick faster
     * than the hardware (the firmware uses a 1 ms tick). Speeds up all of
     * the board's timing. Floored at 50 us.
     */
    {
        const char *sp = getenv("CDJ850_CMT_SPEED");
        unsigned pct = sp ? (unsigned)strtoul(sp, NULL, 0) : 100;

        if (pct && pct != 100) {
            s->period_ns = s->period_ns * 100 / pct;
            if (s->period_ns < 50000) {
                s->period_ns = 50000;
            }
        }
    }
    if (s->period_ns == 0) {
        s->period_ns = 1000;
    }
    s->next_ns = qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL) + s->period_ns;
    if (getenv("CDJ850_CMT_DEBUG")) {
        info_report("cdj850: cmt rearm cmcor=0x%04x cks=%d div=%d "
                    "period=%llu ns", s->cmcor0, s->cmcsr0 & 3,
                    cmt_div[s->cmcsr0 & 3],
                    (unsigned long long)s->period_ns);
    }
    timer_mod(s->timer, s->next_ns);
}

/*
 * The next compare match is due one period after the last one was due, not
 * after this callback ran: QEMU runs timer callbacks late while TCG executes
 * a slice, and rearming from "now" added that lateness to every period (the
 * RTOS tick measured 500-750 per emulated second instead of 1000, slowing
 * key-hold timers and playback). A callback more than CMT_MAX_LAG periods
 * late (a stopped VM, a debugger) resynchronises instead of bursting.
 */
#define CMT_MAX_LAG 8

/*
 * A compare match that finds CMF still set (the CPU has not taken the last
 * tick yet, because the emulated CPU fell behind wall-clock time for a
 * moment) would merge into it and the RTOS would lose a tick for good. Count
 * it instead and raise it again as soon as the handler clears CMF, so the
 * tick count catches up after a stall. At most CMT_MAX_MISSED are kept.
 * CDJ850_CMT_CATCHUP=0 restores the plain one-flag behaviour.
 */
#define CMT_MAX_MISSED 50

static bool cmt_catchup(void)
{
    static int on = -1;

    if (on < 0) {
        const char *e = getenv("CDJ850_CMT_CATCHUP");

        on = !(e && *e == '0');
    }
    return on;
}

static void cmt_fire(void *opaque)
{
    Cdj850Cmt *s = opaque;
    int64_t now = qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL);

    if ((s->cmcsr0 & CDJ850_CMT_CMF) && cmt_catchup() &&
        s->missed < CMT_MAX_MISSED) {
        s->missed++;
    }
    s->cmcsr0 |= CDJ850_CMT_CMF;
    cmt_update_irq(s);
    if (!(s->cmstr & 1) || s->cmcor0 == 0 || s->period_ns == 0) {
        cmt_rearm(s);
        return;
    }
    s->next_ns += s->period_ns;
    if ((int64_t)s->next_ns < now - (int64_t)(CMT_MAX_LAG * s->period_ns)) {
        s->next_ns = now + s->period_ns;
    }
    timer_mod(s->timer, s->next_ns);
}

static uint64_t cmt_read(void *opaque, hwaddr off, unsigned size)
{
    Cdj850Cmt *s = opaque;
    uint64_t v;

    (void)size;
    switch (off) {
    case 0x00: v = s->cmstr; break;
    case 0x02: v = s->cmcsr0; break;
    case 0x04:
        if (s->period_ns) {
            int64_t left = s->next_ns - qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL);

            if (left < 0) {
                left = 0;
            }
            v = s->cmcor0 -
                (uint16_t)((uint64_t)left * s->cmcor0 / s->period_ns);
        } else {
            v = s->cmcnt0;
        }
        break;
    case 0x06: v = s->cmcor0; break;
    case 0x08: v = s->cmcsr1; break;
    case 0x0A: v = s->cmcnt1; break;
    case 0x0C: v = s->cmcor1; break;
    default:   v = 0; break;
    }
    if (getenv("CDJ850_TRACE")) {
        info_report("cdj850: cmt read +0x%02x = 0x%04x at pc=0x%08x "
                    "%" PRIu64 " ms", (unsigned)off, (unsigned)v,
                    s->cpu->env.pc,
                    qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL) / 1000000);
    }
    return v;
}

static void cmt_write(void *opaque, hwaddr off, uint64_t val, unsigned size)
{
    Cdj850Cmt *s = opaque;

    (void)size;
    if (getenv("CDJ850_TRACE")) {
        info_report("cdj850: cmt write +0x%02x = 0x%04x at pc=0x%08x "
                    "%" PRIu64 " ms", (unsigned)off, (unsigned)val,
                    s->cpu->env.pc,
                    qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL) / 1000000);
    }
    if (getenv("CDJ850_CMT_DEBUG")) {
        info_report("cdj850: cmt write +0x%02x = 0x%04x",
                    (unsigned)off, (unsigned)val);
    }

    switch (off) {
    case 0x00:
        s->cmstr = val;
        cmt_rearm(s);
        break;
    case 0x02: {
        /* CMF is write-0-to-clear; the handler's ack drops the request.
         * Clearing CMF does not restart the counter: only a new clock
         * select (CKS) changes the period. Rearming on every ack made each
         * tick last one period plus the interrupt latency. */
        unsigned old_cks = s->cmcsr0 & 3;

        s->cmcsr0 = (s->cmcsr0 & val & CDJ850_CMT_CMF) | (val & 0x7f);
        if (!(s->cmcsr0 & CDJ850_CMT_CMF) && s->missed) {
            s->missed--;
            s->cmcsr0 |= CDJ850_CMT_CMF;
        }
        cmt_update_irq(s);
        if ((s->cmcsr0 & 3) != old_cks || !timer_pending(s->timer)) {
            cmt_rearm(s);
        }
        break;
    }
    case 0x04: s->cmcnt0 = val; break;
    case 0x06: s->cmcor0 = val; cmt_rearm(s); break;
    case 0x08: s->cmcsr1 = val; break;
    case 0x0A: s->cmcnt1 = val; break;
    case 0x0C: s->cmcor1 = val; break;
    default:   break;
    }
}

static const MemoryRegionOps cmt_ops = {
    .read = cmt_read,
    .write = cmt_write,
    .endianness = DEVICE_BIG_ENDIAN,
    .valid = { .min_access_size = 1, .max_access_size = 4 },
};

static const VMStateDescription vmstate_cdj850_cmt = {
    .name = "cdj850-cmt",
    .version_id = 1,
    .minimum_version_id = 1,
    .fields = (const VMStateField[]) {
        VMSTATE_TIMER_PTR(timer, Cdj850Cmt),
        VMSTATE_UINT16(cmstr, Cdj850Cmt),
        VMSTATE_UINT16(cmcsr0, Cdj850Cmt),
        VMSTATE_UINT16(cmcnt0, Cdj850Cmt),
        VMSTATE_UINT16(cmcor0, Cdj850Cmt),
        VMSTATE_UINT16(cmcsr1, Cdj850Cmt),
        VMSTATE_UINT16(cmcnt1, Cdj850Cmt),
        VMSTATE_UINT16(cmcor1, Cdj850Cmt),
        VMSTATE_UINT64(period_ns, Cdj850Cmt),
        VMSTATE_UINT64(next_ns, Cdj850Cmt),
        VMSTATE_UINT32(missed, Cdj850Cmt),
        VMSTATE_END_OF_LIST()
    }
};

static void cdj850_irq_reset(void *opaque)
{
    memset(cdj850_irq.q, 0, sizeof(cdj850_irq.q));
    cdj850_irq.head = cdj850_irq.tail = 0;
    cdj850_irq.cur_valid = false;
    if (cdj850_irq.pump) {
        timer_del(cdj850_irq.pump);
    }
}

static void cdj850_cmt_reset(void *opaque)
{
    Cdj850Cmt *s = opaque;

    s->cmstr = s->cmcsr0 = s->cmcnt0 = s->cmcor0 = 0;
    s->cmcsr1 = s->cmcnt1 = s->cmcor1 = 0;
    s->period_ns = s->next_ns = 0;
    s->missed = 0;
    timer_del(s->timer);
}

void cdj850_cmt_init(MemoryRegion *sysmem, SuperHCPU *cpu)
{
    Cdj850Cmt *s;

    cdj850_irq.debug = getenv("CDJ850_IRQ_DEBUG") != NULL;
    qemu_register_reset(cdj850_irq_reset, NULL);
    vmstate_register_any(NULL, &vmstate_cdj850_irq, &cdj850_irq);

    /* CDJ850_CMT_OFF=1 leaves the timer unimplemented (no RTOS tick). */
    if (getenv("CDJ850_CMT_OFF")) {
        create_unimplemented_device("cdj850.cmt", CDJ850_CMT_BASE,
                                    CDJ850_CMT_SIZE);
        return;
    }

    s = g_new0(Cdj850Cmt, 1);
    cdj850_cmt = s;
    s->cpu = cpu;
    s->timer = timer_new_ns(QEMU_CLOCK_VIRTUAL, cmt_fire, s);
    qemu_register_reset(cdj850_cmt_reset, s);
    vmstate_register_any(NULL, &vmstate_cdj850_cmt, s);
    memory_region_init_io(&s->iomem, NULL, &cmt_ops, s, "cdj850.cmt",
                          CDJ850_CMT_SIZE);
    /* Priority 1: the sh7263.internal unimplemented region covers this. */
    memory_region_add_subregion_overlap(sysmem, CDJ850_CMT_BASE, &s->iomem,
                                        1);
}
