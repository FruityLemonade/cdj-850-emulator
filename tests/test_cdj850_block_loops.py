# SPDX-License-Identifier: GPL-2.0-or-later
"""Block-precise quantized loops on the CDJ-850 MAIN 1.13 (quant_loops + the shared loop-out
dispatcher, docs/cdj850-block-loops-design.md).

* interpreter (tests/sh2a.py): the compiled payload with a small feeder simulation in Python (one
  call of c850_loop_out_isr per 84-sample block, the read pointer advanced 0x150 before the call,
  a stock wrap to the in record's ring sector when the test says so). Every whole pass is within a
  block of the exact grid length L*21/40 and the average over 1000 passes is exact at 128.00,
  127.96 and 133.33 BPM; the ring-miss fallback wraps stock for that pass; RELOOP and a memory loop
  found by the state-4 tick move the stock in-sector entry on to the in block; a roll inside a
  block loop still lands on its counted shadow.
* live (QEMU, the quant_loops grid stick): a 4-beat auto loop on Q12796 plays for 60 s; every whole
  pass within a block, the loop's phase against the grid under 2 ms at the end; against the 1 ms
  tick the emulated feeder clock itself drifts (0.016 % slow, -9.5 ms over 60 s), checked as a rate.
"""
import json
import struct
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "mods"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import payloadbuild  # noqa: E402
import test_cdj850_quant_loops as tq  # noqa: E402

FLASH = ROOT / "extract" / "cdj850" / "flash_cdj850.bin"
RING = 0x0C1E8E5C
READ_PTR, PLAY_SEC, WRITE_SEC = 0x0C7F6470, 0x0C7F645C, 0x0C7F645E
LOOP_IN, IN_SEC, OUT_REC, OUT_REQ = 0x0C7F6498, 0x0C7F64A8, 0x0C7F64B0, 0x0C7F64DC
LOOP_ACTIVE, STATE = 0x0CE6D0ED, 0x0CE6D25D + 2              # source index 2 (USB)
TICK = 0x0CE49F54

needs_toolchain = pytest.mark.skipif(not payloadbuild.have_toolchain(), reason="sh-elf toolchain not installed")


class Deck:
    """quant_loops (and optionally loop_roll) on the interpreter with a feeder simulation."""

    def __init__(self, entries, mods=("quant_loops",)):
        from sh2a import Sh2a
        from test_cdj850_hot_cues import _stub
        self.Sh2a = Sh2a
        self.p = p = payloadbuild.build(list(mods), "cdj850")
        self.g = tq.Grid(entries)
        self.mem = mem = tq._mem(p, entries, base=(entries[0][1] + 5) // 10, state=4)
        mem.add(0x0CE6CA00, bytes(0x800))                    # K0..K2, loop active, D154/D156
        mem.add(0x0C7F6400, bytes(0x400))                    # feeder words, loop records
        mem.add(TICK, bytes(4))
        log = 0x0DDFE000
        mem.add(log, bytes(0x40))
        mem.add(tq.NEXT_SECTOR, bytes(4))
        tq._stubs(mem, log)                                  # ring_sector_to_frame from the table
        tq._ring(mem, first_frame=0)                         # ring sector s holds frame s
        _stub(mem, 0x0C015B18, "rts\nnop")                   # audio_clear_loop_segments
        _stub(mem, 0x0C0DD492, "rts\nmov #0,r0")             # the key word's stock function
        # isr_loop_out_reached: wrap when the play sector's frame (the sector here) is past out
        _stub(mem, 0x0C0158D4, "cmp/hi r5,r4\nrts\nmovt r0")
        mem.write(STATE, b"\x04")
        mem.write(LOOP_ACTIVE, b"\x01")
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
        self.mem.write(PLAY_SEC, struct.pack(">H", s))

    def pos(self):
        ptr = self.mem.u32(READ_PTR) - RING
        s = ptr // 0x930
        return self.mem.u32(tq.SECTOR_TABLE + 16 * s) * 7 + (ptr - s * 0x930) // 0x150

    def records(self, in_frame, out_frame, in_slot=None):
        self.mem.put32(LOOP_IN, in_frame)
        self.mem.write(IN_SEC, struct.pack(">H", in_frame if in_slot is None else in_slot))
        self.mem.put32(OUT_REC, out_frame)
        self.mem.write(WRITE_SEC, struct.pack(">H", (in_frame + 1500) % 2400))

    def block(self):
        """One DSPDREQ: play the block, advance the pointer, the loop-out test (a stock wrap goes
        to the in record's ring sector start)."""
        ptr = self.mem.u32(READ_PTR) + 0x150
        s = (ptr - RING) // 0x930
        self.mem.put32(READ_PTR, ptr)
        self.mem.write(PLAY_SEC, struct.pack(">H", s))
        self.mem.put32(TICK, self.mem.u32(TICK) + 2)          # about 1.9 ms
        if self.call("c850_loop_out_isr", s, self.mem.u32(OUT_REC), 0) & 0xFF:
            s = struct.unpack(">H", self.mem.read(IN_SEC, 2))[0]
            self.mem.put32(READ_PTR, RING + s * 0x930)
            self.mem.write(PLAY_SEC, struct.pack(">H", s))
            self.stock_wraps += 1

    def arm(self, i, s, q):
        """An auto loop's block loop at grid point (i, s), q/16 beats (quant_loops_set_block_loop)."""
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
    return tq.const_grid(bpm, 350, n)


@needs_toolchain
@pytest.mark.parametrize("bpm,q", [(128.0, 16), (127.96, 16), (127.96, 4), (133.33, 8)])
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
                                       ("120->133.33", 40, 64), ("120->133.33", 30, 64)])
