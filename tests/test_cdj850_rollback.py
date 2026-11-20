# SPDX-License-Identifier: GPL-2.0-or-later
"""A modded CDJ-850 goes back to stock through its own service-mode USB update.

The live test runs scripts/test/cdj850_rollback.py end to end (about 10 minutes):
install every user mod of the branch, exactly as patch_update.py writes it, on an
emulated stock deck through USB SELECT + RELOOP at power-on, boot and play it, roll back
with the stock C850MAIN.UPD the same way (with gdb breakpoints on all mod code: zero
hits in service mode), and check the flash and the stock boot. The static tests pin the
version label rule (upd_flash_write 0x0C08EC44): the updater skips a file whose label
matches the running one in the first char and the two after the dot, unless the file's
first char is below '1'; patch_update.py labels a modded file "0.13" by default.
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


def test_updater_skip_rule_as_traced():
    io = c850_repack.installs_over
    assert not io("1.13", "1.13")                      # same version: skipped (COMPLETE!, nothing written)
    assert not io("1x13", "1.13") and not io("1.13\0x", "1.13")   # the dot and what follows "D.DD" are not compared
    assert io("1.14", "1.13") and io("1.13", "1.14") and io("2.13", "1.13")
    assert io("0.13", "1.13") and io("0.13", "0.13") and io("0.99", "0.99")   # below '1': always written
    assert io("1.13", "0.13")                          # rollback: stock over a 0.13 deck


def test_mod_label_satisfies_install_rebuild_and_rollback():
    label = c850_repack.mod_label("1.13")
    assert label == "0.13" and c850_repack.base_version(label) == "1.13"
    assert c850_repack.installs_over(label, "1.13")    # (a) over stock
    assert c850_repack.installs_over(label, label)     # (b) over a deck already running a mod build
    assert c850_repack.installs_over("1.13", label)    # (c) the stock file rolls it back
    assert c850_repack.base_version("1.13") == "1.13"


def test_patch_update_label_options():
    import patch_update
    stock = STOCK.read_bytes() if STOCK.is_file() else c850_repack.set_version(bytes(0x60020), "1.13")
    assert patch_update.c850_label(stock, ("default", None))[0] == "0.13"
    assert patch_update.c850_label(c850_repack.set_version(stock, "0.13"), ("default", None))[0] == "0.13"
    assert patch_update.c850_label(stock, ("set", "1.14"))[0] == "1.14"
    label, why = patch_update.c850_label(stock, ("keep", None))
    assert label == "1.13" and "SKIPPED" in why
    assert patch_update.label_option(["a", "--version", "0.20", "b"]) == (("set", "0.20"), ["a", "b"])
    assert patch_update.label_option(["--keep-version", "a"]) == (("keep", None), ["a"])
    assert patch_update.label_option(["a"]) == (("default", None), ["a"])
    with pytest.raises(SystemExit):
        patch_update.label_option(["--version", "1.2"])


@pytest.mark.skipif(not STOCK.is_file(), reason="C850MAIN.UPD is not present")
def test_patch_update_writes_the_mod_label_by_default(tmp_path):
    """One real run: the output of patch_update for a data-only mod carries "0.13",
    validates, and differs from its input only in the version field and the patched MAIN."""
    import subprocess
    out = tmp_path / "C850MAIN.UPD"
    r = subprocess.run([sys.executable, str(ROOT / "mods" / "patch_update.py"), "--model", "cdj850", str(STOCK),
                        str(out), "auto_standby"], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "label 0.13 (default mod label)" in r.stdout
    data = out.read_bytes()
    assert c850_repack.get_version(data) == "0.13" and c850_repack.check_upd(data) == []
    stock = STOCK.read_bytes()
    diff = [i for i in range(0x60020) if stock[i] != data[i]]
    assert diff == [c850_repack.VERSION_OFF]            # "1.13" -> "0.13", nothing else below the stream
    v = subprocess.run([sys.executable, str(ROOT / "mods" / "validate.py"), "--model", "cdj850", "--stock",
                        str(STOCK), str(out)], capture_output=True, text=True)
    assert v.returncode == 0 and "version label 0.13" in v.stdout, v.stdout


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
