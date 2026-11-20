# SPDX-License-Identifier: GPL-2.0-or-later
"""A modded CDJ-850 goes back to stock through its own service-mode USB update.

The live test runs scripts/test/cdj850_rollback.py end to end (about 7 minutes):
install every user mod of the branch on an emulated stock deck through USB SELECT +
RELOOP at power-on, boot and play it, roll back with the stock C850MAIN.UPD the same
way, and check the flash and the stock boot. The static test pins the one thing a
user must do for that to work: the modded file needs a version label other than the
running one, or the updater skips it (0x0C08EC44).
"""
import importlib.util
import os
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "mods"))
sys.path.insert(0, str(ROOT / "scripts" / "firmware"))
sys.path.insert(0, str(ROOT / "scripts" / "test"))

import c850_repack  # noqa: E402
import payloadbuild  # noqa: E402

STOCK = ROOT / "C850MAIN.UPD"
FLASH = ROOT / "extract" / "cdj850" / "flash_cdj850.bin"
BAKEN = ROOT / "tools" / "baken" / "baken"


@pytest.mark.skipif(not STOCK.is_file(), reason="C850MAIN.UPD is not present")
def test_relabel_changes_only_the_version_field_and_keeps_the_checksums():
    stock = STOCK.read_bytes()
    assert stock[c850_repack.VERSION_OFF:c850_repack.VERSION_OFF + 4] == b"1.13"
    out = c850_repack.set_version(stock, "1.14")
    diff = [i for i in range(len(stock)) if stock[i] != out[i]]
    assert diff == [c850_repack.VERSION_OFF + 3]          # "1.13" -> "1.14"
    assert c850_repack.check_upd(out) == []                 # the label is outside the summed bytes


def _qemu():
    for b in (os.environ.get("GUI_QEMU", ""), str(ROOT / "qemu-build-eb" / "qemu-system-sh4eb")):
        if b and os.path.isfile(b):
            return b
    return None


@pytest.mark.skipif(not (STOCK.is_file() and FLASH.is_file()), reason="stock update or flash not present")
@pytest.mark.skipif(_qemu() is None, reason="qemu-system-sh4eb is not built")
@pytest.mark.skipif(not payloadbuild.have_toolchain(), reason="sh-elf toolchain not installed")
@pytest.mark.skipif(not BAKEN.is_file() or importlib.util.find_spec("pyfatfs") is None
                    or importlib.util.find_spec("numpy") is None,
                    reason="tools/baken/baken, pyfatfs and numpy are needed for the stick")
@pytest.mark.skipif(os.name == "nt", reason="QMP socket descriptor inheritance requires POSIX")
def test_install_all_mods_and_roll_back_through_the_service_update(tmp_path, capsys):
    import cdj850_rollback
    rc = cdj850_rollback.main(["--work", str(tmp_path)])
    out = capsys.readouterr().out
    assert rc == 0, "\n".join(ln for ln in out.splitlines() if ln.startswith("FAIL"))