def test_average_over_1000_passes_is_exact(label, i, q):
    """1000 wraps (two feeder blocks per pass: the last one before the out and the out): the pass
    lengths (out block - in block) are floor or ceil of L and their sum is L * 1000 within a block.
    QVAR from beat 30: 4 beats across the tempo change at beat 32 (ms form)."""
    entries = tq.GRIDS[label]
    d = Deck(entries)
    b = d.arm(i, 0, q)
    g = d.g
    if label == "120->133.33" and i == 40:
        assert b[2:] == (q * 7875000, 13333)                  # constant 133.33 over the loop
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
    # the wraps follow the exact grid from the in: sum within half a block of 1000 * L
    first = (b[0] * 40 + b[1] - 20) / 40                      # exact in, blocks
    assert abs(b[0] + sum(lens) - (first + 1000 * exact)) <= 0.5 + 1e-9, (sum(lens), exact)
    if label.startswith("128"):
        assert exact == 64 * 7875000 / (40 * 12800) == 984.375 * q / 64
    bpm = entries[i][1] / 100
    if b[3] != 1:
        assert abs(exact - q / 16 * 60 / bpm * 525) < 1e-6


@needs_toolchain
def test_ring_miss_wraps_stock_for_that_pass():
    """The in's sector is no longer where the ring should hold it: that pass wraps stock (frame
    precise) to the in record's sector, the entry moves on to the in block, and the next wrap is
    block precise again."""
    d = Deck(const_grid(128.0))
    b = d.arm(20, 0, 16)
    fin = b[0] // 7
    x = 2000                                                  # a second copy of the audio from fin
    for k in range(400):
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
def test_memory_loop_found_by_the_tick(legacy):
    """A loop no writer set (a memory or hot-cue loop from the stick): the state-4 tick finds its
    grid point and length from the stock records, the stock entry moves on to the in block.
    legacy: frames as quant_loops stored them before block loops (rounded in and out)."""
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
    d.put_pos(fin * 7)                                         # CALL, PLAY: the in sector's start
    d.block()
    if b[0] != fin * 7 + 1:
        assert d.sym("blk_realigns") == 1 and d.pos() == b[0] + 1
    d.run_passes(3)
    assert d.stock_wraps == 0
    assert int(L(b)) <= d.sym("blk_len_min") <= d.sym("blk_len_max") <= int(L(b)) + 1
    # quantize off: stock (the key filter disarms; the tick does not arm)
    d.mem.write(tq.SETTINGS + 0x22, b"\x53")                 # BEAT SELECT + LOOP IN: quantize off
    d.mem.put32(OUT_REC, fout + 1)
    d.call("quant_loops_loop_tick")
    w = d.sym("blk_wraps")
    for _ in range(int(L(b)) + 20):
        d.block()
    assert d.sym("blk_wraps") == w and d.stock_wraps >= 1


