# SPDX-License-Identifier: GPL-2.0-or-later
"""quant_loops drift correction on the CDJ-850 MAIN 1.13 (payload quant_loops_drift_tick).

Loop points are whole frames (1/75 s), so a loop of q/16 grid beats plays n = out - in frames per
pass instead of the exact L = q * 28125 / BPMx100: every wrap jumps back n instead of L and the
track drifts against the grid by n - L per pass. The tick moves the out of each next pass to
in + round(L - e), e = sum(n) - passes * L, so the total stays within half a frame.

* the compiled payload on the SH-2A interpreter (tests/sh2a.py), driven by a simulated feeder
  that plays the loop tick by tick and wraps at the out in effect, against a Python reference:
  128.00 / 127.96 / 174.00 BPM grids, whole-beat and 1/2-beat (fraction mode) loops, a tempo
  change inside the loop, an in off the grid; the stock resize call (auto_beat_loop_set) is a
  stub that does what its state-4 branch does to the requested out;
* what it leaves alone: no grid, loops under 12 frames, the run-time switch off, other states;
* resets: a resize by someone else, a jump that is no wrap (jog), a new loop;
* the bytes the resize call writes besides the loop records are put back (beat LED, 0x0CE6D155/6,
  CA90); the new out is never behind the playhead;
* the shared tick (c850.h): with loop_resize and quant_loops both hooks run, stock last.
"""
import struct
import subprocess
import sys
import tempfile
import time
from fractions import Fraction
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "mods"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import payloadbuild  # noqa: E402
from sh2a import Memory, Sh2a  # noqa: E402
from test_cdj850_loop_resize import Grid, const_grid, var_grid  # noqa: E402

needs_toolchain = pytest.mark.skipif(not payloadbuild.have_toolchain(), reason="sh-elf toolchain not installed")

GRID_CTX = 0x0C9D24FC
BASE_TENTHS = 0x0C9D23EC
USB_MODE = 0x0CE6D25A
SOURCE = 0x0CE6D252
PLAYER_STATE = 0x0CE6D25D       # + source
LOOP_ACTIVE = 0x0CE6D0ED
FRAC = 0x0CE6D154
D155, D156 = 0x0CE6D155, 0x0CE6D156
BEAT_LOCK = 0x0CE6D26A
CA90 = 0x0CE6CA90
BEATS = 0x0C1E1088
PLAY_FRAME = 0x0C7F647C
LOOP_IN = 0x0C7F6498
OUT_REC = 0x0C7F64B0
OUT_REQ = 0x0C7F64DC
AUTO_BEAT_LOOP = 0x0C0E570A
STATE4_KEYS = 0x0C0E0F34
LOG = 0x0DDFE000
MIN_FRAMES = 12


# ---- Python reference of quant_loops_drift_length / _next ----

def exact_length(g, loop_in, out):
    """(q, L as a Fraction of frames, num, den) or None, as quant_loops_drift_length."""
    if out <= loop_in or out - loop_in < MIN_FRAMES:
        return None
    a = g.anchor(loop_in)
    if a is None:
        return None
    q = g.q_of(loop_in, out)
    if not q:
        return None
    i, s = a
    p0 = s if s < 16 else 0
    bpm = g.e[i][1]
    last = i + (p0 + q + 15) // 16
    const = all(g.e[j][1] == bpm for j in range(i + 1, min(last, g.n - 1) + 1))
    if bpm and const:
        num, den = q * 28125, bpm
    else:
        num, den = (g.grid_ms(i, p0 + q) - g.grid_ms(i, p0)) * 75, 1000
    return q, Fraction(num, den), num, den


