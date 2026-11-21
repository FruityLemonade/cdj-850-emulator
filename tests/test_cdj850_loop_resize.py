# SPDX-License-Identifier: GPL-2.0-or-later
"""loop_resize on the CDJ-850 MAIN 1.13: CALL < halves and CALL > doubles an active loop.

* static: the one pool-word redirect (0x0C0D9994, its two readers, all outside the boot-critical
  closure), nothing else written outside the loader, composition with every mod on the branch;
* the compiled payload on the SH-2A interpreter (tests/sh2a.py) against a Python reference: the
  new out for grid loops (whole beats, 1/16 steps, 127.96 BPM, a variable grid, an in off the
  grid), frame loops without a grid, the limits, and the entry point end to end with stand-ins for
  cue_call_prev_next and the state-4 BEAT handler (CA90 set for the wanted out and put back);
* live (QEMU + a baken stick: 128.00, 127.96, 120 -> 133.33 BPM and a WAV without grid): a 4-beat
  loop, CALL < twice and CALL > three times, against the grid frames in RAM; the loop plays
  between the points after every press; a manual LOOP IN/OUT loop, a memory loop, a halving with
  the playhead past the new out, the limits, CALL outside a loop (stock cue), EXIT/RELOOP/MEMORY.
"""
import json
import os
import struct
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "mods"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import patch_main  # noqa: E402
import payloadbuild  # noqa: E402
from sh2a import Memory, Sh2a  # noqa: E402

MAIN = ROOT / "extract" / "cdj850" / "main_unpacked.bin"
FLASH = ROOT / "extract" / "cdj850" / "flash_cdj850.bin"
BAKEN = ROOT / "tools" / "baken" / "baken"
BASE = 0x0C000000
MODS = ["loop_resize", "loop_resize_tick", "loop_resize_memory"]
POOL, STOCK, READERS = 0x0C0D9994, 0x0C0D3B1A, [0x0C0D98AC, 0x0C0D98EA]
STATE4_KEYS, MEMORY_BUTTON = 0x0C0E0F34, 0x0C0D4172
# (pool word, stock value, instructions that read it, payload symbol)
POOLS = [(POOL, STOCK, READERS, "loop_resize_call"),
         (0x0C0D7B98, STATE4_KEYS, [0x0C0D79A6], "c850_loop_tick"),
         (0x0C0D9714, MEMORY_BUTTON, [0x0C0D9664], "loop_resize_memory_button")]
BEAT_HANDLER4 = 0x0C0E5390
GRID_CTX = 0x0C9D24FC
BASE_TENTHS = 0x0C9D23EC
USB_MODE = 0x0CE6D25A
SOURCE = 0x0CE6D252
PLAYER_STATE = 0x0CE6D25D       # + source
LOOP_ACTIVE = 0x0CE6D0ED
CA90 = 0x0CE6CA90
LOOP_IN = 0x0C7F6498
OUT_REC = 0x0C7F64B0
OUT_REQ = 0x0C7F64DC
TRACKLEN = 0x0C7F650C
BEATS = 0x0C1E1088
FRAC = 0x0CE6D154
MAX_Q = 512

needs_toolchain = pytest.mark.skipif(not payloadbuild.have_toolchain(), reason="sh-elf toolchain not installed")
needs_main = pytest.mark.skipif(not MAIN.is_file(), reason="extract/cdj850/main_unpacked.bin is not present")


# ---- Python reference of payload/loop_resize.c ----

def ms_to_frames(ms):
    return (ms * 75 + 500) // 1000


