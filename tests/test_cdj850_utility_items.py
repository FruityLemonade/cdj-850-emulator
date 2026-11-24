# SPDX-License-Identifier: GPL-2.0-or-later
"""The UTILITY items after VERSION No. (c850_ui.c) on the CDJ-850 MAIN 1.13: WAVE PLAYHEAD (waveform)
and QUANTIZE DRIFT ON/OFF (quant_loops).

* static: quant_loops and waveform redirect the same two UTILITY pool words to the same routines
  (the second is 'applied', shared), either mod alone brings them, the image passes the validator;
* the item table holds the linked mods' items;
* the compiled payload on the SH-2A interpreter (tests/sh2a.py): quant_loops alone has QUANTIZE
  DRIFT as item 7, with waveform it is item 8 after WAVE PLAYHEAD; scrolling clamps at the last
  item and stays stock before VERSION No.; a press switches ON/OFF and saves settings +0x21 bit 1
  (a fresh deck reads ON); a 30-character HISTORY name keeps the choice in RAM; stock items go to
  utility_set_menu_level.
"""
import struct
import sys
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
BASE = 0x0C000000
SCROLL_POOL, SELECT_POOL = 0x0C0E797C, 0x0C0E7F10
SETTINGS = 0x0C9DA23C
ROWS, MODE = 0x0C9D0B18, 0x0C9D0AF8
LEVEL, CURSOR, TIMEOUT = 0x0C1E108C, 0x0C1E108D, 0x0C1E1078
LOG = 0x0DDFE000
QUANT = ["quant_loops", "quant_loops_auto_send", "quant_loops_auto_send2", "quant_loops_auto_keep",
         "quant_loops_loop_in", "quant_loops_loop_out", "quant_loops_loop_out2", "quant_loops_keys",
         "quant_loops_flash", "quant_loops_drift", "quant_loops_utility_scroll", "quant_loops_utility_select"]
WAVE = ["waveform", "waveform_rotary", "waveform_utility_scroll", "waveform_utility_select",
        "waveform_center_marker"]

needs_toolchain = pytest.mark.skipif(not payloadbuild.have_toolchain(), reason="sh-elf toolchain not installed")
needs_main = pytest.mark.skipif(not MAIN.is_file(), reason="extract/cdj850/main_unpacked.bin is not present")


# ---- static ----

@needs_main
@needs_toolchain
@pytest.mark.parametrize("names,folders", [(QUANT, ["quant_loops"]), (WAVE, ["waveform"]),
                                           (QUANT + WAVE, ["quant_loops", "waveform"])])
def test_utility_words_shared_and_valid(names, folders):
    import validate
    from players.cdj850 import GATED_BOOT_POOLS, MAIN_PROFILE
    stock = MAIN.read_bytes()
    report, spans = {}, {}
    out = patch_main.patch(stock, patch_main.with_loader(names, "cdj850"), "cdj850", report, spans)
    assert {report[n][0] for n in names} == {"applied"}, report
    p = payloadbuild.build(folders, "cdj850")
    for pool, routine in ((SCROLL_POOL, "c850_ui_utility_scroll"), (SELECT_POOL, "c850_ui_utility_select")):
        gate = payloadbuild.lookup(p.symbols, payloadbuild.gate_name(routine))
        assert struct.unpack_from(">I", out, pool - BASE)[0] == gate
    if len(folders) == 2:
        assert report["waveform_utility_scroll"][1] == "shared with quant_loops_utility_scroll", report
        assert report["waveform_utility_select"][1] == "shared with quant_loops_utility_select", report
    rep = validate.Report()
    validate.check_main_image(stock, out, MAIN_PROFILE, spans, rep, gated_boot_pools=GATED_BOOT_POOLS)
    assert rep.exit_code == 0, rep.text()


@needs_toolchain
def test_item_table_has_the_linked_mods():
    for folders, want in ((["quant_loops"], ["ql_utility_item"]), (["waveform"], ["wf_utility_item"]),
                          (["hot_cues"], []),
                          (["quant_loops", "waveform"], ["ql_utility_item", "wf_utility_item"])):
        p = payloadbuild.build(folders, "cdj850")
        a, b = (payloadbuild.lookup(p.symbols, n) for n in ("c850_ui_items_start", "c850_ui_items_end"))
        words = [struct.unpack_from(">I", p.blob, x - payloadbuild.BASE)[0] for x in range(a, b + 4, 4)]
        names = {v: k.lstrip("_") for k, v in p.symbols.items()}
        assert words[0] == 0 and words[-1] == 0
        assert sorted(names[w] for w in words[1:-1]) == want, folders