def next_len(num, den, err):
    return (num - err + den // 2) // den


# ---- interpreter ----

def _asm(text):
    """Assemble position-independent SH-2A code (PC-relative literals only)."""
    pre = payloadbuild.tool_prefix()
    with tempfile.TemporaryDirectory() as d:
        Path(d, "s.s").write_text("\t.text\n\t.align 2\n" + text + "\n")
        subprocess.run([pre + "as", "-isa=sh2a", "-big", "-o", d + "/s.o", d + "/s.s"], check=True)
        subprocess.run([pre + "objcopy", "-O", "binary", "-j", ".text", d + "/s.o", d + "/s.bin"], check=True)
        return Path(d, "s.bin").read_bytes()


# auto_beat_loop_set(8) in a USB loop: FUN_0C0E5668 makes out = in + (CA90 - in) / (frac * 15 + 1),
# FUN_0C00A4CC writes it to the requested out at once; the call also writes the beat LED byte and
# clears 0x0CE6D156 (and the stub scribbles on 0x0CE6D155 too). Each call bumps the log word.
AUTO_BEAT_LOOP_STUB = """
    nop                     ! loaded at 0x0C0E5708: the entry 0x0C0E570A is not 4-aligned
    mov.l   L_ca90, r1
    mov.l   @r1, r0
    mov.l   L_in, r1
    mov.l   @r1, r2
    mov.l   L_frac, r1
    mov.b   @r1, r3
    tst     r3, r3
    bt      1f
    sub     r2, r0
    shlr2   r0
    shlr2   r0
    add     r2, r0
1:  mov.l   L_req, r1
    mov.l   r0, @r1
    mov.l   L_beats, r1
    mov.b   r4, @r1
    mov.l   L_d155, r1
    mov     #0x5A, r3
    mov.b   r3, @r1
    mov     #0, r3
    mov.b   r3, @(1, r1)
    mov.l   L_log, r1
    mov.l   @r1, r3
    add     #1, r3
    mov.l   r3, @r1
    rts
    mov     #1, r0
    .align 2
L_ca90:  .long 0x0CE6CA90
L_in:    .long 0x0C7F6498
L_frac:  .long 0x0CE6D154
L_req:   .long 0x0C7F64DC
L_beats: .long 0x0C1E1088
L_d155:  .long 0x0CE6D155
L_log:   .long 0x0DDFE000
"""
# keys_state4_looping: log 3 at LOG + 4 (the stock handler ran)
STATE4_STUB = """
    mov.l   L_l4, r1
    mov     #3, r0
    rts
    mov.l   r0, @(4, r1)
    .align 2
L_l4:    .long 0x0DDFE000
"""


def _payload(mods=("quant_loops",)):
    return payloadbuild.build(list(mods), "cdj850")


_STUBS = {}


def _mem(p, entries, base=None, usb=1, state=4, active=1, frac=0):
    if not _STUBS:
        _STUBS["abl"], _STUBS["s4"] = _asm(AUTO_BEAT_LOOP_STUB), _asm(STATE4_STUB)
    mem = Memory()
    mem.add(0x0DE00020, p.blob)
    mem.add(0x0DDFE000, bytes(0x2000))                    # log, stack
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
    mem.add(FRAC, bytes([frac, 0x11, 0x22]))              # 0x0CE6D154..56
    mem.add(BEAT_LOCK, bytes([0]))
    mem.add(BEATS, bytes([4, 0, 0, 0]))
    mem.add(CA90 - 0x10, bytes(0x20))
    mem.add(0x0C7F6470, bytes(0xA0))                      # play frame, loop records
    mem.add(AUTO_BEAT_LOOP - 2, _STUBS["abl"])
    mem.add(STATE4_KEYS, _STUBS["s4"])
    return mem


def _tick(p, mem):
    mem.write(LOG + 4, bytes(4))
    cpu = Sh2a(mem)
    cpu.r[15] = 0x0DDFFF00
    cpu.run(payloadbuild.lookup(p.symbols, "c850_loop_tick"), Sh2a.RET)
    assert mem.u32(LOG + 4) == 3                          # the stock handler always ran


def _sym(p, mem, name):
    v = mem.u32(payloadbuild.lookup(p.symbols, "quant_loops_drift_" + name))
    return v - (1 << 32) if name == "err" and v >> 31 else v


def _set_sym(p, mem, name, value):
    mem.put32(payloadbuild.lookup(p.symbols, name), value)


def play(p, mem, loop_in, out, passes, step, start=None, on_tick=None):
    """The simulated feeder: a loop [loop_in, out) with the out in the record (a new loop), the
    playhead from `start` advancing `step` frames per player cycle and wrapping at the out in
    effect (the request once the payload moved it). Returns the out of every wrap."""
    mem.put32(LOOP_IN, loop_in)
    mem.put32(OUT_REC, out)
    mem.put32(OUT_REQ, 0xFFFFFFFF if out != 0xFFFFFFFF else 0)  # stale request of an earlier loop
    pos = loop_in + 1 if start is None else start
    cur, req = out, mem.u32(OUT_REQ)
    wraps = []
    while len(wraps) < passes:
        mem.put32(PLAY_FRAME, pos)
        _tick(p, mem)
        if on_tick:
            on_tick(pos, cur)
        if mem.u32(OUT_REQ) != req:                       # a resize: the feeder loops to it
            req = mem.u32(OUT_REQ)
            assert req > pos, (req, pos)                  # never behind the playhead
            cur = req
        pos += step
        if pos >= cur:
            wraps.append(cur)
            pos = loop_in + (pos - cur)
    mem.put32(PLAY_FRAME, pos)                            # the cycle that sees the last wrap
    _tick(p, mem)
    return wraps


CASES = [
    # (grid, in beat, q, frac mode, step)
    ("128", 4, 64, 0, 3),                # 4 beats: 140.625 frames
    ("128", 9, 16, 0, 2),                # 1 beat: 35.156
    ("128", 6, 128, 0, 5),               # 8 beats: 281.25
    ("128", 4, 8, 1, 1),                 # 1/2 beat in fraction mode: 17.578
    ("127.96", 5, 64, 0, 3),             # 140.669
    ("174", 8, 64, 0, 3),                # 103.448
    ("174", 8, 16, 0, 2),                # 25.862
]
GRID_OF = {"128": const_grid(128.0, 350, 400), "127.96": const_grid(127.96, 350, 400),
           "174": const_grid(174.0, 120, 600)}


@needs_toolchain
@pytest.mark.parametrize("name,beat,q,frac,step", CASES)
def test_drift_stays_within_half_a_frame(name, beat, q, frac, step):
    p = _payload()
    entries = GRID_OF[name]
    g = Grid(entries)
    mem = _mem(p, entries, frac=frac)
    loop_in = g.f(beat)
    out = g.out(loop_in, q)                               # the grid out quant_loops gives the loop
    ref = exact_length(g, loop_in, out)
    assert ref and ref[0] == q
    _, L, num, den = ref
    wraps = play(p, mem, loop_in, out, 40, step)
    assert _sym(p, mem, "q") == q and (_sym(p, mem, "num"), _sym(p, mem, "den")) == (num, den)
    assert _sym(p, mem, "passes") == 40 and _sym(p, mem, "sum") == sum(w - loop_in for w in wraps)
    err = Fraction(0)
    e = 0
    for k, w in enumerate(wraps):
        n = w - loop_in
        if k == 0:
            assert w == out                               # the first pass keeps its out
        else:
            assert n == next_len(num, den, e), k          # the reference's next length
        e += n * den - num
        err += n - L
        if k >= 1:
            assert abs(err) <= Fraction(1, 2), (k, float(err))
        assert abs(err) < 1
    assert _sym(p, mem, "err") == e
    lens = {w - loop_in for w in wraps}
    if L.denominator > 1:
        assert lens <= {L.numerator // L.denominator, L.numerator // L.denominator + 1}, lens
        assert len(lens) == 2                             # it alternates
    # the stock resize call's other writes are put back
    assert mem.read(BEATS, 1)[0] == 4 and mem.read(D155, 2) == bytes([0x11, 0x22])
    assert mem.u32(CA90) == 0
    assert _sym(p, mem, "moves") == mem.u32(LOG) and _sym(p, mem, "refused") == 0
    # without the correction the same loop drifts (n - L) per pass
    assert abs(40 * ((out - loop_in) - L)) > 1 or L.denominator == 1


@needs_toolchain
def test_tempo_change_inside_the_loop_uses_the_grid_duration():
    p = _payload()
    entries = var_grid()                                  # 120 for 32 beats, then 133.33
    g = Grid(entries)
    mem = _mem(p, entries)
    loop_in = g.f(30)                                     # beats 30..34: 2 at 120, 2 at 133.33
    out = g.out(loop_in, 64)
    q, L, num, den = exact_length(g, loop_in, out)
    assert (q, den) == (64, 1000) and num == (entries[34][2] - entries[30][2]) * 75
    wraps = play(p, mem, loop_in, out, 20, 3)
    err = sum(Fraction(w - loop_in) - L for w in wraps)
    assert abs(err) <= Fraction(1, 2)


@needs_toolchain
def test_in_off_the_grid_counts_from_the_in():
    p = _payload()
    entries = GRID_OF["128"]
    g = Grid(entries)
    mem = _mem(p, entries)
    loop_in = g.f(4) + 7                                  # stock auto loop: in = the playhead
    out = g.out(loop_in, 64)
    q, L, num, den = exact_length(g, loop_in, out)
    assert (q, num, den) == (64, 64 * 28125, 12800)
    wraps = play(p, mem, loop_in, out, 20, 3)
    assert abs(sum(Fraction(w - loop_in) - L for w in wraps)) <= Fraction(1, 2)


@needs_toolchain
@pytest.mark.parametrize("kind", ["nogrid", "short", "off", "cd", "state6", "inactive", "locked"])
def test_left_alone(kind):
    p = _payload()
    entries = [] if kind == "nogrid" else GRID_OF["128"]
    mem = _mem(p, entries, usb=0 if kind == "cd" else 1, state=6 if kind == "state6" else 4,
               active=0 if kind == "inactive" else 1, base=1280)
    if kind == "off":
        _set_sym(p, mem, "quant_loops_drift_on", 0)
    if kind == "locked":
        mem.write(BEAT_LOCK, bytes([0xFF]))
    g = Grid(GRID_OF["128"])
    loop_in = g.f(4)
    q = 4 if kind == "short" else 64                      # 1/4 beat = 8.8 frames: under 12
    out = g.out(loop_in, q)
    play(p, mem, loop_in, out, 12, 1 if kind == "short" else 3)
    assert mem.u32(LOG) == 0                              # the resize was never called
    assert _sym(p, mem, "moves") == 0
    if kind == "locked":
        assert _sym(p, mem, "refused") > 0


@needs_toolchain
def test_resets_on_a_resize_a_jog_jump_and_a_new_loop():
    p = _payload()
    entries = GRID_OF["128"]
    g = Grid(entries)
    mem = _mem(p, entries)
    loop_in = g.f(4)
    out = g.out(loop_in, 64)
    play(p, mem, loop_in, out, 6, 3)
    loops, lost = _sym(p, mem, "loops"), _sym(p, mem, "lost")
    assert _sym(p, mem, "passes") == 6
    # someone else resizes (BEAT 2): the requested out changes -> a new 2-beat loop is tracked
    mem.put32(PLAY_FRAME, loop_in + 5)
    _tick(p, mem)
    mem.put32(OUT_REQ, g.out(loop_in, 32))
    mem.put32(PLAY_FRAME, loop_in + 8)
    _tick(p, mem)
    assert _sym(p, mem, "loops") == loops + 1 and _sym(p, mem, "q") == 32 and _sym(p, mem, "passes") == 0
    # a jump back that is no wrap (jog in the loop): tracking starts over
    mem.put32(PLAY_FRAME, loop_in + 30)
    _tick(p, mem)
    mem.put32(PLAY_FRAME, loop_in + 12)
    _tick(p, mem)
    assert _sym(p, mem, "lost") == lost + 1 and _sym(p, mem, "passes") == 0
    # a new loop elsewhere (the record changes)
    loops = _sym(p, mem, "loops")
    play(p, mem, g.f(20), g.out(g.f(20), 128), 3, 5)
    assert _sym(p, mem, "loops") == loops + 1 and _sym(p, mem, "q") == 128 and _sym(p, mem, "passes") == 3


@needs_toolchain
def test_shared_tick_runs_both_hooks_then_stock():
    p = _payload(("loop_resize", "quant_loops"))
    for sym in ("c850_loop_tick", "loop_resize_state4", "quant_loops_drift_tick"):
        assert payloadbuild.lookup(p.symbols, sym)
    entries = GRID_OF["128"]
    g = Grid(entries)
    mem = _mem(p, entries)
    loop_in = g.f(4)
    out = g.out(loop_in, 64)
    wraps = play(p, mem, loop_in, out, 10, 3)
    assert mem.u32(payloadbuild.lookup(p.symbols, "loop_resize_ticks")) == _sym(p, mem, "ticks") > 0
    L = exact_length(g, loop_in, out)[1]
    assert abs(sum(Fraction(w - loop_in) - L for w in wraps)) <= Fraction(1, 2)
    # loop_resize alone: its tick, no drift hook (the weak no-op)
    p = _payload(("loop_resize",))
    mem = _mem(p, entries)
    play(p, mem, loop_in, out, 3, 3)
    assert mem.u32(LOG) == 0 and mem.u32(payloadbuild.lookup(p.symbols, "loop_resize_ticks")) > 0


@needs_toolchain
def test_drift_code_has_no_fpu_and_small_frames():
    p = _payload()
    for name in ("quant_loops_drift_tick", "quant_loops_drift_length", "quant_loops_drift_next",
                 "quant_loops_grid_ms", "c850_loop_tick"):
        at = payloadbuild.lookup(p.symbols, name) - 0x0DE00020
        words = struct.unpack(">8H", p.blob[at:at + 16])
        assert not [w for w in words if w >> 12 == 0xF], name
    assert max(p.stack.values()) <= 64


# ---- live (QEMU + a baken stick with rekordbox grids) ----

FLASH = ROOT / "extract" / "cdj850" / "flash_cdj850.bin"
QL_MODS = ["quant_loops", "quant_loops_auto_send", "quant_loops_auto_send2", "quant_loops_auto_keep",
           "quant_loops_loop_in", "quant_loops_loop_out", "quant_loops_loop_out2", "quant_loops_drift"]
LR_MODS = ["loop_resize", "loop_resize_tick", "loop_resize_memory"]
STATE_USB = 0x0CE6D25F
LED_B7 = 0x0C9D0AEB
DSYMS = ("ticks", "loops", "passes", "sum", "err", "num", "den", "q", "moves", "refused", "lost")


def live_drift(stick, flash, syms, passes=16, after_halving=8):
    """Boot, and on each of the stick's three tracks (in the order the deck loads them): BEAT 4 (an auto loop on the grid), follow `passes` wraps,
    CALL < (loop_resize halves it to 2 beats), follow `after_halving` more, EXIT. Returns
    {bpm100: {...}} with the payload's evidence and what the frames showed."""
    sys.path.insert(0, str(ROOT / "scripts" / "debug"))
    import cdj850_trace as tr
    res = {}

    def sym(d, name):
        v = d.rw(payloadbuild.lookup(syms, "quant_loops_drift_" + name))
        return v - (1 << 32) if name == "err" and v >> 31 else v

    def snap(d):
        d.q("stop")
        v = {s: sym(d, s) for s in DSYMS}
        v.update({"in": d.rw(LOOP_IN), "rec": d.rw(OUT_REC), "req": d.rw(OUT_REQ), "frame": d.rw(PLAY_FRAME),
                  "state": d.rb(STATE_USB), "beats": d.rb(BEATS)})
        d.q("cont")
        return v

    def follow(d, until_passes, timeout=240):
        """Poll the frames and the out in effect until the payload counted `until_passes`
        wraps of the loop it tracks: every wrap (frame went back), the outs seen, the beat byte."""
        loop_in = d.rw(LOOP_IN)
        fr, outs, beats, wraps = [d.rw(PLAY_FRAME)], set(), set(), 0
        end = time.monotonic() + timeout
        while sym(d, "passes") < until_passes:
            assert time.monotonic() < end, ("no progress", sym(d, "passes"), fr[-5:])
            time.sleep(0.02)
            f = d.rw(PLAY_FRAME)
            if f < fr[-1]:
                wraps += 1
            fr.append(f)
            outs.add((d.rw(OUT_REC), d.rw(OUT_REQ)))
            beats.add(d.rb(BEATS))
        return {"in": loop_in, "min": min(fr), "max": max(fr), "wraps": wraps, "outs": sorted(outs),
                "beats": sorted(beats), "n": len(fr)}

    def ensure_playing(d):
        for _ in range(6):
            st = d.rb(STATE_USB)
            if st == 3:
                return
            d.key("reloop" if st == 4 else "play")
            time.sleep(2.5)
        assert d.rb(STATE_USB) == 3

    with tr.Deck(stick, flash=flash) as d:
        d.wait_line("mode=0x0a", "[Q", timeout=300)
        for k in range(3):
            time.sleep(1.5)
            ensure_playing(d)
            raw = d.dump(GRID_CTX + 0x7C, 8 * 200)
            n = d.rw(GRID_CTX + 0x74)
            entries = [struct.unpack_from(">HHI", raw, 8 * i) for i in range(min(n, 200))]
            t = res[entries[0][1]] = {"grid": {"count": n, "entries": entries}}
            end = time.monotonic() + 90
            while d.rw(PLAY_FRAME) < 400:
                assert time.monotonic() < end
                time.sleep(0.05)
            for _ in range(3):
                d.key("beat_4")
                time.sleep(1.2)
                if d.rb(STATE_USB) == 4:
                    break
            t0 = snap(d)
            t["start"] = t0
            t["follow"] = follow(d, passes)
            t["loop4"] = snap(d)
            d.key("call_prev")                              # loop_resize: half (2 beats)
            time.sleep(0.5)
            t["halved_at"] = snap(d)
            t["follow2"] = follow(d, after_halving)
            t["loop2"] = snap(d)
            d.key("reloop")                                 # EXIT
            time.sleep(1.5)
            t["exit"] = snap(d)
            if k < 2:
                fid = d.rw(GRID_CTX + 4)
                d.key("next")
                end = time.monotonic() + 180
                while d.rw(GRID_CTX + 4) == fid:
                    assert time.monotonic() < end, "no next grid"
                    time.sleep(0.5)
    return res


def _check_loop(v, f, q, bpm100, start_out=None):
    num, den = q * 28125, bpm100
    assert (v["q"], v["num"], v["den"]) == (q, num, den), v
    assert v["lost"] == 0 and v["refused"] == 0, v
    assert v["err"] == v["sum"] * den - v["passes"] * num, v      # the payload's books balance
    L = Fraction(num, den)
    assert abs(Fraction(v["err"], den)) <= Fraction(1, 2), v     # within half a frame of the grid
    assert abs(Fraction(v["sum"]) - v["passes"] * L) <= Fraction(1, 2), v
    lo = L.numerator // L.denominator
    assert f["min"] >= f["in"] and f["max"] <= f["in"] + lo + 1, f   # in its points (the frame reads the out at a wrap)
    assert f["wraps"] >= v["passes"] - 1, (f, v)                  # the frames show the wraps it counted
    assert f["beats"] == [4] or q != 64, f                        # the BEAT 4 LED byte is left alone


@needs_toolchain
@pytest.mark.skipif(not FLASH.is_file(), reason="extract/cdj850/flash_cdj850.bin is not present")
def test_live_drift_correction(tmp_path):
    import json
    import os
    import patch_main
    from test_cdj850_quant_loops import build_stick
    if os.name == "nt":
        pytest.skip("QMP socket descriptor inheritance requires POSIX")
    stick = build_stick(tmp_path)
    flash = tmp_path / "flash.bin"
    report = {}
    flash.write_bytes(patch_main.patch_flash(FLASH.read_bytes(), QL_MODS + LR_MODS, "cdj850", report))
    assert {report[n][0] for n in QL_MODS + LR_MODS} <= {"applied", "present"}, report
    syms = payloadbuild.build(["loop_resize", "quant_loops"], "cdj850").symbols
    res = live_drift(stick, flash, syms)
    print("loop drift live:", json.dumps(res))
    assert set(res) == {12800, 12796, 12000}               # Q128, Q12796, QVAR (120 at its start)
    for bpm100, t in res.items():
        g = Grid([tuple(e) for e in t["grid"]["entries"]], total=t["grid"]["count"])
        s = t["start"]
        assert s["state"] == 4 and g.f(g.at_or_before(s["in"])) == s["in"], s   # quant_loops: on a beat
        v = t["loop4"]
        exact = (64 * 28125) % bpm100 == 0                  # 120.00: 4 beats are 150 frames
        assert v["passes"] >= 16 and (v["moves"] == s["moves"] if exact else v["moves"] > s["moves"]), v
        _check_loop(v, t["follow"], 64, bpm100)
        h = t["loop2"]                                     # CALL <: a new 2-beat loop, tracked anew
        assert h["loops"] == v["loops"] + 1 and h["in"] == v["in"], (v, h)
        _check_loop(h, t["follow2"], 32, bpm100)
        assert t["exit"]["state"] == 3, t["exit"]
        # the tick runs every player cycle: many per frame-pass, so no wrap is missed
        frames = v["passes"] * Fraction(v["num"], v["den"])
        assert v["ticks"] - s["ticks"] > frames / 6, (v["ticks"], s["ticks"], float(frames))
