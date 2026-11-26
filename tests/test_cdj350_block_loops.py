# SPDX-License-Identifier: GPL-2.0-or-later
"""Block-precise quantized loops on the CDJ-350 MAIN 1.14 (quant_loops, docs/cdj350.md,
"Block-precise loops").

* interpreter (tests/sh2a.py): the compiled payload with a small feeder simulation in Python (one
  call of quant_loops_out_isr per 84-sample block, the read pointer advanced 0x150 before the
  call, a stock wrap to the in record's ring sector when the test says so). Every whole pass is
  within a block of the exact grid length and the average over 1000 passes is exact at 128.00,
  127.96 and 133.33 BPM and across a tempo change; the stock test is never let wrap first (the
  out record holds the pass's last block); the ring-miss fallback wraps stock for that pass;
  RELOOP and a loop found by the state-4 tick (including one made by the frame-loop builds) move
  the stock in-sector entry on to the in block; no grid stays stock.
* live (QEMU cdj350 machine + baken stick): a 4-beat auto loop at 128 BPM plays for 40 s; every
  whole pass 984 or 985 blocks, no misses, and LOOP DIVIDE still folds the feeder's source.
"""
import os
import struct
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "mods"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import payloadbuild  # noqa: E402
import test_cdj350_quant_loops as tq  # noqa: E402

RING = 0x0C1D7710
READ_PTR, READ_SEC, WRITE_SEC, OLDEST = 0x0C7E4D24, 0x0C7E4D10, 0x0C7E4D12, 0x0C7E4D14
LOOP_IN, IN_SEC, OUT_REC = 0x0C7E4D4C, 0x0C7E4D5C, 0x0C7E4D60
STATE = tq.PLAYER_STATE + 2                                    # source index 2 (USB)
TICK = 0x0CD2DCC0

needs_toolchain = pytest.mark.skipif(not payloadbuild.have_toolchain(), reason="sh-elf toolchain not installed")


