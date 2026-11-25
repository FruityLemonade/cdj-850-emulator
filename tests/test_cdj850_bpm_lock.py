# SPDX-License-Identifier: GPL-2.0-or-later
"""bpm_lock on the CDJ-850 MAIN 1.13: CDJ-350 BPM Lock with ±100% range.

* static: all patch sites (range1/2 DataPatches, tempowidemath1/2, key filter,
  state tick, notify1/2 pool redirects, display & flash hooks) exist once,
  outside the boot-critical closure, and compose cleanly with all CDJ-850 mods;
* interpreter (tests/sh2a.py): BEAT SELECT + MASTER TEMPO short toggles lock,
  1 s hold enters Master BPM edit, ±100% pitch lock switches to WIDE when
  |locked_pitch| > 16%, notify flashes text, display hook stages Master/locked
  BPM and locked pitch/sign/decimal flags;
* live (QEMU + baken stick: GRID128 at 128 BPM and GRID80 at 80 BPM):
  hold BEAT SELECT + MASTER TEMPO 1 s -> Master BPM edit (128.0) -> rotary
  adjusts -> exit -> next track (80 BPM) -> short BEAT SELECT + MASTER TEMPO
  engages BPM Lock at >+50% pitch (WIDE active, DSP speed > 1.5x).
"""
import json
import os
import socket
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
from test_cdj850_hot_cues import _logger, _stub  # noqa: E402

MAIN = ROOT / "extract" / "cdj850" / "main_unpacked.bin"
FLASH = ROOT / "extract" / "cdj850" / "flash_cdj850.bin"
BAKEN = ROOT / "tools" / "baken" / "baken"
BASE = 0x0C000000

MODS = [
    "bpm_lock", "bpm_lock_range1", "bpm_lock_range2",
    "tempowidemath1", "tempowidemath2",
    "bpm_lock_tick", "bpm_lock_notify1", "bpm_lock_notify2",
    "bpm_lock_display", "bpm_lock_flash",
]

POOLS = [
    (0x0C0ED038, 0x0C0DD492, 0x0C0ECEFC, "c850_ui_keys"),
    (0x0C0EC838, 0x0C0EB388, 0x0C0EC73E, "c850_ui_tick"),
    (0x0C0D1DA4, 0x0C06E58A, 0x0C0D1B70, "bpm_lock_notify"),
    (0x0C0D1F34, 0x0C06E58A, 0x0C0D1E34, "bpm_lock_notify"),
]

K0, K1, K2 = 0x0CE6CA08, 0x0CE6CA0A, 0x0CE6CA0C
D154, D156, UI_LOCK = 0x0CE6D154, 0x0CE6D156, 0x0CE6D151
TICK_MS = 0x0CE49F54
TEMPO_RANGE = 0x0CE6D0E1
PITCH_WIDE, PITCH_SCALE, PITCH = 0x0CE6CA4C, 0x0CE6CA4E, 0x0CE6CA50
BASE_TENTHS = 0x0C9D23EC
STAGING = 0x0C9D0AD8
BPM_STAGING, PITCH_STAGING = 0x0C9D0AE0, 0x0C9D0AE2
STAGING_S18, STAGING_S19, STAGING_S1B = 0x0C9D0AF0, 0x0C9D0AF1, 0x0C9D0AF3
BL_ACTIVE, BL_EDIT, BL_SET = 0x0CE649AC, 0x0CE649AD, 0x0CE649AE
BL_MASTER, BL_PITCH = 0x0CE649B0, 0x0CE649BC
ROWS, DISPLAY_MODE = 0x0C9D0B18, 0x0C9D0AF8
BEAT_SELECT = 0x0040
MASTER_TEMPO = 0x0100

needs_toolchain = pytest.mark.skipif(not payloadbuild.have_toolchain(), reason="sh-elf toolchain not installed")
needs_main = pytest.mark.skipif(not MAIN.is_file(), reason="extract/cdj850/main_unpacked.bin is not present")


# ---- static ----

