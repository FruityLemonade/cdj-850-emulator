# SPDX-License-Identifier: GPL-2.0-or-later
"""waveform on the CDJ-850 MAIN 1.13: zoomed scrolling half-waveform on INFO Page 1.

* static: hooks at display_stage_bpm tail (0x0C0642AA) and INFO rotary branch
  (0x0C0E782E), both outside the boot-critical closure, zero FPU instructions,
  small stack frames, composes cleanly with info_order and phase_meter;
* interpreter (tests/sh2a.py): logarithmic amplitude mapping, left-edge playhead
  and per-zoom contrast with PWAV fallback, rotary CCW zoom out (0 -> 1 -> 2,
  clamped) and CW zoom back in (2 -> 1 -> 0, consuming CW click until back
  at 0), cursor > 0 untouched, overview restore, dot-matrix text rows untouched;
* live (QEMU + baken stick): waveform + info_order together — INFO Page 1 keeps
  info_order's Title & BPM/Key/Stars rows while streaming a live scrolling
  waveform into 0x0C9D1199; CCW zooms close -> medium -> whole-track overview;
  CW zooms back in and then advances cleanly to info_order Page 2.
"""
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

MAIN = ROOT / "extract" / "cdj850" / "main_unpacked.bin"
FLASH = ROOT / "extract" / "cdj850" / "flash_cdj850.bin"
BAKEN = ROOT / "tools" / "baken" / "baken"
BASE = 0x0C000000
BPM_HOOK_SITE = 0x0C0642AA
ROTARY_HOOK_SITE = 0x0C0E782E
MODS = ["waveform", "waveform_rotary", "waveform_utility_scroll",
        "waveform_utility_select", "waveform_center_marker", "waveform_cue_column",
        "waveform_tx_waveform"]

DISPLAY_MODE = 0x0C9D0AF8
ROWS = 0x0C9D0B18
WF_SEQ = 0x0C9D1184
WF_CHANGED = 0x0C9D1188
WF_SAMPLES = 0x0C9D1199
WF_PWAV = 0x0CE65862
WF_OVERVIEW = 0x0CE65B82
PLAY_FRAME = 0x0C7F647C
LOOP_IN_FRAME = 0x0C7F6498
LOOP_OUT_FRAME = 0x0C7F64B0
LOOP_OUT_REQUEST = 0x0C7F64DC
RING_READ = 0x0C7F645C
RING_WRITE = 0x0C7F645E
TRACK_LEN = 0x0C7F650C
PCM_RING = 0x0C1E8E5C
PCM_SECTOR_BYTES = 2352
INFO_TRACK = 0x0C9D1D96
INFO_CURSOR = 0x0CE6D1E4
SOURCE_INDEX = 0x0CE6D252
PLAYER_STATE_BASE = 0x0CE6D25D
LOOP_ACTIVE = 0x0CE6D0ED
TICK_MS = 0x0CE49F54
SETTINGS = 0x0C9DA23C
UTILITY_LEVEL = 0x0C1E108C
UTILITY_CURSOR = 0x0C1E108D
TX_BASE = 0x0C9D07D0
TX_LAST = 0x0C9D0AC0
GRID_CTX = 0x0C9D24FC

needs_toolchain = pytest.mark.skipif(not payloadbuild.have_toolchain(), reason="sh-elf toolchain not installed")
needs_main = pytest.mark.skipif(not MAIN.is_file(), reason="extract/cdj850/main_unpacked.bin is not present")


# ---- static ----

@needs_main
@needs_toolchain
def test_static_hooks_outside_boot_critical_closure():
    import bootcrit
    import validate
    from players.cdj850 import GATED_BOOT_POOLS, MAIN_PROFILE
    stock = MAIN.read_bytes()
    report, spans = {}, {}
    combo = MODS + ["info_order", "info_order_rating", "phase_meter"]
    out = patch_main.patch(stock, patch_main.with_loader(combo, "cdj850"), "cdj850", report, spans)
    assert all(report[name][0] == "applied" for name in MODS), report
    assert report["info_order"][0] == "applied", report
    assert report["phase_meter"][0] == "applied", report

    closure = bootcrit.closure(stock, BASE, byteorder=">")
    assert not closure.hit(BPM_HOOK_SITE - BASE, BPM_HOOK_SITE - BASE + 14)
    assert not closure.hit(ROTARY_HOOK_SITE - BASE, ROTARY_HOOK_SITE - BASE + 12)

    rep = validate.Report()
    validate.check_main_image(stock, out, MAIN_PROFILE, spans, rep, gated_boot_pools=GATED_BOOT_POOLS)
    assert rep.exit_code == 0, rep.text()

    p = payloadbuild.build(combo, "cdj850")
    for pool, name in ((0x0C0E797C, "c850_ui_utility_scroll"),
                       (0x0C0E7F10, "c850_ui_utility_select"),
                       (0x0C064DD8, "waveform_tx_waveform"),
                       (0x0C064DDC, "waveform_center_marker"),
                       (0x0C063128, "waveform_cue_column")):
        assert struct.unpack_from(">I", out, pool - BASE)[0] == _sym(p, "__gate_" + name)
    wf_hook = payloadbuild.lookup(p.symbols, "waveform_hook")
    rot_hook = payloadbuild.lookup(p.symbols, "waveform_rotary_hook")
    lit_bpm = ((BPM_HOOK_SITE + 9) & ~3) - BASE
    tramp_bpm = struct.unpack(">I", out[lit_bpm:lit_bpm + 4])[0]
    assert wf_hook.to_bytes(4, "big") in out[tramp_bpm - BASE:tramp_bpm - BASE + 0x100]
    lit_rot = ((ROTARY_HOOK_SITE + 9) & ~3) - BASE
    tramp_rot = struct.unpack(">I", out[lit_rot:lit_rot + 4])[0]
    assert rot_hook.to_bytes(4, "big") in out[tramp_rot - BASE:tramp_rot - BASE + 0x80]