class Deck:
    """quant_loops on the interpreter with a feeder simulation."""

    def __init__(self, entries):
        from sh2a import Sh2a
        from test_cdj850_hot_cues import _stub
        self.Sh2a = Sh2a
        self.p = p = tq._payload()
        self.g = tq.Grid(entries)
        self.mem, self.log = tq._entry_mem(p, entries, state=4)
        mem = self.mem
        mem.add(0x0DDFE400, bytes(0x1C00))                     # the stack
        mem.add(0x0C7E4D00, bytes(0x400))                      # feeder words, loop records
        mem.add(TICK, bytes(4))
        mem.add(0x0C1AB7D0, bytes(0x20))                       # LOOP DIVIDE's beat module
        mem.add(0x0C1D4CF0, bytes(0x40))                       # wrap flag 0x0C1D4CFD
        tq._ring(mem, first_frame=0)                           # ring sector s holds frame s
        # the stock loop-out test: wrap when the play sector's frame (the sector here) is past out
        _stub(mem, 0x0C0161C8, "cmp/hi r5,r4\nrts\nmovt r0")
        _stub(mem, 0x0C01684E, "rts\nnop")                     # LOOP DIVIDE source = -1
        _stub(mem, 0x0C00B71C, "rts\nmov #0,r0")               # USB ring trim
        _stub(mem, 0x0C00B4A4, "rts\nmov #0,r0")               # short loop?
        _stub(mem, 0x0C016474, "rts\nnop")                     # beat_lamp_update
        _stub(mem, tq.STATE4_KEYS, "rts\nnop")
        _stub(mem, 0x0C084DE4, "rts\nmov #1,r0")               # settings_save
        _stub(mem, 0x0C06777A, "rts\nmov #0,r0")               # display_request
        mem.add(0x0C1CAF00, bytes(0x20))                       # UTILITY level, cursor, timeout
        mem.write(tq.LOOP_ACTIVE, b"\x01")
        self.stock_wraps = 0

    def call(self, name, *args):
        cpu = self.Sh2a(self.mem)
        cpu.r[15] = 0x0DDFFF00
        for i, a in enumerate(args):
            cpu.r[4 + i] = a & 0xFFFFFFFF
        cpu.run(payloadbuild.lookup(self.p.symbols, name), self.Sh2a.RET)
        return cpu.r[0]

    def sym(self, name):
        return self.mem.u32(payloadbuild.lookup(self.p.symbols, "quant_loops_" + name))

    def set_table(self, slot, frame):
        self.mem.put32(tq.SECTOR_TABLE + 16 * slot, frame)

    def put_pos(self, b):
        """Park the read pointer on block b (the next block to play)."""
        s = b // 7
        self.mem.put32(READ_PTR, RING + s * 0x930 + (b % 7) * 0x150)
        self.mem.write(READ_SEC, struct.pack(">H", s))

    def pos(self):
        ptr = self.mem.u32(READ_PTR) - RING
        s = ptr // 0x930
        return self.mem.u32(tq.SECTOR_TABLE + 16 * s) * 7 + (ptr - s * 0x930) // 0x150

    def records(self, in_frame, out_frame, in_slot=None):
        self.mem.put32(LOOP_IN, in_frame)
        self.mem.write(IN_SEC, struct.pack(">H", in_frame if in_slot is None else in_slot))
        self.mem.put32(OUT_REC, out_frame)
        self.mem.write(WRITE_SEC, struct.pack(">H", (in_frame + 1500) % 2400))
        self.mem.write(OLDEST, struct.pack(">H", (in_frame - 900) % 2400))

    def block(self):
        """One feeder interrupt: play the block, advance the pointer, the loop-out test (a stock
        wrap goes to the in record's ring sector start)."""
        ptr = self.mem.u32(READ_PTR) + 0x150
        s = (ptr - RING) // 0x930
        self.mem.put32(READ_PTR, ptr)
        self.mem.write(READ_SEC, struct.pack(">H", s))
        self.mem.put32(TICK, self.mem.u32(TICK) + 2)          # about 1.9 ms
        if self.call("quant_loops_out_isr", s, self.mem.u32(OUT_REC), 0) & 0xFF:
            s = struct.unpack(">H", self.mem.read(IN_SEC, 2))[0]
            self.mem.put32(READ_PTR, RING + s * 0x930)
            self.mem.write(READ_SEC, struct.pack(">H", s))
            self.stock_wraps += 1

    def arm(self, i, s, q):
        """A writer's block loop at grid point (i, s), q/16 beats (quant_loops_set_block_loop)."""
        b = self.g.point_loop(i, s, q)
        out = self.call("quant_loops_set_block_loop", *b)
        assert out == self.g.out_frame(b)
        self.records(b[0] // 7, out)
        return b

    def run_passes(self, n, limit=200000):
        w0 = self.sym("blk_wraps")
        k = 0
        while self.sym("blk_wraps") < w0 + n:
            self.block()
            k += 1
            assert k < limit
        return k


def L(b):
    return b[2] / (40 * b[3])


def const_grid(bpm, n=200):
    return tq._grid(n, round(bpm * 100), 350)


@needs_toolchain
@pytest.mark.parametrize("bpm,q", [(128.0, 64), (128.0, 16), (127.96, 16), (127.96, 4), (133.33, 8)])
def test_every_pass_is_within_a_block(bpm, q):
    """Block by block: the passes alternate between floor and ceil of the exact length, the wrap
    lands on the in block, and the stock test is never let wrap."""
    d = Deck(const_grid(bpm))
    b = d.arm(20, 0, q)
    d.put_pos(b[0] - 30)                                      # playing into the loop
    d.run_passes(8)
    assert d.stock_wraps == 0
    n = d.sym("blk_passes")
    assert n == 7                                             # the first pass started before the in
    lo, hi = d.sym("blk_len_min"), d.sym("blk_len_max")
    assert int(L(b)) <= lo <= hi <= int(L(b)) + 1, (lo, hi, L(b))
    assert abs(d.sym("blk_len_sum") - n * L(b)) < 1.0
    assert d.pos() == b[0]                                # the last wrap landed on the in block
    assert d.sym("blk_misses") == 0 and d.sym("blk_realigns") == 0


@needs_toolchain
@pytest.mark.parametrize("label,i,q", [("128 @0.35", 20, 64), ("127.96 @0.35", 20, 64),
                                       ("120->133.33", 30, 64)])
def test_average_over_1000_passes_is_exact(label, i, q):
    """1000 wraps: the pass lengths (out block - in block) are floor or ceil of L and the wraps
    stay within half a block of the exact grid from the in. 120->133.33 from beat 30: 4 beats
    across the tempo change at beat 32 (ms form)."""
    entries = tq.GRIDS[label]
    d = Deck(entries)
    b = d.arm(i, 0, q)
    if i == 30:
        assert b[3] == 1                                      # a tempo change inside: ms * 21
    lens = []
    d.put_pos(b[0])
    for _ in range(1000):
        out = d.sym("blk_next")
        lens.append(out - b[0])
        d.put_pos(out - 1)                                    # (a jump, not counted as a pass)
        d.block()
        assert d.pos() == b[0], (len(lens), d.pos())      # wrapped to the in block
    exact = L(b)
    assert set(lens) <= {int(exact), int(exact) + 1}, (exact, sorted(set(lens)))
    first = (b[0] * 40 + b[1] - 20) / 40                      # exact in, blocks
    assert abs(b[0] + sum(lens) - (first + 1000 * exact)) <= 0.5 + 1e-9, (sum(lens), exact)
    assert d.stock_wraps == 0
    if label.startswith("128"):
        assert exact == 984.375


@needs_toolchain
def test_the_stock_test_still_sees_the_out_frame():
    """Every owned block also runs the stock test (its "out frame reached" side effects); with
    the out record on the frame of the first pass's last block the play reaches that frame every
    pass. Later passes (Bresenham, 984 or 985 blocks) may end one block into the next frame,
    where the stock verdict would wrap: the hook owns the wrap and ignores it."""
    d = Deck(const_grid(128.0))
    b = d.arm(20, 0, 64)
    out = d.mem.u32(OUT_REC)
    d.put_pos(b[0])
    frames = set()
    for _ in range(3 * 985):
        d.block()
        frames.add(d.pos() // 7)
    assert out in frames and max(frames) <= out + 1
    assert d.stock_wraps == 0 and d.sym("blk_wraps") >= 2


@needs_toolchain
def test_ring_miss_wraps_stock_for_that_pass():
    """The in's sector is no longer where the ring should hold it: that pass wraps stock (frame
    precise) to the in record's sector, the entry moves on to the in block, and the next wrap is
    block precise again."""
    d = Deck(const_grid(128.0))
    b = d.arm(20, 0, 16)
    fin = b[0] // 7
    x = 2000                                                  # a second copy of the audio from fin
    for k in range(300):
        d.set_table(x + k, fin + k)
    d.records(fin, d.mem.u32(OUT_REC), in_slot=x)
    d.set_table(fin, 0xFFFFF)                                 # the slot the jump would use: gone
    d.put_pos(b[0] + 10)                                      # playing inside the loop
    d.run_passes(1)                                           # the first wrap missed, stock wrapped
    assert d.sym("blk_misses") == 1 and d.stock_wraps == 1
    assert d.sym("blk_realigns") == 1                         # entered at x, moved on to the in block
    assert d.sym("blk_wraps") == 1                            # the next wrap is ours, from slot x
    d.run_passes(2)
    assert d.sym("blk_misses") == 1 and d.stock_wraps == 1
    lo, hi = d.sym("blk_len_min"), d.sym("blk_len_max")
    assert int(L(b)) <= lo <= hi <= int(L(b)) + 1


@needs_toolchain
def test_behind_the_oldest_sector_is_a_miss():
    """The ring keeps sectors back to 0x0C7E4D14 only: an in older than that is not jumped to."""
    d = Deck(const_grid(128.0))
    b = d.arm(20, 0, 64)
    d.mem.write(OLDEST, struct.pack(">H", b[0] // 7 + 5))     # the in's sector was dropped
    d.put_pos(b[0] + 10)
    n = 0
    while d.stock_wraps == 0:
        d.block()
        n += 1
        assert n < 2000
    assert d.sym("blk_misses") == 1 and d.sym("blk_wraps") == 0


@needs_toolchain
def test_reloop_moves_the_entry_to_the_in_block():
    d = Deck(const_grid(127.96))
    s = next(s for s in range(16) if d.g.point_loop(20, s, 16)[0] % 7 >= 3)
    b = d.arm(20, s, 16)                                       # an in a few blocks into its frame
    f0 = (b[0] // 7) * 7
    assert b[0] >= f0 + 3
    d.put_pos(b[0] - 5)
    d.run_passes(2)
    # EXIT, play on, RELOOP: the stock entry parks the pointer on the in sector's start
    d.mem.write(STATE, b"\x03")
    d.put_pos(b[0] + 500)
    d.mem.write(STATE, b"\x04")
    d.put_pos(f0)
    d.block()                                                 # block f0 played: realigned
    assert d.sym("blk_realigns") == 1
    assert d.pos() == b[0] + 1                                # as if the entry had been the in block
    p0 = d.sym("blk_passes")
    d.run_passes(2)
    assert d.sym("blk_passes") == p0 + 2
    assert d.sym("blk_len_last") in (int(L(b)), int(L(b)) + 1)


@needs_toolchain
@pytest.mark.parametrize("legacy", [False, True])
def test_loop_found_by_the_tick(legacy):
    """A loop no writer here armed (RELOOP of a loop set with quantize off, a loop of an earlier
    build): the state-4 tick finds its grid point and length from the stock records, the stock
    entry moves on to the in block. legacy: the frame-loop builds' records (in and out rounded
    frames, out = in + the rounded grid length)."""
    d = Deck(const_grid(128.0))
    g = d.g
    b = g.point_loop(20, 4, 32)                                # beat 20 + 1/4, 2 beats
    if legacy:
        fin = tq.ms_to_frames(g.grid_ms(20, 4))
        fout = fin + tq.ms_to_frames(g.grid_ms(20, 36) - g.grid_ms(20, 4))
    else:
        fin, fout = b[0] // 7, g.out_frame(b)
    d.records(fin, fout)
    d.call("quant_loops_loop_tick")
    assert d.sym("blk_derived") == 1
    assert (d.sym("blk_in"), d.sym("blk_a0"), d.sym("blk_num"), d.sym("blk_den")) == b
    d.call("quant_loops_loop_tick")                            # looked at: no second arm
    assert d.sym("blk_arms") == 1
    d.put_pos(fin * 7)                                         # RELOOP: the in sector's start
    d.block()
    if b[0] != fin * 7 + 1:
        assert d.sym("blk_realigns") == 1 and d.pos() == b[0] + 1
    d.run_passes(3)
    assert d.stock_wraps == 0
    assert int(L(b)) <= d.sym("blk_len_min") <= d.sym("blk_len_max") <= int(L(b)) + 1
    # UTILITY QUANTIZE OFF: stock (the switch disarms; the tick does not arm)
    d.call("c350_ui_utility_select", 3, 5)
    d.mem.put32(OUT_REC, fout + 1)
    d.call("quant_loops_loop_tick")
    w = d.sym("blk_wraps")
    for _ in range(int(L(b)) + 20):
        d.block()
    assert d.sym("blk_wraps") == w and d.stock_wraps >= 1


@needs_toolchain
def test_no_grid_and_cd_stay_stock():
    d = Deck(const_grid(128.0))
    d.mem.write(tq.BASE_TENTHS, struct.pack(">H", 1300))       # the grid did not give the tempo
    d.records(300, 341)
    d.call("quant_loops_loop_tick")
    assert d.sym("blk_arms") == 0
    d.put_pos(300 * 7)
    for _ in range(400):
        d.block()
    assert d.stock_wraps >= 1 and d.sym("blk_calls") == 0
    # an armed loop with the feeder on the CD path: not ours
    d = Deck(const_grid(128.0))
    d.arm(20, 0, 16)
    d.mem.write(tq.RING_ALT, b"\x01")
    d.put_pos(d.sym("blk_in"))
    for _ in range(400):
        d.block()
    assert d.sym("blk_calls") == 0 and d.stock_wraps >= 1


@needs_toolchain
def test_isr_path_frames_are_small():
    p = tq._payload()
    for name in ("quant_loops_out_isr", "quant_loops_out_hook", "quant_loops_ring_jump", "quant_loops_read_pos"):
        assert p.stack["quant_loops:" + name] <= 64, (name, p.stack["quant_loops:" + name])


def test_live_block_loop_and_loop_divide(tmp_path):
    """Live: BEAT LOOP at 128 BPM, 40 s of passes, then LOOP DIVIDE - (count 1)."""
    import subprocess
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    qemu = os.environ.get("GUI_QEMU") or str(ROOT / "qemu-build-eb" / "qemu-system-sh4eb")
    if not (os.path.isfile(qemu) and tq.FLASH.is_file() and tq.BAKEN.is_file() and payloadbuild.have_toolchain()):
        pytest.skip("cdj350 qemu, flash_cdj350.bin, baken or the toolchain not available")
    flash = tmp_path / "flash.bin"
    subprocess.run([sys.executable, str(ROOT / "mods" / "patch_main.py"), "--flash", "--model", "cdj350",
                    str(tq.FLASH), str(flash), *tq.MODS], cwd=str(ROOT), check=True)
    deck = tq.live_deck(tmp_path, flash, qemu,
                        "9000:usb:1500,13000:rot+2,15000:push:200,17000:push:200,"
                        "21000:beat_loop:200,65000:loop_divide_minus:200")
    p = tq._payload()
    sym = lambda n: deck.w(payloadbuild.lookup(p.symbols, "quant_loops_blk_" + n))
    try:
        deck.wait(lambda: deck.b(tq.PLAYER_STATE + deck.b(tq.SOURCE)) == 4, 120)
        assert sym("arms") == 1
        b_in, out0 = sym("in"), sym("out0")
        assert deck.w(LOOP_IN) == b_in // 7 and deck.w(OUT_REC) == (out0 - 1) // 7
        deck.wait(lambda: sym("passes") >= 18, 120)
        assert sym("misses") == 0 and sym("realigns") == 0
        assert 984 <= sym("len_min") <= sym("len_max") <= 985
        n = sym("passes")
        assert abs(sym("len_sum") - n * 984.375) < 1.0
        # LOOP DIVIDE -: the beat module folds the feeder's source back within each beat
        deck.wait(lambda: deck.w(0x0C1AB7D8) == 1, 60)
        folded = 0
        end = time.monotonic() + 8
        while time.monotonic() < end:
            raw = deck.mem(0x0C7E4D24, 4)
            src = deck.mem(0x0C7E5034, 4)
            ptr, s = struct.unpack(">I", raw)[0], struct.unpack(">I", src)[0]
            folded += s != 0xFFFFFFFF and s < ptr
        assert folded > 20
        assert deck.b(tq.PLAYER_STATE + deck.b(tq.SOURCE)) == 4 and sym("wraps") > n
    finally:
        deck.close()