@needs_main
@needs_toolchain
def test_static_patches_outside_boot_critical_closure():
    import bootcrit
    stock = MAIN.read_bytes()
    report = {}
    out = patch_main.patch(stock, MODS, "cdj850", report)
    assert {n: report[n][0] for n in MODS} == {n: "applied" for n in MODS}, report
    p = payloadbuild.build(["bpm_lock"], "cdj850")
    closure = bootcrit.closure(stock, BASE, byteorder=">")

    for pool, old, reader, sym in POOLS:
        o = pool - BASE
        assert struct.unpack_from(">I", stock, o)[0] == old, hex(pool)
        assert struct.unpack_from(">I", out, o)[0] == payloadbuild.lookup(p.symbols, payloadbuild.gate_name(sym)), hex(pool)
        assert not closure.hit(o, o + 4), hex(pool)
        assert not closure.hit(reader - BASE, reader - BASE + 2), hex(reader)

    # ±16% -> ±100% edits in FUN_0C0D1CE6 and FUN_0C0D1D56
    assert stock[0x0C0D1D22 - BASE:0x0C0D1D2E - BASE].hex() == "01f0f9c030138b0f01000640"
    assert out[0x0C0D1D22 - BASE:0x0C0D1D2E - BASE].hex() == "01f0d8f030138b0f01002710"
    assert stock[0x0C0D1D82 - BASE:0x0C0D1D8E - BASE].hex() == "05f0f9c032538b0505000640"
    assert out[0x0C0D1D82 - BASE:0x0C0D1D8E - BASE].hex() == "05f0d8f032538b0505002710"
    assert not closure.hit(0x0C0D1CE6 - BASE, 0x0C0D1DA0 - BASE)


@needs_main
@needs_toolchain
def test_composes_with_all_cdj850_mods():
    names = [
        "autostandby", "bpm999", "bpm999grid", "bpm999range", "bpm_probe", "dspprobe",
        "payload_demo", "payload_demo_redirect", "tempowide",
        "bpm_decimals", "bpm_decimals_grid",
        "hot_cues", "hot_cues_tick", "hot_cues_select", "hot_cues_select2",
        "hot_cues_leds", "hot_cues_anlz", "hot_cues_flash",
        "quant_loops", "quant_loops_auto_send", "quant_loops_auto_send2", "quant_loops_auto_keep",
        "quant_loops_loop_in", "quant_loops_loop_out", "quant_loops_loop_out2",
        "quant_loops_keys", "quant_loops_flash", "quant_loops_tick", "quant_loops_blocks", "quant_loops_resize", "quant_loops_resize_req",
        "loop_resize", "loop_resize_tick", "loop_resize_memory",
        "phase_meter",
    ] + MODS
    report = {}
    patch_main.patch(MAIN.read_bytes(), names, "cdj850", report)
    assert all(report[n][0] == "applied" or (n in ("hot_cues", "hot_cues_tick", "quant_loops_keys", "loop_resize_tick")
                                             and report[n][0] == "present" or "shared" in report[n][1])
               for n in names), report


@needs_toolchain
def test_payload_has_no_fpu_code_and_small_frames():
    p = payloadbuild.build(["bpm_lock"], "cdj850")
    text = p.blob[:p.symbols["__bss_start"] - 0x0DE00020]
    dis = subprocess.run([payloadbuild.tool_prefix() + "objdump", "-d", "-b", "binary", "-m", "sh2a", "-EB",
                          "--adjust-vma=0x0DE00020", "/dev/stdin"], input=text, capture_output=True).stdout
    assert b"fmov" not in dis and b"fsts" not in dis and b"flds" not in dis and b"fpscr" not in dis
    assert max(p.stack.values()) <= 96


# ---- interpreter ----

LOG = 0x0DDFE000


def _sym(p, name):
    a = payloadbuild.lookup(p.symbols, name)
    assert a is not None, name
    return a