# ---- interpreter ----

def _sym(p, name):
    a = payloadbuild.lookup(p.symbols, name)
    assert a is not None, name
    return a


def _mem(p, name=b"HISTORY"):
    rec = bytearray(0x58)
    rec[0:3] = b"\xC3\x02\x10"
    rec[3:3 + len(name)] = name
    mem = Memory()
    mem.add(0x0DE00020, p.blob)
    mem.add(0x0DDFD000, bytes(0x3000))                    # log, stack
    mem.add(SETTINGS, bytes(rec))
    mem.add(0x0C9D0AF0, bytes(0x10))                      # display mode word
    mem.add(ROWS, bytes(3 * 0x105))
    mem.add(0x0C1E1070, bytes(0x20))                      # UTILITY timer, level, item
    mem.add(0x0CE49F50, bytes(8))                         # 1 ms tick
    mem.put32(MODE, 12)
    mem.write(LEVEL, b"\x02")
    _stub(mem, 0x0C08E9B0, _logger(0, 1))                 # settings save
    _stub(mem, 0x0C0E4DF6, _logger(1))                    # utility_scroll_item
    _stub(mem, 0x0C0E4C14, _logger(2))                    # utility_set_menu_level
    _stub(mem, 0x0C06F71C, _logger(3))                    # display update request
    return mem


def _call(p, mem, name, *args):
    cpu = Sh2a(mem)
    cpu.r[15] = 0x0DDFFF00
    for i, a in enumerate(args):
        cpu.r[4 + i] = a & 0xFFFFFFFF
    cpu.run(_sym(p, name), Sh2a.RET)


def _calls(mem, k):
    return mem.u32(LOG + 0x40 * k + 28)


def _rows(p, mem):
    mem.write(ROWS, bytes(3 * 0x105))
    _call(p, mem, "c850_ui_flash_hook", 0, 0)
    row = lambda r: bytes(mem.read(ROWS + r * 0x105 + 5, mem.read(ROWS + r * 0x105 + 4, 1)[0])).decode()
    return row(1), row(2)


@needs_toolchain
def test_quantize_drift_alone_is_item_7():
    p = payloadbuild.build(["quant_loops"], "cdj850")
    mem = _mem(p)
    mem.write(CURSOR, b"\x03")
    _call(p, mem, "c850_ui_utility_scroll", 1, 1)         # stock items: stock scroll
    assert _calls(mem, 1) == 1 and mem.read(CURSOR, 1) == b"\x03"
    mem.write(CURSOR, b"\x06")                            # VERSION No.
    _call(p, mem, "c850_ui_utility_scroll", 1, 1)
    assert mem.read(CURSOR, 1) == b"\x07" and mem.u32(TIMEOUT) == 15000 and _calls(mem, 3) == 1
    assert _rows(p, mem) == ("QUANTIZE DRIFT", "ON")      # a fresh deck: correction on
    _call(p, mem, "c850_ui_utility_scroll", 1, 3)         # the last item: clamps
    assert mem.read(CURSOR, 1) == b"\x07"

    _call(p, mem, "c850_ui_utility_select", 3, 7)
    assert mem.read(SETTINGS + 0x21, 2) == b"\x52\x00" and _calls(mem, 0) == 1 and _calls(mem, 2) == 0
    assert _rows(p, mem) == ("QUANTIZE DRIFT", "OFF")
    _call(p, mem, "c850_ui_utility_select", 3, 7)
    assert mem.read(SETTINGS + 0x21, 2) == b"\x00\x00" and _calls(mem, 0) == 2
    assert _rows(p, mem) == ("QUANTIZE DRIFT", "ON")

    _call(p, mem, "c850_ui_utility_select", 3, 2)         # a stock item
    assert _calls(mem, 2) == 1 and mem.u32(LOG + 0x80) == 3 and mem.u32(LOG + 0x84) == 2
    _call(p, mem, "c850_ui_utility_scroll", 0, 1)
    assert mem.read(CURSOR, 1) == b"\x06"
    assert _rows(p, mem) == ("", "")                      # stock draws VERSION No.

    mem.put32(_sym(p, "quant_loops_on"), 0)               # the mod off at run time: no item
    _call(p, mem, "c850_ui_utility_scroll", 1, 1)
    assert mem.read(CURSOR, 1) == b"\x06"


