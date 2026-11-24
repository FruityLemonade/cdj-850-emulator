# SPDX-License-Identifier: GPL-2.0-or-later
"""The mod settings file on the USB stick (c850_ui.c, prototype) on the CDJ-850 MAIN 1.13.

When a stick mounts, c850_ui_usb_mount (pool 0x0C0C11E8, after the stock USB ANLZ manager set-up
FUN_0C0C45B4) reads <drive>:/PIONEER/MODSET.DAT; from then on the mod flags come from it, and every
c850_ui_set_flags rewrites it as well as the flash record.

* static: quant_loops_usb_settings and waveform_usb_settings redirect the same pool word to the same
  routine (the second is shared), outside the boot-critical closure;
* interpreter (tests/sh2a.py) with stand-ins for the file layer: no stick = no file calls; a stick
  without the file keeps the flash flags; a valid file overrides them; a change rewrites the file
  ("w+", 16 bytes) and the flash record; with a 30-character HISTORY name the file still saves; a
  bad file is ignored;
* live (QEMU, writable stick): QUANTIZE DRIFT OFF in UTILITY writes MODSET.DAT; a deck with fresh
  flash and the same stick shows QUANTIZE DRIFT OFF (read from the stick at mount).
"""
import shutil
import struct
import sys
import tempfile
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "mods"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import patch_main  # noqa: E402
import payloadbuild  # noqa: E402
from sh2a import Memory, Sh2a  # noqa: E402
from test_cdj850_hot_cues import _asm, _logger, _stub  # noqa: E402
from test_cdj850_utility_items import QUANT, WAVE  # noqa: E402

MAIN = ROOT / "extract" / "cdj850" / "main_unpacked.bin"
FLASH = ROOT / "extract" / "cdj850" / "flash_cdj850.bin"
BAKEN = ROOT / "tools" / "baken" / "baken"
BASE = 0x0C000000
MOUNT_POOL, MOUNT_STOCK = 0x0C0C11E8, 0x0C0C45B4
SETTINGS = 0x0C9DA23C
LOG = 0x0DDFE000
CTX = 0x0DDFD800                     # the ANLZ manager record handed to the mount routine
FILE = 0x0DDFDC00                    # what the fread stand-in returns
DRIFT_OFF, WAVE_CENTER, QUANT_OFF = 0x20, 0x10, 0x01

needs_toolchain = pytest.mark.skipif(not payloadbuild.have_toolchain(), reason="sh-elf toolchain not installed")
needs_main = pytest.mark.skipif(not MAIN.is_file(), reason="extract/cdj850/main_unpacked.bin is not present")


# ---- static ----

@needs_main
@needs_toolchain
def test_mount_word_shared_outside_the_closure():
    import bootcrit
    stock = MAIN.read_bytes()
    report = {}
    out = patch_main.patch(stock, QUANT + WAVE, "cdj850", report)
    assert report["quant_loops_usb_settings"] == ("applied", "")
    assert report["waveform_usb_settings"] == ("applied", "shared with quant_loops_usb_settings")
    p = payloadbuild.build(["quant_loops", "waveform"], "cdj850")
    gate = payloadbuild.lookup(p.symbols, payloadbuild.gate_name("c850_ui_usb_mount"))
    assert struct.unpack_from(">I", stock, MOUNT_POOL - BASE)[0] == MOUNT_STOCK
    assert struct.unpack_from(">I", out, MOUNT_POOL - BASE)[0] == gate
    closure = bootcrit.closure(stock, BASE, byteorder=">")
    assert not closure.hit(MOUNT_POOL - BASE, MOUNT_POOL - BASE + 4)
    assert not closure.hit(0x0C0C1010 - BASE, 0x0C0C1014 - BASE)


# ---- interpreter ----

FREAD = """
        mov.l   1f,r1
        mov     #16,r3
2:      mov.b   @r1+,r2
        mov.b   r2,@r4
        dt      r3
        bf/s    2b
        add     #1,r4
        rts
        mov     #16,r0
        .align 2
1:      .long   %#x
""" % FILE