@needs_toolchain
def test_payload_has_no_fpu_code_and_small_frames():
    p = payloadbuild.build(MODS, "cdj850")
    text = p.blob[:p.symbols["__bss_start"] - 0x0DE00020]
    dis = subprocess.run([payloadbuild.tool_prefix() + "objdump", "-d", "-b", "binary", "-m", "sh2a", "-EB",
                          "--adjust-vma=0x0DE00020", "/dev/stdin"], input=text, capture_output=True).stdout
    assert b"fmov" not in dis and b"fsts" not in dis and b"flds" not in dis and b"fpscr" not in dis
    assert max(p.stack.values()) <= 128


# ---- interpreter ----

def _sym(p, name):
    a = payloadbuild.lookup(p.symbols, name)
    assert a is not None, name
    return a


def _mem(p):
    mem = Memory()
    mem.add(0x0DE00020, p.blob)
    mem.add(0x0DDFF000, bytes(0x1000))
    mem.add(0x0C7F6450, bytes(0x100))
    mem.add(0x0C9D0AE0, bytes(0x800))
    mem.add(GRID_CTX, bytes(0x2000))
    mem.add(0x0CE65840, bytes(0x20))
    mem.add(0x0CE65860, bytes(0x400))
    mem.add(0x0CE6D0E0, bytes(0x20))
    mem.add(0x0CE6D1E0, bytes(0x90))
    mem.add(INFO_TRACK, bytes(2))
    mem.add(TICK_MS, bytes(4))
    mem.add(SETTINGS, bytes(0x58))
    # Invalid ring index forces clean ANLZ PWAV/overview fallback without calling MAIN
    mem.write(RING_READ, struct.pack(">H", 0xFFFF))
    mem.write(RING_WRITE, struct.pack(">H", 0xFFFF))
    mem.write(SOURCE_INDEX, b"\x02")
    mem.write(PLAYER_STATE_BASE + 2, b"\x03")  # playing
    return mem


def _call(p, mem, name, *args):
    cpu = Sh2a(mem)
    cpu.r[15] = 0x0DE00000
    for i, a in enumerate(args):
        cpu.r[4 + i] = a & 0xFFFFFFFF
    cpu.run(_sym(p, name), Sh2a.RET)
    return cpu.r[0]


