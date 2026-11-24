# SPDX-License-Identifier: GPL-2.0-or-later
"""auto_standby: AUTO STANDBY timeout (20/40/60/120 MIN) in UTILITY and PIONEER/MODSET.DAT."""
import struct
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "mods"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import bootcrit  # noqa: E402
import patch_main  # noqa: E402
import payloadbuild  # noqa: E402
import validate  # noqa: E402
from players.cdj850 import GATED_BOOT_POOLS, MAIN_PROFILE  # noqa: E402
from sh2a import Memory, Sh2a  # noqa: E402
from test_cdj850_hot_cues import _logger, _stub  # noqa: E402
from test_cdj850_mods import UNPACKED_BIN  # noqa: E402
from test_cdj850_usb_settings import CTX, FILE, FREAD, _file  # noqa: E402

from auto_standby.cdj850 import patch as standby  # noqa: E402

BASE = 0x0C000000
STANDBY_POOL = 0x0C0EF998
STANDBY_STOCK = 0x0C08F186
IDLE_SECS = 0x0C1E10AE
IDLE_MS = 0x0C1E10B0
SETTINGS = 0x0C9DA23C
ROWS, MODE = 0x0C9D0B18, 0x0C9D0AF8
LEVEL, CURSOR, TIMEOUT = 0x0C1E108C, 0x0C1E108D, 0x0C1E1078
LOG = 0x0DDFE000
MODS = ["autostandby", "autostandby_usb_settings", "autostandby_utility_scroll",
        "autostandby_utility_select", "autostandby_flash"]

needs_toolchain = pytest.mark.skipif(not payloadbuild.have_toolchain(), reason="sh-elf toolchain not installed")
needs_main = pytest.mark.skipif(not UNPACKED_BIN.is_file(), reason="extract/cdj850/main_unpacked.bin is not present")


def _sym(p, name):
    a = payloadbuild.lookup(p.symbols, name)
    assert a is not None, name
    return a


def _mem(p, stock_mode=1, fopen=0x55, file=None):
    rec = bytearray(0x58)
    rec[0:3] = b"\xC3\x02\x10"
    rec[3:10] = b"HISTORY"
    mem = Memory()
    mem.add(0x0DE00020, p.blob)
    mem.add(0x0DDFD000, bytes(0x3000))                    # ctx, file, log, stack
    mem.add(SETTINGS, bytes(rec))
    mem.add(0x0C1E1070, bytes(0x50))                      # UTILITY + idle seconds/ms
    mem.add(0x0C9D0AF0, bytes(0x10))
    mem.add(ROWS, bytes(3 * 0x105))
    mem.add(0x0CE49F50, bytes(8))
    mem.put32(MODE, 12)
    mem.write(LEVEL, b"\x02")
    mem.write(CTX + 0xC, b"\x00C")
    mem.write(FILE, file or bytes(16))
    _stub(mem, STANDBY_STOCK, _logger(0, stock_mode))
    _stub(mem, 0x0C0C45B4, _logger(1, 0))                 # stock ANLZ mount
    _stub(mem, 0x0C088F74, _logger(2, fopen))             # fopen
    _stub(mem, 0x0C0890E6, FREAD)                         # fread
    _stub(mem, 0x0C08910C, _logger(4, 16))                # fwrite
    _stub(mem, 0x0C088FCE, _logger(5, 0))                 # fclose
    _stub(mem, 0x0C08E9B0, _logger(6, 1))                 # settings save
    _stub(mem, 0x0C0E4DF6, _logger(7))                    # utility_scroll_item
    _stub(mem, 0x0C0E4C14, _logger(8))                    # utility_set_menu_level
    _stub(mem, 0x0C06F71C, _logger(9))                    # display update
    return mem


def _call(p, mem, name, *args):
    cpu = Sh2a(mem)
    cpu.r[15] = 0x0DDFFF00
    for i, a in enumerate(args):
        cpu.r[4 + i] = a & 0xFFFFFFFF
    cpu.run(_sym(p, name), Sh2a.RET)
    return cpu.r[0]


def _rows(p, mem):
    mem.write(ROWS, bytes(3 * 0x105))
    _call(p, mem, "c850_ui_flash_hook", 0, 0)
    row = lambda r: bytes(mem.read(ROWS + r * 0x105 + 5, mem.read(ROWS + r * 0x105 + 4, 1)[0])).decode()
    return row(1), row(2)