def _file(flags, magic=b"C850MODS", version=1):
    return magic + bytes([version, 4, 0, 0]) + struct.pack(">I", flags)


def _mem(p, name=b"HISTORY", fopen=0x55, file=None):
    rec = bytearray(0x58)
    rec[0:3] = b"\xC3\x02\x10"
    rec[3:3 + len(name)] = name
    mem = Memory()
    mem.add(0x0DE00020, p.blob)
    mem.add(0x0DDFD000, bytes(0x3000))                    # ctx, file, log, stack
    mem.add(SETTINGS, bytes(rec))
    mem.write(CTX + 0xC, b"\x00C")                        # drive record: letter C
    mem.write(FILE, file or bytes(16))
    _stub(mem, 0x0C08E9B0, _logger(0, 1))                 # flash settings save
    _stub(mem, MOUNT_STOCK, _logger(1, 0))                # stock ANLZ manager set-up
    _stub(mem, 0x0C088F74, _logger(2, fopen))             # file_open_drive
    _stub(mem, 0x0C0890E6, FREAD)                         # file_read_logged
    _stub(mem, 0x0C08910C, _logger(4, 16))                # file_write_logged
    _stub(mem, 0x0C088FCE, _logger(5, 0))                 # file_close_logged
    return mem


def _call(p, mem, name, *args):
    cpu = Sh2a(mem)
    cpu.r[15] = 0x0DDFFF00
    for i, a in enumerate(args):
        cpu.r[4 + i] = a & 0xFFFFFFFF
    cpu.run(payloadbuild.lookup(p.symbols, name), Sh2a.RET)
    return cpu.r[0]


def _calls(mem, k):
    return mem.u32(LOG + 0x40 * k + 28)


def _arg(mem, k, i):
    return mem.u32(LOG + 0x40 * k + 4 * i)


def _cstr(mem, a):
    out = b""
    while (c := mem.read(a + len(out), 1)) != b"\x00":
        out += bytes(c)
    return out


def _u16str(mem, a):
    out, i = "", 0
    while (c := struct.unpack(">H", bytes(mem.read(a + 2 * i, 2)))[0]):
        out += chr(c)
        i += 1
    return out


def _flag(p, mem, f):
    return _call(p, mem, "c850_ui_flag", f)


def _counter(p, mem, n):
    return mem.u32(payloadbuild.lookup(p.symbols, "c850_ui_usb_" + n))


@pytest.fixture(scope="module")
def payload():
    if not payloadbuild.have_toolchain():
        pytest.skip("sh-elf toolchain not installed")
    return payloadbuild.build(["quant_loops", "waveform"], "cdj850")


def test_no_stick_no_file_calls(payload):
    p = payload
    mem = _mem(p)
    assert _call(p, mem, "c850_ui_set_flag", DRIFT_OFF, 1) == 1
    assert _calls(mem, 2) == 0 and _calls(mem, 0) == 1
    assert mem.read(SETTINGS + 0x21, 1) == b"\x52" and _flag(p, mem, DRIFT_OFF) == 1


def test_stick_without_file_keeps_the_flash_flags(payload):
    p = payload
    mem = _mem(p, fopen=0)
    mem.write(SETTINGS + 0x21, b"\x51")                   # flash: WAVE CENTER
    _call(p, mem, "c850_ui_usb_mount", CTX, 7)
    assert _calls(mem, 1) == 1 and _arg(mem, 1, 0) == CTX and _arg(mem, 1, 1) == 7  # stock ran first
    assert _calls(mem, 2) == 1 and _cstr(mem, _arg(mem, 2, 3)) == b"r"
    assert _u16str(mem, _arg(mem, 2, 0)) == "C"
    assert _u16str(mem, _arg(mem, 2, 2)) == "/PIONEER/MODSET.DAT"
    assert _counter(p, mem, "loads") == 0 and _flag(p, mem, WAVE_CENTER) == 1


