# SPDX-License-Identifier: GPL-2.0-or-later
"""quant_loops on the CDJ-850 MAIN 1.13: loops snap to the rekordbox beat grid.

* static: the seven pool-word redirects (stock words, the instructions that read them, all outside
  the boot-critical closure), nothing else written outside the loader, and the composition with
  every mod already on the branch;
* the compiled payload on the SH-2A interpreter (tests/sh2a.py) against a Python reference: the
  frame rounding, the beat search in the grid window, the auto loop points (whole beats, fraction
  mode, variable tempo, window edge), LOOP IN/OUT points, and the four entry points end to end
  with small stand-ins for the MAIN routines they call (ring sectors, audio_send_cmd);
* live (QEMU + a baken stick: 128.00 BPM with the first downbeat at 0.35 s, 127.96 BPM, and
  120.00 -> 133.33 BPM): auto loops of 4 and 1 beats, a 1/2-beat fraction loop and LOOP IN/OUT
  pressed off-beat, against the frames computed from the grid entries in RAM; the loop then plays
  between them, EXIT and RELOOP work.
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
MODS = ["quant_loops", "quant_loops_auto_send", "quant_loops_auto_send2", "quant_loops_auto_keep",
        "quant_loops_loop_in", "quant_loops_loop_out", "quant_loops_loop_out2", "quant_loops_drift"]
AUDIO_SEND_CMD = 0x0C0D7464
# (pool word, stock value, instructions that read it, payload symbol)
POOLS = [
    (0x0C0E5D4C, 0x0C073954, [0x0C0E5C0E], "quant_loops_auto_in"),
    (0x0C0E5FC8, AUDIO_SEND_CMD, [0x0C0E5D90, 0x0C0E5EB4, 0x0C0E5EE4, 0x0C0E5F02, 0x0C0E5F22,
                                  0x0C0E5F6E, 0x0C0E5F8C, 0x0C0E5FB0], "quant_loops_auto_send"),
    (0x0C0E5D08, AUDIO_SEND_CMD, [0x0C0E5B00, 0x0C0E5B20, 0x0C0E5B4C, 0x0C0E5CC8, 0x0C0E5CE2],
     "quant_loops_auto_send"),
    (0x0C0E5FD0, 0x0C0059D2, [0x0C0E5DAA], "quant_loops_auto_keep"),
    (0x0C0DFA80, 0x0C0059D2, [0x0C0DF95E], "quant_loops_loop_in"),
    (0x0C0E0090, AUDIO_SEND_CMD, [0x0C0DFE32, 0x0C0DFE5E, 0x0C0DFEB8, 0x0C0DFEC2, 0x0C0E0022,
                                  0x0C0E002C, 0x0C0E0076, 0x0C0E0080], "quant_loops_loop_out"),
    (0x0C0E02C0, AUDIO_SEND_CMD, [0x0C0E0138, 0x0C0E0142, 0x0C0E018E, 0x0C0E0198], "quant_loops_loop_out"),
    # the shared state-4 tick (c850.h), the same word and symbol as loop_resize_tick
    (0x0C0D7B98, 0x0C0E0F34, [0x0C0D79A6], "c850_loop_tick"),
]
GRID_CTX = 0x0C9D24FC
BASE_TENTHS = 0x0C9D23EC
USB_MODE = 0x0CE6D25A
SOURCE = 0x0CE6D252
PLAYER_STATE = 0x0CE6D25D       # + source
D1C0 = 0x0CE6D1C0
CA90 = 0x0CE6CA90
IN_SECTOR = 0x0C7F6468
LOOP_IN = 0x0C7F6498
BEATS = 0x0C1E1088
FRAC = 0x0CE6D154
SECTOR_TABLE = 0x0C750C3C       # 16 bytes per ring sector, +0 = frame
TRACKLEN_TABLE = 0x0CE59B58     # + slot * 0x3A4
NEXT_SECTOR = 0x0DDFE100        # test stand-in: what FUN_0C0059D2 "computes"

needs_toolchain = pytest.mark.skipif(not payloadbuild.have_toolchain(), reason="sh-elf toolchain not installed")
needs_main = pytest.mark.skipif(not MAIN.is_file(), reason="extract/cdj850/main_unpacked.bin is not present")


# ---- Python reference of payload/quant_loops.c ----

def ms_to_frames(ms):
    return (ms * 75 + 500) // 1000


class Grid:
    """The grid window as the payload sees it: entries (beat_in_bar, bpm100, ms) of window w."""

    def __init__(self, entries, w=0, total=None, base=None):
        self.e, self.w = entries, w
        self.total = len(entries) + 500 * w if total is None else total
        self.base = (entries[0][1] + 5) // 10 if base is None else base

    def ok(self, i):
        """The grid gave the deck's tempo: a beat within 8 of i has the base BPM word's tenths."""
        return any((self.e[j][1] + 5) // 10 == self.base for j in range(max(0, i - 8), min(self.n, i + 9)))

    @property
    def n(self):
        return max(0, min(1000, self.total - 500 * self.w))

    def f(self, i):
        return ms_to_frames(self.e[i][2])

    def more_after(self):
        return 500 * self.w + self.n < self.total

    def at_or_before(self, frame):
        n = self.n
        if n == 0:
            return "away"
        if frame < self.f(0):
            return None if self.w == 0 else "away"
        if frame >= self.f(n - 1):
            return "away" if self.more_after() else n - 1
        return max(i for i in range(n) if self.f(i) <= frame)

    def nearest(self, frame):
        i = self.at_or_before(frame)
        if i == "away":
            return "away"
        if i is None:
            return 0
        if i + 1 >= self.n:
            return i
        return i if frame - self.f(i) <= self.f(i + 1) - frame else i + 1

    def ms_after(self, i, k):
        if i + k < self.n:
            return self.e[i + k][2]
        bpm100 = self.e[i][1] or 13000
        return self.e[i][2] + (k * 6000000 + bpm100 // 2) // bpm100

    def auto_points(self, pos, beats, frac):
        """The in is the grid point nearest the playhead, behind or ahead (ties to the earlier one)."""
        if frac:
            i = self.at_or_before(pos)
            if i is None:
                i = 0
            if i == "away" or not self.ok(i):
                return None
            t_beat = self.e[i][2]
            d = self.ms_after(i, 1) - t_beat
            steps = 16 // beats                     # k == steps: the next beat
            k = min(range(steps + 1), key=lambda k: (abs(ms_to_frames(t_beat + (k * beats * d + 8) // 16) - pos), k))
            if k == steps:
                if i + 1 >= self.n:
                    return None
                i += 1
                t_beat = self.e[i][2]
                d = self.ms_after(i, 1) - t_beat
                k = 0
            t_in = t_beat + (k * beats * d + 8) // 16
            t_out = t_beat + ((k + 1) * beats * d + 8) // 16
        else:
            i = self.nearest(pos)
            if i == "away" or not self.ok(i):
                return None
            t_beat = t_in = self.e[i][2]
            t_out = self.ms_after(i, beats)
        fin, fout = ms_to_frames(t_in), ms_to_frames(t_out)
        fout = max(fout, fin + 1)
        if fout <= pos:
            return None                             # the playhead must be before the out
        return fin, fout, ms_to_frames(t_in + self.ms_after(i, 8) - t_beat)

    def in_point(self, pos):
        i = self.nearest(pos)
        return pos if i == "away" or not self.ok(i) else self.f(i)

    def out_point(self, loop_in, pos):
        i = self.nearest(pos)
        if i == "away" or not self.ok(i):
            return pos
        if self.f(i) <= loop_in:
            j = self.at_or_before(loop_in)
            if j == "away":
                return pos
            j = 0 if j is None else j + 1
            if j >= self.n or self.f(j) <= loop_in:
                return pos
            i = j
        return self.f(i)


def const_grid(bpm, first_ms, n):
    return [((i % 4) + 1, round(bpm * 100), round(first_ms + i * 60000 / bpm)) for i in range(n)]


def var_grid():
    """120.00 for 32 beats (500 ms), then 133.33 (450 ms), like baken's QVAR track."""
    e = [((i % 4) + 1, 12000, 500 * i) for i in range(32)]
    e += [((i % 4) + 1, 13333, 16000 + 450 * (i - 32)) for i in range(32, 96)]
    return e