@needs_toolchain
def test_no_grid_stays_stock():
    d = Deck(const_grid(128.0))
    d.mem.write(tq.BASE_TENTHS, struct.pack(">H", 1300))       # the grid did not give the tempo
    d.records(300, 341)
    d.call("quant_loops_loop_tick")
    assert d.sym("blk_arms") == 0
    d.put_pos(300 * 7)
    for _ in range(400):
        d.block()
    assert d.stock_wraps >= 1 and d.sym("blk_calls") == 0


@needs_toolchain
def test_a_roll_inside_a_block_loop_lands_on_its_shadow():
    """loop_roll first, then the block loop: while the roll counts, the block loop still wraps
    to the block; the landing is the counted shadow block, and from then on loop_roll owns the
    test until playertask has left the loop."""
    d = Deck(const_grid(128.0), mods=("loop_roll", "quant_loops"))
    b = d.arm(20, 0, 16)
    d.put_pos(b[0] - 2)

    def keys(k0=0, k1=0):
        d.mem.write(0x0CE6CA08, struct.pack(">HH", k0, k1))
        d.call("c850_ui_keys")

    def roll(name):
        return d.mem.u32(payloadbuild.lookup(d.p.symbols, "loop_roll_" + name))

    keys(0x0040)
    keys(0x0840)                                               # BEAT SELECT + LOOP OUT: roll mode
    keys()
    d.mem.write(STATE, b"\x03")
    keys(0, 0x0040)                                            # BEAT 4 down: a roll starts
    d.mem.write(STATE, b"\x04")
    assert roll("rolls") == 1
    d.run_passes(3)
    keys()                                                     # released
    n = 0
    while roll("jumps") == 0:
        d.block()
        n += 1
        assert n < 3000
    assert d.sym("blk_wraps") >= 3
    counted = roll("last_target") - roll("start_pos")
    assert counted == roll("isr_calls") - 1
    assert d.pos() == roll("last_target")
    w = d.sym("blk_wraps")
    for _ in range(int(L(b)) + 10):                            # loop_roll holds the test (JUMPED)
        d.block()
    assert d.sym("blk_wraps") == w


@needs_toolchain
def test_isr_frames_and_no_fpu():
    import test_cdj850_loop_roll as tr
    p = payloadbuild.build(["quant_loops", "loop_roll"], "cdj850")
    for name in ("quant_loops:quant_loops_out_hook", "common:c850_loop_out_isr", "common:c850_ring_jump",
                 "common:c850_read_pos", "loop_roll:loop_roll_out_hook"):
        assert p.stack[name] <= 64, name
    assert max(p.stack.values()) <= 64
    assert not tr.fpu_mnemonics(ROOT / "mods" / "quant_loops" / "cdj850" / "payload" / "quant_loops.c")


# ---- live ----

LIVE_SYMS = ("blk_arms", "blk_derived", "blk_calls", "blk_wraps", "blk_realigns", "blk_misses",
             "blk_passes", "blk_len_last", "blk_len_min", "blk_len_max", "blk_len_sum", "blk_blocks",
             "blk_tick0", "blk_tick", "blk_pos", "blk_in", "blk_a0", "blk_out0", "blk_num", "blk_den",
             "blk_next", "auto_count")