def test_file_overrides_flash_and_a_change_rewrites_it(payload):
    p = payload
    mem = _mem(p, file=_file(DRIFT_OFF | QUANT_OFF))
    mem.write(SETTINGS + 0x21, b"\x51")                   # flash says WAVE CENTER, drift on
    _call(p, mem, "c850_ui_usb_mount", CTX, 0)
    assert _counter(p, mem, "loads") == 1 and _calls(mem, 5) == 1        # read and closed
    assert (_flag(p, mem, DRIFT_OFF), _flag(p, mem, QUANT_OFF), _flag(p, mem, WAVE_CENTER)) == (1, 1, 0)
    assert _call(p, mem, "c850_ui_quantize") == 0

    assert _call(p, mem, "c850_ui_set_flag", DRIFT_OFF, 0) == 1
    assert _calls(mem, 2) == 2 and _cstr(mem, _arg(mem, 2, 3)) == b"w+"
    assert _calls(mem, 4) == 1 and _arg(mem, 4, 1) == 16 and _arg(mem, 4, 2) == 0x55
    assert bytes(mem.read(_arg(mem, 4, 0), 16)) == _file(QUANT_OFF)      # the stick's flags, drift on
    assert _counter(p, mem, "saves") == 1 and _calls(mem, 0) == 1        # and the flash record
    assert mem.read(SETTINGS + 0x21, 2) == b"\x51\x00"   # flash +0x21: only the drift bit changed
    assert _flag(p, mem, DRIFT_OFF) == 0 and _flag(p, mem, QUANT_OFF) == 1


def test_long_history_name_still_saves_on_the_stick(payload):
    p = payload
    mem = _mem(p, name=b"H" * 31, file=_file(0))
    _call(p, mem, "c850_ui_usb_mount", CTX, 0)
    assert _call(p, mem, "c850_ui_set_flag", DRIFT_OFF, 1) == 1          # saved: on the stick
    assert _calls(mem, 0) == 0 and mem.read(SETTINGS + 0x21, 2) == b"H\x00"   # the name, untouched
    assert bytes(mem.read(_arg(mem, 4, 0), 16)) == _file(DRIFT_OFF)
    assert _flag(p, mem, DRIFT_OFF) == 1


@pytest.mark.parametrize("bad", [_file(DRIFT_OFF, magic=b"C850MODX"), _file(DRIFT_OFF, version=2)])
def test_bad_file_is_ignored(payload, bad):
    p = payload
    mem = _mem(p, file=bad)
    _call(p, mem, "c850_ui_usb_mount", CTX, 0)
    assert _counter(p, mem, "loads") == 0 and _counter(p, mem, "errors") == 1
    assert _flag(p, mem, DRIFT_OFF) == 0


def test_failed_write_falls_back_to_flash(payload):
    p = payload
    mem = _mem(p, fopen=0)                                # every open fails (read-only or gone stick)
    _call(p, mem, "c850_ui_usb_mount", CTX, 0)
    assert _call(p, mem, "c850_ui_set_flag", DRIFT_OFF, 1) == 1
    assert _counter(p, mem, "errors") == 1 and mem.read(SETTINGS + 0x21, 1) == b"\x52"


@pytest.mark.parametrize("flag", [QUANT_OFF, DRIFT_OFF, WAVE_CENTER, 0x04])
def test_change_after_a_failed_stick_write_still_reads_back(payload, flag):
    """A file was loaded at mount, then the stick stops taking writes: every
    flag (quantize, drift, playhead too, not only bits 6+) must still switch.
    Before the fix the low six bits stayed at the file's value."""
    p = payload
    mem = _mem(p, file=_file(flag))
    _call(p, mem, "c850_ui_usb_mount", CTX, 0)
    assert _flag(p, mem, flag) == 1
    mem.write(0x0C08910C, _asm(0x0C08910C, _logger(4, 0)))   # writes fail from now on
    _call(p, mem, "c850_ui_set_flag", flag, 0)
    assert _counter(p, mem, "errors") == 1
    assert _flag(p, mem, flag) == 0
    _call(p, mem, "c850_ui_set_flag", flag, 1)
    assert _flag(p, mem, flag) == 1