# ---- static ----

@needs_main
@needs_toolchain
def test_static_pool_words_outside_the_boot_critical_closure():
    import bootcrit
    stock = MAIN.read_bytes()
    report = {}
    out = patch_main.patch(stock, MODS, "cdj850", report)
    assert {n: report[n][0] for n in MODS} == {n: "applied" for n in MODS}
    p = payloadbuild.build(["quant_loops"], "cdj850")
    closure = bootcrit.closure(stock, BASE, byteorder=">")
    reads = {}
    for k in range(len(stock) // 2):
        op = struct.unpack_from(">H", stock, 2 * k)[0]
        if op & 0xF000 == 0xD000:
            pc = BASE + 2 * k
            reads.setdefault((pc & ~3) + 4 + (op & 0xFF) * 4, []).append(pc)
    for pool, old, loaders, symbol in POOLS:
        o = pool - BASE
        assert struct.unpack_from(">I", stock, o)[0] == old, hex(pool)
        assert struct.unpack_from(">I", out, o)[0] == payloadbuild.lookup(p.symbols, payloadbuild.gate_name(symbol)), hex(pool)
        assert reads[pool] == loaders, (hex(pool), [hex(a) for a in reads[pool]])
        assert not closure.hit(o, o + 4), hex(pool)
        for a in loaders:
            assert not closure.hit(a - BASE, a - BASE + 2), hex(a)
    # nothing else changes in the code that reaches the eight words (auto_beat_loop_set, its
    # resize helper and the play-state key handler that holds LOOP IN / LOOP OUT)
    for lo, hi in ((0x0C0E5590, 0x0C0E630C), (0x0C0DEB50, 0x0C0E0F34)):
        diff = [a for a in range(lo, hi) if stock[a - BASE] != out[a - BASE]]
        assert all(any(pool <= a < pool + 4 for pool, *_ in POOLS) for a in diff), [hex(a) for a in diff]
    assert len([a for a in range(0x0C0D0000, 0x0C0F0000) if stock[a - BASE] != out[a - BASE]]) <= 32


@needs_main
@needs_toolchain
def test_composes_with_the_branch_mods():
    names = ["autostandby", "bpm999", "bpm999grid", "bpm999range", "bpm_probe", "dspprobe",
             "payload_demo", "payload_demo_redirect", "tempowide", "tempowidemath1", "tempowidemath2",
             "bpm_decimals", "bpm_decimals_grid"] + MODS
    report = {}
    patch_main.patch(MAIN.read_bytes(), names, "cdj850", report)
    assert all(report[n][0] == "applied" for n in names), report


@needs_toolchain
def test_payload_has_no_fpu_code_and_small_frames():
    p = payloadbuild.build(["quant_loops"], "cdj850")
    end = p.symbols["__bss_start"] - 0x0DE00020
    names = ["quant_loops_ms_to_frames", "quant_loops_beat_at_or_before", "quant_loops_auto_points",
             "quant_loops_auto_in", "quant_loops_auto_send", "quant_loops_loop_in", "quant_loops_loop_out"]
    for name in names:
        at = payloadbuild.lookup(p.symbols, name) - 0x0DE00020
        words = struct.unpack(">8H", p.blob[at:at + 16])
        assert at < end and not [w for w in words if w >> 12 == 0xF], name
    assert max(p.stack.values()) <= 64


# ---- interpreter ----

def _payload():
    return payloadbuild.build(["quant_loops"], "cdj850")


SETTINGS = 0x0C9DA23C                                   # settings record (c850_ui.h flags at +0x22)


def _settings(flags=0):
    """The settings record as a fresh deck has it, with the mod flags byte (0 = quantize on)."""
    rec = bytearray(0x58)
    rec[0:3] = b"\xC3\x02\x10"
    rec[3:10] = b"HISTORY"
    rec[0x23:0x2B] = b"TAG LIST"
    rec[0x22] = flags
    return bytes(rec)


def _mem(p, entries, w=0, total=None, base=1280, usb=1, state=3, flags=0):
    mem = Memory()
    mem.add(0x0DE00020, p.blob)
    mem.add(SETTINGS, _settings(flags))                   # mod flags byte +0x22: quantize
    mem.add(0x0DDFF000, bytes(0x1000))                   # stack + out pointers
    ctx = bytearray(0x7C + 8 * 1000)
    struct.pack_into(">I", ctx, 0x70, 1)
    struct.pack_into(">I", ctx, 0x74, len(entries) + 500 * w if total is None else total)
    struct.pack_into(">H", ctx, 0x78, w)
    for i, e in enumerate(entries[:1000]):
        struct.pack_into(">HHI", ctx, 0x7C + 8 * i, *e)
    mem.add(GRID_CTX, ctx)
    mem.add(BASE_TENTHS, struct.pack(">H", base))
    mem.add(USB_MODE, bytes([usb]))
    mem.add(SOURCE, bytes([2]))                                    # source index 2 (USB)
    mem.add(PLAYER_STATE, bytes([state] * 4))
    return mem


def _call(p, mem, name, *args, stack=()):
    cpu = Sh2a(mem)
    cpu.r[15] = 0x0DDFFF00
    for i, a in enumerate(args):
        cpu.r[4 + i] = a & 0xFFFFFFFF
    for i, a in enumerate(stack):
        mem.put32(0x0DDFFF00 + 4 * i, a)
    cpu.run(payloadbuild.lookup(p.symbols, name), Sh2a.RET)
    return cpu.r[0]


def _s32(v):
    return v - (1 << 32) if v >> 31 else v


GRIDS = {
    "128 @0.35": const_grid(128.0, 350, 120),
    "127.96 @0.35": const_grid(127.96, 350, 120),
    "120->133.33": var_grid(),
}


@needs_toolchain
def test_ms_to_frames_rounds():
    p = _payload()
    mem = _mem(p, [])
    for ms in (0, 6, 7, 13, 14, 20, 350, 2693, 2694, 4568, 4569, 16900, 57000000):
        assert _call(p, mem, "quant_loops_ms_to_frames", ms) == ms_to_frames(ms), ms
    assert ms_to_frames(4568) == 343 and ms_to_frames(16900) == 1268 and ms_to_frames(6) == 0


@needs_toolchain
def test_beat_search_on_the_interpreter():
    p = _payload()
    for label, entries in GRIDS.items():
        g = Grid(entries)
        mem = _mem(p, entries)
        frames = list(range(0, g.f(len(entries) - 1) + 40, 7)) + [g.f(i) + d for i in (0, 5, 31, 32, 33) for d in (-1, 0, 1) if g.f(i) + d >= 0]
        for fr in frames:
            want = g.at_or_before(fr)
            got = _s32(_call(p, mem, "quant_loops_beat_at_or_before", fr))
            assert got == {None: -1, "away": -2}.get(want, want), (label, fr, got, want)
            want = g.nearest(fr)
            got = _s32(_call(p, mem, "quant_loops_beat_nearest", fr))
            assert got == (-2 if want == "away" else want), (label, fr, got, want)
    # a later window: before it -> away; past its end with more beats -> away
    e = const_grid(128.0, 350, 1500)
    later = Grid(e[500:], w=1, total=1500)
    mem = _mem(p, e[500:], w=1, total=1500)
    assert _s32(_call(p, mem, "quant_loops_beat_at_or_before", later.f(0) - 1)) == -2
    assert _s32(_call(p, mem, "quant_loops_beat_at_or_before", later.f(999) + 5)) == 999   # grid ends
    mem = _mem(p, e[500:], w=1, total=1600)
    assert _s32(_call(p, mem, "quant_loops_beat_at_or_before", later.f(999) + 5)) == -2    # window 2 has it
    assert _s32(_call(p, mem, "quant_loops_beat_at_or_before", later.f(10) + 1)) == 10


def _points(p, mem, pos, beats, frac):
    outp = 0x0DDFF800
    ok = _call(p, mem, "quant_loops_auto_points", pos, beats, frac, outp, stack=(outp + 4, outp + 8))
    return (mem.u32(outp), mem.u32(outp + 4), mem.u32(outp + 8)) if ok else None


@needs_toolchain
def test_auto_points_on_the_interpreter():
    p = _payload()
    for label, entries in GRIDS.items():
        g = Grid(entries)
        mem = _mem(p, entries, base=(entries[0][1] + 5) // 10)
        for pos in range(0, g.f(len(entries) - 1) + 60, 11):
            for beats in (1, 2, 4, 8):
                for frac in (0, 1):
                    got = _points(p, mem, pos, beats, frac)
                    assert got == g.auto_points(pos, beats, frac), (label, pos, beats, frac, got)
                    assert got is None or (got[0] < got[1] and pos < got[1]), (label, pos, beats, frac, got)
    # hand-checked examples (128.00 from 0.35 s: beat k at 350 + 468.75 k ms): the in is the grid
    # point nearest the playhead, behind or ahead of it
    g = Grid(GRIDS["128 @0.35"])
    assert g.auto_points(231, 4, 0) == (237, 378, 518)       # beat 6 (3162.5 ms, 6 ahead) .. beat 10
    assert g.auto_points(231, 1, 0) == (237, 272, 518)
    assert g.auto_points(215, 4, 0) == (202, 343, 483)       # beat 5 (13 behind) .. beat 9 (4568.75 ms)
    # fraction loops on the sub-beat grid (beat 5 = 2694 ms = frame 202, beat 6 = 3163 ms = 237):
    # 1/2 pressed at 231 (3/4 into the beat): steps 202, 220, 237 -> beat 6, out = 3397 ms
    assert g.auto_points(231, 8, 1) == (237, 255, 518)
    # 1/4 at 231: steps at 2694, 2811, 2929, 3046 ms (202, 211, 220, 228), beat 6 (237): in 228
    assert g.auto_points(231, 4, 1) == (228, 237, 510)
    assert g.auto_points(205, 8, 1) == (202, 220, 483)      # early in the beat: from the beat
    assert g.auto_points(236, 1, 1) == (235, 237, 516)      # 1/16: 235 and 237 tie: the earlier one
    v = Grid(var_grid())
    assert v.auto_points(1140, 4, 0) == (1125, 1268, 1403)   # 15.0 s, 15.5, 16.0, 16.45 -> 16.9 s; 8: 18.7 s
    assert v.auto_points(1150, 4, 0) == (1163, 1301, 1436)   # 15.5 s is nearer: .. 17.35 s
    # no grid tempo: stock
    mem = _mem(p, GRIDS["128 @0.35"], base=1300)
    assert _points(p, mem, 231, 4, 0) is None
    mem = _mem(p, GRIDS["128 @0.35"], usb=0)
    assert _points(p, mem, 231, 4, 0) is None
    mem = _mem(p, GRIDS["128 @0.35"])
    assert _points(p, mem, 10, 4, 0) == (26, 167, 308)       # lead-in: the first beat is nearest
    # the window (1000 beats) ends before in + N, more beats follow: the grid tempo of the in beat
    e = const_grid(127.96, 350, 1200)
    g = Grid(e[:1000], total=1200)
    mem = _mem(p, e[:1000], total=1200, base=1280)
    for pos in (g.f(995) + 3, g.f(998) + 1):
        assert _points(p, mem, pos, 8, 0) == g.auto_points(pos, 8, 0)
        assert _points(p, mem, pos, 4, 0) == g.auto_points(pos, 4, 0)
    pos = g.f(995) + 3
    assert g.auto_points(pos, 8, 0)[1] == ms_to_frames(e[995][2] + (8 * 6000000 + 6398) // 12796)
    # every position up to the window edge: whole and fraction loops keep in < out and pos < out
    for pos in range(g.f(990), g.f(999) + 30):
        for beats in (1, 2, 4, 8):
            for frac in (0, 1):
                got = _points(p, mem, pos, beats, frac)
                assert got == g.auto_points(pos, beats, frac), (pos, beats, frac, got)
                assert got is None or (got[0] < got[1] and pos < got[1]), (pos, beats, frac, got)
    # past the last beat of a grid that ends (no more windows): stock once the beat is behind
    e = const_grid(128.0, 350, 40)
    g = Grid(e)
    mem = _mem(p, e)
    assert _points(p, mem, g.f(39) + 3, 4, 0) == g.auto_points(g.f(39) + 3, 4, 0) is not None
    assert _points(p, mem, g.f(39) + 400, 4, 0) is None and g.auto_points(g.f(39) + 400, 4, 0) is None
    assert _points(p, mem, g.f(39) + 40, 8, 1) is None


@needs_toolchain
def test_loop_in_out_points_on_the_interpreter():
    p = _payload()
    for label, entries in GRIDS.items():
        g = Grid(entries)
        mem = _mem(p, entries, base=(entries[0][1] + 5) // 10)
        for pos in range(0, g.f(len(entries) - 1) + 40, 5):
            assert _call(p, mem, "quant_loops_in_point", pos) == g.in_point(pos), (label, pos)
            for loop_in in (pos - 80, pos - 20, pos - 3, pos):
                if loop_in < 0:
                    continue
                got = _call(p, mem, "quant_loops_out_point", loop_in, pos)
                assert got == g.out_point(loop_in, pos), (label, loop_in, pos, got)
    g = Grid(GRIDS["128 @0.35"])
    assert g.in_point(231) == 237 and g.in_point(215) == 202
    assert g.out_point(202, 300) == 308 and g.out_point(202, 210) == 237   # nearest is in -> next beat


# MAIN stand-ins for the entry points: ring sectors map to frames through SECTOR_TABLE
def _op_movl(pc, lit, rn):
    return 0xD000 | rn << 8 | (lit - ((pc & ~3) + 4)) // 4


def _stubs(mem, log):
    def put(addr, ops, lits=()):
        code = struct.pack(">%dH" % len(ops), *ops) + b"".join(struct.pack(">I", v) for v in lits)
        mem.add(addr, code)
    # FUN_0C073954 (track -> slot): return the track
    put(0x0C073954, [0x000B, 0x6043])
    # FUN_0C00586C (sector -> track): 1
    put(0x0C00586C, [0x000B, 0xE001])
    # FUN_0C0059D2 (next sector): 0x0C7F6468 = *(s16 *)NEXT_SECTOR; code at 0x0C0059D0 (aligned) + 2
    a = 0x0C0059D0
    put(a, [0x0009, _op_movl(a + 2, a + 16, 1), _op_movl(a + 4, a + 20, 2), 0x6021, 0x000B, 0x2101,
            0x0009, 0x0009], [IN_SECTOR, NEXT_SECTOR])
    # ring_sector_to_frame: frame = *(SECTOR_TABLE + 16 r4); code at 0x0C00851C (aligned) + 2
    a = 0x0C00851C
    ops = [0x0009, _op_movl(a + 2, a + 12, 0), 0x4408, 0x4408, 0x000B, 0x004E]
    put(a, ops, [SECTOR_TABLE])
    # FUN_0C008294 (sector + delta, mod 2400)
    a = 0x0C008294
    put(a, [0x6043, 0x305C, 0x9107, 0x3013, 0x8B00, 0x3018, 0x4011, 0x8900, 0x301C, 0x000B, 0x0009,
            2400, 0x0009])
    # audio_send_cmd: log r4..r7 and the four stack words
    a = 0x0C0D7464
    put(a, [_op_movl(a, a + 32, 0), 0x2042, 0x1051, 0x1062, 0x1073, 0x51F0, 0x1014,
            0x51F1, 0x1015, 0x51F2, 0x1016, 0x51F3, 0x1017, 0x000B, 0x0009, 0x0009], [log])


def _ring(mem, first_frame, base_sector=0):
    """2400 ring sectors holding consecutive frames from first_frame at base_sector."""
    t = bytearray(16 * 2400)
    for s in range(2400):
        struct.pack_into(">I", t, 16 * s, (first_frame + (s - base_sector) % 2400) & 0xFFFFFFFF)
    mem.add(SECTOR_TABLE, t)


def _entry_mem(p, entries, state=3):
    mem = _mem(p, entries, base=(entries[0][1] + 5) // 10, state=state)
    log = 0x0DDFE000
    mem.add(log, bytes(0x40))
    mem.add(NEXT_SECTOR, bytes(4))
    _stubs(mem, log)
    mem.add(0x0CE6D1A0, bytes(0x40))           # D1C0
    mem.add(CA90 - 0x10, bytes(0x20))
    mem.add(0x0C7F6460, bytes(0x60))           # 0x0C7F6468 in sector, loop in record 0x0C7F6498
    mem.add(BEATS, bytes(4))
    mem.add(FRAC, bytes(4))
    tl = bytearray(4 * 0x3A4)
    for slot in range(4):
        struct.pack_into(">I", tl, slot * 0x3A4, 100000)
    mem.add(TRACKLEN_TABLE, tl)
    return mem, log


def _log(mem, log):
    return [mem.u32(log + 4 * i) for i in range(8)]


@needs_toolchain
def test_auto_loop_entry_points_end_to_end():
    p = _payload()
    g = Grid(GRIDS["128 @0.35"])
    for beats, frac, pos in ((4, 0, 231), (1, 0, 600), (8, 1, 900), (2, 0, 1001)):
        mem, log = _entry_mem(p, GRIDS["128 @0.35"])
        _ring(mem, first_frame=pos - 1000, base_sector=0)     # sector 1000 holds pos
        mem.write(IN_SECTOR, struct.pack(">h", 1000))
        mem.put32(D1C0, pos)
        mem.write(BEATS, bytes([beats]))
        mem.write(FRAC, bytes([frac]))
        assert _call(p, mem, "quant_loops_auto_in", 1) == 1                  # slot from the original
        fin, fout, f8 = g.auto_points(pos, beats, frac)
        assert struct.unpack(">h", mem.read(IN_SECTOR, 2))[0] == 1000 - (pos - fin)
        assert mem.u32(D1C0) == fin
        stock_out = fin + beats * 4500 // (128 * (frac * 15 + 1))             # what beat_loop_out_from_bpm sends
        mem.write(IN_SECTOR, struct.pack(">h", 7))                            # whatever ran in between
        _call(p, mem, "quant_loops_auto_send", 6, 0, stock_out, 0, stack=(0, 1, 0, 2))
        # the sector is the snapped one when the command is queued (audio task first) ...
        assert struct.unpack(">h", mem.read(IN_SECTOR, 2))[0] == 1000 - (pos - fin)
        assert _log(mem, log) == [6, 0, fout, 0, 0, 1, 0, 2]
        assert mem.u32(D1C0) == fout and mem.u32(CA90) == f8
        # ... and after the post-send refresh (audio task later); that is left out once, then stock
        mem.write(NEXT_SECTOR, struct.pack(">h", 1001))
        _call(p, mem, "quant_loops_auto_keep")
        assert struct.unpack(">h", mem.read(IN_SECTOR, 2))[0] == 1000 - (pos - fin)
        _call(p, mem, "quant_loops_auto_keep")
        assert struct.unpack(">h", mem.read(IN_SECTOR, 2))[0] == 1001
        # the next send (a resize) passes through untouched
        _call(p, mem, "quant_loops_auto_send", 6, 0, 1234, 1, stack=(0, 1, 0, 2))
        assert _log(mem, log) == [6, 0, 1234, 1, 0, 1, 0, 2]
    # the beat sector is not in the ring (a jump): in stays, the out keeps the grid length
    mem, log = _entry_mem(p, GRIDS["128 @0.35"])
    _ring(mem, first_frame=5000)
    mem.write(IN_SECTOR, struct.pack(">h", 1000))
    mem.put32(D1C0, 231)
    mem.write(BEATS, bytes([4]))
    _call(p, mem, "quant_loops_auto_in", 1)
    assert mem.u32(D1C0) == 231 and struct.unpack(">h", mem.read(IN_SECTOR, 2))[0] == 1000
    _call(p, mem, "quant_loops_auto_send", 6, 0, 371, 0, stack=(0, 1, 0, 2))
    assert _log(mem, log)[2] == 231 + (378 - 237)                           # beat 6 is nearest 231
    mem.write(NEXT_SECTOR, struct.pack(">h", 1001))
    _call(p, mem, "quant_loops_auto_keep")                                 # nothing snapped: stock refresh
    assert struct.unpack(">h", mem.read(IN_SECTOR, 2))[0] == 1001
    # the track ends inside the loop: out clamps to its last frame; ends at the playhead or at the
    # in (beat 6 = 237, nearest 231): stock
    for tracklen, want in ((240, (237, 239)), (232, None), (238, None)):
        mem, log = _entry_mem(p, GRIDS["128 @0.35"])
        for slot in range(4):
            mem.put32(TRACKLEN_TABLE + slot * 0x3A4, tracklen)
        _ring(mem, first_frame=231 - 1000)
        mem.write(IN_SECTOR, struct.pack(">h", 1000))
        mem.put32(D1C0, 231)
        mem.write(BEATS, bytes([4]))
        _call(p, mem, "quant_loops_auto_in", 1)
        _call(p, mem, "quant_loops_auto_send", 6, 0, 231, 0, stack=(0, 1, 0, 2))
        sector = struct.unpack(">h", mem.read(IN_SECTOR, 2))[0]
        if want:
            assert (mem.u32(D1C0), sector, _log(mem, log)[2]) == (want[1], 1000 + (want[0] - 231), want[1])
            assert mem.u32(CA90) == tracklen - 1
        else:
            assert (mem.u32(D1C0), sector, _log(mem, log)[2]) == (231, 1000, 231)
    # cued (state 6): the in is the cue, only the out moves
    mem, log = _entry_mem(p, GRIDS["128 @0.35"], state=6)
    _ring(mem, first_frame=231 - 1000)
    mem.write(IN_SECTOR, struct.pack(">h", 1000))
    mem.put32(D1C0, 231)
    mem.write(BEATS, bytes([4]))
    _call(p, mem, "quant_loops_auto_in", 1)
    assert mem.u32(D1C0) == 231
    # no grid tempo: stock values everywhere
    mem, log = _entry_mem(p, GRIDS["128 @0.35"])
    mem.write(BASE_TENTHS, struct.pack(">H", 1300))
    _ring(mem, first_frame=231 - 1000)
    mem.write(IN_SECTOR, struct.pack(">h", 1000))
    mem.put32(D1C0, 231)
    mem.write(BEATS, bytes([4]))
    _call(p, mem, "quant_loops_auto_in", 1)
    _call(p, mem, "quant_loops_auto_send", 6, 0, 371, 0, stack=(0, 1, 0, 2))
    assert mem.u32(D1C0) == 231 and _log(mem, log)[2] == 371


@needs_toolchain
def test_loop_in_out_entry_points_end_to_end():
    p = _payload()
    g = Grid(GRIDS["127.96 @0.35"])
    for pos in (215, 231, 600, 1003):
        mem, log = _entry_mem(p, GRIDS["127.96 @0.35"])
        _ring(mem, first_frame=pos - 1000)
        mem.write(NEXT_SECTOR, struct.pack(">h", 1000))
        _call(p, mem, "quant_loops_loop_in")
        assert struct.unpack(">h", mem.read(IN_SECTOR, 2))[0] == 1000 + g.in_point(pos) - pos
        loop_in = g.in_point(pos)
        mem.put32(LOOP_IN, loop_in)
        for out_pos in (loop_in + 2, loop_in + 40, loop_in + 80):
            sector = 1000 + out_pos - pos
            _call(p, mem, "quant_loops_loop_out", 1, 1, 0, 3, stack=(sector, 1, 0, 2))
            want = g.out_point(loop_in, out_pos)
            assert _log(mem, log) == [1, 1, 0, 3, sector + want - out_pos, 1, 0, 2], (pos, out_pos)
    # other commands through the same pool words pass untouched; CD mode is stock
    _call(p, mem, "quant_loops_loop_out", 1, 2, 0, 1, stack=(1010, 0, 0, 2))
    assert _log(mem, log) == [1, 2, 0, 1, 1010, 0, 0, 2]
    mem.write(USB_MODE, b"\x00")
    _call(p, mem, "quant_loops_loop_out", 1, 1, 0, 3, stack=(1010, 1, 0, 2))
    assert _log(mem, log)[4] == 1010


# ---- live ----

# Stick: Q128 (128.00 BPM, first downbeat 0.35 s), QVAR (120.00 for 16 s, then 133.33) and Q12796
# (127.96 BPM, first downbeat 0.35 s), built by scripts/media/make_test_stick.py with baken; QVAR
# gets two <TEMPO> entries (as in tests/test_cdj850_bpm_decimals.py).
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
tracks = [("Q128", 128.00, 0.35, 24), ("Q12796", 127.96, 0.35, 24), ("QVAR", 120.0, 0.0, 24)]
spec = {"device_name": "QLOOPS", "playlist": "Quant Loops", "tracks": [
    {"title": t, "bpm": b, "first_downbeat": fd, "bars": n} for t, b, fd, n in tracks]}
sp = pathlib.Path(out_dir) / 'spec.json'
sp.write_text(json.dumps(spec))
raise SystemExit(stick.main(['--spec', str(sp), '--out', out_dir + '/wav-stick',
                             '--image', image, '--size-mb', '32', '--baken', baken]))
'''

PLAY_FRAME, OUT_REC, OUT_REQ, STATE_USB = 0x0C7F647C, 0x0C7F64B0, 0x0C7F64DC, 0x0CE6D25F
LED_B7, LED_B6 = 0x0C9D0AEB, 0x0C9D0AEA
MEMCUE_LIST, CUE_FRAME = 0x0CE649D4, 0x0CE6D158
# per grid (first entry's BPMx100): (scenario, key, press frame or (LOOP IN frame, LOOP OUT gap))
# ("q", F): press at about 3/4 of the beat at or after frame F
LIVE_PLAN = {
    12800: [("auto4", "beat_4", 300), ("auto1", "beat_1", 600), ("frac8", "beat_8", 900),
            ("frac8q", "beat_8", ("q", 1050)), ("frac4q", "beat_4", ("q", 1150)), ("manual", None, (1250, 76))],
    12000: [("auto4", "beat_4", 1135), ("auto1", "beat_1", 1400), ("frac8", "beat_8", 1600),
            ("frac8q", "beat_8", ("q", 1700)), ("frac4q", "beat_4", ("q", 1780)), ("manual", None, (1880, 80))],
    12796: [("auto4", "beat_4", 300), ("auto1", "beat_1", 600), ("frac8", "beat_8", 900),
            ("frac8q", "beat_8", ("q", 1050)), ("frac4q", "beat_4", ("q", 1150)), ("manual", None, (1250, 76))],
}
FRACTION = {"frac8": 8, "frac8q": 8, "frac4q": 4}
SYMS = ("last_pos", "last_in", "last_out", "auto_count", "in_count", "out_count", "refused")


def build_stick(path):
    import importlib.util
    if not BAKEN.is_file() or importlib.util.find_spec("pyfatfs") is None or importlib.util.find_spec("numpy") is None:
        pytest.skip("tools/baken/baken, pyfatfs and numpy are needed for the grid stick")
    image = path / "qloops.img"
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


def drive(stick, flash=None, syms=None):
    """Boot, play each track and press the plan; returns {grid bpm100: {scenario: values}}."""
    sys.path.insert(0, str(ROOT / "scripts" / "debug"))
    import cdj850_trace as tr
    res = {}

    def q(d, name):
        return d.rw(payloadbuild.lookup(syms, "quant_loops_" + name)) if syms else None

    def snap(d):
        d.q("stop")
        v = {"in": d.rw(LOOP_IN), "out": d.rw(OUT_REC), "req": d.rw(OUT_REQ), "ca90": d.rw(CA90),
             "state": d.rb(STATE_USB), "beats": d.rb(BEATS), "frac": d.rb(FRAC), "led7": d.rb(LED_B7),
             "led6": d.rb(LED_B6), "frame": d.rw(PLAY_FRAME)}
        v.update({"q_" + s: q(d, s) for s in SYMS})
        d.q("cont")
        return v

    def wait_frame(d, f):
        end = time.monotonic() + 90
        while d.rw(PLAY_FRAME) < f:
            assert time.monotonic() < end, f
            time.sleep(0.05)
        return d.rw(PLAY_FRAME)

    def watch(d, secs):
        """Play frames while a loop plays, until `secs` of audio (virtual time, 75 frames/s) have
        been played after the first wrap, or 30 s of host time."""
        fr, need, end = [d.rw(PLAY_FRAME)], int(secs * 75), time.monotonic() + 30
        played = wraps = 0
        first = None
        while time.monotonic() < end and (wraps == 0 or played < need):
            time.sleep(0.03)
            fr.append(d.rw(PLAY_FRAME))
            if fr[-1] < fr[-2]:
                wraps += 1
                first = first or len(fr) - 1
            elif wraps:
                played += fr[-1] - fr[-2]
        # a loop whose in is ahead of the playhead: the deck plays on to the in, then loops; "min"
        # counts from the first wrap, "start" is where the watch began
        looped = fr[first:] if first else fr
        return {"min": min(looped), "max": max(fr), "wraps": wraps, "start": fr[0],
                "wrap": [fr[first - 1], fr[first]] if first else None}

    def set_frac(d, want):
        """BEAT SELECT toggles fraction mode (u8 0x0CE6D154); press until it reads `want`."""
        for _ in range(4):
            if d.rb(FRAC) == want:
                return
            d.key("beat_select")
            time.sleep(1.2)
        assert d.rb(FRAC) == want, "fraction mode did not follow BEAT SELECT"

    def exit_loop(d):
        if d.rb(STATE_USB) == 4:
            d.key("reloop")
            time.sleep(1.5)
        return d.rb(STATE_USB)

    port, drift_on = _drift_gdb(syms)
    with tr.Deck(stick, flash=flash, gdb_port=port) as d:
        if port:
            g = tr.Gdb(port)
            g._send("c")
        d.wait_line("mode=0x0a", "[Q128]", timeout=300)
        if port:
            assert g.poke32(drift_on, 0) == "OK"
        for k in range(3):
            time.sleep(1.5)
            for _ in range(4):
                if d.rb(STATE_USB) == 3:
                    break
                d.key("play")
                time.sleep(2.5)
            raw = d.dump(GRID_CTX + 0x7C, 8 * 200)
            n = d.rw(GRID_CTX + 0x74)
            entries = [struct.unpack_from(">HHI", raw, 8 * i) for i in range(min(n, 200))]
            t = res[entries[0][1]] = {"grid": {"count": n, "w": d.rh(GRID_CTX + 0x78), "entries": entries}}
            for name, key, at in LIVE_PLAN[entries[0][1]]:
                if name == "manual":
                    v = {"pre_in": wait_frame(d, at[0])}
                    d.key("loop_in")
                    time.sleep(0.8)
                    v.update({"in_after_loop_in": d.rw(LOOP_IN), "q_in_pos": q(d, "last_pos")})
                    v["pre_out"] = wait_frame(d, at[0] + at[1])
                    d.key("loop_out")
                    time.sleep(1.2)
                    v.update(snap(d))
                    v["watch"] = watch(d, 3.0)
                    d.key("memory")                     # store the loop as a memory loop
                    time.sleep(1.5)
                    v["exit_state"] = exit_loop(d)
                    d.key("reloop")
                    time.sleep(1.5)
                    v["reloop"] = {"state": d.rb(STATE_USB), "in": d.rw(LOOP_IN), "out": d.rw(OUT_REC),
                                   "watch": watch(d, 2.0)}
                    exit_loop(d)
                    cues = d.dump(MEMCUE_LIST, 0x24 * 11)
                    v["memcues"] = [struct.unpack_from(">9I", cues, 0x24 * i)[3:5] for i in range(11)
                                    if struct.unpack_from(">I", cues, 0x24 * i)[0] not in (0xFFFFFFFF,)
                                    and struct.unpack_from(">I", cues, 0x24 * i)[0] & 1]
                    d.key("call_prev")                  # recall it: cued at its in, loop armed
                    time.sleep(2.0)
                    v["call"] = {"state": d.rb(STATE_USB), "cue": d.rw(CUE_FRAME)}
                    d.key("play")
                    time.sleep(1.0)
                    v["call"]["watch"] = watch(d, 2.5)
                    v["call"]["state_playing"] = d.rb(STATE_USB)
                    exit_loop(d)
                else:
                    set_frac(d, int(name.startswith("frac")))
                    if isinstance(at, tuple):          # 3/4 into a beat (the press lands ~1-2 frames later)
                        g = Grid([tuple(e) for e in entries], total=n)
                        # the first beat from at[1] whose 3/4 point is still ahead (the steps
                        # before can take longer than planned on a loaded host)
                        soon = max(at[1], d.rw(PLAY_FRAME) + 8)
                        q34 = [g.f(i) + (3 * (g.f(i + 1) - g.f(i))) // 4 - 2 for i in range(len(entries) - 1)]
                        at = next(f for f in q34 if f >= soon)
                    v = {"pre": wait_frame(d, at)}
                    d.key(key)
                    time.sleep(1.2)
                    v.update(snap(d))
                    v["watch"] = watch(d, 3.0)
                    if name == "auto4":                 # resize inside the loop: stock, from CA90
                        d.key("beat_1")
                        time.sleep(1.0)
                        v["resize1_req"] = d.rw(OUT_REQ)
                    v["exit_state"] = exit_loop(d)
                    set_frac(d, 0)
                t[name] = v
            if k < 2:
                fid = d.rw(GRID_CTX + 4)
                d.key("next")
                end = time.monotonic() + 180
                while d.rw(GRID_CTX + 4) == fid:
                    assert time.monotonic() < end, "no next grid"
                    time.sleep(0.5)
    return res


def expected(entries, count, v, name):
    """What the mod must give for this press, from the grid entries the deck had in RAM."""
    g = Grid([tuple(e) for e in entries], total=count)
    g.ok = lambda i: True               # the base BPM word was the grid's (checked by q_refused/counters)
    if name == "manual":
        loop_in = g.in_point(v["q_in_pos"])
        return {"in": loop_in, "out": g.out_point(loop_in, v["q_last_pos"])}
    beats, frac = {"auto4": (4, 0), "auto1": (1, 0)}.get(name, (FRACTION.get(name), 1))
    fin, fout, f8 = g.auto_points(v["q_last_pos"], beats, frac)
    return {"in": fin, "out": fout, "ca90": f8}


@needs_toolchain
@pytest.mark.skipif(not FLASH.is_file(), reason="extract/cdj850/flash_cdj850.bin is not present")
def test_live_loops_on_the_grid(tmp_path):
    if os.name == "nt":
        pytest.skip("QMP socket descriptor inheritance requires POSIX")
    stick = build_stick(tmp_path)
    flash = tmp_path / "flash.bin"
    flash.write_bytes(patch_main.patch_flash(FLASH.read_bytes(), MODS, "cdj850"))
    syms = payloadbuild.build(["quant_loops"], "cdj850").symbols
    res = drive(stick, flash, syms)
    print("quant_loops live:", json.dumps(res))
    assert set(res) == {12800, 12000, 12796}
    for bpm, t in res.items():
        grid = t["grid"]
        for name, v in t.items():
            if name == "grid":
                continue
            want = expected(grid["entries"], grid["count"], v, name)
            assert v["q_refused"] == 0, (bpm, name, v)
            assert v["state"] == 4 and v["in"] == want["in"], (bpm, name, v, want)
            assert v["req" if name != "manual" else "out"] == want["out"], (bpm, name, v, want)
            assert v["in"] < want["out"]
            w = v["watch"]                                  # the loop plays between the points
            assert w["min"] >= v["in"] and w["max"] <= want["out"] and w["wraps"] >= 1, (bpm, name, v)
            assert v["exit_state"] == 3, (bpm, name, v)
            if name != "manual":
                assert v["ca90"] == want["ca90"], (bpm, name, v)
                bit, frac = {"auto4": (4, 0), "auto1": (1, 0)}.get(name, (FRACTION.get(name), 1))
                assert v["beats"] == bit
                assert v["q_last_pos"] < v["req"], (bpm, name, v)   # the playhead is before the out
                # the deck played on (an in ahead: no jump back) and wrapped at the out to the in
                assert w["start"] >= min(v["in"], v["q_last_pos"]) - 2, (bpm, name, v)
                assert w["wrap"][0] >= want["out"] - 6 and v["in"] <= w["wrap"][1] <= v["in"] + 6, (bpm, name, v)
                lit = (0, bit) if frac else (0xF, 0xF ^ bit)    # stock: active bit blinks
                assert v["led6"] & 1 and v["led7"] & 0xF in lit, (bpm, name, v)
            else:
                r = v["reloop"]
                assert r["state"] == 4 and (r["in"], r["out"]) == (v["in"], v["out"]), (bpm, v)
                assert r["watch"]["min"] >= v["in"] and r["watch"]["max"] <= v["out"]
                assert (v["in"], v["out"]) in [tuple(c) for c in v["memcues"]], (bpm, v)
                assert v["call"]["cue"] == v["in"], (bpm, v)
                cw = v["call"]["watch"]
                assert cw["min"] >= v["in"] and cw["max"] <= v["out"], (bpm, v)
        g = Grid([tuple(e) for e in grid["entries"]], total=grid["count"])
        for name in ("frac8q", "frac4q"):                     # 3/4 into a beat: a step of it or the next beat
            v = t[name]
            b = g.at_or_before(v["q_last_pos"])
            assert g.f(b) < v["in"] <= g.f(b + 1) and v["req"] <= g.f(b + 2), (bpm, name, v)
        a4 = t["auto4"]                                       # beat 1 inside it: stock resize from CA90
        assert a4["resize1_req"] == a4["in"] + (a4["ca90"] - a4["in"]) // 8, a4
    # the variable grid: the 4-beat loop pressed at ~15.15 s (15.0 s nearer than 15.5 s) spans the change at 16 s
    v = res[12000]["auto4"]
    assert (v["in"], v["req"]) == (1125, 1268), v           # 15.0 s .. 16.9 s (15.5, 16.0, 16.45)