def _mem(p):
    mem = Memory()
    mem.add(0x0DE00020, p.blob)
    mem.add(0x0DDFE000, bytes(0x2000))
    mem.add(0x0CE49F50, bytes(8))
    mem.add(0x0CE649A0, bytes(0x40))
    mem.add(0x0CE6CA00, bytes(0x60))
    mem.add(0x0CE6D0E0, bytes(0x100))
    mem.add(0x0C9D0AD0, bytes(0x600))
    mem.add(0x0C9D23E0, bytes(0x20))
    mem.write(TEMPO_RANGE, b"\x02")
    mem.write(PITCH_SCALE, struct.pack(">H", 200))
    mem.write(BASE_TENTHS, struct.pack(">H", 800))   # 80.0 BPM track
    mem.put32(DISPLAY_MODE, 10)
    # Stock stand-ins:
    # 0x0C0DD492 (jog), 0x0C0EB388 (stock tick), 0x0C06E58A (stock notify -> log 0),
    # 0x0C0D9B44 (tempo_fader_to_pitch -> sets PITCH = +5000 if WIDE else +500),
    # 0x0C0D1CE6 (recalc -> computes locked pitch from BL_MASTER and BASE_TENTHS),
    # 0x0C0D1F14 (dispatcher -> cmd 0 toggles, cmd 1 enters edit with 128.0)
    _stub(mem, 0x0C0DD492, "rts\nmov #0,r0\n")
    _stub(mem, 0x0C0EB388, "rts\nnop\n")
    _stub(mem, 0x0C06E58A, _logger(0, 0x42))
    _stub(mem, 0x0C0D9B44, """
        mov.l   1f,r1
        mov.b   @r1,r0
        tst     r0,r0
        bt      2f
        mov.w   3f,r0
        bra     4f
        nop
    2:  mov.w   5f,r0
    4:  mov.l   6f,r2
        rts
        mov.w   r0,@r2
        .align 2
    1:  .long   %#x
    6:  .long   %#x
    3:  .word   5000
    5:  .word   500
    """ % (PITCH_WIDE, PITCH))
    _stub(mem, 0x0C0D1CE6, """
        mov.l   1f,r1
        mov.l   @r1,r2
        mov.l   2f,r1
        mov.w   @r1,r3
        mov.w   3f,r0
        mulu.w  r2,r0
        sts     macl,r4
        mov     r3,r0
        divu    r0,r4
        mov.w   3f,r0
        sub     r0,r4
        mov.l   4f,r1
        mov.w   r4,@r1
        rts
        mov     #0,r0
        .align 2
    1:  .long   %#x
    2:  .long   %#x
    4:  .long   %#x
    3:  .word   10000
        .word   0
    """ % (BL_MASTER, BASE_TENTHS, BL_PITCH))
    _stub(mem, 0x0C0D1F14, """
        mov     #1,r0
        cmp/eq  r0,r4
        bt      edit
        mov.l   1f,r1
        mov.b   @r1,r0
        tst     r0,r0
        bf      unlock
        mov     #1,r0
        mov.b   r0,@r1
        mov.w   5f,r0
        mov.l   4f,r2
        mov.w   r0,@r2
        rts
        mov     #0,r0
    unlock:
        mov     #0,r0
        mov.b   r0,@r1
        rts
        nop
    edit:
        mov.l   2f,r1
        mov     #1,r0
        mov.b   r0,@r1
        mov.l   3f,r1
        mov.w   6f,r0
        rts
        mov.l   r0,@r1
        .align 2
    1:  .long   %#x
    2:  .long   %#x
    3:  .long   %#x
    4:  .long   %#x
    5:  .word   6000
    6:  .word   1280
    """ % (BL_ACTIVE, BL_EDIT, BL_MASTER, BL_PITCH))
    return mem


def _call(p, mem, name, *args):
    cpu = Sh2a(mem)
    cpu.r[15] = 0x0DDFFF00
    for i, a in enumerate(args):
        cpu.r[4 + i] = a & 0xFFFFFFFF
    cpu.run(_sym(p, name), Sh2a.RET)
    return cpu.r[0]


def _step_keys(p, mem, k0=0, k1=0, t=0):
    mem.put32(TICK_MS, t)
    mem.write(K0, struct.pack(">HHH", k0, k1, 0))
    _call(p, mem, "c850_ui_keys")
    res = struct.unpack(">HHH", mem.read(K0, 6))
    _call(p, mem, "c850_ui_tick")
    return res


