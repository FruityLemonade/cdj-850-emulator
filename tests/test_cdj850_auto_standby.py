# SPDX-License-Identifier: GPL-2.0-or-later
"""auto_standby: the AUTO STANDBY limit table entry and its CDJ850_STANDBY_MINUTES knob."""
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "mods"))
sys.path.insert(0, str(ROOT / "tests"))

import patch_main
import sigpatch
from test_cdj850_mods import FLASH_BIN, UNPACKED_BIN
from test_cdj850_controls import _free_udp_port, _tap
from test_cdj850_usb import _cdj850_binary, _qmp_read

from auto_standby.cdj850 import patch as standby

LIMIT_OFF = 0x18CCDE            # file offset of the index-1 u16 limit in MAIN


def _apply(minutes):
    """Apply the autostandby DataPatch to the stock unpacked MAIN, with the knob set."""
    old = os.environ.get("CDJ850_STANDBY_MINUTES")
    if minutes is None:
        os.environ.pop("CDJ850_STANDBY_MINUTES", None)
    else:
        os.environ["CDJ850_STANDBY_MINUTES"] = str(minutes)
    try:
        stock = UNPACKED_BIN.read_bytes()
        report = {}
        out = sigpatch.patch_tables(stock, standby.MODS, ["autostandby"], "auto_standby", report, None)
        assert report["autostandby"][0] == "applied"
        return stock, out
    finally:
        if old is None:
            os.environ.pop("CDJ850_STANDBY_MINUTES", None)
        else:
            os.environ["CDJ850_STANDBY_MINUTES"] = old


def test_default_limit_is_20_minutes_and_only_the_limit_changes():
    if not UNPACKED_BIN.is_file():
        pytest.skip("extract/cdj850/main_unpacked.bin is not present")
    stock, out = _apply(None)
    changed = [i for i in range(len(stock)) if stock[i] != out[i]]
    assert changed == [LIMIT_OFF, LIMIT_OFF + 1]
    assert out[LIMIT_OFF:LIMIT_OFF + 2] == (20 * 60).to_bytes(2, "big") == b"\x04\xb0"
    # The index-0 (OFF) entry and the rest of the table are untouched.
    assert out[0x18CCDC:0x18CCDE] == b"\x00\x00"
    assert stock[0x18CCDC:0x18CCE2] == bytes.fromhex("000038400901")


def test_knob_sets_the_limit_in_seconds_big_endian():
    if not UNPACKED_BIN.is_file():
        pytest.skip("extract/cdj850/main_unpacked.bin is not present")
    _, one = _apply(1)
    assert one[LIMIT_OFF:LIMIT_OFF + 2] == b"\x00\x3c"            # 60 s
    _, maxed = _apply(standby.MAX_MINUTES)
    assert maxed[LIMIT_OFF:LIMIT_OFF + 2] == b"\xff\xf0"          # 65520 s, fits u16


@pytest.mark.parametrize("raw", ["0", "-5", "1093", "abc", "1.5", ""])
def test_knob_rejects_values_that_do_not_fit(raw):
    with pytest.raises(SystemExit):
        standby.standby_limit({"CDJ850_STANDBY_MINUTES": raw})


def test_default_when_knob_unset():
    assert standby.standby_limit({}) == (20 * 60).to_bytes(2, "big")


def test_mod_registered_off_by_default_and_composes_with_stock_mods():
    conf = (ROOT / "mods" / "auto_standby" / "mod.conf").read_text()
    assert "MOD_DEFAULT=off" in conf and "MOD_MODELS=cdj850" in conf
    assert "CDJ_MAIN_AUTOSTANDBY" in conf
    assert set(standby.MODS) == {"autostandby"}