@needs_toolchain
def test_quantize_drift_after_wave_playhead():
    p = payloadbuild.build(["quant_loops", "waveform"], "cdj850")
    mem = _mem(p)
    mem.write(CURSOR, b"\x06")
    _call(p, mem, "c850_ui_utility_scroll", 1, 1)
    assert mem.read(CURSOR, 1) == b"\x07" and _rows(p, mem) == ("WAVE PLAYHEAD", "LEFT 1/3")
    _call(p, mem, "c850_ui_utility_scroll", 1, 5)
    assert mem.read(CURSOR, 1) == b"\x08" and _rows(p, mem) == ("QUANTIZE DRIFT", "ON")
    _call(p, mem, "c850_ui_utility_select", 3, 8)
    assert mem.read(SETTINGS + 0x21, 2) == b"\x52\x00"   # drift off, playhead untouched
    assert _rows(p, mem) == ("QUANTIZE DRIFT", "OFF")
    _call(p, mem, "c850_ui_utility_select", 3, 7)         # WAVE PLAYHEAD -> CENTER (bit 0), drift kept
    assert mem.read(SETTINGS + 0x21, 2) == b"\x53\x00"
    _call(p, mem, "c850_ui_utility_scroll", 0, 1)
    assert _rows(p, mem) == ("WAVE PLAYHEAD", "CENTER")
    mem.write(ROWS, bytes(3 * 0x105))
    _call(p, mem, "waveform_hook", 0, 0)                  # waveform's display hook draws it too
    assert bytes(mem.read(ROWS + 0x105 + 5, 13)) == b"WAVE PLAYHEAD"


@needs_toolchain
def test_long_history_name_keeps_the_choice_in_ram():
    p = payloadbuild.build(["quant_loops"], "cdj850")
    mem = _mem(p, name=b"H" * 30)                         # reaches +0x21
    mem.write(CURSOR, b"\x07")
    _call(p, mem, "c850_ui_utility_select", 3, 7)
    assert mem.read(SETTINGS + 0x21, 2) == b"\x00\x00" and _calls(mem, 0) == 0
    assert _rows(p, mem) == ("QUANTIZE DRIFT", "OFF")
    _call(p, mem, "c850_ui_utility_select", 3, 7)
    assert _rows(p, mem) == ("QUANTIZE DRIFT", "ON")


# ---- live ----

FLASH = ROOT / "extract" / "cdj850" / "flash_cdj850.bin"
BAKEN = ROOT / "tools" / "baken" / "baken"


@needs_toolchain
@pytest.mark.skipif(not FLASH.is_file(), reason="extract/cdj850/flash_cdj850.bin is not present")
def test_live_quantize_drift_in_utility(tmp_path):
    """QEMU with quant_loops + waveform: hold MENU, turn past VERSION No. to WAVE PLAYHEAD, then
    QUANTIZE DRIFT ON; a press switches it OFF (saved, +0x21 bit 1) and it is still OFF after a
    restart; a second press switches it back ON."""
    import importlib.util
    import os
    if os.name == "nt":
        pytest.skip("QMP socket descriptor inheritance requires POSIX")
    if not BAKEN.is_file() or importlib.util.find_spec("pyfatfs") is None:
        pytest.skip("tools/baken/baken and pyfatfs are needed")
    sys.path.insert(0, str(ROOT / "scripts" / "debug"))
    import cdj850_trace as tr

    stick = tmp_path / "drift.img"
    tr.build_stick(stick, {"device_name": "DRIFT", "playlist": "Drift",
                           "tracks": [{"title": "GRID128", "bpm": 128, "first_downbeat": 0, "bars": 32}]})
    flash = tmp_path / "flash.bin"
    flash.write_bytes(patch_main.patch_flash(FLASH.read_bytes(), QUANT + WAVE, "cdj850"))

    script = "3800:menu:1700,6500:rot+6,7500:rot+1,8500:rot+1,9500:enter:200"
    with tr.Deck(stick, flash=flash, script=script, writable=True) as d:
        d.wait_line("[VERSION No.]", timeout=60)
        d.wait_line("[WAVE PLAYHEAD]", "[LEFT 1/3]", timeout=60)
        d.wait_line("[QUANTIZE DRIFT]", "[ON]", timeout=60)
        d.wait_line("[QUANTIZE DRIFT]", "[OFF]", timeout=60)
        d.wait_line("input release enter", timeout=60)
        assert d.rb(SETTINGS + 0x21) == 0x52

    with tr.Deck(stick, flash=flash, script="3800:menu:1700,6500:rot+8,8000:enter:200",
                 writable=True) as d:
        d.wait_line("[QUANTIZE DRIFT]", "[OFF]", timeout=120)
        d.wait_line("[QUANTIZE DRIFT]", "[ON]", timeout=60)
        d.wait_line("input release enter", timeout=60)
        assert d.rb(SETTINGS + 0x21) == 0
