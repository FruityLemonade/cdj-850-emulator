# SPDX-License-Identifier: GPL-2.0-or-later
"""version_display on the CDJ-850 MAIN 1.13: UTILITY VERSION No. shows "<custom> (1.13)".

* static: hook on display_stage_bpm tail (0x0C0642AA) is outside bootcrit,
  idempotent, and automatically included by patch_update.py on modded updates;
* interpreter (tests/sh2a.py): in UTILITY mode (12), level 2, item 6, rewrites
  dot-matrix row 2 to "Ver. 1.23 (1.13)" (or "Ver. 0.13 (1.13)"), empty running
  version keeps "Ver. 1.13", other modes/levels/items are untouched, and the
  service-mode gate skips the hook;
* live (QEMU): booting a flash patched with version label "1.23", opening UTILITY
  and turning +6 to VERSION No. emits "[Ver. 1.23 (1.13)]" on SCIF1.
"""
import os
import struct
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "mods"))
sys.path.insert(0, str(ROOT / "scripts" / "firmware"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import bootcrit  # noqa: E402
import c850_repack  # noqa: E402
import patch_main  # noqa: E402
from sh2a import Memory, Sh2a  # noqa: E402

MAIN = ROOT / "extract" / "cdj850" / "main_unpacked.bin"
FLASH = ROOT / "extract" / "cdj850" / "flash_cdj850.bin"


def _stock_upd():
    for rel in ("C850MAIN.UPD", "extract/cdj850/C850MAIN.UPD",
                "fw/cdj850/C850MAIN.UPD", "fw/C850MAIN.UPD"):
        p = ROOT / rel
        if p.is_file():
            return p
    return ROOT / "C850MAIN.UPD"


STOCK = _stock_upd()
BASE = 0x0C000000
HOOK_SITE = 0x0C0642AA
HOOK_RESUME = 0x0C0642B8

DISPLAY_MODE = 0x0C9D0AF8
UTILITY_LEVEL = 0x0C1E108C
UTILITY_CURSOR = 0x0C1E108D
RUNNING_VER = 0x0CE6D242
ROW2_REC = 0x0C9D0B18 + 2 * 0x105  # 0x0C9D0D22
SERVICE_UPDATE_MODE = 0x0C1E109A
SERVICE_KEYS_PENDING = 0x0C1E10AA


def _run_hook(patched, *, mode=12, level=2, cursor=6, running=b"1.23",
              row2_init=b"Ver. 1.13", update_mode=0, keys_pending=0):
    mem = Memory()
    mem.add(BASE, patched)
    mem.add(0x0C9D0000, bytearray(0x4000))
    mem.add(0x0CE60000, bytearray(0x10000))
    mem.add(0x0CF00000, bytearray(0x1000))

    mem.write(DISPLAY_MODE, struct.pack(">I", mode))
    mem.write(UTILITY_LEVEL, bytes([level]))
    mem.write(UTILITY_CURSOR, bytes([cursor]))
    mem.write(SERVICE_UPDATE_MODE, bytes([update_mode]))
    mem.write(SERVICE_KEYS_PENDING, bytes([keys_pending]))
    mem.write(RUNNING_VER, running.ljust(16, b"\x00")[:16])
    # Row 2 record: [tag, attr0, attr1, attr2, len, text[len]]
    mem.write(ROW2_REC, bytes([0x43, 0x00, 0x00, 0x00, len(row2_init)]) + row2_init)

    cpu = Sh2a(mem)
    cpu.r[5] = 0x0C9D0AD8  # status staging base used by display_stage_bpm tail
    cpu.r[15] = 0x0CF00F00
    pc = HOOK_SITE
    for _ in range(400):
        pc = cpu.step(pc)
        if pc == HOOK_RESUME:
            break
    assert pc == HOOK_RESUME, f"stopped at {pc:#x}"
    rec = mem.read(ROW2_REC, 48)
    assert rec[:4] == b"\x43\x00\x00\x00"
    length = rec[4]
    return rec[5:5 + length].decode("ascii")


@pytest.mark.skipif(not MAIN.is_file(), reason="main_unpacked.bin not present")
def test_version_display_static_and_interpreter():
    stock = MAIN.read_bytes()
    profile = patch_main.PATCH_SETS["cdj850"].profile
    cl = bootcrit.closure(stock, profile.flash_base, byteorder=">")
    assert not cl.hit(HOOK_SITE - BASE, HOOK_RESUME - BASE)

    report, spans = {}, {}
    patched = patch_main.patch(stock, ["version_display"], "cdj850", report, spans)
    assert report["version_display"][0] == "applied"
    assert patch_main.patch(patched, ["version_display"], "cdj850", report) == patched
    assert report["version_display"][0] == "present"

    assert _run_hook(patched, running=b"1.23") == "Ver. 1.23 (1.13)"
    assert _run_hook(patched, running=b"0.13") == "Ver. 0.13 (1.13)"
    assert _run_hook(patched, running=b"1.13") == "Ver. 1.13 (1.13)"
    assert _run_hook(patched, running=b"") == "Ver. 1.13"
    # Other modes, levels, or items are untouched
    assert _run_hook(patched, mode=10, row2_init=b"TITLE") == "TITLE"
    assert _run_hook(patched, level=3, row2_init=b"SUB") == "SUB"
    assert _run_hook(patched, cursor=5, row2_init=b"ON") == "ON"
    # Service-mode gate skips the hook
    assert _run_hook(patched, running=b"1.23", update_mode=1) == "Ver. 1.13"
    assert _run_hook(patched, running=b"1.23", keys_pending=1) == "Ver. 1.13"


@pytest.mark.skipif(not STOCK.is_file(), reason="C850MAIN.UPD not present")
def test_patch_update_auto_includes_version_display_and_validates(tmp_path):
    out = tmp_path / "C850MAIN.UPD"
    r = subprocess.run(
        [sys.executable, str(ROOT / "mods" / "patch_update.py"), "--model", "cdj850",
         "--version", "1.23", str(STOCK), str(out), "auto_standby"],
        capture_output=True, text=True,
    )
    assert r.returncode == 0, r.stdout + r.stderr
    assert "Version display:" in r.stdout
    assert c850_repack.get_version(out.read_bytes()) == "1.23"

    v = subprocess.run(
        [sys.executable, str(ROOT / "mods" / "validate.py"), "--model", "cdj850",
         "--stock", str(STOCK), str(out)],
        capture_output=True, text=True,
    )
    assert v.returncode == 0, v.stdout + v.stderr
    assert "all checks passed" in v.stdout


def _qemu():
    for b in (os.environ.get("GUI_QEMU", ""), str(ROOT / "qemu-build-eb" / "qemu-system-sh4eb")):
        if b and os.path.isfile(b):
            return b
    return None


@pytest.mark.skipif(not FLASH.is_file(), reason="flash_cdj850.bin not present")
@pytest.mark.skipif(_qemu() is None, reason="qemu-system-sh4eb not built")
@pytest.mark.skipif(os.name == "nt", reason="QMP socket descriptor inheritance requires POSIX")
def test_live_utility_displays_custom_and_base_version(tmp_path):
    sys.path.insert(0, str(ROOT / "scripts" / "debug"))
    import cdj850_trace as tr

    flash_bytes = patch_main.patch_flash(FLASH.read_bytes(), ["version_display"], "cdj850")
    flash_bytes = c850_repack.set_version(flash_bytes, "1.23")
    flash = tmp_path / "flash_123.bin"
    flash.write_bytes(flash_bytes)

    stick = tmp_path / "empty.img"
    stick.write_bytes(b"\x00" * (1024 * 1024))

    script = "3800:menu:1700,6500:rot+6"
    with tr.Deck(stick, flash=flash, script=script) as d:
        d.wait_line("[.UTILITY.]", "[A.CUE LEVEL]", timeout=60)
        d.wait_line("[VERSION No.]", "[Ver. 1.23 (1.13)]", timeout=60)