def _capture_standby_frames(path):
    """(first virtual ns with page-2 byte 8 == 0x01, count of such frames) from an SCIF1 capture."""
    first, n = None, 0
    for line in open(path):
        if not line.startswith("T "):
            continue
        parts = line.split()
        for tok in parts[5:5 + int(parts[4])]:
            ns, hexs = tok.split(":")
            f = bytes.fromhex(hexs)
            if f[0] == 0x02 and f[8] == 0x01:
                n += 1
                first = int(ns) if first is None else first
    return first, n


def test_live_one_minute_build_requests_standby_after_idle_and_key_press_resets(tmp_path):
    """Boot the 1-minute build, press TEMPO partway in, and check the standby request.

    Measured by hand on this build: the seconds counter reset on the press,
    the MAIN state byte became 4 about 60 s later, and the page-2 status byte 8
    went to 0x01 (first at 86.1 virtual s with the press at about 30 real s).
    """
    if not FLASH_BIN.is_file():
        pytest.skip("extract/cdj850/flash_cdj850.bin is not present")
    if os.name == "nt":
        pytest.skip("QMP socket descriptor inheritance requires POSIX")
    import json
    import socket
    import subprocess
    import time

    os.environ["CDJ850_STANDBY_MINUTES"] = "1"
    try:
        flash = patch_main.patch_flash(FLASH_BIN.read_bytes(), ["autostandby"], "cdj850")
    finally:
        os.environ.pop("CDJ850_STANDBY_MINUTES", None)
    fp = tmp_path / "flash_1min.bin"
    fp.write_bytes(flash)
    cap = tmp_path / "scif1.txt"

    key_port = _free_udp_port()
    env = os.environ.copy()
    env["CDJ850_SCIF1_CAPTURE"] = str(cap)
    env["CDJ_PANEL_KEYSOCK"] = str(key_port)
    for k in ("CDJ850_DSP_BOOT_HLE", "CDJ850_PF16_HIGH", "CDJ850_DSP_PLAY_PF"):
        env.pop(k, None)
    qmp_s, qmp_c = socket.socketpair()
    p = subprocess.Popen(
        [_cdj850_binary(), "-M", "cdj850", "-display", "none", "-monitor", "none", "-serial", "none",
         "-icount", "shift=2,sleep=off",
         "-drive", f"if=pflash,format=raw,file={fp},snapshot=on",
         "-chardev", f"socket,id=qmp,fd={qmp_c.fileno()}",
         "-mon", "chardev=qmp,mode=control"],
        stderr=subprocess.DEVNULL, env=env, pass_fds=(qmp_c.fileno(),),
    )
    qmp_c.close()
    qmp = qmp_s.makefile("rwb", buffering=0)
    try:
        assert "QMP" in json.loads(qmp.readline())
        qmp.write(b'{"execute":"qmp_capabilities"}\n')
        assert "return" in json.loads(qmp.readline())

        secs_before_press = None
        pressed_at = None
        start = time.time()
        while time.time() - start < 240:
            time.sleep(1)
            el = time.time() - start
            if pressed_at is None and el >= 30:
                secs_before_press = _qmp_read(qmp, 0x0C1E10AE, "h")
                _tap(key_port, 0x13, 0x08)
                pressed_at = el
            if pressed_at is not None and el >= pressed_at + 3:
                # A key press restarts the idle count: the seconds counter drops back.
                assert _qmp_read(qmp, 0x0C1E10AE, "h") < secs_before_press
                break
        assert pressed_at is not None

        # The seconds counter restarts from the press, so standby comes about a minute later.
        standby = False
        t_wait = time.time()
        while time.time() - t_wait < 150:
            time.sleep(2)
            if _qmp_read(qmp, 0x0C1E10AC, "b") == 4:
                standby = True
                break
        assert standby, "MAIN did not reach the standby state within 150 s of the press"
    finally:
        qmp.close()
        qmp_s.close()
        p.terminate()
        p.wait(timeout=10)

    first, n = _capture_standby_frames(cap)
    assert first is not None and n > 0, "no page-2 frame carried standby request byte 8 = 0x01"
