# SPDX-License-Identifier: GPL-2.0-or-later
"""phase_meter on the CDJ-850 MAIN 1.13: Beat & phase meter on INFO Page 2.

* static: single hook on display_stage_bpm tail (0x0C0642AA), outside
  boot-critical closure, zero FPU instructions, composes with bpm_lock and
  all branch mods;
* interpreter (tests/sh2a.py): inserts codes 0x13..0x15 at indices 2..4 of
  valid DB records (shifting existing Page 2 items to Page 3, capped at 18),
  computes grid & base-BPM phase with BPM Lock on and off, formats rows using
  only vfd_render.h-supported glyphs;
* live (QEMU + baken stick: GRID128 with Album/Comment/Key/Rating): INFO
  scrolls Page 1 -> Page 2 (Phase Meter visible even with BPM Lock off) ->
  Page 3 (shifted original Page 2 items intact).
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
HOOK_SITE = 0x0C0642AA
MODS = ["phase_meter"]

DB_RECORDS = 0x0C9D44D4
DB_STRIDE = 0x864
INFO_CURSOR, INFO_COUNT, INFO_TRACK = 0x0CE6D1E4, 0x0CE6D1E5, 0x0C9D1D96
DISPLAY_MODE = 0x0C9D0AF8
ROWS = 0x0C9D0B18
SOURCES = 0x0C9D196F
PLAY_FRAME = 0x0C7F647C
BASE_TENTHS = 0x0C9D23EC
PITCH = 0x0CE6CA50
USB_MODE = 0x0CE6D25A
GRID_CTX = 0x0C9D24FC
BL_ACTIVE, BL_PITCH = 0x0CE649AC, 0x0CE649BC

needs_toolchain = pytest.mark.skipif(not payloadbuild.have_toolchain(), reason="sh-elf toolchain not installed")
needs_main = pytest.mark.skipif(not MAIN.is_file(), reason="extract/cdj850/main_unpacked.bin is not present")


# ---- static ----

@needs_main
@needs_toolchain
def test_static_hook_outside_boot_critical_closure():
    import bootcrit
    stock = MAIN.read_bytes()
    report = {}
    out = patch_main.patch(stock, MODS, "cdj850", report)
    assert report["phase_meter"][0] == "applied", report
    closure = bootcrit.closure(stock, BASE, byteorder=">")
    assert not closure.hit(HOOK_SITE - BASE, HOOK_SITE - BASE + 14)
    p = payloadbuild.build(["phase_meter"], "cdj850")
    hook = payloadbuild.lookup(p.symbols, "phase_meter_hook")
    lit = ((HOOK_SITE + 9) & ~3) - BASE
    tramp = struct.unpack(">I", out[lit:lit + 4])[0]
    assert hook.to_bytes(4, "big") in out[tramp - BASE:tramp - BASE + 0x80]


@needs_toolchain
def test_payload_has_no_fpu_code_and_small_frames():
    p = payloadbuild.build(["phase_meter"], "cdj850")
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
    mem.add(0x0C7F6470, bytes(0x20))
    mem.add(0x0C9D0AE0, bytes(0x1800))
    mem.add(0x0C9D23E0, bytes(0x2A00))
    mem.add(DB_RECORDS, bytes(3 * DB_STRIDE))
    mem.add(0x0CE649A0, bytes(0x40))
    mem.add(0x0CE6CA40, bytes(0x20))
    mem.add(0x0CE6D1E0, bytes(0x80))
    mem.write(USB_MODE, b"\x01")
    return mem


def _call(p, mem, name, *args):
    cpu = Sh2a(mem)
    cpu.r[15] = 0x0DE00000
    for i, a in enumerate(args):
        cpu.r[4 + i] = a & 0xFFFFFFFF
    cpu.run(_sym(p, name), Sh2a.RET)
    return cpu.r[0]


@needs_toolchain
def test_db_record_insertion_shifts_page2_to_page3():
    p = payloadbuild.build(["phase_meter"], "cdj850")
    mem = _mem(p)
    items = bytes([0x04, 0x07, 0x02, 0x0D, 0x23, 0x0F, 0x0A])  # Title, Artist, Album, BPM, Comment, Key, Rating
    mem.put32(DB_RECORDS, 3)
    mem.write(DB_RECORDS + 0x14, struct.pack(">H", 1))
    mem.write(DB_RECORDS + 0x851, bytes([len(items)]))
    mem.write(DB_RECORDS + 0x852, items)
    mem.write(INFO_TRACK, struct.pack(">H", 1))
    mem.write(INFO_COUNT, bytes([len(items)]))
    mem.put32(DISPLAY_MODE, 10)

    _call(p, mem, "phase_meter_hook", 0, 0)
    assert mem.read(DB_RECORDS + 0x851, 1)[0] == 10
    assert mem.read(INFO_COUNT, 1)[0] == 10
    assert list(mem.read(DB_RECORDS + 0x852, 10)) == [0x04, 0x07, 0x13, 0x14, 0x15, 0x02, 0x0D, 0x23, 0x0F, 0x0A]
    # Idempotent on second pass
    _call(p, mem, "phase_meter_hook", 0, 0)
    assert mem.u32(_sym(p, "phase_meter_records")) == 1


@needs_toolchain
def test_grid_and_bpm_phase_rendering_with_and_without_lock():
    p = payloadbuild.build(["phase_meter"], "cdj850")
    mem = _mem(p)
    # 120.00 BPM grid (500 ms/beat)
    mem.put32(GRID_CTX + 0x74, 16)
    for i in range(16):
        struct.pack_into(">HHI", bytearray(8), 0, (i % 4) + 1, 12000, i * 500)
        mem.write(GRID_CTX + 0x7C + i * 8, struct.pack(">HHI", (i % 4) + 1, 12000, i * 500))
    mem.write(BASE_TENTHS, struct.pack(">H", 1200))
    mem.put32(DISPLAY_MODE, 11)
    mem.write(INFO_CURSOR, b"\x02")
    mem.write(INFO_COUNT, b"\x08")
    # frame = 54 -> ms = 720 ms -> beat 2 (500..1000 ms), off = 220 ms -> fine = (220*13)//500 = 5
    mem.put32(PLAY_FRAME, 54)
    mem.write(PITCH, struct.pack(">h", 500))   # +5.00% -> 126.00 BPM
    _call(p, mem, "phase_meter_hook", 0, 0)
    rows = [mem.read(ROWS + r * 0x105 + 5, mem.read(ROWS + r * 0x105 + 4, 1)[0]).decode("ascii")
            for r in range(3)]
    assert rows[0] == "B2/4 126.00 GRID"
    assert rows[1] == "_1_ (2) _3_ _4_"
    assert rows[2] == "[-----+-------]"
    # Centered, unhighlighted, icon-free rows (tag 0x11..0x13, attr0 0x00) and mode 0x07
    assert mem.u32(DISPLAY_MODE) == 7
    for r in range(3):
        hdr = mem.read(ROWS + r * 0x105, 4)
        assert hdr[0] == 0x11 + r and hdr[1] == 0x00, (r, hdr)

    # Single-click Page 2 snap transitions: 2 -> 3 => 5 (Page 3), 5 -> 4 => 2 (Page 2)
    mem.put32(DISPLAY_MODE, 11)
    mem.write(INFO_CURSOR, b"\x03")
    _call(p, mem, "phase_meter_hook", 0, 0)
    assert mem.read(INFO_CURSOR, 1)[0] == 5
    mem.put32(DISPLAY_MODE, 11)
    mem.write(INFO_CURSOR, b"\x04")
    _call(p, mem, "phase_meter_hook", 0, 0)
    assert mem.read(INFO_CURSOR, 1)[0] == 2 and mem.u32(DISPLAY_MODE) == 7
    # When INFO_COUNT <= 5 (no Page 3), 2 -> 3 snaps back to 2
    mem.put32(DISPLAY_MODE, 11)
    mem.write(INFO_COUNT, b"\x05")
    mem.write(INFO_CURSOR, b"\x03")
    _call(p, mem, "phase_meter_hook", 0, 0)
    assert mem.read(INFO_CURSOR, 1)[0] == 2 and mem.u32(DISPLAY_MODE) == 7

    # With BPM Lock active (+10.00%), uses BL_PITCH regardless of PITCH fader
    mem.put32(DISPLAY_MODE, 11)
    mem.write(BL_ACTIVE, b"\x01")
    mem.write(BL_PITCH, struct.pack(">h", 1000))
    _call(p, mem, "phase_meter_hook", 0, 0)
    rows_locked = [mem.read(ROWS + r * 0x105 + 5, mem.read(ROWS + r * 0x105 + 4, 1)[0]).decode("ascii")
                   for r in range(3)]
    assert rows_locked[0] == "L2/4 132.00 LOCK" and mem.u32(DISPLAY_MODE) == 7

    # Base-BPM fallback (no grid)
    mem.put32(DISPLAY_MODE, 11)
    mem.put32(GRID_CTX + 0x74, 0)
    mem.write(BL_ACTIVE, b"\x00")
    mem.write(PITCH, struct.pack(">h", 0))
    mem.put32(PLAY_FRAME, 75)  # 1000 ms at 120 BPM -> beat 3, start of beat
    _call(p, mem, "phase_meter_hook", 0, 0)
    rows_fb = [mem.read(ROWS + r * 0x105 + 5, mem.read(ROWS + r * 0x105 + 4, 1)[0]).decode("ascii")
               for r in range(3)]
    assert rows_fb[0] == "B3/4 120.00 BPM" and mem.u32(DISPLAY_MODE) == 7
    assert rows_fb[1] == "_1_ _2_ (3) _4_"
    assert rows_fb[2] == "[+------------]"


# ---- live ----

def _udp(deck, msg):
    import cdj_panelsock
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.sendto(msg.encode(), (cdj_panelsock.HOST, cdj_panelsock.port_for(deck.keysock)))
    s.close()


@needs_toolchain
@pytest.mark.skipif(not FLASH.is_file(), reason="extract/cdj850/flash_cdj850.bin is not present")
def test_live_info_page2_phase_meter_and_page3_shift(tmp_path):
    """Boot phase_meter + bpm_lock (lock left OFF): INFO -> Page 2 shows live Phase Meter,
    Page 3 shows the shifted original Page 2 items (Album, BPM, Comment)."""
    if os.name == "nt":
        pytest.skip("QMP socket descriptor inheritance requires POSIX")
    import importlib.util
    if not BAKEN.is_file() or importlib.util.find_spec("pyfatfs") is None or importlib.util.find_spec("numpy") is None:
        pytest.skip("tools/baken/baken, pyfatfs and numpy are needed")
    sys.path.insert(0, str(ROOT / "scripts" / "debug"))
    import cdj850_trace as tr

    stick = tmp_path / "phasemeter.img"
    tr.build_stick(stick, {
        "device_name": "PHASEM",
        "playlist": "Phase",
        "tracks": [{"title": "GRID128", "bpm": 128, "first_downbeat": 0, "bars": 32}],
    })
    flash = tmp_path / "flash.bin"
    mods = ["phase_meter", "bpm_lock", "bpm_lock_range1", "bpm_lock_range2",
            "tempowidemath1", "tempowidemath2", "bpm_lock_tick",
            "bpm_lock_notify1", "bpm_lock_notify2", "bpm_lock_display", "bpm_lock_flash"]
    flash.write_bytes(patch_main.patch_flash(FLASH.read_bytes(), mods, "cdj850"))

    def read_rows(d):
        d.q("stop")
        out = []
        for r in range(3):
            b = d.dump(ROWS + r * 0x105, 0x30)
            out.append((b[0], b[1], b[5:5 + b[4]].decode("latin1")))
        cur = d.rb(INFO_CURSOR)
        cnt = d.rb(INFO_COUNT)
        mode = d.rw(DISPLAY_MODE)
        hdr_mode = d.rb(0x0C9D07D2)
        d.q("cont")
        return mode, hdr_mode, cur, cnt, out

    script = tr.LOAD_SCRIPT + ",13600:info:200,14200:rot+1,14800:rot+1"
    with tr.Deck(stick, flash=flash, script=script) as d:
        frame, _ = d.wait_line("mode=0x0a", "[GRID128]", timeout=300)
        # Page 2 enters centered mode 0x07, icon-free attr=00, tag=11/12/13
        frame, line_a = d.wait_line("mode=0x07", "tag=11/12/13",
                                    "attr=00:00:00/00:00:00/00:00:00",
                                    "128.00", start=frame + 1, timeout=180)
        assert "B" in line_a and ("GRID" in line_a or "BPM" in line_a), line_a
        mode, hdr_mode, cur, cnt, page2_a = read_rows(d)
        assert mode in (7, 11) and hdr_mode == 7 and cur == 2 and cnt >= 5, (mode, hdr_mode, cur, cnt)
        assert [(r, tag, attr) for r, (tag, attr, _) in enumerate(page2_a)] == [
            (0, 0x11, 0), (1, 0x12, 0), (2, 0x13, 0)], page2_a
        assert page2_a[0][2].startswith("B") and "128.00" in page2_a[0][2], page2_a
        assert "(" in page2_a[1][2] and ")" in page2_a[1][2], page2_a
        assert page2_a[2][2].startswith("[") and "+" in page2_a[2][2], page2_a

        # While playing, phase updates across consecutive panel frames
        f0 = d.rw(PLAY_FRAME)
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline and d.rw(PLAY_FRAME) < f0 + 15:
            time.sleep(0.2)
        _, hdr2_b, _, _, page2_b = read_rows(d)
        assert hdr2_b == 7 and (page2_b != page2_a or page2_b[0][2].startswith("B"))

        # 1 click CW from Page 2 snaps straight to Page 3 (mode 0x0b, cursor = 5, 128.0 bpm)
        _udp(d, "0x03:1:0:rot")
        frame, _ = d.wait_line("mode=0x0b", "[128.0 bpm]", start=frame + 1, timeout=60)
        mode3, hdr_mode3, cur3, _, page3 = read_rows(d)
        assert mode3 == 11 and hdr_mode3 == 11 and cur3 == 5, (mode3, hdr_mode3, cur3, page3)
        assert any(attr == 0x0D and "128.0 bpm" in text for _, attr, text in page3), page3

        # 1 click CCW from Page 3 snaps straight back to Page 2 (mode 0x07, cursor = 2)
        _udp(d, "0x03:-1:0:rot")
        frame, _ = d.wait_line("mode=0x07", "tag=11/12/13",
                               "attr=00:00:00/00:00:00/00:00:00",
                               start=frame + 1, timeout=60)
        mode2b, hdr_mode2b, cur2b, _, page2_back = read_rows(d)
        assert mode2b in (7, 11) and hdr_mode2b == 7 and cur2b == 2, (mode2b, hdr_mode2b, cur2b, page2_back)
        assert [(r, tag, attr) for r, (tag, attr, _) in enumerate(page2_back)] == [
            (0, 0x11, 0), (1, 0x12, 0), (2, 0x13, 0)], page2_back
