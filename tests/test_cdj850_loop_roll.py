# SPDX-License-Identifier: GPL-2.0-or-later
"""loop_roll on the CDJ-850 MAIN 1.13: a held beat button is a slip loop (loop roll).

* static: the ISR pool word 0x0C07730C (one reader; MAIN installs the IRQ7 vector at run time, so
  the static boot-critical walk does not reach it) and its service gate; the shared c850_ui words;
  composition with the branch mods; no FPU code;
* live (QEMU + the loop_resize grid stick, Q128): roll mode on (BEAT SELECT + LOOP OUT), BEAT 4
  held for several passes of the loop, then released: the loop wrapped while held, the landing is
  the counted shadow position, the shadow moved at the guest's 1 ms clock rate (75 frames per
  second), and the deck plays on from the landing in state 3. Then roll mode off: BEAT 4 is a
  stock loop that keeps playing after the release.
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

import patch_main  # noqa: E402
import payloadbuild  # noqa: E402

MAIN = ROOT / "extract" / "cdj850" / "main_unpacked.bin"
FLASH = ROOT / "extract" / "cdj850" / "flash_cdj850.bin"
BASE = 0x0C000000
MODS = ["loop_roll", "loop_roll_keys", "loop_roll_tick", "loop_roll_flash"]
ISR_POOL, ISR_STOCK, ISR_READER = 0x0C07730C, 0x0C0158D4, 0x0C07715E
PLAY_FRAME, STATE_USB, LOOP_ACTIVE = 0x0C7F647C, 0x0CE6D25F, 0x0CE6D0ED
LOOP_IN, OUT_REQ = 0x0C7F6498, 0x0C7F64DC
SYMS = ("rolls", "isr_calls", "jumps", "misses", "aborts", "exits", "toggles", "start_pos",
        "last_target", "last_from", "last_sector", "start_tick", "jump_tick")

needs_toolchain = pytest.mark.skipif(not payloadbuild.have_toolchain(), reason="sh-elf toolchain not installed")
needs_main = pytest.mark.skipif(not MAIN.is_file(), reason="extract/cdj850/main_unpacked.bin is not present")


@needs_main
@needs_toolchain
def test_isr_pool_is_gated_boot_pool():
    import bootcrit
    import c850loader
    import validate
    from players.cdj850 import MAIN_PROFILE
    stock = MAIN.read_bytes()
    report = {}
    out = patch_main.patch(stock, MODS, "cdj850", report)
    assert {n: report[n][0] for n in MODS} == {n: "applied" for n in MODS}
    p = payloadbuild.build(["loop_roll"], "cdj850")
    o = ISR_POOL - BASE
    assert struct.unpack_from(">I", stock, o)[0] == ISR_STOCK
    assert struct.unpack_from(">I", out, o)[0] == payloadbuild.lookup(p.symbols, payloadbuild.gate_name("c850_loop_out_isr"))
    # the word's only reader is the forward-path loop-out test of irq7_dspdreq_isr
    readers = []
    for k in range(len(stock) // 2):
        op = struct.unpack_from(">H", stock, 2 * k)[0]
        pc = BASE + 2 * k
        if op & 0xF000 == 0xD000 and (pc & ~3) + 4 + (op & 0xFF) * 4 == ISR_POOL:
            readers.append(pc)
    assert readers == [ISR_READER]
    # The ISR runs on every deck, but MAIN installs its vector at run time, so the static
    # boot-critical walk does not reach it: the word and its reader are outside the set, and the
    # generated service gate in front of c850_loop_out_isr still returns to the stock test.
    closure = bootcrit.closure(stock, BASE, byteorder=">")
    assert not closure.hit(o, o + 4)
    assert not closure.hit(ISR_READER - BASE, ISR_READER - BASE + 2)
    payload = c850loader.installed(out)
    gate = payloadbuild.lookup(p.symbols, payloadbuild.gate_name("c850_loop_out_isr"))
    assert validate.is_gated_pool(stock, out, MAIN_PROFILE, ("loop_roll", "site", o, o + 4),
                                  frozenset((ISR_POOL,)), payload)
    assert gate % 4 == 0


ALL_MODS = ["bpm_decimals", "bpm_decimals_grid", "quant_loops", "quant_loops_auto_send",
            "quant_loops_auto_send2", "quant_loops_auto_keep", "quant_loops_loop_in", "quant_loops_loop_out",
            "quant_loops_loop_out2", "quant_loops_keys", "quant_loops_flash", "quant_loops_tick", "quant_loops_blocks", "quant_loops_resize", "quant_loops_resize_req",
            "loop_resize", "loop_resize_tick", "loop_resize_memory"]


@needs_main
@needs_toolchain
def test_composes_with_the_loop_mods():
    names = ALL_MODS + MODS
    report = {}
    patch_main.patch(MAIN.read_bytes(), names, "cdj850", report)
    assert all(report[n][0] in ("applied", "present") for n in names), report


def fpu_mnemonics(src):
    """FPU instructions in the assembly gcc makes of `src` (the payload's flags)."""
    import re
    import subprocess
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        asm = Path(tmp) / "x.s"
        subprocess.run([payloadbuild.tool_prefix() + "gcc", *payloadbuild.CFLAGS, "-I", payloadbuild.INCLUDE,
                        "-S", "-o", str(asm), str(src)], check=True)
        text = asm.read_text()
    insns = [ln.split()[0] for ln in text.splitlines()
             if ln.startswith("\t") and ln.split() and not ln.split()[0].startswith(".")]
    assert insns
    return [i for i in insns if i.startswith("f")] + re.findall(r"\b(fpul|fpscr|fr\d+|dr\d+)\b", text)


@needs_toolchain
def test_payload_has_no_fpu_code_and_small_isr_frame():
    p = payloadbuild.build(["loop_roll"], "cdj850")
    for src in (ROOT / "mods" / "loop_roll" / "cdj850" / "payload" / "loop_roll.c",
                ROOT / "mods" / "payload" / "common" / "c850_loop_out.c"):
        assert not fpu_mnemonics(src), src.name
    # runs on the interrupt stack inside irq7_dspdreq_isr (dispatcher -> hook -> jump)
    assert p.stack["loop_roll:loop_roll_out_hook"] <= 64
    assert p.stack.get("loop_roll:lr_jump", 0) <= 48
    for name in ("c850_loop_out_isr", "c850_ring_jump", "c850_read_pos"):
        assert p.stack["common:" + name] <= 64, name


# ---- live ----

def drive(stick, flash, syms):
    sys.path.insert(0, str(ROOT / "scripts" / "debug"))
    import cdj850_trace as tr
    script = "6000:usb:200,7600:rot+2,9000:enter:200,10600:rot+3,12000:enter:200"
    res = {}

    def sym(d, name):
        return d.rw(payloadbuild.lookup(syms, "loop_roll_" + name))

    def ev(d):
        return {n: sym(d, n) for n in SYMS}

    def wait_frame(d, f, timeout=150):
        end = time.monotonic() + timeout
        while d.rw(PLAY_FRAME) < f:
            assert time.monotonic() < end, (f, d.rw(PLAY_FRAME), d.rb(STATE_USB))
            time.sleep(0.03)

    def ensure_playing(d):
        for _ in range(6):
            st = d.rb(STATE_USB)
            if st == 3:
                return
            d.key("reloop" if st == 4 else "play")
            time.sleep(2.5)
        assert d.rb(STATE_USB) == 3

    def frames_for(d, secs):
        fr, end = [], time.monotonic() + secs
        while time.monotonic() < end:
            fr.append((d.rw(PLAY_FRAME), d.rb(STATE_USB)))
            time.sleep(0.02)
        return fr

    def roll(d, hold_ms):
        """Hold BEAT 4 for hold_ms (guest ms), sampling frames, then follow the landing."""
        pre = ev(d)
        f0 = d.rw(PLAY_FRAME)
        d.key("beat_4", dur=hold_ms)
        fr = frames_for(d, 1.0)
        # wait for the release to be handled (jump, miss or abort counted)
        end = time.monotonic() + 60
        while time.monotonic() < end:
            e = ev(d)
            if (e["jumps"], e["misses"], e["aborts"]) != (pre["jumps"], pre["misses"], pre["aborts"]):
                break
            fr.append((d.rw(PLAY_FRAME), d.rb(STATE_USB)))
            time.sleep(0.02)
        time.sleep(1.0)
        after = frames_for(d, 1.5)
        return {"f0": f0, "pre": pre, "ev": ev(d), "held": fr, "after": after,
                "in": d.rw(LOOP_IN), "req": d.rw(OUT_REQ), "state": d.rb(STATE_USB),
                "active": d.rb(LOOP_ACTIVE)}

    with tr.Deck(stick, flash=flash, script=script) as d:
        d.wait_line("mode=0x0a", "Q128", timeout=300)
        time.sleep(1.5)
        ensure_playing(d)
        wait_frame(d, 600)
        # roll mode on: hold BEAT SELECT, press LOOP OUT
        d.key("beat_select", dur=1200)
        time.sleep(0.15)
        d.key("loop_out", dur=300)
        time.sleep(2.0)
        res["toggle"] = ev(d)
        ensure_playing(d)
        res["roll"] = [roll(d, ms) for ms in (6000, 2500)]
        # roll mode off: stock loop that stays after the release
        ensure_playing(d)
        d.key("beat_select", dur=1200)
        time.sleep(0.15)
        d.key("loop_out", dur=300)
        time.sleep(2.0)
        ensure_playing(d)
        res["stock"] = roll(d, 2500)
    return res


def check_roll(r, frames_per_s=75.0):
    e, pre = r["ev"], r["pre"]
    assert e["rolls"] == pre["rolls"] + 1, r
    assert e["jumps"] == pre["jumps"] + 1 and e["misses"] == pre["misses"], r
    assert e["exits"] == pre["exits"] + 1, r
    # the loop wrapped at least once while held
    held = [f for f, st in r["held"] if st == 4]
    assert any(b < a for a, b in zip(held, held[1:])), r["held"]
    # the landing is the counted shadow position, ahead of where the loop played
    counted = e["last_target"] - e["start_pos"]
    assert counted == e["isr_calls"] - pre["isr_calls"] - 1, e
    assert e["last_target"] > e["last_from"], e
    # the shadow moved at the guest clock: blocks/7 frames over the 1 ms ticks
    ms = e["jump_tick"] - e["start_tick"]
    assert ms > 500, e
    assert abs(counted / 7.0 - ms * frames_per_s / 1000.0) <= 3.0, (counted / 7.0, ms)
    # then plain play (state 3) from the landing frame onwards
    after = [f for f, st in r["after"] if st == 3]
    assert r["state"] == 3 and after, r
    target_frame = e["last_target"] // 7
    assert target_frame <= after[0] <= target_frame + 2 * 75, (target_frame, after[:3])
    assert all(b >= a for a, b in zip(after, after[1:])), after


@needs_toolchain
@pytest.mark.skipif(not FLASH.is_file(), reason="extract/cdj850/flash_cdj850.bin is not present")
def test_live_loop_roll(tmp_path):
    import test_cdj850_loop_resize as lr
    stick = lr.build_stick(tmp_path)
    flash = tmp_path / "flash.bin"
    flash.write_bytes(patch_main.patch_flash(FLASH.read_bytes(), MODS, "cdj850"))
    syms = payloadbuild.build(["loop_roll"], "cdj850").symbols
    res = drive(stick, flash, syms)
    print("loop_roll live:", json.dumps(res))
    assert res["toggle"]["toggles"] == 1
    for r in res["roll"]:
        check_roll(r)
    s = res["stock"]
    assert s["ev"]["rolls"] == s["pre"]["rolls"] and s["ev"]["jumps"] == s["pre"]["jumps"]
    assert s["state"] == 4 and s["active"] == 1, s          # a stock loop keeps playing


# ---- interpreter: the quantized release ----

def _roll_deck(div=1, quantize=True):
    """loop_roll + quant_loops on the SH-2A interpreter: the 128.00 grid from 0.35 s, ring sector s
    holding frame s (stand-in ring_sector_to_frame), a stock loop-out test that never wraps."""
    from sh2a import Sh2a
    from test_cdj850_hot_cues import _stub
    import test_cdj850_quant_loops as tq
    p = payloadbuild.build(["loop_roll", "quant_loops"], "cdj850")
    entries = tq.GRIDS["128 @0.35"]
    mem = tq._mem(p, entries, base=1280, flags=0 if quantize else 0x53)
    mem.add(0x0CE6CA00, bytes(0x800))                    # K0..K2, loop active, UI lock, D154/D156
    mem.add(0x0C7F6400, bytes(0x400))                    # feeder words (read ptr, sectors, segments)
    mem.add(0x0CE49F54, bytes(4))                        # 1 ms tick
    _stub(mem, 0x0C00851E, "rts\nmov r4,r0")             # ring_sector_to_frame: identity
    _stub(mem, 0x0C0158D4, "rts\nmov #0,r0")             # isr_loop_out_reached: no stock wrap
    _stub(mem, 0x0C015B18, "rts\nnop")                   # audio_clear_loop_segments
    _stub(mem, 0x0C0DD492, "rts\nmov #0,r0")             # the key word's stock function

    def call(name, *args):
        cpu = Sh2a(mem)
        cpu.r[15] = 0x0DDFFF00
        for i, a in enumerate(args):
            cpu.r[4 + i] = a & 0xFFFFFFFF
        cpu.run(payloadbuild.lookup(p.symbols, name), Sh2a.RET)
        return cpu.r[0]

    def keys(k0=0, k1=0):
        mem.write(0x0CE6CA08, struct.pack(">HH", k0, k1))
        call("c850_ui_keys")

    def sym(name):
        return mem.u32(payloadbuild.lookup(p.symbols, name))

    if div != 1:
        call("c850_ui_set_flags", 0x30000, {2: 1, 4: 2}[div] << 16)
    return p, mem, call, keys, sym, tq.Grid(entries)


@needs_toolchain
@pytest.mark.parametrize("div,quantize", [(1, True), (4, True), (1, False)])
def test_quantized_release_lands_on_the_next_point(div, quantize):
    p, mem, call, keys, sym, g = _roll_deck(div, quantize)
    keys(0x0040)                                         # BEAT SELECT + LOOP OUT: roll mode on
    keys(0x0840)
    keys()
    assert sym("loop_roll_toggles") == 1
    mem.write(0x0CE6D252 + 0, b"\x02")
    mem.write(0x0CE6D25D + 2, b"\x03")                   # playing
    keys(0, 0x0040)                                      # BEAT 4 down: a roll starts
    assert sym("loop_roll_rolls") == 1
    mem.write(0x0CE6D25D + 2, b"\x04")                   # the stock handler made the loop
    mem.write(LOOP_ACTIVE, b"\x01")
    cur = 214                                            # read pointer: sector 214, block 3
    mem.write(0x0C7F645C, struct.pack(">hh", cur, cur + 600))
    mem.put32(0x0C7F6470, 0x0C1E8E5C + cur * 0x930 + 3 * 0x150)
    for _ in range(40):                                  # 40 blocks counted while held
        call("c850_loop_out_isr", cur, 0, 0)
    start = cur * 7 + 3
    keys()                                               # released
    release = start + 39
    assert sym("loop_roll_release_pos") == release
    want = g.roll_target(release, 10, div) if quantize else None
    target = want if want is not None and want > release else release
    assert sym("loop_roll_quantized") == (1 if target > release else 0)
    n = 0
    while sym("loop_roll_jumps") == 0:
        call("c850_loop_out_isr", cur, 0, 0)
        n += 1
        assert n < 2000
    if target == release:
        target += 1                                      # at once: the next block's shadow
    assert sym("loop_roll_last_target") == target and n == target - release
    # landed on the counted block of the target: sector target // 7, block target % 7
    assert mem.u32(0x0C7F6470) == 0x0C1E8E5C + (target // 7) * 0x930 + (target % 7) * 0x150


@needs_toolchain
def test_a_beat_press_while_waiting_takes_the_release_back():
    p, mem, call, keys, sym, g = _roll_deck(1, True)
    keys(0x0040)
    keys(0x0840)
    keys()
    mem.write(0x0CE6D25D + 2, b"\x03")
    keys(0, 0x0040)
    mem.write(0x0CE6D25D + 2, b"\x04")
    mem.write(LOOP_ACTIVE, b"\x01")
    cur = 214
    mem.write(0x0C7F645C, struct.pack(">hh", cur, cur + 600))
    mem.put32(0x0C7F6470, 0x0C1E8E5C + cur * 0x930)
    call("c850_loop_out_isr", cur, 0, 0)
    keys()                                               # release: waits for the beat
    assert sym("loop_roll_quantized") == 1
    keys(0, 0x0010)                                      # BEAT 1 before it: roll on (stock resizes)
    assert sym("loop_roll_cancels") == 1
    for _ in range(800):
        call("c850_loop_out_isr", cur, 0, 0)
    assert sym("loop_roll_jumps") == 0                   # still rolling
    keys()                                               # released again: lands on a later point
    for _ in range(800):
        call("c850_loop_out_isr", cur, 0, 0)
    assert sym("loop_roll_jumps") == 1