@needs_toolchain
def test_short_and_hold_combos_and_wide_range():
    p = payloadbuild.build(["bpm_lock"], "cdj850")
    mem = _mem(p)
    mem.put32(BL_MASTER, 1280)
    mem.write(BL_SET, b"\x01")
    # Hold BEAT SELECT + short press MASTER TEMPO (<1000 ms) -> engages lock at +60.0% -> switches to WIDE!
    assert _step_keys(p, mem, k0=BEAT_SELECT, t=100) == (BEAT_SELECT, 0, 0)
    mem.write(D154, b"\x01")
    assert _step_keys(p, mem, k0=BEAT_SELECT, k1=MASTER_TEMPO, t=150) == (0, 0, 0)
    assert mem.read(D154, 1)[0] == 0  # fraction toggle undone
    assert _step_keys(p, mem, k0=BEAT_SELECT, k1=0, t=300) == (0, 0, 0)
    assert mem.read(BL_ACTIVE, 1)[0] == 1
    assert mem.read(TEMPO_RANGE, 1)[0] == 4
    assert mem.read(PITCH_WIDE, 1)[0] == 1
    assert mem.u16(PITCH_SCALE) == 200
    assert mem.u32(_sym(p, "bpm_lock_wide_switches")) == 1
    assert struct.unpack(">h", mem.read(BL_PITCH, 2))[0] == 6000

    # Short toggle again -> unlocks and restores ±10% range
    _step_keys(p, mem, k0=0, t=400)
    _step_keys(p, mem, k0=BEAT_SELECT, t=500)
    _step_keys(p, mem, k0=BEAT_SELECT, k1=MASTER_TEMPO, t=550)
    _step_keys(p, mem, k0=0, k1=0, t=700)
    assert mem.read(BL_ACTIVE, 1)[0] == 0
    assert mem.read(TEMPO_RANGE, 1)[0] == 2
    assert mem.read(PITCH_WIDE, 1)[0] == 0

    # Hold BEAT SELECT + MASTER TEMPO >= 1000 ms -> Master BPM edit mode + flash
    _step_keys(p, mem, k0=BEAT_SELECT, t=1000)
    _step_keys(p, mem, k0=BEAT_SELECT, k1=MASTER_TEMPO, t=1050)
    assert mem.read(BL_EDIT, 1)[0] == 0
    _step_keys(p, mem, k0=BEAT_SELECT, k1=MASTER_TEMPO, t=2100)
    assert mem.read(BL_EDIT, 1)[0] == 1
    assert mem.u32(_sym(p, "bpm_lock_edits")) == 1


@needs_toolchain
def test_notify_and_display_hook_on_interpreter():
    p = payloadbuild.build(["bpm_lock"], "cdj850")
    mem = _mem(p)
    # Notify 0x0D -> "BPM LOCK ON" without calling stock
    assert _call(p, mem, "bpm_lock_notify", 0x0D, 100) == 0x0D
    assert mem.u32(LOG + 28) == 0
    _call(p, mem, "c850_ui_flash_hook", 0, 0)
    row0 = mem.read(ROWS, 0x20)
    assert row0[5:5 + row0[4]] == b"BPM LOCK ON"
    # Unknown notify code falls back to stock FUN_0C06E58A
    assert _call(p, mem, "bpm_lock_notify", 0x06, 100) == 0x42
    assert mem.u32(LOG + 28) == 1

    # Display hook stages locked BPM and +60.0% WIDE pitch
    mem.write(BL_ACTIVE, b"\x01")
    mem.write(BL_EDIT, b"\x00")
    mem.write(PITCH_WIDE, b"\x01")
    mem.write(BL_PITCH, struct.pack(">h", 6000))
    mem.write(BASE_TENTHS, struct.pack(">H", 800))
    mem.write(BPM_STAGING, struct.pack(">H", 80))
    _call(p, mem, "bpm_lock_display_hook", 0, 0)
    assert mem.u16(BPM_STAGING) == 128
    assert mem.u16(PITCH_STAGING) == 600          # 60.0% in tenths for WIDE
    assert (mem.read(STAGING_S18, 1)[0] & 0x03) == 0x03
    assert (mem.read(STAGING_S19, 1)[0] & 0x30) == 0x20