def test_high_flag_bits_and_standby_index_roundtrip_on_usb(payload):
    p = payload
    want = DRIFT_OFF | 0x0040 | 0x0100 | 0x2000 | (3 << 14)
    mem = _mem(p, file=_file(0))
    _call(p, mem, "c850_ui_usb_mount", CTX, 0)
    assert _call(p, mem, "c850_ui_set_flags", 0xFFFF, want) == 1
    written = bytes(mem.read(_arg(mem, 4, 0), 16))
    assert written == _file(want)

    mem2 = _mem(p, file=written)
    _call(p, mem2, "c850_ui_usb_mount", CTX, 0)
    assert _flag(p, mem2, DRIFT_OFF) == 1
    assert _flag(p, mem2, 0x0040) == 1
    assert _flag(p, mem2, 0x0100) == 1
    assert _flag(p, mem2, 0x2000) == 1
    assert _flag(p, mem2, 0x0080) == 0
    assert _call(p, mem2, "c850_ui_standby_index") == 3


# ---- live ----

def _stick_file(image, path):
    from pyfatfs.PyFatFS import PyFatFS
    with tempfile.TemporaryDirectory() as tmp:
        part = Path(tmp) / "part.img"
        part.write_bytes(Path(image).read_bytes()[63 * 512:])
        f = PyFatFS(str(part))
        try:
            return f.readbytes(path) if f.exists(path) else None
        finally:
            f.close()


@needs_toolchain
@pytest.mark.skipif(not FLASH.is_file(), reason="extract/cdj850/flash_cdj850.bin is not present")
def test_live_settings_travel_with_the_stick(tmp_path):
    import importlib.util
    import os
    if os.name == "nt":
        pytest.skip("QMP socket descriptor inheritance requires POSIX")
    if not BAKEN.is_file() or importlib.util.find_spec("pyfatfs") is None:
        pytest.skip("tools/baken/baken and pyfatfs are needed")
    sys.path.insert(0, str(ROOT / "scripts" / "debug"))
    import cdj850_trace as tr

    stick = tmp_path / "usbset.img"
    tr.build_stick(stick, {"device_name": "USBSET", "playlist": "U",
                           "tracks": [{"title": "GRID128", "bpm": 128, "first_downbeat": 0, "bars": 32}]})
    fresh = patch_main.patch_flash(FLASH.read_bytes(), QUANT + WAVE, "cdj850")
    p = payloadbuild.build(["quant_loops", "waveform"], "cdj850")
    sym = lambda n: payloadbuild.lookup(p.symbols, "c850_ui_usb_" + n)
    (tmp_path / "flash1.bin").write_bytes(fresh)
    (tmp_path / "flash2.bin").write_bytes(fresh)
    assert _stick_file(stick, "/PIONEER/MODSET.DAT") is None

    script = "6000:usb:200,14000:menu:1700,17000:rot+8,18500:enter:200"
    with tr.Deck(stick, flash=tmp_path / "flash1.bin", script=script, writable=True) as d:
        written = d.usb
        d.wait_line("[QUANTIZE DRIFT]", "[ON]", timeout=120)
        d.wait_line("[QUANTIZE DRIFT]", "[OFF]", timeout=60)
        d.wait_line("input release enter", timeout=60)
        time.sleep(1)
        assert (d.rw(sym("loads")), d.rw(sym("saves")), d.rw(sym("errors"))) == (0, 1, 0)
        assert d.rb(SETTINGS + 0x21) == 0x52
    carried = tmp_path / "carried.img"
    shutil.copyfile(written, carried)
    assert _stick_file(carried, "/PIONEER/MODSET.DAT") == _file(DRIFT_OFF)

    # another deck (fresh flash: drift on) with that stick: the setting comes from the stick
    with tr.Deck(carried, flash=tmp_path / "flash2.bin", script="6000:usb:200,14000:menu:1700,17000:rot+8") as d:
        d.wait_line("[QUANTIZE DRIFT]", "[OFF]", timeout=120)
        assert d.rw(sym("loads")) == 1 and d.rb(SETTINGS + 0x21) == 0