@needs_toolchain
def test_interpreter_zoom_levels_and_rotary_scroll():
    p = payloadbuild.build(MODS, "cdj850")
    mem = _mem(p)
    sp = 0x0DDFF800
    mem.write(SETTINGS + 0x22, b"\x56")  # saved Left preference

    # 1. Amplitude-to-height table: 0 / floor -> 0 (0 dots), quiet -> 1..3 (1 dot), loud -> 15 (5 dots)
    assert _call(p, mem, "waveform_amp_to_height", 0) == 0
    assert _call(p, mem, "waveform_amp_to_height", 0x0040) == 0
    assert 1 <= _call(p, mem, "waveform_amp_to_height", 0x0200) <= 3
    assert 4 <= _call(p, mem, "waveform_amp_to_height", 0x0400) <= 6
    assert _call(p, mem, "waveform_amp_to_height", 0x6000) == 15

    # Each PWAV bin spans 12 frames. A loud bin just before the playhead must
    # stay offscreen. The six bins starting at the playhead span levels 1..5 dots.
    pwav = bytearray(800)
    pwav[2 * 199] = 31
    pwav[2 * 200:2 * 206:2] = bytes([8, 12, 16, 20, 25, 31])
    pwav[2 * 399] = 31
    mem.write(WF_PWAV, bytes(pwav))
    overview = bytes([((i % 15) + 1) for i in range(100)])
    mem.write(WF_OVERVIEW, overview)
    mem.put32(TRACK_LEN, 4800)
    mem.put32(PLAY_FRAME, 2400)
    mem.put32(DISPLAY_MODE, 10)
    mem.write(INFO_CURSOR, b"\x00")

    # Sentinel in row 0 text to verify waveform_hook never touches text rows
    sentinel = b"KEEP_INFO_ORDER"
    mem.write(ROWS, sentinel)

    # Zoom 0 (close, 3 frames/col): each bin takes 4 columns; out-of-range / silent columns are 0.
    _call(p, mem, "waveform_hook", 0, 0)
    samples_z0 = mem.read(WF_SAMPLES, 100)
    assert all(0 <= b <= 15 for b in samples_z0)
    assert [(b + 2) // 3 for b in samples_z0] == [
        1, 1, 1, 1, 2, 2, 2, 2, 2, 2, 2, 2,
        3, 3, 3, 3, 4, 4, 4, 4, 5, 5, 5, 5,
    ] + [0] * 76
    assert mem.u32(WF_CHANGED) == 1
    assert mem.read(ROWS, len(sentinel)) == sentinel

    # First CCW at cursor 0 -> zoom 1 (mid, 6 frames/col): each bin takes 2 columns.
    mem.write(sp + 0x1C, b"\x00")
    _call(p, mem, "waveform_rotary_hook", 0, sp)
    assert mem.u32(_sym(p, "waveform_zoom")) == 1
    mem.put32(WF_CHANGED, 0)
    _call(p, mem, "waveform_hook", 0, 0)
    samples_z1 = mem.read(WF_SAMPLES, 100)
    assert all(0 <= b <= 15 for b in samples_z1)
    assert [(b + 2) // 3 for b in samples_z1] == [
        1, 1, 2, 2, 2, 2, 3, 3, 4, 4, 5, 5,
    ] + [0] * 88
    assert mem.u32(WF_CHANGED) == 1

    # Second CCW at cursor 0 -> zoom 2 (medium, 12 frames/col): each bin takes 1 column.
    mem.write(sp + 0x1C, b"\x00")
    _call(p, mem, "waveform_rotary_hook", 0, sp)
    assert mem.u32(_sym(p, "waveform_zoom")) == 2
    mem.put32(WF_CHANGED, 0)
    _call(p, mem, "waveform_hook", 0, 0)
    samples_z2 = mem.read(WF_SAMPLES, 100)
    assert all(0 <= b <= 15 for b in samples_z2)
    assert [(b + 2) // 3 for b in samples_z2] == [1, 2, 2, 3, 4, 5] + [0] * 94
    assert mem.u32(WF_CHANGED) == 1

    # At the final frame, column 0 still shows audio and future columns are 0 (0 dots).
    mem.put32(PLAY_FRAME, 4799)
    _call(p, mem, "waveform_hook", 0, 0)
    assert mem.read(WF_SAMPLES, 100) == bytes([15] + [0] * 99)

    # Third CCW at cursor 0 -> zoom 3 (whole-track overview restored from 0x0CE65B82)
    mem.write(sp + 0x1C, b"\x00")
    _call(p, mem, "waveform_rotary_hook", 0, sp)
    assert mem.u32(_sym(p, "waveform_zoom")) == 3
    mem.put32(WF_CHANGED, 0)
    _call(p, mem, "waveform_hook", 0, 0)
    assert mem.read(WF_SAMPLES, 100) == overview
    assert mem.u32(WF_CHANGED) == 1

    # Fourth CCW clamps at zoom 3
    mem.write(sp + 0x1C, b"\x00")
    _call(p, mem, "waveform_rotary_hook", 0, sp)
    assert mem.u32(_sym(p, "waveform_zoom")) == 3

    # CW at cursor 0 zooms back in 3 -> 2 -> 1 -> 0, consuming CW click (dir_p -> 0)
    for want_z in (2, 1, 0):
        mem.write(sp + 0x1C, b"\x01")
        _call(p, mem, "waveform_rotary_hook", 0, sp)
        assert mem.u32(_sym(p, "waveform_zoom")) == want_z
        assert mem.read(sp + 0x1C, 1)[0] == 0

    # CW at cursor 0 while zoom == 0 -> leaves dir_p == 1 so stock advances cursor
    mem.write(sp + 0x1C, b"\x01")
    _call(p, mem, "waveform_rotary_hook", 0, sp)
    assert mem.u32(_sym(p, "waveform_zoom")) == 0
    assert mem.read(sp + 0x1C, 1)[0] == 1

    # At cursor >= 1, rotary hook does nothing
    mem.write(INFO_CURSOR, b"\x01")
    mem.write(sp + 0x1C, b"\x00")
    _call(p, mem, "waveform_rotary_hook", 0, sp)
    assert mem.u32(_sym(p, "waveform_zoom")) == 0


@needs_toolchain
def test_interpreter_medium_waveform_preserves_detail_during_pcm_refill():
    p = payloadbuild.build(MODS, "cdj850")
    mem = _mem(p)
    mem.write(SETTINGS + 0x22, b"\x56")

    # Ring slot N holds frame N, and every populated slot belongs to track 7.
    mem.add(0x0C00851E, bytes.fromhex("6043 000b 0009"))  # mov r4,r0; rts; nop
    mem.add(0x0C00586C, bytes.fromhex("e007 000b 0009"))  # mov #7,r0; rts; nop
    loud = struct.pack(">h", 0x6000) * (PCM_SECTOR_BYTES // 2)
    quiet = struct.pack(">h", 0x1000) * (PCM_SECTOR_BYTES // 2)
    mem.add(PCM_RING, loud + quiet * 12)
    mem.write(RING_READ, struct.pack(">H", 0))
    mem.write(RING_WRITE, struct.pack(">H", 13))
    mem.write(INFO_TRACK, struct.pack(">h", 7))
    mem.write(WF_PWAV, bytes([31, 0]) * 400)
    mem.put32(TRACK_LEN, 4800)
    mem.put32(DISPLAY_MODE, 10)
    mem.put32(_sym(p, "waveform_zoom"), 2)

    _call(p, mem, "waveform_hook", 0, 0)
    detailed = mem.read(WF_SAMPLES, 100)
    # A transient in column 0 and loud PWAV in the partially filled column 1
    # cannot max out those columns. Column 2 has only PWAV available.
    assert 1 <= detailed[0] < 13, detailed[:3]
    assert 1 <= detailed[1] < 10, detailed[:3]
    assert detailed[2] == 15, detailed[:3]

    mem.write(RING_WRITE, struct.pack(">H", 0))
    mem.put32(TICK_MS, 100)
    mem.put32(PLAY_FRAME, 3)
    mem.write(WF_SAMPLES, bytes(100))  # stock stager overwrote the previous image
    _call(p, mem, "waveform_hook", 0, 0)
    assert mem.read(WF_SAMPLES, 100) == detailed

    # A sustained gap must still fall back to the available waveform.
    mem.put32(TICK_MS, 250)
    _call(p, mem, "waveform_hook", 0, 0)
    assert mem.read(WF_SAMPLES, 100) == bytes([15]) * 100

    # A seek to a different part of the same track should update immediately.
    mem.write(RING_WRITE, struct.pack(">H", 13))
    mem.put32(PLAY_FRAME, 0)
    _call(p, mem, "waveform_hook", 0, 0)
    assert mem.read(WF_SAMPLES, 100) == detailed
    mem.put32(PLAY_FRAME, 1200)
    _call(p, mem, "waveform_hook", 0, 0)
    assert mem.read(WF_SAMPLES, 100) == bytes([15]) * 100


def _red_cols(packet):
    return [c for c in range(100) if packet[2 + c // 8] & (0x80 >> (c % 8))]


@needs_toolchain
def test_interpreter_playhead_setting_and_red_marker():
    p = payloadbuild.build(MODS, "cdj850")
    mem = _mem(p)
    mem.add(0x0C1E1078, bytes(0x20))
    mem.add(TX_BASE, bytes(TX_LAST - TX_BASE + 4))
    # Stock stubs for settings save, display refresh and construction of the cue packet.
    mem.add(0x0C08E9B0, bytes.fromhex("e000 000b 0009"))
    mem.add(0x0C06F71C, bytes.fromhex("000b 0009"))
    mem.add(0x0C0664A8, bytes.fromhex("d102 e001 2102 000b 0009 0009 0c9d0ac0"))

    # Default is LEFT 1/3 (column 33).
    mem.put32(TRACK_LEN, 1200)
    mem.put32(PLAY_FRAME, 312)
    mem.put32(DISPLAY_MODE, 10)
    mem.write(WF_PWAV + 104 * 2, b"\x1f")
    _call(p, mem, "waveform_hook", 0, 0)
    assert mem.read(WF_SAMPLES, 100) == bytes([0] * 33 + [15] + [0] * 66)

    base_packet = bytearray(16)
    base_packet[:3] = bytes([0x60, 1, 0x80])  # a genuine saved cue at column 0
    mem.write(TX_BASE + 16, base_packet)
    _call(p, mem, "waveform_center_marker")
    # Zoomed view clears stock memory cues and shows only the playhead marker.
    assert _red_cols(mem.read(TX_BASE + 16, 16)) == [33]

    # Active loop in close zoom (step = 3, base = 312 - 33*3 = 213):
    # frames [303..330) -> columns (303-213)//3 = 30 .. (329-213)//3 = 38,
    # with playhead column 33 dark/inverted inside the lit loop span.
    mem.write(LOOP_ACTIVE, b"\x01")
    mem.put32(LOOP_IN_FRAME, 303)
    mem.put32(LOOP_OUT_FRAME, 330)
    mem.put32(TX_LAST, 0)
    mem.write(TX_BASE + 16, base_packet)
    _call(p, mem, "waveform_center_marker")
    assert _red_cols(mem.read(TX_BASE + 16, 16)) == [c for c in range(30, 39) if c != 33]

    # Resized loop via LOOP_OUT_REQUEST updates the red loop span immediately:
    # frames [303..345) -> columns 30..43, playhead column 33 dark/inverted.
    mem.put32(LOOP_OUT_REQUEST, 345)
    mem.put32(TX_LAST, 0)
    mem.write(TX_BASE + 16, base_packet)
    _call(p, mem, "waveform_center_marker")
    assert _red_cols(mem.read(TX_BASE + 16, 16)) == [c for c in range(30, 44) if c != 33]

    # Mid and medium zoom also clear stock memory cues and invert playhead in loop.
    for z, want_cols in ((1, [c for c in range(31, 39) if c != 33]), (2, [32, 34, 35])):
        mem.put32(_sym(p, "waveform_zoom"), z)
        mem.put32(TX_LAST, 0)
        mem.write(TX_BASE + 16, base_packet)
        _call(p, mem, "waveform_center_marker")
        assert _red_cols(mem.read(TX_BASE + 16, 16)) == want_cols

    # Zoomed out to overview: 0x60 behaves like stock (saved memory cue preserved).
    mem.put32(_sym(p, "waveform_zoom"), 3)
    mem.put32(TX_LAST, 0)
    mem.write(TX_BASE + 16, base_packet)
    _call(p, mem, "waveform_center_marker")
    assert _red_cols(mem.read(TX_BASE + 16, 16)) == [0]

    mem.put32(_sym(p, "waveform_zoom"), 0)
    mem.write(LOOP_ACTIVE, b"\x00")

    # Cycle UTILITY: item 7 is WAVEFORM, item 8 is WAVE PLAYHEAD (LEFT 1/3 -> CENTER -> LEFT -> LEFT 1/3).
    mem.put32(DISPLAY_MODE, 12)
    mem.write(UTILITY_LEVEL, b"\x02")
    mem.write(UTILITY_CURSOR, b"\x06")  # VERSION No.
    _call(p, mem, "c850_ui_utility_scroll", 1, 2)
    assert mem.read(UTILITY_CURSOR, 1) == b"\x08"
    _call(p, mem, "waveform_hook", 0, 0)
    assert mem.read(ROWS + 0x105 + 5, 13) == b"WAVE PLAYHEAD"
    assert mem.read(ROWS + 0x20A + 4, 9) == b"\x08LEFT 1/3"

    # Press 1 -> CENTER (+0x21 = 0x51, +0x22 = 0)
    _call(p, mem, "c850_ui_utility_select", 3, 8)
    assert mem.read(SETTINGS + 0x21, 2) == b"\x51\x00"
    _call(p, mem, "waveform_hook", 0, 0)
    assert mem.read(ROWS + 0x20A + 4, 7) == b"\x06CENTER"

    mem.put32(DISPLAY_MODE, 10)
    _call(p, mem, "waveform_hook", 0, 0)
    assert mem.read(WF_SAMPLES, 100) == bytes([0] * 50 + [15] + [0] * 49)
    mem.put32(TX_LAST, 0)
    mem.write(TX_BASE + 16, base_packet)
    _call(p, mem, "waveform_center_marker")
    assert _red_cols(mem.read(TX_BASE + 16, 16)) == [50]

    # Press 2 -> LEFT (+0x21 = 0, +0x22 = 0x56)
    mem.put32(DISPLAY_MODE, 12)
    _call(p, mem, "c850_ui_utility_select", 3, 8)
    assert mem.read(SETTINGS + 0x21, 2) == b"\x00\x56"
    _call(p, mem, "waveform_hook", 0, 0)
    assert mem.read(ROWS + 0x20A + 4, 5) == b"\x04LEFT"
    _call(p, mem, "c850_ui_utility_scroll", 0, 2)
    assert mem.read(UTILITY_CURSOR, 1) == b"\x06"

    mem.put32(DISPLAY_MODE, 10)
    _call(p, mem, "waveform_hook", 0, 0)
    assert mem.read(WF_SAMPLES, 100) == bytes([15] + [0] * 99)
    mem.put32(TX_LAST, 0)
    mem.write(TX_BASE + 16, base_packet)
    _call(p, mem, "waveform_center_marker")
    assert _red_cols(mem.read(TX_BASE + 16, 16)) == [0]

    # Active loop in LEFT mode: playhead column 0 is dark/inverted inside the red loop bar.
    mem.write(LOOP_ACTIVE, b"\x01")
    mem.put32(LOOP_IN_FRAME, 303)
    mem.put32(LOOP_OUT_FRAME, 330)
    mem.put32(LOOP_OUT_REQUEST, 0)
    mem.put32(TX_LAST, 0)
    mem.write(TX_BASE + 16, base_packet)
    _call(p, mem, "waveform_center_marker")
    assert _red_cols(mem.read(TX_BASE + 16, 16)) == [1, 2, 3, 4, 5]
    mem.write(LOOP_ACTIVE, b"\x00")

    # Press 3 -> back to LEFT 1/3 (+0x21 = 0, +0x22 = 0)
    mem.put32(DISPLAY_MODE, 12)
    _call(p, mem, "c850_ui_utility_select", 3, 8)
    assert mem.read(SETTINGS + 0x21, 2) == b"\x00\x00"


@needs_toolchain
def test_interpreter_cue_markers_hotcues_loop_in_and_bottom_cue_bar():
    p = payloadbuild.build(MODS, "cdj850")
    mem = _mem(p)
    mem.add(0x0C1E1078, bytes(0x20))
    mem.add(TX_BASE, bytes(TX_LAST - TX_BASE + 4))
    mem.add(0x0CE649D0, bytes(0x400))                      # ANLZ_CTX + MEMCUE_LIST
    mem.add(0x0CE65C0C, bytes(0x30))                       # JUMP_TARGET
    mem.add(0x0CE6D140, bytes(0x20))                       # cue alt sel + CUE_FRAME
    mem.write(0x0CE6D25A, b"\x01")                         # USB mode
    mem.add(0x0C0664A8, bytes.fromhex("d102 e001 2102 000b 0009 0009 0c9d0ac0"))
    mem.add(0x0C0642CC, bytes.fromhex("d102 e02a 2100 000b 0009 0009 0c9d0aef"))  # sets 0x0C9D0AEF = 42

    mem.put32(TRACK_LEN, 1200)
    mem.put32(PLAY_FRAME, 312)                             # LEFT 1/3 (col 33), step 3 -> base 213
    mem.put32(DISPLAY_MODE, 10)
    _call(p, mem, "waveform_hook", 0, 0)

    # 1. Active cue off-screen -> bottom strip cleared to 0; on-screen -> zoomed column
    mem.write(0x0CE6D0EC, b"\x01")                         # CUE_OR_LOOP_EXISTS = 1
    mem.put32(LOOP_IN_FRAME, 50)                           # off-screen (< 213)
    _call(p, mem, "waveform_cue_column")
    assert mem.read(0x0C9D0AEF, 1)[0] == 0
    mem.put32(LOOP_IN_FRAME, 303)                          # col (303-213)//3 + 1 = 31
    _call(p, mem, "waveform_cue_column")
    assert mem.read(0x0C9D0AEF, 1)[0] == 31

    # 2. Overview zoom leaves stock 0x0C9D0AEF (42) untouched
    mem.put32(_sym(p, "waveform_zoom"), 3)
    _call(p, mem, "waveform_cue_column")
    assert mem.read(0x0C9D0AEF, 1)[0] == 42
    mem.put32(_sym(p, "waveform_zoom"), 0)

    # 3. Pending Loop-In marker (frame 360 -> 1-based col 50) displays on the
    #    bottom white bar; top red strip only shows playhead (ignoring memory
    #    cues, hot cues, and pending loop-in).
    mem.write(0x0CE65850, struct.pack(">H", 1))            # MEMCUE_COUNT = 1
    mem.put32(0x0CE649D4, 0x1000)                          # point cue
    mem.put32(0x0CE649D4 + 12, 243)                        # col 10
    ctx = 0x0D000000
    c = bytearray(0x1500)
    struct.pack_into(">I", c, 0, 0xBFA14C7E)
    struct.pack_into(">I", c, 0x1414, 0x9768A153)
    struct.pack_into(">H", c, 0xF90 + 0x0E, 1)
    c[0xF90 + 0x1C] = 2                                    # loop hot cue A
    struct.pack_into(">HII", c, 0xF90 + 0x1E, 150, 255 * 2, 270 * 2)
    mem.add(ctx, c)
    mem.put32(0x0CE649D0, ctx)
    mem.write(0x0CE6D0EE, b"\x00")
    mem.write(0x0CE6D0F0, b"\x01")
    mem.put32(LOOP_IN_FRAME, 360)                          # col 49 (1-based 50)

    _call(p, mem, "waveform_cue_column")
    assert mem.read(0x0C9D0AEF, 1)[0] == 50

    base_packet = bytearray(16)
    base_packet[:2] = bytes([0x60, 1])
    mem.put32(TX_LAST, 0)
    mem.write(TX_BASE + 16, base_packet)
    _call(p, mem, "waveform_center_marker")
    assert _red_cols(mem.read(TX_BASE + 16, 16)) == [33]


@needs_toolchain
def test_interpreter_beat_markers_on_red_strip():
    p = payloadbuild.build(MODS, "cdj850")
    mem = _mem(p)
    mem.add(0x0C1E1078, bytes(0x20))
    mem.add(TX_BASE, bytes(TX_LAST - TX_BASE + 4))
    mem.add(0x0CE649D0, bytes(0x400))
    mem.write(0x0CE6D25A, b"\x01")  # USB mode
    mem.add(0x0C0664A8, bytes.fromhex("d102 e001 2102 000b 0009 0009 0c9d0ac0"))

    # 125 BPM (480 ms = 36 frames per beat). 16 beats starting at 0 ms.
    mem.put32(GRID_CTX + 0x70, 1)
    mem.put32(GRID_CTX + 0x74, 16)
    for i in range(16):
        mem.write(GRID_CTX + 0x7C + i * 8, struct.pack(">HHI", (i % 4) + 1, 12500, i * 480))

    mem.put32(TRACK_LEN, 1200)
    mem.put32(PLAY_FRAME, 300)  # LEFT 1/3 (col 33)
    mem.put32(DISPLAY_MODE, 10)
    _call(p, mem, "waveform_hook", 0, 0)

    base_packet = bytearray(16)
    base_packet[:2] = bytes([0x60, 1])

    # Zoom 0 (close, step=3, base = 300 - 99 = 201, window 201..501):
    # beats at 216(col 5), 252(17), 288(29), 324(41), 360(53), 396(65), 432(77), 468(89)
    # plus always-lit playhead at col 33.
    mem.put32(TX_LAST, 0)
    mem.write(TX_BASE + 16, base_packet)
    _call(p, mem, "waveform_center_marker")
    assert _red_cols(mem.read(TX_BASE + 16, 16)) == [5, 17, 29, 33, 41, 53, 65, 77, 89]

    # When playhead lands directly on a beat (frame 288), playhead col 33 stays lit!
    mem.put32(PLAY_FRAME, 288)
    _call(p, mem, "waveform_hook", 0, 0)
    mem.put32(TX_LAST, 0)
    mem.write(TX_BASE + 16, base_packet)
    _call(p, mem, "waveform_center_marker")
    assert _red_cols(mem.read(TX_BASE + 16, 16)) == [9, 21, 33, 45, 57, 69, 81, 93]

    # Active loop [288..372) -> cols 33..60: playhead (col 33) and beats at 324 (col 45)
    # and 360 (col 57) are all dark/inverted inside the loop bar. Saved memory cues stay
    # off the red bar.
    mem.write(LOOP_ACTIVE, b"\x01")
    mem.put32(LOOP_IN_FRAME, 288)
    mem.put32(LOOP_OUT_FRAME, 372)
    mem.write(0x0CE65850, struct.pack(">H", 1))
    mem.put32(0x0CE649D4, 0x1000)
    mem.put32(0x0CE649D4 + 12, 342)
    mem.put32(TX_LAST, 0)
    mem.write(TX_BASE + 16, base_packet)
    _call(p, mem, "waveform_center_marker")
    want_loop = [c for c in range(33, 61) if c not in (33, 45, 57)]
    assert _red_cols(mem.read(TX_BASE + 16, 16)) == [9, 21] + want_loop + [69, 81, 93]
    mem.write(LOOP_ACTIVE, b"\x00")
    mem.write(0x0CE65850, struct.pack(">H", 0))

    # Zoom 1 (mid, step=6, base = 288 - 198 = 90, window 90..690):
    # beats at 108(3), 144(9), 180(15), 216(21), 252(27), 288(33), 324(39), 360(45),
    # 396(51), 432(57), 468(63), 504(69), 540(75)
    mem.put32(_sym(p, "waveform_zoom"), 1)
    _call(p, mem, "waveform_hook", 0, 0)
    mem.put32(TX_LAST, 0)
    mem.write(TX_BASE + 16, base_packet)
    _call(p, mem, "waveform_center_marker")
    assert _red_cols(mem.read(TX_BASE + 16, 16)) == list(range(3, 76, 6))

    # Zoom 2 (medium, step=12): no beat markers, only playhead 33.
    mem.put32(_sym(p, "waveform_zoom"), 2)
    _call(p, mem, "waveform_hook", 0, 0)
    mem.put32(TX_LAST, 0)
    mem.write(TX_BASE + 16, base_packet)
    _call(p, mem, "waveform_center_marker")
    assert _red_cols(mem.read(TX_BASE + 16, 16)) == [33]


@needs_toolchain
def test_interpreter_quiet_master_adaptation_and_zero_tx():
    p = payloadbuild.build(MODS, "cdj850")
    mem = _mem(p)
    mem.add(TX_BASE, bytes(TX_LAST - TX_BASE + 4))
    mem.write(SETTINGS + 0x22, b"\x56")  # LEFT playhead (col 0)
    mem.add(0x0C00851E, bytes.fromhex("6043 000b 0009"))  # mov r4,r0; rts; nop
    mem.add(0x0C00586C, bytes.fromhex("e007 000b 0009"))  # mov #7,r0; rts; nop
    mem.add(0x0C0662AE, bytes.fromhex("e02a 000b 0009"))  # stock panel_tx_waveform stub returning 42

    # Build PCM ring with:
    # - slots 0..2 (col 0): silence (0)
    # - slots 3..5 (col 1): quiet sustain (0x0280)
    # - slots 6..8 (col 2): quiet transient hit (0x0A00 peak on sustain)
    # - slots 9..11 (col 3): loud peak (0x6000)
    silent = bytes(PCM_SECTOR_BYTES)
    q_sustain = struct.pack(">h", 0x0280) * (PCM_SECTOR_BYTES // 2)
    q_hit = struct.pack(">h", 0x0A00) * (PCM_SECTOR_BYTES // 2)
    loud = struct.pack(">h", 0x6000) * (PCM_SECTOR_BYTES // 2)
    mem.add(PCM_RING, silent * 3 + q_sustain * 3 + q_hit * 3 + loud * 3 + bytes(12 * PCM_SECTOR_BYTES))
    mem.write(RING_READ, struct.pack(">H", 0))
    mem.write(RING_WRITE, struct.pack(">H", 12))
    mem.write(INFO_TRACK, struct.pack(">h", 7))
    mem.put32(TRACK_LEN, 4800)
    mem.put32(DISPLAY_MODE, 10)

    _call(p, mem, "waveform_hook", 0, 0)
    wf = mem.read(WF_SAMPLES, 100)
    # Silence = 0 dots; quiet sustain = 1 dot (1..3); quiet hit = 2 dots (4..6); loud = 5 dots (13..15)
    assert wf[0] == 0, wf[:5]
    assert 1 <= wf[1] <= 3, wf[:5]
    assert 4 <= wf[2] <= 6, wf[:5]
    assert 13 <= wf[3] <= 15, wf[:5]

    # waveform_tx_waveform sends 0 bytes mid-strip without truncating later columns!
    mem.put32(0x0C9D0AF8, 10)
    mem.put32(0x0C9D0B04, 0x200)
    mem.put32(TX_LAST, 0)
    mem.put32(WF_CHANGED, 1)
    _call(p, mem, "waveform_tx_waveform")
    assert mem.u32(TX_LAST) == 8
    f70 = mem.read(TX_BASE + 16, 16)
    assert f70[0] == 0x70 and f70[3] == 0 and f70[4] == wf[1] and f70[5] == wf[2] and f70[6] == wf[3]

    # Overview mode delegates to stock panel_tx_waveform (stub returns 42).
    mem.put32(_sym(p, "waveform_zoom"), 3)
    assert _call(p, mem, "waveform_tx_waveform") == 42

    # Quiet master adaptation: track 8 peaks at 0x1000 (-18 dBFS), with PWAV = 31 confirming peak.
    mem.put32(_sym(p, "waveform_zoom"), 0)
    mem.write(0x0C00586C, bytes.fromhex("e008 000b 0009"))  # mov #8,r0; rts; nop
    mem.write(INFO_TRACK, struct.pack(">h", 8))
    q_master_peak = struct.pack(">h", 0x1000) * (PCM_SECTOR_BYTES // 2)
    mem.write(PCM_RING, q_master_peak * 24)
    mem.write(RING_WRITE, struct.pack(">H", 24))
    mem.write(WF_PWAV, bytes([31, 0]) * 400)
    _call(p, mem, "waveform_hook", 0, 0)
    wf_qm = mem.read(WF_SAMPLES, 100)
    assert wf_qm[0] >= 10, wf_qm[:8]  # adapted up to 4..5 dots instead of sitting flat!


# ---- live ----

def _udp(deck, msg):
    import cdj_panelsock
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.sendto(msg.encode(), (cdj_panelsock.HOST, cdj_panelsock.port_for(deck.keysock)))
    s.close()


@needs_toolchain
@pytest.mark.skipif(not FLASH.is_file(), reason="extract/cdj850/flash_cdj850.bin is not present")
def test_live_waveform_zoom_and_info_order_coexistence(tmp_path):
    """Boot waveform + info_order on QEMU: INFO Page 1 keeps info_order rows
    intact while streaming a live scrolling waveform; CCW zooms out (close ->
    mid -> medium -> whole-track overview); CW zooms back in and then scrolls
    to info_order Page 2."""
    if os.name == "nt":
        pytest.skip("QMP socket descriptor inheritance requires POSIX")
    import importlib.util
    if not BAKEN.is_file() or importlib.util.find_spec("pyfatfs") is None or importlib.util.find_spec("numpy") is None:
        pytest.skip("tools/baken/baken, pyfatfs and numpy are needed")
    sys.path.insert(0, str(ROOT / "scripts" / "debug"))
    import cdj850_trace as tr

    stick = tmp_path / "waveform.img"
    tr.build_stick(stick, {
        "device_name": "WAVEFM",
        "playlist": "Wave",
        "tracks": [{"title": "GRID128", "bpm": 128, "first_downbeat": 0, "bars": 32}],
    })
    flash = tmp_path / "flash.bin"
    combo = MODS + ["info_order", "info_order_rating"]
    flash.write_bytes(patch_main.patch_flash(FLASH.read_bytes(), combo, "cdj850"))
    p = payloadbuild.build(combo, "cdj850")
    sym_zoom = _sym(p, "waveform_zoom")
    sym_updates = _sym(p, "waveform_updates")
    sym_pcm_hits = _sym(p, "waveform_pcm_hits")

    def snap(d):
        d.q("stop")
        wf = d.dump(WF_SAMPLES, 100)
        ov = d.dump(WF_OVERVIEW, 100)
        seq = d.rb(WF_SEQ)
        cur = d.rb(INFO_CURSOR)
        mode = d.rw(DISPLAY_MODE)
        zoom = d.rw(sym_zoom)
        updates = d.rw(sym_updates)
        pcm_hits = d.rw(sym_pcm_hits)
        rows = []
        for r in range(3):
            b = d.dump(ROWS + r * 0x105, 0x30)
            rows.append((b[1], b[5:5 + b[4]].decode("latin1")))
        d.q("cont")
        return mode, cur, zoom, seq, updates, pcm_hits, wf, ov, rows

    def wait_for_snap(d, ready):
        deadline = time.monotonic() + 30
        while True:
            state = snap(d)
            if ready(state):
                return state
            assert time.monotonic() < deadline, state
            time.sleep(0.2)

    with tr.Deck(stick, flash=flash) as d:
        d.wait_line("mode=0x0a", "[GRID128]", timeout=300)
        time.sleep(1.5)
        if d.rb(PLAYER_STATE_BASE + 2) != 3:
            d.key("play")
            time.sleep(1.5)

        mode, cur, zoom, seq1, upd1, pcm1, wf1, ov1, rows1 = snap(d)
        assert mode == 10 and cur == 0 and zoom == 0, (mode, cur, zoom)
        # info_order Page 1 row 1 (BPM/key/stars) is intact
        assert rows1[0][1] == "GRID128", rows1
        assert rows1[1][0] == 0x0D and "128.00" in rows1[1][1], rows1
        # Live scrolling waveform uses PCM ring and 0..15 strip range
        assert all(0 <= b <= 15 for b in wf1) and any(b > 0 for b in wf1), list(wf1)
        assert upd1 > 0 and pcm1 > 0, (upd1, pcm1)

        # Wait for playback to change the waveform and for SCIF1 to send it.
        wait_for_snap(d, lambda s: s[3] != seq1 and s[4] > upd1)
        d.q("stop")
        cue_packet = d.dump(TX_BASE + 16 * d.rw(TX_LAST), 16)
        d.q("cont")
        # Close zoom shows playhead (col 33) + beat markers on 0x60 red strip.
        assert cue_packet[0] == 0x60 and cue_packet[6] & 0x40, cue_packet.hex()
        assert len(_red_cols(cue_packet)) >= 6, _red_cols(cue_packet)
        d.q("stop")
        assert d.rb(0x0C9D0AEF) == 0                     # off-screen active cue cleared on bottom bar
        d.q("cont")

        # Start a 1-beat loop and verify the red strip lights a multi-column span
        # with playhead col 33 dark/inverted inside the loop span.
        d.key("beat_1")
        deadline = time.monotonic() + 15
        while True:
            d.q("stop")
            loop_active = d.rb(LOOP_ACTIVE)
            cue_packet = d.dump(TX_BASE + 16 * d.rw(TX_LAST), 16)
            d.q("cont")
            if loop_active == 1 and cue_packet[0] == 0x60:
                cols = _red_cols(cue_packet)
                if len(cols) >= 5 and 33 not in cols:
                    break
            assert time.monotonic() < deadline, (loop_active, cue_packet.hex())
            time.sleep(0.2)
        d.key("reloop")

        # 1 click CCW on Page 1 -> zoom 1 (mid, 8 s window, beat markers still shown)
        _udp(d, "0x03:-1:0:rot")
        mode_mid, cur_mid, zoom_mid, _, _, _, wf_mid, _, _ = wait_for_snap(d, lambda s: s[2] == 1)
        assert mode_mid == 10 and cur_mid == 0 and zoom_mid == 1, (mode_mid, cur_mid, zoom_mid)
        assert all(0 <= b <= 15 for b in wf_mid)
        time.sleep(0.2)
        d.q("stop")
        cue_mid = d.dump(TX_BASE + 16 * d.rw(TX_LAST), 16)
        d.q("cont")
        assert cue_mid[0] == 0x60 and len(_red_cols(cue_mid)) >= 8, _red_cols(cue_mid)

        # 2nd click CCW -> zoom 2 (medium, 16 s window, no beat markers)
        _udp(d, "0x03:-1:0:rot")
        mode_m, cur_m, zoom_m, _, _, _, wf_m, _, _ = wait_for_snap(d, lambda s: s[2] == 2)
        assert mode_m == 10 and cur_m == 0 and zoom_m == 2, (mode_m, cur_m, zoom_m)
        assert all(0 <= b <= 15 for b in wf_m)
        time.sleep(0.2)
        d.q("stop")
        cue_m = d.dump(TX_BASE + 16 * d.rw(TX_LAST), 16)
        d.q("cont")
        assert cue_m[0] == 0x60 and _red_cols(cue_m) == [33], _red_cols(cue_m)

        # 3rd click CCW -> zoom 3 (whole-track overview matching 0x0CE65B82)
        _udp(d, "0x03:-1:0:rot")
        mode_o, cur_o, zoom_o, _, _, _, wf_o, ov_o, _ = wait_for_snap(
            d, lambda s: s[2] == 3 and s[6] == s[7]
        )
        assert mode_o == 10 and cur_o == 0 and zoom_o == 3, (mode_o, cur_o, zoom_o)
        assert wf_o == ov_o

        # 3 clicks CW -> zooms back in (3 -> 2 -> 1 -> 0) without leaving cursor 0
        for want_z in (2, 1, 0):
            _udp(d, "0x03:1:0:rot")
            _, cur_b, zoom_b, _, _, _, _, _, _ = wait_for_snap(d, lambda s: s[2] == want_z)
            assert cur_b == 0 and zoom_b == want_z, (cur_b, zoom_b, want_z)

        # 2 more clicks CW -> advances cleanly to info_order Page 2 (cursor 2, mode 11)
        for want_cursor in (1, 2):
            _udp(d, "0x03:1:0:rot")
            wait_for_snap(d, lambda s: s[1] == want_cursor)
        mode_p2, cur_p2, zoom_p2, _, _, _, _, _, rows_p2 = wait_for_snap(
            d, lambda s: s[0] == 11 and s[8][0][0] == 0x07
        )
        assert mode_p2 == 11 and cur_p2 == 2 and zoom_p2 == 0, (mode_p2, cur_p2, zoom_p2)
        assert rows_p2[0][0] == 0x07, rows_p2  # info_order Page 2 starts with Artist (0x07)

    utility_script = "3800:menu:1700,6500:rot+6,7500:rot+3,8500:enter:200,9500:enter:200"
    with tr.Deck(stick, flash=flash, script=utility_script, writable=True) as d:
        d.wait_line("[.UTILITY.]", "[A.CUE LEVEL]", timeout=60)
        d.wait_line("[VERSION No.]", timeout=60)
        d.wait_line("[WAVE PLAYHEAD]", "[LEFT 1/3]", timeout=60)
        d.wait_line("[WAVE PLAYHEAD]", "[CENTER]", timeout=60)
        d.wait_line("[WAVE PLAYHEAD]", "[LEFT]", timeout=60)
        d.wait_line("input release enter", timeout=60)
        assert d.rb(SETTINGS + 0x22) & 0x04

    with tr.Deck(stick, flash=flash, script="3800:menu:1700,6500:rot+9") as d:
        d.wait_line("[WAVE PLAYHEAD]", "[LEFT]", timeout=120)