# ---- live ----

def _udp(deck, msg):
    import cdj_panelsock
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.sendto(msg.encode(), (cdj_panelsock.HOST, cdj_panelsock.port_for(deck.keysock)))
    s.close()


@needs_toolchain
@pytest.mark.skipif(not FLASH.is_file(), reason="extract/cdj850/flash_cdj850.bin is not present")
def test_live_bpm_lock_wide_in_qemu(tmp_path):
    """Hold BEAT SELECT + MASTER TEMPO 1 s on 128 BPM track -> Master BPM 128.0 -> rotary +1.0 ->
    load 80 BPM track -> short press engages BPM Lock at +61.25% (WIDE, >16% range)."""
    if os.name == "nt":
        pytest.skip("QMP socket descriptor inheritance requires POSIX")
    import importlib.util
    if not BAKEN.is_file() or importlib.util.find_spec("pyfatfs") is None or importlib.util.find_spec("numpy") is None:
        pytest.skip("tools/baken/baken, pyfatfs and numpy are needed")
    sys.path.insert(0, str(ROOT / "scripts" / "debug"))
    import cdj850_trace as tr

    stick = tmp_path / "bpmlock.img"
    tr.build_stick(stick, {
        "device_name": "BPMLOCK",
        "playlist": "BPM Lock",
        "tracks": [
            {"title": "GRID128", "bpm": 128, "first_downbeat": 0, "bars": 16},
            {"title": "GRID80", "bpm": 80, "first_downbeat": 0, "bars": 16},
        ],
    })
    flash = tmp_path / "flash.bin"
    flash.write_bytes(patch_main.patch_flash(FLASH.read_bytes(), MODS, "cdj850"))

    with tr.Deck(stick, flash=flash) as d:
        d.wait_line("mode=0x0a", "[GRID128]", timeout=300)
        time.sleep(1.5)
        # Hold BEAT SELECT + MASTER TEMPO >= 1s (under -icount host load, hold ~2s for 1000+ tick_ms)
        d.hold("beat_select")
        time.sleep(0.2)
        d.hold("master_tempo")
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline and d.rb(BL_EDIT) != 1:
            time.sleep(0.3)
        d.release("master_tempo")
        d.release("beat_select")
        assert d.rb(BL_EDIT) == 1
        m0 = d.rw(BL_MASTER)
        assert m0 == 1280, m0

        # Rotary +10 clicks -> +1.0 BPM -> 129.0 BPM, then exit edit with BACK
        for _ in range(10):
            _udp(d, "0x03:1:0:rot")
            time.sleep(0.15)
        time.sleep(0.8)
        m1 = d.rw(BL_MASTER)
        assert m1 > 1280, m1
        d.key("back")
        time.sleep(0.8)
        assert d.rb(BL_EDIT) == 0

        # Next track (80 BPM) -> short press BEAT SELECT + MASTER TEMPO -> +61.25% lock in WIDE!
        d.key("next")
        d.wait_line("mode=0x0a", "[GRID80]", timeout=180)
        time.sleep(1.5)
        d.hold("beat_select")
        time.sleep(0.2)
        d.key("master_tempo")
        time.sleep(0.3)
        d.release("beat_select")
        time.sleep(1.0)

        d.q("stop")
        active = d.rb(BL_ACTIVE)
        locked_p = struct.unpack(">h", struct.pack(">H", d.rh(BL_PITCH)))[0]
        rng = d.rb(TEMPO_RANGE)
        bpm_stage = d.rh(BPM_STAGING)
        pitch_stage = d.rh(PITCH_STAGING)
        dsp_speed = d.rw(0x0CE6C940) & 0x1FFFFF
        d.q("cont")

        assert active == 1
        assert locked_p > 5000
        assert rng == 4               # auto-switched to WIDE for >±16%
        assert bpm_stage == (m1 + 5) // 10
        assert pitch_stage == locked_p // 10
        assert dsp_speed > 0x0C0000   # > 1.5x DSP playback speed!