@needs_main
@needs_toolchain
def test_auto_standby_pool_redirect_and_valid():
    stock = UNPACKED_BIN.read_bytes()
    report, spans = {}, {}
    out = patch_main.patch(stock, patch_main.with_loader(MODS, "cdj850"), "cdj850", report, spans)
    assert {report[n][0] for n in MODS} == {"applied"}, report
    p = payloadbuild.build(["auto_standby"], "cdj850")
    gate = payloadbuild.lookup(p.symbols, payloadbuild.gate_name("auto_standby_get_mode"))
    assert struct.unpack_from(">I", stock, STANDBY_POOL - BASE)[0] == STANDBY_STOCK
    assert struct.unpack_from(">I", out, STANDBY_POOL - BASE)[0] == gate
    closure = bootcrit.closure(stock, BASE, byteorder=">")
    assert not closure.hit(STANDBY_POOL - BASE, STANDBY_POOL - BASE + 4)
    assert not closure.hit(0x0C0EF846 - BASE, 0x0C0EF848 - BASE)
    rep = validate.Report()
    validate.check_main_image(stock, out, MAIN_PROFILE, spans, rep, gated_boot_pools=GATED_BOOT_POOLS)
    assert rep.exit_code == 0, rep.text()


def test_mod_registered_off_by_default():
    conf = (ROOT / "mods" / "auto_standby" / "mod.conf").read_text()
    assert "MOD_DEFAULT=off" in conf and "MOD_MODELS=cdj850" in conf
    assert "CDJ_MAIN_AUTOSTANDBY" in conf
    assert set(standby.MODS) == set(MODS)


@needs_toolchain
def test_standby_time_utility_cycles_and_persists_to_usb():
    p = payloadbuild.build(["auto_standby"], "cdj850")
    mem = _mem(p, fopen=0x55, file=_file(0))
    _call(p, mem, "c850_ui_usb_mount", CTX, 0)
    mem.write(CURSOR, b"\x06")
    _call(p, mem, "c850_ui_utility_scroll", 1, 1)
    assert mem.read(CURSOR, 1) == b"\x07"
    assert _rows(p, mem) == ("STANDBY TIME", "20 MIN")

    for want_label, want_idx in (("40 MIN", 1), ("60 MIN", 2), ("120 MIN", 3), ("20 MIN", 0)):
        _call(p, mem, "c850_ui_utility_select", 3, 7)
        written = bytes(mem.read(mem.u32(LOG + 0x40 * 4), 16))
        assert written == _file(want_idx << 14)
        assert _rows(p, mem) == ("STANDBY TIME", want_label)
        assert _call(p, mem, "c850_ui_standby_index") == want_idx


@needs_toolchain
@pytest.mark.parametrize("idx,limit", [(0, 1200), (1, 2400), (2, 3600), (3, 7200)])
def test_auto_standby_get_mode_triggers_at_selected_limit(idx, limit):
    p = payloadbuild.build(["auto_standby"], "cdj850")
    mem = _mem(p, stock_mode=1)
    _call(p, mem, "c850_ui_set_flags", 0xC000, idx << 14)

    # One ms before the limit: stock idle counter is left alone.
    mem.write(IDLE_SECS, struct.pack(">HI", limit - 1, 998))
    assert _call(p, mem, "auto_standby_get_mode") == 1
    assert struct.unpack(">H", bytes(mem.read(IDLE_SECS, 2)))[0] == limit - 1

    # On the tick that completes `limit` seconds: pre-loads 0x383F so stock increments to 0x3840.
    mem.write(IDLE_SECS, struct.pack(">HI", limit - 1, 999))
    assert _call(p, mem, "auto_standby_get_mode") == 1
    assert struct.unpack(">H", bytes(mem.read(IDLE_SECS, 2)))[0] == 0x383F

    # When stock AUTO STANDBY is OFF, returns 0 without touching the idle counter.
    mem_off = _mem(p, stock_mode=0)
    _call(p, mem_off, "c850_ui_set_flags", 0xC000, idx << 14)
    mem_off.write(IDLE_SECS, struct.pack(">HI", limit, 999))
    assert _call(p, mem_off, "auto_standby_get_mode") == 0
    assert struct.unpack(">H", bytes(mem_off.read(IDLE_SECS, 2)))[0] == limit