def drive(stick, flash, syms, seconds=60.0):
    sys.path.insert(0, str(ROOT / "scripts" / "debug"))
    import cdj850_trace as tr
    res = {}

    def ev(d):
        d.q("stop")
        v = {n: d.rw(payloadbuild.lookup(syms, "quant_loops_" + n)) for n in LIVE_SYMS}
        v["tick_now"] = d.rw(TICK)
        v["state"] = d.rb(tq.STATE_USB)
        v["in_rec"], v["out_rec"], v["out_req"] = d.rw(LOOP_IN), d.rw(OUT_REC), d.rw(OUT_REQ)
        d.q("cont")
        return v

    with tr.Deck(stick, flash=flash) as d:
        d.wait_line("mode=0x0a", "[Q", timeout=300)
        for _ in range(4):                                    # on to Q12796 (127.96 BPM)
            time.sleep(1.5)
            if d.rh(tq.GRID_CTX + 0x7C + 2) == 12796:
                break
            fid = d.rw(tq.GRID_CTX + 4)
            d.key("next")
            end = time.monotonic() + 180
            while d.rw(tq.GRID_CTX + 4) == fid:
                assert time.monotonic() < end, "no next grid"
                time.sleep(0.5)
        res["bpm100"] = d.rh(tq.GRID_CTX + 0x7C + 2)
        for _ in range(6):
            if d.rb(tq.STATE_USB) == 3:
                break
            d.key("play")
            time.sleep(2.5)
        f = d.rw(tq.PLAY_FRAME)
        while d.rw(tq.PLAY_FRAME) < f + 150:
            time.sleep(0.05)
        for _ in range(3):                                    # BEAT 4 until the deck loops
            d.key("beat_4")
            time.sleep(1.5)
            if d.rb(tq.STATE_USB) == 4 and d.rb(LOOP_ACTIVE) == 1:
                break
        res["start"] = ev(d)
        t0 = d.rw(TICK)                                       # the loop plays 60 s of guest time
        while d.rw(TICK) - t0 < seconds * 1000:
            time.sleep(0.5)
        res["end"] = ev(d)
    return res


@needs_toolchain
@pytest.mark.skipif(not FLASH.is_file(), reason="extract/cdj850/flash_cdj850.bin is not present")
def test_live_block_loop_60s(tmp_path):
    import patch_main
    stick = tq.build_stick(tmp_path)
    mods = tq.MODS
    flash = tmp_path / "flash.bin"
    flash.write_bytes(patch_main.patch_flash(FLASH.read_bytes(), mods, "cdj850"))
    syms = payloadbuild.build(["quant_loops"], "cdj850").symbols
    res = drive(stick, flash, syms)
    print("block loops live:", json.dumps({k: v for k, v in res.items() if k != "grid"}))
    assert res["bpm100"] == 12796
    e = res["end"]
    assert e["state"] == 4 and e["blk_misses"] == 0 and e["blk_realigns"] == 0, e
    exact = e["blk_num"] / (40 * e["blk_den"])
    assert e["blk_den"] == 12796 and abs(exact - 4 * 60 / 127.96 * 525) < 1e-6
    n = e["blk_passes"]
    assert n >= 25, e                                         # 60 s of 1.875 s passes
    # every whole pass within a block of the grid length
    assert int(exact) <= e["blk_len_min"] <= e["blk_len_max"] <= int(exact) + 1, e
    assert abs(e["blk_len_sum"] - n * exact) < 1.0, e
    # phase against the 1 ms tick: the blocks played since the first wrap, as time, against the
    # tick over the same span (both read in the ISR at a block)
    ms_blocks = e["blk_blocks"] * 84000 / 44100
    ms_tick = e["blk_tick"] - e["blk_tick0"]
    # where the loop is against where the grid says it should be after that time
    grid_pos = e["blk_blocks"] - (e["blk_wraps"] - 1) * exact   # blocks into the pass, from the in
    actual = e["blk_pos"] - e["blk_in"]
    res["phase_ms_tick"] = ms_blocks - ms_tick
    res["phase_blocks_grid"] = actual - grid_pos
    print("phase vs tick %.2f ms, vs grid %.2f blocks" % (res["phase_ms_tick"], res["phase_blocks_grid"]))
    # The loop's phase: where it is against where the grid puts it after the blocks played, in ms.
    # Emulator run (60 s, Q12796): 0.16 block (0.3 ms) after 31 passes.
    assert abs(actual - grid_pos) * 84000 / 44100 < 2.0, (actual, grid_pos)
    # Against the 1 ms tick the emulated feeder itself runs slow: 30781 blocks in 58640 ms
    # (30786 due), -9.5 ms over the run, the same in plain play. That is the feeder clock against
    # the tick, not the loop: bounded as a rate, reported in ms.
    assert abs(ms_blocks - ms_tick) / ms_tick < 0.0005, (ms_blocks, ms_tick)


