# SPDX-License-Identifier: GPL-2.0-or-later
"""waveform on the CDJ-850 MAIN 1.13: zoomed scrolling half-waveform on INFO Page 1.

* static: hooks at display_stage_bpm tail (0x0C0642AA) and INFO rotary branch
  (0x0C0E782E), both outside the boot-critical closure, zero FPU instructions,
  small stack frames, composes cleanly with info_order and phase_meter;
* interpreter (tests/sh2a.py): logarithmic amplitude mapping, rotary CCW zoom
  out (0 -> 1 -> 2, clamped) and CW zoom back in (2 -> 1 -> 0, consuming CW
  click until back at 0), cursor > 0 untouched, PWAV fallback + overview
  restore, dot-matrix text rows untouched;
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
MODS = ["waveform", "waveform_rotary"]

DISPLAY_MODE = 0x0C9D0AF8
ROWS = 0x0C9D0B18
WF_SEQ = 0x0C9D1184
WF_CHANGED = 0x0C9D1188
WF_SAMPLES = 0x0C9D1199
WF_PWAV = 0x0CE65862
WF_OVERVIEW = 0x0CE65B82
PLAY_FRAME = 0x0C7F647C
RING_READ = 0x0C7F645C
RING_WRITE = 0x0C7F645E
TRACK_LEN = 0x0C7F650C
INFO_CURSOR = 0x0CE6D1E4
SOURCE_INDEX = 0x0CE6D252
PLAYER_STATE_BASE = 0x0CE6D25D

needs_toolchain = pytest.mark.skipif(not payloadbuild.have_toolchain(), reason="sh-elf toolchain not installed")
needs_main = pytest.mark.skipif(not MAIN.is_file(), reason="extract/cdj850/main_unpacked.bin is not present")


# ---- static ----

@needs_main
@needs_toolchain
def test_static_hooks_outside_boot_critical_closure():
    import bootcrit
    stock = MAIN.read_bytes()
    report = {}
    combo = MODS + ["info_order", "info_order_rating", "phase_meter"]
    out = patch_main.patch(stock, combo, "cdj850", report)
    assert report["waveform"][0] == "applied", report
    assert report["waveform_rotary"][0] == "applied", report
    assert report["info_order"][0] == "applied", report
    assert report["phase_meter"][0] == "applied", report

    closure = bootcrit.closure(stock, BASE, byteorder=">")
    assert not closure.hit(BPM_HOOK_SITE - BASE, BPM_HOOK_SITE - BASE + 14)
    assert not closure.hit(ROTARY_HOOK_SITE - BASE, ROTARY_HOOK_SITE - BASE + 12)

    p = payloadbuild.build(combo, "cdj850")
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
    mem.add(0x0CE65860, bytes(0x400))
    mem.add(0x0CE6D1E0, bytes(0x90))
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

    # 1. Amplitude-to-height table: 0 -> 1, quiet -> 2..4, loud -> 15
    assert _call(p, mem, "waveform_amp_to_height", 0) == 1
    assert _call(p, mem, "waveform_amp_to_height", 0x0400) == 2
    assert _call(p, mem, "waveform_amp_to_height", 0x6000) == 15

    # 2. Populate PWAV (400 entries with a peak in the middle) and overview (100 entries)
    pwav = bytearray(800)
    for i in range(400):
        pwav[2 * i] = 31 if (180 <= i < 220) else 6
    mem.write(WF_PWAV, bytes(pwav))
    overview = bytes([((i % 15) + 1) for i in range(100)])
    mem.write(WF_OVERVIEW, overview)
    mem.put32(TRACK_LEN, 9000)  # 120 seconds
    mem.put32(PLAY_FRAME, 4500)  # midpoint (PWAV index 200)
    mem.put32(DISPLAY_MODE, 10)
    mem.write(INFO_CURSOR, b"\x00")

    # Sentinel in row 0 text to verify waveform_hook never touches text rows
    sentinel = b"KEEP_INFO_ORDER"
    mem.write(ROWS, sentinel)

    # Zoom 0 (close, 4 s = 300 frames = PWAV indices ~193..207 -> all inside peak 180..220)
    _call(p, mem, "waveform_hook", 0, 0)
    samples_z0 = mem.read(WF_SAMPLES, 100)
    assert all(1 <= b <= 15 for b in samples_z0)
    assert samples_z0[50] == 15
    assert mem.u32(WF_CHANGED) == 1
    assert mem.read(ROWS, len(sentinel)) == sentinel

    # CCW at cursor 0 -> zoom 1 (medium, 16 s = 1200 frames -> edges outside 180..220)
    mem.write(sp + 0x1C, b"\x00")
    _call(p, mem, "waveform_rotary_hook", 0, sp)
    assert mem.u32(_sym(p, "waveform_zoom")) == 1
    mem.put32(WF_CHANGED, 0)
    _call(p, mem, "waveform_hook", 0, 0)
    samples_z1 = mem.read(WF_SAMPLES, 100)
    assert samples_z1[50] == 15 and samples_z1[0] < 15 and samples_z1[99] < 15
    assert samples_z1 != samples_z0
    assert mem.u32(WF_CHANGED) == 1

    # Second CCW at cursor 0 -> zoom 2 (whole-track overview restored from 0x0CE65B82)
    mem.write(sp + 0x1C, b"\x00")
    _call(p, mem, "waveform_rotary_hook", 0, sp)
    assert mem.u32(_sym(p, "waveform_zoom")) == 2
    mem.put32(WF_CHANGED, 0)
    _call(p, mem, "waveform_hook", 0, 0)
    assert mem.read(WF_SAMPLES, 100) == overview
    assert mem.u32(WF_CHANGED) == 1

    # Third CCW clamps at zoom 2
    mem.write(sp + 0x1C, b"\x00")
    _call(p, mem, "waveform_rotary_hook", 0, sp)
    assert mem.u32(_sym(p, "waveform_zoom")) == 2

    # CW at cursor 0 while zoom == 2 -> zoom 1 and consumes CW click (dir_p -> 0)
    mem.write(sp + 0x1C, b"\x01")
    _call(p, mem, "waveform_rotary_hook", 0, sp)
    assert mem.u32(_sym(p, "waveform_zoom")) == 1
    assert mem.read(sp + 0x1C, 1)[0] == 0

    # CW at cursor 0 while zoom == 1 -> zoom 0 and consumes CW click
    mem.write(sp + 0x1C, b"\x01")
    _call(p, mem, "waveform_rotary_hook", 0, sp)
    assert mem.u32(_sym(p, "waveform_zoom")) == 0
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
    medium -> whole-track overview); CW zooms back in and then scrolls to
    info_order Page 2."""
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
        # Live scrolling waveform is non-zero in all 100 columns and uses PCM ring
        assert all(1 <= b <= 15 for b in wf1), list(wf1)
        assert upd1 > 0 and pcm1 > 0, (upd1, pcm1)

        # Wait ~1.5 s while playing: waveform scrolls and sequence advances
        time.sleep(1.5)
        _, _, _, seq2, upd2, _, wf2, _, _ = snap(d)
        assert upd2 > upd1 and seq2 != seq1, (upd1, upd2, seq1, seq2)

        # 1 click CCW on Page 1 -> zoom 1 (medium, step in between)
        _udp(d, "0x03:-1:0:rot")
        time.sleep(0.8)
        mode_m, cur_m, zoom_m, _, _, _, wf_m, _, _ = snap(d)
        assert mode_m == 10 and cur_m == 0 and zoom_m == 1, (mode_m, cur_m, zoom_m)
        assert all(1 <= b <= 15 for b in wf_m)

        # 1 more click CCW -> zoom 2 (whole-track overview matching 0x0CE65B82)
        _udp(d, "0x03:-1:0:rot")
        time.sleep(0.8)
        mode_o, cur_o, zoom_o, _, _, _, wf_o, ov_o, _ = snap(d)
        assert mode_o == 10 and cur_o == 0 and zoom_o == 2, (mode_o, cur_o, zoom_o)
        assert wf_o == ov_o

        # 2 clicks CW -> zooms back in (2 -> 1 -> 0) without leaving cursor 0
        for want_z in (1, 0):
            _udp(d, "0x03:1:0:rot")
            time.sleep(0.8)
            _, cur_b, zoom_b, _, _, _, _, _, _ = snap(d)
            assert cur_b == 0 and zoom_b == want_z, (cur_b, zoom_b, want_z)

        # 2 more clicks CW -> advances cleanly to info_order Page 2 (cursor 2, mode 11)
        for _ in range(2):
            _udp(d, "0x03:1:0:rot")
            time.sleep(0.8)
        mode_p2, cur_p2, zoom_p2, _, _, _, _, _, rows_p2 = snap(d)
        assert mode_p2 == 11 and cur_p2 == 2 and zoom_p2 == 0, (mode_p2, cur_p2, zoom_p2)
        assert rows_p2[0][0] == 0x07, rows_p2  # info_order Page 2 starts with Artist (0x07)