class Grid:
    """The grid window (entries (beat_in_bar, bpm100, ms) of window 0) and the base BPM word."""

    def __init__(self, entries, base=None, total=None):
        self.e = entries
        self.total = len(entries) if total is None else total
        self.base = (entries[0][1] + 5) // 10 if base is None else base

    @property
    def n(self):
        return min(1000, self.total)

    def f(self, i):
        return ms_to_frames(self.e[i][2])

    def ok(self, i):
        return any((self.e[j][1] + 5) // 10 == self.base for j in range(max(0, i - 8), min(self.n, i + 9)))

    def at_or_before(self, frame):
        n = self.n
        if n == 0 or frame < self.f(0):
            return None
        if frame >= self.f(n - 1):
            return None if self.total > n else n - 1
        return max(i for i in range(n) if self.f(i) <= frame)

    def beat_ms(self, i, k):
        if i + k < self.n:
            return self.e[i + k][2]
        bpm100 = self.e[i][1] or 13000
        return self.e[i][2] + (k * 6000000 + bpm100 // 2) // bpm100

    def grid_ms(self, i, p):
        t0, r = self.beat_ms(i, p >> 4), p & 15
        return t0 if r == 0 else t0 + (r * (self.beat_ms(i, (p >> 4) + 1) - t0) + 8) // 16

    def anchor(self, loop_in):
        i = self.at_or_before(loop_in)
        if i is None or not self.ok(i):
            return None
        s = next((s for s in range(16) if ms_to_frames(self.grid_ms(i, s)) == loop_in), 16)
        return i, s

    def out(self, loop_in, q):
        i, s = self.anchor(loop_in)
        if s < 16:
            return ms_to_frames(self.grid_ms(i, s + q))
        return loop_in + ms_to_frames(self.grid_ms(i, q) - self.e[i][2])

    def q_of(self, loop_in, out):
        """The q whose out is within a frame of `out` (the first such, as the payload's search)."""
        lo = next((q for q in range(1, MAX_Q + 1) if self.out(loop_in, q) >= out), MAX_Q)
        best, d_best = 0, 2
        tz = lambda q: (q & -q).bit_length()
        for q in ([lo - 1] if lo > 1 else []) + [lo]:
            d = abs(self.out(loop_in, q) - out)
            if d < d_best or (d == d_best and best and tz(q) > tz(best)):
                best, d_best = q, d
        return best


def new_out(g, loop_in, out, tracklen, double):
    """(new out or None, q, mode) as loop_resize_new_out gives them."""
    q, mode, k = 0, 2, 0
    if out <= loop_in or tracklen == 0 or loop_in >= tracklen - 1:
        return None, q, mode
    if g is not None and g.anchor(loop_in) is not None:
        k = g.q_of(loop_in, out)
    if k:
        mode = 1
        if double:
            if 2 * k > MAX_Q:
                return None, q, mode
            k *= 2
        else:
            if k < 2:
                return None, q, mode
            k //= 2
        nout, q = g.out(loop_in, k), k
    else:
        length = out - loop_in
        if double:
            nout = loop_in + 2 * length
        else:
            if length < 2:
                return None, q, mode
            nout = loop_in + length // 2
    if nout >= tracklen:
        if out >= tracklen - 1:
            return None, q, mode
        nout, q = tracklen - 1, 0
    if nout <= loop_in or nout == out:
        return None, q, mode
    return nout, q, mode


def const_grid(bpm, first_ms, n):
    return [((i % 4) + 1, round(bpm * 100), round(first_ms + i * 60000 / bpm)) for i in range(n)]


def var_grid():
    """120.00 for 32 beats (500 ms), then 133.33 (450 ms), like baken's QVAR track."""
    e = [((i % 4) + 1, 12000, 500 * i) for i in range(32)]
    e += [((i % 4) + 1, 13333, 16000 + 450 * (i - 32)) for i in range(32, 96)]
    return e


GRIDS = {
    "128 @0.35": const_grid(128.0, 350, 120),
    "127.96 @0.35": const_grid(127.96, 350, 120),
    "120->133.33": var_grid(),
}


# ---- static ----

def _pool_readers(stock, pool):
    out = []
    for k in range(len(stock) // 2):
        op = struct.unpack_from(">H", stock, 2 * k)[0]
        if op & 0xF000 == 0xD000:
            pc = BASE + 2 * k
            if (pc & ~3) + 4 + (op & 0xFF) * 4 == pool:
                out.append(pc)
    return out


@needs_main
@needs_toolchain
def test_static_pool_words_outside_the_boot_critical_closure():
    import bootcrit
    stock = MAIN.read_bytes()
    report = {}
    out = patch_main.patch(stock, MODS, "cdj850", report)
    assert {n: report[n][0] for n in MODS} == {n: "applied" for n in MODS}
    p = payloadbuild.build(["loop_resize"], "cdj850")
    closure = bootcrit.closure(stock, BASE, byteorder=">")
    for pool, old, readers, symbol in POOLS:
        o = pool - BASE
        assert struct.unpack_from(">I", stock, o)[0] == old, hex(pool)
        assert struct.unpack_from(">I", out, o)[0] == payloadbuild.lookup(p.symbols, payloadbuild.gate_name(symbol)), hex(pool)
        assert _pool_readers(stock, pool) == readers, hex(pool)
        assert not closure.hit(o, o + 4), hex(pool)
        for a in readers:
            assert not closure.hit(a - BASE, a - BASE + 2), hex(a)
    # CALL > loads with r4 = 1 in the delay slot, CALL < with r4 = 0; state 4 tail-jumps
    assert struct.unpack_from(">3H", stock, READERS[0] - BASE) == (0xD239, 0x420B, 0xE401)
    assert struct.unpack_from(">3H", stock, READERS[1] - BASE) == (0xD22A, 0x420B, 0xE400)
    assert struct.unpack_from(">3H", stock, 0x0C0D79A6 - BASE) == (0xD37C, 0x432B, 0x4F26)
    # what the payload calls is not boot-critical either
    for a in (STOCK, BEAT_HANDLER4, STATE4_KEYS, MEMORY_BUTTON):
        assert not closure.hit(a - BASE, a - BASE + 2), hex(a)
    diff = [a for a in range(0x0C0C0000, 0x0C100000) if stock[a - BASE] != out[a - BASE]]
    assert all(any(pool <= a < pool + 4 for pool, *_ in POOLS) for a in diff), [hex(a) for a in diff]


ALL_MODS = ["autostandby", "bpm999", "bpm999grid", "bpm999range", "bpm_probe", "dspprobe",
            "payload_demo", "payload_demo_redirect", "tempowide", "tempowidemath1", "tempowidemath2",
            "bpm_decimals", "bpm_decimals_grid", "quant_loops", "quant_loops_auto_send",
            "quant_loops_auto_send2", "quant_loops_auto_keep", "quant_loops_loop_in", "quant_loops_loop_out",
            "quant_loops_loop_out2"]


@needs_main
@needs_toolchain
def test_composes_with_the_branch_mods():
    names = ALL_MODS + MODS
    report = {}
    out = patch_main.patch(MAIN.read_bytes(), names, "cdj850", report)
    assert all(report[n][0] == "applied" for n in names), report
    p = payloadbuild.build(["bpm_decimals", "loop_resize", "payload_demo", "quant_loops"], "cdj850")
    for pool, _, _, symbol in POOLS:
        assert struct.unpack_from(">I", out, pool - BASE)[0] == payloadbuild.lookup(p.symbols, payloadbuild.gate_name(symbol))


@needs_toolchain
def test_payload_has_no_fpu_code_and_small_frames():
    p = payloadbuild.build(["loop_resize"], "cdj850")
    elf_text = p.blob[:p.symbols["__bss_start"] - 0x0DE00020]
    names = ["loop_resize_call", "loop_resize_state4", "loop_resize_memory_button", "loop_resize_new_out", "loop_resize_grid_out", "loop_resize_grid_ms",
             "loop_resize_beat_at_or_before", "loop_resize_ms_to_frames"]
    starts = sorted(payloadbuild.lookup(p.symbols, n) - 0x0DE00020 for n in names)
    for at in starts:
        words = struct.unpack(">8H", elf_text[at:at + 16])
        assert not [w for w in words if w >> 12 == 0xF], hex(at)
    assert max(p.stack.values()) <= 96


# ---- interpreter ----

def _payload():
    return payloadbuild.build(["loop_resize"], "cdj850")


SETTINGS = 0x0C9DA23C                                   # settings record (c850_ui.h flags at +0x22)


def _settings(flags=0):
    """The settings record as a fresh deck has it, with the mod flags byte (0 = quantize on)."""
    rec = bytearray(0x58)
    rec[0:3] = b"\xC3\x02\x10"
    rec[3:10] = b"HISTORY"
    rec[0x23:0x2B] = b"TAG LIST"
    rec[0x22] = flags
    return bytes(rec)


def _mem(p, entries, base=None, usb=1, state=4, active=1, frac=0, tracklen=100000, flags=0):
    mem = Memory()
    mem.add(0x0DE00020, p.blob)
    mem.add(SETTINGS, _settings(flags))                   # mod flags byte +0x22: quantize
    mem.add(0x0DDFE000, bytes(0x2000))                    # stack, out pointers, stub log
    ctx = bytearray(0x7C + 8 * 1000)
    struct.pack_into(">I", ctx, 0x70, 1 if entries else 0)
    struct.pack_into(">I", ctx, 0x74, len(entries))
    for i, e in enumerate(entries[:1000]):
        struct.pack_into(">HHI", ctx, 0x7C + 8 * i, *e)
    mem.add(GRID_CTX, ctx)
    if base is None:
        base = (entries[0][1] + 5) // 10 if entries else 0
    mem.add(BASE_TENTHS, struct.pack(">H", base))
    mem.add(USB_MODE, bytes([usb]))
    mem.add(SOURCE, bytes([2]))
    mem.add(PLAYER_STATE, bytes([state] * 4))
    mem.add(LOOP_ACTIVE, bytes([active]))
    mem.add(FRAC, bytes([frac]))
    mem.add(BEATS, bytes(4))
    mem.add(CA90 - 0x10, bytes(0x20))
    mem.add(0x0C7F6470, bytes(0xA0))                      # play frame, loop records, track length
    mem.put32(TRACKLEN, tracklen)
    return mem


LOG = 0x0DDFE000


def _stubs(mem):
    """cue_call_prev_next logs (1, r4); the state-4 BEAT handler logs (2, r4, CA90) and returns 1."""
    def put(addr, ops, lits=()):
        code = struct.pack(">%dH" % len(ops), *ops) + b"".join(struct.pack(">I", v) for v in lits)
        mem.add(addr, code)
    # mov.l @(lit),r0 ; mov #1,r1 ; mov.l r1,@r0 ; mov.l r4,@(4,r0) ; rts ; nop ; lit
    assert STOCK == 0x0C0D3B1A                                # code at 0x0C0D3B18 + 2, literal at +0x10
    put(STOCK - 2, [0x0009, 0xD003, 0xE101, 0x2012, 0x1041, 0x000B, 0x0009, 0x0009], [LOG])
    # FUN_0C0E5390: log 2, r4, CA90; r0 = 1 (literals at +0x14 / +0x18)
    put(BEAT_HANDLER4, [0xD004, 0xE102, 0x2012, 0x1041, 0xD103, 0x6112, 0x1012, 0x000B, 0xE001, 0x0009],
        [LOG, CA90])
    # keys_state4_looping: log 3 (code at 0x0C0E0F34, literal at +8)
    put(STATE4_KEYS, [0xD001, 0xE103, 0x000B, 0x2012], [LOG])
    # memory_button: log 4 and the out record and its minutes/seconds/frames as they are during the call
    a = MEMORY_BUTTON                                         # 0x0C0D4172: code at +2 of 0x0C0D4170
    put(a - 2, [0x0009, 0xD004, 0xE104, 0x2012, 0xD103, 0x6112, 0x1011, 0x000B, 0x0009, 0x0009],
        [LOG, OUT_REC])


def _call(p, mem, name, *args, stack=()):
    cpu = Sh2a(mem)
    cpu.r[15] = 0x0DDFFF00
    for i, a in enumerate(args):
        cpu.r[4 + i] = a & 0xFFFFFFFF
    for i, a in enumerate(stack):
        mem.put32(0x0DDFFF00 + 4 * i, a)
    cpu.run(payloadbuild.lookup(p.symbols, name), Sh2a.RET)
    return cpu.r[0]


def _new_out(p, mem, loop_in, out, tracklen, double):
    outp = 0x0DDFF800
    r = _call(p, mem, "loop_resize_new_out", loop_in, out, tracklen, double, stack=(outp, outp + 4))
    return (r or None), mem.u32(outp), mem.u32(outp + 4)


@needs_toolchain
def test_new_out_on_the_interpreter_against_the_reference():
    p = _payload()
    for label, entries in GRIDS.items():
        for b in (0, 3, 9, 31, 33, 50):               # loop in on a beat (both sides of the QVAR change)
            base = (entries[b][1] + 5) // 10          # the deck's BPM word there
            g = Grid(entries, base=base)
            mem = _mem(p, entries, base=base)
            loop_in = g.f(b)
            for q in (1, 2, 3, 4, 8, 12, 16, 24, 32, 48, 64, 128, 256, 512):
                out = g.out(loop_in, q)
                for double in (0, 1):
                    want = new_out(g, loop_in, out, 100000, double)
                    got = _new_out(p, mem, loop_in, out, 100000, double)
                    assert got == want, (label, b, q, double, got, want)
        # in on a 1/16 step (quant_loops' fraction loops) and off the grid (no quant_loops)
        g = Grid(entries)
        mem = _mem(p, entries)
        for loop_in in (g.f(5) + 3, g.f(5) + 7, g.f(40) + 1, g.f(40) + 5):
            for out in (loop_in + 1, loop_in + 9, loop_in + 35, loop_in + 141, loop_in + 77):
                for double in (0, 1):
                    want = new_out(g, loop_in, out, 100000, double)
                    assert _new_out(p, mem, loop_in, out, 100000, double) == want, (label, loop_in, out)
    # no grid tempo (base word not the grid's), CD mode, an empty grid: frames
    for kw in (dict(base=1300), dict(usb=0)):
        mem = _mem(p, GRIDS["128 @0.35"], **kw)
        assert _new_out(p, mem, 202, 343, 100000, 0) == (272, 0, 2)
        assert _new_out(p, mem, 202, 343, 100000, 1) == (484, 0, 2)
    mem = _mem(p, [])
    assert _new_out(p, mem, 100, 141, 100000, 0) == (120, 0, 2)
    assert _new_out(p, mem, 100, 101, 100000, 0) == (None, 0, 2)      # 1 frame: the minimum
    assert _new_out(p, mem, 100, 102, 100000, 0) == (101, 0, 2)


@needs_toolchain
def test_hand_computed_grid_frames():
    """128.00 from 0.35 s: beat k at 350 + 468.75 k ms; beat 5 = 2694 ms = frame 202."""
    g = Grid(GRIDS["128 @0.35"])
    p = _payload()
    mem = _mem(p, GRIDS["128 @0.35"])
    loop_in, out = 202, 343                                   # 4 beats (beat 9 = 4569 ms = 343)
    seq = []
    for double in (0, 0, 1, 1, 1):
        out, q, mode = _new_out(p, mem, loop_in, out, 100000, double)
        seq.append((out, q))
    # 2 beats: beat 7 = 3631 ms -> 272; 1 beat: beat 6 = 3163 ms -> 237; then 2, 4, 8 beats:
    # beat 13 = 6444 ms -> 483
    assert seq == [(272, 32), (237, 16), (272, 32), (343, 64), (483, 128)]
    # down to 1/16 (5 more halvings: 1/2 = 2928 ms -> 220, 1/4 -> 211, 1/8 -> 206, 1/16 -> 204)
    out, seq = 237, []
    for _ in range(5):
        out, q, _ = _new_out(p, mem, loop_in, out, 100000, 0)
        seq.append((out, q))
    assert seq == [(220, 8), (211, 4), (206, 2), (204, 1), (None, 0)][:4] + [seq[4]]
    assert seq[4][0] is None                                  # 1/16 beat: ignored
    # 32 beats is the most (beat 37 = 17694 ms -> 1327); doubling it is ignored
    assert _new_out(p, mem, loop_in, g.out(loop_in, 256), 100000, 1)[:2] == (1327, 512)
    assert _new_out(p, mem, loop_in, 1327, 100000, 1)[0] is None
    # the track ends inside the doubled loop: clamped to tracklen - 1, then ignored
    assert _new_out(p, mem, loop_in, 343, 400, 1) == (399, 0, 1)
    assert _new_out(p, mem, loop_in, 399, 400, 1)[0] is None
    # an in on a 1/16 step (683 = step 11 of beat 18) with a stock 4-beat out (683 + 140): both
    # 63/16 (822) and 64/16 (824) are a frame away; 4 beats wins (seen in the emulator)
    assert g.anchor(683) == (18, 11) and (g.out(683, 63), g.out(683, 64)) == (822, 824)
    assert _new_out(p, mem, 683, 823, 100000, 0) == (g.out(683, 32), 32, 1)
    # 127.96: beat 5 = 350 + 5 * 468.896... = 2694 ms (202); 4 beats: 4570 ms -> 343, 2: 3632 -> 272
    g2 = Grid(GRIDS["127.96 @0.35"])
    assert (g2.f(5), g2.f(9), g2.f(7)) == (202, 343, 272)
    # QVAR: in at beat 30 (15.0 s, frame 1125), 4 beats over the change: 15.5, 16.0, 16.45, 16.9 s
    v = Grid(var_grid())
    assert v.out(1125, 64) == 1268 and v.out(1125, 128) == ms_to_frames(16000 + 450 * 6) == 1403
    assert v.out(1125, 32) == 1200 and v.out(1125, 8) == 1144


PLAY_FRAME_ADDR = 0x0C7F647C


def _entry(p, mem, loop_in, applied, frac=0, ca90=0, pos=None):
    mem.put32(LOOP_IN, loop_in)
    mem.put32(OUT_REC, applied)
    mem.put32(CA90, ca90)
    mem.put32(PLAY_FRAME_ADDR, loop_in if pos is None else pos)
    mem.write(FRAC, bytes([frac]))
    mem.write(LOG, bytes(16))


def _press(p, mem, nxt):
    """CALL, then what the stock resize does to the requested out (FUN_0C00A4CC writes it)."""
    mem.write(LOG, bytes(16))
    _call(p, mem, "loop_resize_call", nxt)
    if mem.u32(LOG) == 2:
        b, ca90, loop_in = mem.u32(LOG + 4), mem.u32(LOG + 8), mem.u32(LOOP_IN)
        frac = mem.read(FRAC, 1)[0]
        mem.put32(OUT_REQ, loop_in + (ca90 - loop_in) // ((8 // b) * (frac * 15 + 1)))


def _sym(p, mem, name):
    return mem.u32(payloadbuild.lookup(p.symbols, "loop_resize_" + name))


@needs_toolchain
def test_call_entry_point_end_to_end():
    p = _payload()
    g = Grid(GRIDS["128 @0.35"])
    for frac in (0, 1):
        mem = _mem(p, GRIDS["128 @0.35"], frac=frac)
        _stubs(mem)
        _entry(p, mem, 202, 343, frac=frac, ca90=483)
        out = 343
        for nxt in (0, 0, 1, 1, 1, 1, 0):
            _press(p, mem, nxt)
            want, q, _ = new_out(g, 202, out, 100000, nxt)
            kind, b = mem.u32(LOG), mem.u32(LOG + 4)
            assert kind == 2 and b in (1, 2, 4, 8)
            # what FUN_0C0E5668 computes from that CA90 is the wanted out
            assert mem.u32(OUT_REQ) == want, (nxt, b)
            assert mem.u32(CA90) == 483                       # the BEAT buttons' reference is back
            led = {16: 1, 32: 2, 64: 4, 128: 8} if not frac else {}
            assert mem.read(BEATS, 1)[0] == led.get(q, 0), q
            assert _sym(p, mem, "last_new") == want
            out = want                                        # the requested out is the loop's out now
    # 16 beats (q 256) lights no BEAT LED; in fraction mode 1/2 beat (q 8) lights 8
    mem = _mem(p, GRIDS["128 @0.35"], frac=1)
    _stubs(mem)
    _entry(p, mem, 202, 237, frac=1)
    _press(p, mem, 0)
    assert mem.u32(LOG + 4) == 8 and mem.read(BEATS, 1)[0] == 8 and mem.u32(OUT_REQ) == 220
    # not in a loop (state 3/6, loop flag 0, CD): stock CALL with the same r4
    for kw in (dict(state=3), dict(state=6), dict(active=0), dict(usb=0)):
        mem = _mem(p, GRIDS["128 @0.35"], **kw)
        _stubs(mem)
        _entry(p, mem, 202, 343)
        for nxt in (0, 1):
            _call(p, mem, "loop_resize_call", nxt)
            assert (mem.u32(LOG), mem.u32(LOG + 4)) == (1, nxt), kw
    # a limit: nothing is called, the press is counted as ignored
    mem = _mem(p, GRIDS["128 @0.35"])
    _stubs(mem)
    _entry(p, mem, 202, 204)
    _call(p, mem, "loop_resize_call", 0)
    assert mem.u32(LOG) == 0 and _sym(p, mem, "ignored") == 1


@needs_toolchain
def test_effective_out_follows_the_records():
    """The requested out counts once it changed after the out record; a new loop (record
    changed, request stale) uses the record."""
    p = _payload()
    mem = _mem(p, GRIDS["128 @0.35"])
    _stubs(mem)
    _entry(p, mem, 202, 343)
    mem.put32(OUT_REQ, 9999)                                  # stale from an earlier loop
    _press(p, mem, 0)
    assert mem.u32(OUT_REQ) == 272                            # from the record (343), not 9999
    _press(p, mem, 0)
    assert mem.u32(OUT_REQ) == 237                            # from the request (272)
    mem.put32(OUT_REQ, 483)                                   # BEAT 8 meanwhile (stock resize)
    _press(p, mem, 0)
    assert mem.u32(OUT_REQ) == 343
    mem.put32(LOOP_IN, 202)
    mem.put32(OUT_REC, 308)                                   # a new loop (LOOP OUT): record wins
    _press(p, mem, 0)
    assert _sym(p, mem, "last_old") == 308


@needs_toolchain
def test_halving_past_the_new_out_waits_for_the_next_pass():
    p = _payload()
    mem = _mem(p, GRIDS["128 @0.35"])
    _stubs(mem)
    _entry(p, mem, 202, 343, pos=300)                        # 4-beat loop, playhead in beat 3
    _press(p, mem, 0)                                         # 2 beats: out 272 is behind 300
    assert mem.u32(LOG) == 0 and _sym(p, mem, "deferred") == 1
    _press(p, mem, 0)                                         # again: 1 beat (237), still waiting
    assert mem.u32(LOG) == 0 and _sym(p, mem, "last_new") == 237
    for pos, sent in ((330, False), (342, False), (202 + 18, False), (202 + 17, True)):
        mem.put32(PLAY_FRAME_ADDR, pos)
        mem.write(LOG, bytes(16))
        _call(p, mem, "c850_loop_tick")
        log = [mem.u32(LOG + 4 * i) for i in range(2)]
        # the stock state-4 handler always runs last (log 3); a send logs 2 first and is overwritten
        assert log[0] == 3
        assert (_sym(p, mem, "count") == 1) == sent, pos
    assert _sym(p, mem, "last_new") == 237
    # halving with the playhead in the first half of the new loop: at once
    mem = _mem(p, GRIDS["128 @0.35"])
    _stubs(mem)
    _entry(p, mem, 202, 343, pos=230)
    _press(p, mem, 0)
    assert mem.u32(OUT_REQ) == 272 and _sym(p, mem, "deferred") == 0
    # a pending halving is dropped when the loop is left
    mem = _mem(p, GRIDS["128 @0.35"])
    _stubs(mem)
    _entry(p, mem, 202, 343, pos=300)
    _press(p, mem, 0)
    mem.write(PLAYER_STATE, bytes([3] * 4))
    mem.put32(PLAY_FRAME_ADDR, 205)
    _call(p, mem, "c850_loop_tick")
    mem.write(PLAYER_STATE, bytes([4] * 4))
    _call(p, mem, "c850_loop_tick")
    assert _sym(p, mem, "count") == 0


@needs_toolchain
def test_memory_stores_the_effective_out():
    p = _payload()
    mem = _mem(p, GRIDS["128 @0.35"])
    _stubs(mem)
    _entry(p, mem, 202, 343)
    for k, v in enumerate((0, 4, 43)):                       # M/S/F of 343
        mem.write(OUT_REC + 8 + 2 * k, struct.pack(">H", v))
    _press(p, mem, 1)                                         # 8 beats: request 483
    mem.write(LOG, bytes(16))
    _call(p, mem, "loop_resize_memory_button")
    assert (mem.u32(LOG), mem.u32(LOG + 4)) == (4, 483)       # the record during the call
    assert mem.u32(OUT_REC) == 343                            # and back after it
    assert struct.unpack(">3H", mem.read(OUT_REC + 8, 6)) == (0, 4, 43)
    assert _sym(p, mem, "memory") == 1
    # MSF of 483 during the call: 0:06:33 (checked with a second stand-in run below)
    # not resized (request stale) or not looping: the record as it is
    mem = _mem(p, GRIDS["128 @0.35"])
    _stubs(mem)
    _entry(p, mem, 202, 343)
    mem.put32(OUT_REQ, 9999)
    _call(p, mem, "loop_resize_memory_button")
    assert (mem.u32(LOG), mem.u32(LOG + 4)) == (4, 343)


# ---- live ----

# Stick: Q128 (128.00 BPM, first downbeat 0.35 s, memory cues at bars 5 and 13), Q12796 (127.96),
# QVAR (120.00 for 16 s, then 133.33) and NOGRID (a 128 BPM WAV with no <TEMPO>: no grid, no BPM).
_STICK_BUILDER = r'''
import json, sys, wave, pathlib, re
import numpy as np
root, out_dir, image, baken = sys.argv[1:5]
sys.path.insert(0, root)
import make_test_stick as stick
import collection_xml
VAR = {"QVAR": [(0.0, 120.00), (16.0, 133.33)]}
_orig_track_xml = collection_xml.track_xml
def track_xml(track_id, path, tags, audio, notes, tempo=None, key="", marks=()):
    x = _orig_track_xml(track_id, path, tags, audio, notes, tempo=tempo, key=key, marks=marks)
    if tags["title"] in VAR:
        lines = "".join('      <TEMPO Inizio="%.3f" Bpm="%.2f" Metro="4/4" Battito="1"/>\n' % (t, b)
                        for t, b in VAR[tags["title"]])
        x, n = re.subn(r'      <TEMPO [^\n]*\n', lines, x, count=1)
        assert n == 1
    if tags["title"] == "NOGRID":
        x = re.sub(r'      <TEMPO [^\n]*\n', '', x)
        x = re.sub(r'AverageBpm="[^"]*"', 'AverageBpm="0.00"', x)
    return x
collection_xml.track_xml = track_xml
stick.collection_xml = collection_xml
def render(track, path):
    rate = 44100
    t = np.arange(round(stick.track_length(track) * rate)) / rate
    phase = (t - track['first_downbeat']) % (60 / track['bpm'])
    sig = 0.65 * np.sin(2 * np.pi * 55 * phase) * np.exp(-25 * phase)
    s = np.rint(sig * 32767).astype('<i2')
    with wave.open(path, 'wb') as w:
        w.setparams((2, 2, rate, 0, 'NONE', 'not compressed'))
        w.writeframes(np.repeat(s, 2).tobytes())
    return True
stick.have_ffmpeg = lambda: True
stick.render_audio = render
stick.file_name = lambda n, title: f'{title}.wav'
pathlib.Path(out_dir).mkdir(parents=True, exist_ok=True)
tracks = [("Q128", 128.00, 0.35, 32), ("Q12796", 127.96, 0.35, 32), ("QVAR", 120.0, 0.0, 32),
          ("NOGRID", 128.0, 0.35, 32)]
spec = {"device_name": "LRESIZE", "playlist": "Loop Resize", "tracks": [
    dict({"title": t, "bpm": b, "first_downbeat": fd, "bars": n},
         **({"memory_cues": [5, 13]} if t in ("Q128", "NOGRID") else {})) for t, b, fd, n in tracks]}
sp = pathlib.Path(out_dir) / 'spec.json'
sp.write_text(json.dumps(spec))
raise SystemExit(stick.main(['--spec', str(sp), '--out', out_dir + '/wav-stick',
                             '--image', image, '--size-mb', '48', '--baken', baken]))
'''

TITLES = ("Q128", "Q12796", "QVAR", "NOGRID")
PLAY_FRAME, STATE_USB, D1C0 = 0x0C7F647C, 0x0CE6D25F, 0x0CE6D1C0
LED_B7, LED_B6 = 0x0C9D0AEB, 0x0C9D0AEA
MEMCUE_LIST, CUE_FRAME = 0x0CE649D4, 0x0CE6D158
SYMS = ("count", "ignored", "passed", "last_old", "last_new", "last_q", "last_mode", "deferred", "ticks",
        "memory")
# where the 4-beat loop of the press sequence starts (frame), per track
SEQ_AT = {"Q128": 600, "Q12796": 600, "QVAR": 1110, "NOGRID": 600}
SEQ = ("call_prev", "call_prev", "call_next", "call_next", "call_next")
# stock firmware: the BEAT button that gives the same whole-beat length (4 -> 2 -> 1 -> 2 -> 4 -> 8)
STOCK_SEQ = ("beat_2", "beat_1", "beat_2", "beat_4", "beat_8")


def build_stick(path):
    import importlib.util
    if not BAKEN.is_file() or importlib.util.find_spec("pyfatfs") is None or importlib.util.find_spec("numpy") is None:
        pytest.skip("tools/baken/baken, pyfatfs and numpy are needed for the grid stick")
    image = path / "lresize.img"
    subprocess.run([sys.executable, "-c", _STICK_BUILDER, str(ROOT / "scripts" / "media"),
                    str(path / "stick"), str(image), str(BAKEN)],
                   check=True, capture_output=True, text=True, timeout=300)
    return image



def _free_port():
    import socket
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _drift_gdb(syms):
    """(gdb port, address of quant_loops_drift_on) when the payload has the drift correction:
    this test checks exact out frames, so it runs with the correction off (it moves the out of
    every pass by up to a frame; tests/test_cdj850_loop_drift.py covers it)."""
    a = payloadbuild.lookup(syms, "quant_loops_drift_on") if syms else None
    return (_free_port(), a) if a else (None, None)


def drive(stick, flash=None, syms=None, stock=False, tracks=TITLES, log=None):
    """Boot, play each track of `tracks` and run the scenarios; returns {title: {scenario: values}}.
    stock=True presses the BEAT button of the same length instead of CALL in the press sequence
    and runs only that and CALL outside a loop."""
    import re
    sys.path.insert(0, str(ROOT / "scripts" / "debug"))
    import cdj850_trace as tr
    res = {}

    def sym(d, name):
        return d.rw(payloadbuild.lookup(syms, "loop_resize_" + name)) if syms else None

    def snap(d):
        d.q("stop")
        v = {"in": d.rw(LOOP_IN), "out": d.rw(OUT_REC), "req": d.rw(OUT_REQ), "ca90": d.rw(CA90),
             "d1c0": d.rw(D1C0), "state": d.rb(STATE_USB), "active": d.rb(LOOP_ACTIVE), "beats": d.rb(BEATS),
             "frac": d.rb(FRAC), "led7": d.rb(LED_B7), "led6": d.rb(LED_B6), "frame": d.rw(PLAY_FRAME),
             "cue": d.rw(CUE_FRAME), "tracklen": d.rw(TRACKLEN), "base": d.rh(BASE_TENTHS),
             "x675c": d.rw(0x0C7F675C)}
        v.update({"lr_" + s: sym(d, s) for s in SYMS})
        d.q("cont")
        return v

    def frames_until(d, done, timeout):
        fr, end = [d.rw(PLAY_FRAME)], time.monotonic() + timeout
        while time.monotonic() < end and not done(fr):
            time.sleep(0.02)
            fr.append(d.rw(PLAY_FRAME))
        return fr

    def summary(fr):
        wraps = [b for a, b in zip(fr, fr[1:]) if b < a]
        return {"min": min(fr), "max": max(fr), "wraps": len(wraps), "wrap_to": wraps[:6], "n": len(fr)}

    def watch(d, loops=1.5, length=None):
        """Frames while the loop plays: until `loops` passes after the first wrap (or 25 s)."""
        need = int(loops * (length or 75))
        state = {"played": 0, "wrapped": False}

        def done(fr):
            if len(fr) > 1:
                if fr[-1] < fr[-2]:
                    state["wrapped"] = True
                elif state["wrapped"]:
                    state["played"] += fr[-1] - fr[-2]
            return state["wrapped"] and state["played"] >= need
        return summary(frames_until(d, done, 25))

    def wait_frame(d, f):
        """Play on to frame f. The emulator's USB feed stalled twice at a track start (state 3,
        frame fixed for 90 s, all-mods build, not reproduced alone): after 20 s without a new
        frame, PLAY twice (pause, play) restarts it; such restarts are counted in res."""
        end, last, since = time.monotonic() + 150, -1, time.monotonic()
        while True:
            fr = d.rw(PLAY_FRAME)
            if fr >= f:
                return fr
            if fr != last:
                last, since = fr, time.monotonic()
            elif time.monotonic() - since > 20:
                res.setdefault("_restarts", []).append(fr)
                for _ in range(2):
                    d.key("play")
                    time.sleep(2.0)
                ensure_playing(d)
                since = time.monotonic()
            assert time.monotonic() < end, (f, fr, d.rb(STATE_USB))
            time.sleep(0.03)

    def beat_loop(d, key="beat_4"):
        """BEAT n until the deck loops (a press can be missed at a track's start)."""
        for _ in range(3):
            d.key(key)
            time.sleep(1.2)
            if d.rb(STATE_USB) == 4 and d.rb(LOOP_ACTIVE) == 1:
                break
            exit_loop(d)
            ensure_playing(d)
        return snap(d)

    def press(d, key, settle=0.3):
        """Press, then follow the frames until the requested out is the new one and the loop has
        wrapped twice since (or 25 s), then watch the new loop."""
        pre = snap(d)
        d.key(key)
        time.sleep(settle)
        v = snap(d)
        v["pre"] = {k: pre[k] for k in ("in", "out", "req", "frame", "lr_count", "lr_ignored", "lr_deferred")}
        moved = syms and (v["lr_count"], v["lr_deferred"]) != (pre["lr_count"], pre["lr_deferred"])
        want = v["lr_last_new"] if moved else v["req"]
        state = {"wraps": 0}

        def done(fr):
            if len(fr) > 1 and fr[-1] < fr[-2] and d.rw(OUT_REQ) == want:
                state["wraps"] += 1
            return state["wraps"] >= 2
        fr = frames_until(d, done, 25)
        v["want"] = want
        v["req_after"] = d.rw(OUT_REQ)
        v["follow"] = summary(fr)
        v["watch"] = watch(d, 1.5, max(1, want - v["in"]))
        return v

    def ensure_playing(d):
        """Back to plain play (state 3): RELOOP/EXIT leaves a loop, PLAY resumes a pause or cue."""
        for _ in range(6):
            st = d.rb(STATE_USB)
            if st == 3:
                return
            d.key("reloop" if st == 4 else "play")
            time.sleep(2.5)
        assert d.rb(STATE_USB) == 3

    def exit_loop(d):
        if d.rb(STATE_USB) == 4:
            d.key("reloop")
            time.sleep(1.5)
        return d.rb(STATE_USB)

    def memcues(d):
        raw = d.dump(MEMCUE_LIST, 0x24 * 11)
        out = []
        for i in range(11):
            e = struct.unpack_from(">9I", raw, 0x24 * i)
            if e[0] == 0xFFFFFFFF:
                break
            out.append((e[0] & 1, e[3], e[4]))
        return out

    def grid(d):
        n = d.rw(GRID_CTX + 0x74)
        raw = d.dump(GRID_CTX + 0x7C, 8 * min(n, 1000))
        return {"count": n, "ok": d.rw(GRID_CTX + 0x70), "file": d.rw(GRID_CTX + 4),
                "entries": [struct.unpack_from(">HHI", raw, 8 * i) for i in range(min(n, 1000))]}

    # the playlist lists NOGRID, QVAR, Q12796, Q128: load the first, or rotate to Q128 alone
    script = tr.LOAD_SCRIPT if tracks != ("Q128",) else \
        "6000:usb:200,7600:rot+2,9000:enter:200,10600:rot+3,12000:enter:200"
    port, drift_on = _drift_gdb(syms)
    with tr.Deck(stick, flash=flash, script=script, log=log, gdb_port=port) as d:
        if port:
            g = tr.Gdb(port)
            g._send("c")
        start = 0
        while not all(x in res for x in tracks):
            i, line = d.wait_line("mode=0x0a", timeout=300, start=start)
            if port:
                assert g.poke32(drift_on, 0) == "OK"
                port = None
            start = i + 1
            m = re.search(r"attr=\S+ \[([^\]]*)\]", line)
            title = m.group(1) if m else ""
            if title not in TITLES or title in res:
                continue
            time.sleep(1.5)
            ensure_playing(d)
            if title not in tracks:
                res[title] = None
            else:
                t = res[title] = {}
                # A: a 4-beat loop, CALL < twice, CALL > three times
                print("loop_resize live: %s" % title, file=sys.stderr, flush=True)
                t["pre"] = wait_frame(d, SEQ_AT[title])
                t["auto4"] = beat_loop(d)
                t["grid"] = grid(d)
                t["auto4"]["watch"] = watch(d, 1.0, t["auto4"]["req"] - t["auto4"]["in"])
                t["seq"] = [press(d, k) for k in (STOCK_SEQ if stock else SEQ)]
                # B: MEMORY stores the resized loop, EXIT, RELOOP re-enters it
                d.key("memory")
                time.sleep(1.5)
                t["memcues"] = memcues(d)
                t["exit_state"] = exit_loop(d)
                d.key("reloop")
                time.sleep(1.5)
                t["reloop"] = snap(d)
                t["reloop"]["watch"] = watch(d, 1.0, t["reloop"]["out"] - t["reloop"]["in"])
                exit_loop(d)
                # F: CALL outside a loop is stock (prev memory cue/loop, cued)
                t["call_out"] = []
                for k in ("call_prev", "call_next"):
                    d.key(k)
                    time.sleep(2.0)
                    t["call_out"].append(snap(d))
                if not stock:
                    # G: a memory loop (CALL until the loop is armed, PLAY), then CALL < in it
                    for k in ("call_prev",) * 4:
                        if d.rb(STATE_USB) == 6 and d.rb(LOOP_ACTIVE) == 1:
                            break
                        d.key(k)
                        time.sleep(2.0)
                    t["memloop_cued"] = snap(d)
                    d.key("play")
                    time.sleep(1.5)
                    t["memloop_play"] = snap(d)
                    t["memloop"] = press(d, "call_prev") if d.rb(STATE_USB) == 4 else None
                    exit_loop(d)
                    # D: halve with the playhead past the new out (in the 4th beat of a 4-beat loop)
                    ensure_playing(d)
                    a = beat_loop(d)
                    length = a["req"] - a["in"]
                    end = time.monotonic() + 20
                    while not a["in"] + (3 * length) // 4 <= d.rw(PLAY_FRAME) < a["in"] + (7 * length) // 8:
                        assert time.monotonic() < end
                        time.sleep(0.005)
                    t["past"] = press(d, "call_prev", settle=0.05)
                    t["past"]["loop"] = a
                    # E: limits: halve until ignored, then double until ignored
                    t["limits"] = {"down": [], "up": []}
                    for k, key in (("down", "call_prev"), ("up", "call_next")):
                        for _ in range(16):
                            before = sym(d, "ignored")
                            seen = [sym(d, n) for n in ("count", "ignored", "deferred")]
                            d.key(key)
                            # a loop of a frame or two slows the emulator down a lot: wait until
                            # the press has been handled
                            end = time.monotonic() + 30
                            while [sym(d, n) for n in ("count", "ignored", "deferred")] == seen and \
                                    time.monotonic() < end:
                                time.sleep(0.2)
                            time.sleep(0.3)
                            v = snap(d)
                            t["limits"][k].append({x: v[x] for x in ("in", "req", "lr_last_new", "lr_last_q",
                                                                     "lr_ignored", "lr_last_mode", "state")})
                            if v["lr_ignored"] != before:
                                break
                    t["limits"]["watch"] = watch(d, 1.0, d.rw(OUT_REQ) - d.rw(LOOP_IN))
                    t["limits"]["end"] = snap(d)
                    exit_loop(d)
                    if title == "Q128":
                        # C: a manual LOOP IN / LOOP OUT loop (about 2 beats), CALL <, >, >
                        ensure_playing(d)
                        f = d.rw(PLAY_FRAME) + 30
                        wait_frame(d, f)
                        d.key("loop_in")
                        time.sleep(0.8)
                        wait_frame(d, f + 75)
                        d.key("loop_out")
                        time.sleep(1.2)
                        t["manual"] = snap(d)
                        t["manual_seq"] = [press(d, k) for k in ("call_prev", "call_next", "call_next")]
                        exit_loop(d)
            if not all(x in res for x in tracks):
                ensure_playing(d)
                d.key("next")
    return {k: v for k, v in res.items() if v is not None and not k.startswith("_")} | \
        ({"_restarts": res["_restarts"]} if "_restarts" in res else {})


def check_track(title, t):
    """Assertions for one track's results (shared by the live tests)."""
    a = t["auto4"]
    assert a["state"] == 4 and a["active"] == 1, (title, a)
    entries = [tuple(e) for e in t["grid"]["entries"]]
    g = None
    if title != "NOGRID":
        g = Grid(entries, base=a["base"], total=t["grid"]["count"])
        assert g.anchor(a["in"]) is not None, (title, a)        # the grid gate holds
    out = a["req"]
    assert a["watch"]["min"] >= a["in"] and a["watch"]["max"] <= out, (title, a)
    for k, v in zip(SEQ, t["seq"]):
        want, q, mode = new_out(g, a["in"], out, a["tracklen"], k == "call_next")
        assert want is not None and v["want"] == want, (title, k, v["want"], want)
        assert v["lr_last_mode"] == (1 if g else 2), (title, k, v)
        assert v["req_after"] == want and v["in"] == a["in"] and v["state"] == 4, (title, k, v)
        top = max(out, want)
        f, w = v["follow"], v["watch"]
        assert f["min"] >= a["in"] and f["max"] <= top, (title, k, f)          # no runaway
        assert all(a["in"] <= x <= a["in"] + 3 for x in f["wrap_to"]), (title, k, f)  # wraps land on in
        assert w["min"] >= a["in"] and w["max"] <= want and w["wraps"] >= 1, (title, k, w)
        out = want
    # MEMORY stored the resized loop, EXIT, RELOOP re-enters it
    assert (1, a["in"], out) in [tuple(c) for c in t["memcues"]], (title, t["memcues"], out)
    assert t["exit_state"] == 3
    r = t["reloop"]
    assert r["state"] == 4 and (r["in"], r["out"]) == (a["in"], out), (title, r)
    assert r["watch"]["min"] >= a["in"] and r["watch"]["max"] <= out
    # CALL outside the loop: stock cue jumps (cued, state 6), nothing resized
    for c in t["call_out"]:
        assert c["state"] == 6 and c["lr_count"] == t["seq"][-1]["lr_count"], (title, c)
    return out


def check_extras(title, t):
    g = None
    if title != "NOGRID":
        a = t["auto4"]
        g = Grid([tuple(e) for e in t["grid"]["entries"]], base=a["base"], total=t["grid"]["count"])
    # the memory loop: armed by CALL (state 6), PLAY loops it, CALL < halves it
    mc, mp, m = t["memloop_cued"], t["memloop_play"], t["memloop"]
    assert mc["state"] == 6 and mc["active"] == 1 and mp["state"] == 4, (title, mc, mp)
    want = new_out(g, mp["in"], mp["out"], mp["tracklen"], False)[0]
    assert m["want"] == want and m["req_after"] == want, (title, m)
    assert m["watch"]["min"] >= mp["in"] and m["watch"]["max"] <= want
    # halving with the playhead past the new out: waits for the next pass, stays in phase
    p, loop = t["past"], t["past"]["loop"]
    want = new_out(g, loop["in"], loop["req"], loop["tracklen"], False)[0]
    assert p["pre"]["frame"] >= want and p["lr_deferred"] == p["pre"]["lr_deferred"] + 1, (title, p)
    f = p["follow"]
    assert f["max"] <= loop["req"] and f["min"] >= loop["in"], (title, f)     # played on to the old out
    assert f["max"] >= loop["req"] - 4, (title, f)                            # ... and reached it
    assert all(loop["in"] <= x <= loop["in"] + 3 for x in f["wrap_to"]), (title, f)
    assert p["watch"]["max"] <= want and p["watch"]["min"] >= loop["in"], (title, p)
    # limits: 1/16 beat (1 frame) and 32 beats are the ends; the press past them is ignored
    down, up = t["limits"]["down"], t["limits"]["up"]
    assert down[-1]["lr_ignored"] == down[-2]["lr_ignored"] + 1
    assert up[-1]["lr_ignored"] == up[-2]["lr_ignored"] + 1
    if g:
        assert down[-1]["lr_last_q"] == 1 and up[-2]["lr_last_q"] == 512, (title, down[-1], up[-2])
    else:
        assert down[-1]["lr_last_new"] - down[-1]["in"] == 1, (title, down[-1])
        assert up[-1]["req"] == t["limits"]["end"]["tracklen"] - 1, (title, up[-2:])   # clamped, then ignored
    w = t["limits"]["watch"]
    end = t["limits"]["end"]
    assert end["state"] == 4 and w["min"] >= end["in"] and w["max"] <= end["req"], (title, w, end)


def _live(tmp_path, names, folders, tracks):
    if os.name == "nt":
        pytest.skip("QMP socket descriptor inheritance requires POSIX")
    stick = build_stick(tmp_path)
    flash = tmp_path / "flash.bin"
    flash.write_bytes(patch_main.patch_flash(FLASH.read_bytes(), names, "cdj850"))
    syms = payloadbuild.build(folders, "cdj850").symbols
    res = drive(stick, flash, syms, tracks=tracks)
    print("loop_resize live:", json.dumps(res))
    restarts = res.pop("_restarts", [])
    assert len(restarts) <= 1, restarts            # one USB-feed restart at a track start at most
    return res


@needs_toolchain
@pytest.mark.skipif(not FLASH.is_file(), reason="extract/cdj850/flash_cdj850.bin is not present")
def test_live_loop_resize_all_tracks(tmp_path):
    """loop_resize alone: stock auto loops (in = the playhead, off the grid) on every track."""
    res = _live(tmp_path, MODS, ["loop_resize"], TITLES)
    assert set(res) == set(TITLES)
    for title, t in res.items():
        check_track(title, t)
        check_extras(title, t)
    m = res["Q128"]
    assert m["manual"]["state"] == 4
    out = m["manual"]["out"]
    for k, v in zip(("call_prev", "call_next", "call_next"), m["manual_seq"]):
        assert v["in"] == m["manual"]["in"] and v["req_after"] == v["want"], v
        assert v["watch"]["min"] >= v["in"] and v["watch"]["max"] <= v["want"], v


@needs_toolchain
@pytest.mark.skipif(not FLASH.is_file(), reason="extract/cdj850/flash_cdj850.bin is not present")
def test_live_with_every_branch_mod(tmp_path):
    """With quant_loops (loops on the grid) and every other mod: exact grid frames."""
    res = _live(tmp_path, ALL_MODS + MODS, ["bpm_decimals", "loop_resize", "payload_demo", "quant_loops"],
                TITLES)
    assert set(res) == set(TITLES)
    for title, t in res.items():
        out = check_track(title, t)
        check_extras(title, t)
        if title != "NOGRID":
            a = t["auto4"]
            g = Grid([tuple(e) for e in t["grid"]["entries"]], base=a["base"], total=t["grid"]["count"])
            i = g.at_or_before(a["in"])
            assert g.f(i) == a["in"], (title, a)               # quant_loops put the in on a beat
            # 4 -> 2 -> 1 -> 2 -> 4 -> 8 beats: the grid frames of beat i + n
            assert [v["want"] for v in t["seq"]] == [g.f(i + 2), g.f(i + 1), g.f(i + 2), g.f(i + 4),
                                                     g.f(i + 8)], (title, t["seq"])
            assert out == g.f(i + 8)