@needs_toolchain
@pytest.mark.parametrize("label,in_beat,beats,frac", [("128 @0.35", 20, 1, 0), ("128 @0.35", 20, 2, 1),
                                                       ("120->133.33", 30, 1, 0), ("120->133.33", 30, 2, 0)])
def test_beat_resize_makes_the_block_loop(label, in_beat, beats, frac):
    """BEAT n inside a loop: the stock resize sends in + (CA90 - in) / (8/n x (15 frac + 1)) with
    the resize flag; the mod sends the block loop of n beats (n/16 in fraction mode) instead and
    arms it. QVAR from beat 30: CA90 (8 beats) spans the tempo change at beat 32, so the stock
    frame is not the grid length of the resized loop."""
    p = payloadbuild.build(["quant_loops"], "cdj850")
    entries = tq.GRIDS[label]
    g = tq.Grid(entries)
    mem, log = tq._entry_mem(p, entries, state=4)
    mem.add(0x0C7F64C0, bytes(0x40))                         # out request 0x0C7F64DC
    mem.add(0x0C7F6440, bytes(0x20))                         # play sector (track slot)
    mem.add(0x0C7F6470, bytes(0x20))                         # PLAY_FRAME 0x0C7F647C
    mem.add(0x0C7F66F0, bytes(0x10))                         # resize count 0x0C7F66F8
    mem.add(0x0CE6D0E0, bytes(0x20))                         # LOOP_ACTIVE 0x0CE6D0ED
    mem.write(0x0CE6D0ED, b"\x01")
    mem.add(TICK, bytes(4))
    b = g.point_loop(in_beat, 0, 64)
    fin = b[0] // 7
    ca90 = g.out_frame(g.point_loop(in_beat, 0, 128))
    mem.put32(LOOP_IN, fin)
    mem.put32(tq.CA90, ca90)
    stock = fin + (ca90 - fin) // ((8 // beats) * (frac * 15 + 1))
    q = beats if frac else beats * 16
    want = g.q_out(fin, q)
    # FUN_0C00A4CC(out, 0): writes the out request, 1 = the send follows
    from test_cdj850_hot_cues import _stub
    _stub(mem, 0x0C00A4CC, "mov.l 1f,r0\nmov.l r4,@r0\nrts\nmov #1,r0\n.align 2\n1: .long 0x0C7F64DC")
    mem.put32(tq.D1C0, stock)
    assert tq._call(p, mem, "quant_loops_resize_req", stock, 0) == 1
    assert mem.u32(OUT_REQ) == want and mem.u32(tq.D1C0) == want
    arms = mem.u32(payloadbuild.lookup(p.symbols, "quant_loops_blk_arms"))
    tq._call(p, mem, "quant_loops_auto_send", 6, 0, mem.u32(tq.D1C0), 1, stack=(0, 1, 0, 2))
    assert tq._log(mem, log)[:4] == [6, 0, want, 1]
    assert mem.u32(payloadbuild.lookup(p.symbols, "quant_loops_blk_arms")) == arms   # armed once
    # the send alone (a resize whose request came another way) arms it too
    mem.put32(LOOP_IN, fin)
    tq._call(p, mem, "quant_loops_set_block_loop", 0, 0, 0, 1)              # some other loop
    tq._call(p, mem, "quant_loops_auto_send", 6, 0, stock, 1, stack=(0, 1, 0, 2))
    assert tq._log(mem, log)[:4] == [6, 0, want, 1]
    assert mem.u32(OUT_REQ) == want and mem.u32(tq.D1C0) == want
    sym = lambda n: mem.u32(payloadbuild.lookup(p.symbols, "quant_loops_" + n))
    assert (sym("blk_in"), sym("blk_num"), sym("blk_den")) == (b[0],) + g.block_len(in_beat, 0, q)
    if label.startswith("120"):
        assert want != stock                                  # the grid length, not CA90 / 8
