# SPDX-License-Identifier: GPL-2.0-or-later
"""The UTILITY items after Ver (c350_ui.c) on the CDJ-350 MAIN 1.14: QUANTIZE and DRIFT FIX
ON/OFF (quant_loops), and bpm999's three immediates.

* static: the three UTILITY pool words have one reader each, outside the boot-critical
  closure; quant_loops redirects them to the gated shared routines; the image passes the
  validator; the item table holds both quant_loops items;
* the compiled payload on the SH-2A interpreter (tests/sh2a.py): the list continues after Ver
  (item 4) with QUANTIZE (5) and DRIFT FIX (6), scrolling clamps at the last item and stays
  stock inside the stock items; a push switches the value, writes settings +0x17 (0x50 | off
  bits, 0 with none) and saves the record; a fresh deck reads ON for both, and so does any
  +0x17 without the 0x5_ magic; the scanner's erased-flash bits of +1 are never touched; the draw routine writes UTILITY
  over the item's row and calls the stock text copier, and draws stock items with stock code;
  the flags gate the snapping and the drift correction;
* bpm999: the three movi20 immediates become 9990 and nothing else changes.
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
from test_cdj850_hot_cues import _stub  # noqa: E402

MAIN = ROOT / "extract" / "cdj350" / "c350_main_unpacked.bin"
BASE = 0x0C000000
SETTINGS = 0x0C9BE004
LEVEL, CURSOR, TIMEOUT = 0x0C1CAF1A, 0x0C1CAF1B, 0x0C1CAF04
TEXT_MODE, ROW1, ROW2 = 0x0C9B816F, 0x0C9B87F3, 0x0C9B88F3
LOG = 0x0DDFE000
# pool, stock target, its one reader, shared routine
POOLS = [(0x0C0C902C, 0x0C0D641A, 0x0C0C8EFE, "c350_ui_utility_scroll"),
         (0x0C0C9670, 0x0C0D6374, 0x0C0C955A, "c350_ui_utility_select"),
         (0x0C0666A4, 0x0C063C88, 0x0C0665E8, "c350_ui_utility_draw")]
QUANT = ["quant_loops", "quant_loops_auto_send", "quant_loops_loop_in", "quant_loops_loop_in2",
         "quant_loops_loop_in3", "quant_loops_loop_out", "quant_loops_loop_out2", "quant_loops_loop_out3",
         "quant_loops_drift", "quant_loops_utility_scroll", "quant_loops_utility_select",
         "quant_loops_utility_draw"]

needs_toolchain = pytest.mark.skipif(not payloadbuild.have_toolchain(), reason="sh-elf toolchain not installed")
needs_main = pytest.mark.skipif(not MAIN.is_file(), reason="extract/cdj350/c350_main_unpacked.bin is not present")


# ---- static ----

@needs_main
def test_utility_pool_words_have_one_reader():
    """Scroll and select are outside the boot-critical closure; the draw word is inside it
    (utility_display runs in DisplayTask) and is listed in GATED_BOOT_POOLS."""
    from players.cdj350 import GATED_BOOT_POOLS
    import bootcrit
    img = MAIN.read_bytes()
    bc = bootcrit.closure(img, BASE, byteorder=">")
    readers = {pool: [] for pool, _, _, _ in POOLS}
    for pc in range(BASE, BASE + len(img) - 1, 2):
        w = struct.unpack_from(">H", img, pc - BASE)[0]
        if (w & 0xF000) == 0xD000:
            pool = (pc & ~3) + 4 + (w & 0xFF) * 4
            if pool in readers:
                readers[pool].append(pc)
    for pool, stock, reader, _ in POOLS:
        assert struct.unpack_from(">I", img, pool - BASE)[0] == stock
        assert readers[pool] == [reader], (hex(pool), [hex(r) for r in readers[pool]])
        inside = bool(bc.hit(pool - BASE, pool - BASE + 4))
        assert inside == (pool == 0x0C0666A4), hex(pool)
        assert inside == (pool in GATED_BOOT_POOLS), hex(pool)


@needs_main
@needs_toolchain
def test_quant_loops_redirects_the_utility_words_and_validates():
    import c850loader
    import validate
    from players.cdj350 import GATED_BOOT_POOLS, LOADER_STARTUP, MAIN_PROFILE
    stock = MAIN.read_bytes()[:LOADER_STARTUP.image_len]
    report, spans = {}, {}
    out = patch_main.patch(stock, patch_main.with_loader(QUANT, "cdj350"), "cdj350", report, spans)
    assert {report[n][0] for n in QUANT} == {"applied"}, report
    p = payloadbuild.build(["quant_loops"], "cdj350")
    for pool, _, _, routine in POOLS:
        gate = payloadbuild.lookup(p.symbols, payloadbuild.gate_name(routine))
        assert struct.unpack_from(">I", out, pool - BASE)[0] == gate, routine
    assert c850loader.verify(stock, out, startup=LOADER_STARTUP) == []
    rep = validate.Report()
    validate.check_main_image(stock, out, MAIN_PROFILE, spans, rep, startup=LOADER_STARTUP,
                              gated_boot_pools=GATED_BOOT_POOLS)
    assert rep.exit_code == 0, rep.text()


@needs_toolchain
def test_item_table_has_the_quant_loops_items():
    p = payloadbuild.build(["quant_loops"], "cdj350")
    a, b = (payloadbuild.lookup(p.symbols, n) for n in ("c350_ui_items_start", "c350_ui_items_end"))
    words = [struct.unpack_from(">I", p.blob, x - payloadbuild.BASE)[0] for x in range(a, b + 4, 4)]
    names = {v: k.lstrip("_") for k, v in p.symbols.items()}
    assert words[0] == 0 and words[-1] == 0
    assert sorted(names[w] for w in words[1:-1]) == ["ql_drift_item", "ql_quant_item", "ql_size_item"]


# ---- interpreter ----

def _sym(p, name):
    a = payloadbuild.lookup(p.symbols, name)
    assert a is not None, name
    return a


def _logger(k, ret=0):
    return """
        mov.l   1f,r0
        mov.l   r4,@r0
        mov.l   r5,@(4,r0)
        mov.l   @(28,r0),r1
        add     #1,r1
        mov.l   r1,@(28,r0)
        rts
        mov     #%d,r0
        .align 2
1:      .long   %#x
""" % (ret, LOG + 0x40 * k)


def _mem(p):
    mem = Memory()
    mem.add(0x0DE00020, p.blob)
    mem.add(0x0DDFD000, bytes(0x3000))                    # log, stack
    mem.add(SETTINGS, b"\xC3\x02\x10" + bytes(0x15))       # a fresh deck's record
    mem.add(0x0C1CAF00, bytes(0x20))                      # UTILITY timeout, level, cursor
    mem.add(0x0C9B8160, bytes(0x10))                      # text mode
    mem.add(0x0C9B87E0, bytes(0x200))                     # the two text rows
    mem.write(LEVEL, b"\x02")
    _stub(mem, 0x0C084DE4, _logger(0, 1))                 # settings_save
    _stub(mem, 0x0C0D641A, _logger(1))                    # utility_scroll
    _stub(mem, 0x0C0D6374, _logger(2))                    # utility_set_level
    _stub(mem, 0x0C06777A, _logger(3))                    # display_request
    _stub(mem, 0x0C063C88, _logger(4))                    # utility_draw_list
    _stub(mem, 0x0C0679CC, _logger(5))                    # text rows -> slot 7
    return mem


def _call(p, mem, name, *args):
    cpu = Sh2a(mem)
    cpu.r[15] = 0x0DDFFF00
    for i, a in enumerate(args):
        cpu.r[4 + i] = a & 0xFFFFFFFF
    cpu.run(_sym(p, name), Sh2a.RET)
    return cpu.r[0]


def _calls(mem, k):
    return mem.u32(LOG + 0x40 * k + 28)


def _text(mem, addr):
    raw = bytes(mem.read(addr, 16))
    return raw[:raw.index(0)].decode()


def _draw(p, mem):
    mem.write(0x0C9B87E0, bytes(0x200))
    _call(p, mem, "c350_ui_utility_draw")
    return _text(mem, ROW1), _text(mem, ROW2)


@needs_toolchain
def test_utility_list_continues_after_ver():
    p = payloadbuild.build(["quant_loops"], "cdj350")
    mem = _mem(p)
    mem.write(CURSOR, b"\x02")
    _call(p, mem, "c350_ui_utility_scroll", 1, 1)         # stock items: stock scroll
    assert _calls(mem, 1) == 1 and mem.read(CURSOR, 1) == b"\x02"
    mem.write(CURSOR, b"\x04")                            # Ver
    _call(p, mem, "c350_ui_utility_scroll", 1, 1)
    assert mem.read(CURSOR, 1) == b"\x05" and mem.u32(TIMEOUT) == 15000 and _calls(mem, 3) == 1
    assert _draw(p, mem) == ("UTILITY", "QUANTIZE   ON")  # a fresh deck: on
    assert mem.read(TEXT_MODE, 1) == b"\x03" and _calls(mem, 5) == 1
    assert mem.u32(LOG + 0x40 * 5) == 3 and mem.u32(LOG + 0x40 * 5 + 4) == 2
    _call(p, mem, "c350_ui_utility_scroll", 1, 1)
    assert mem.read(CURSOR, 1) == b"\x06" and _draw(p, mem) == ("UTILITY", "Q SIZE      1")
    _call(p, mem, "c350_ui_utility_scroll", 1, 1)
    assert mem.read(CURSOR, 1) == b"\x07" and _draw(p, mem) == ("UTILITY", "DRIFT FIX  ON")
    _call(p, mem, "c350_ui_utility_scroll", 1, 4)         # the last item: clamps
    assert mem.read(CURSOR, 1) == b"\x07"
    _call(p, mem, "c350_ui_utility_scroll", 0, 4)         # back into the stock items
    assert mem.read(CURSOR, 1) == b"\x03" and _calls(mem, 1) == 1
    _draw(p, mem)
    assert _calls(mem, 4) == 1                            # stock draws LIBRARY CREATOR

    mem.put32(_sym(p, "quant_loops_on"), 0)               # the mod off at run time: no items
    mem.write(CURSOR, b"\x04")
    _call(p, mem, "c350_ui_utility_scroll", 1, 1)
    assert mem.read(CURSOR, 1) == b"\x04"


@needs_toolchain
def test_push_switches_and_saves_the_flags():
    p = payloadbuild.build(["quant_loops"], "cdj350")
    mem = _mem(p)
    _call(p, mem, "c350_ui_utility_select", 3, 5)         # QUANTIZE -> OFF
    assert mem.read(SETTINGS + 0x17, 1) == b"\x51" and _calls(mem, 0) == 1 and _calls(mem, 2) == 0
    assert mem.read(SETTINGS, 3) == b"\xC3\x02\x10"     # the stock fields are untouched
    assert mem.u32(LOG) == 2 and mem.u32(LOG + 4) == SETTINGS
    mem.write(CURSOR, b"\x05")
    assert _draw(p, mem) == ("UTILITY", "QUANTIZE  OFF")
    _call(p, mem, "c350_ui_utility_select", 3, 6)         # Q SIZE hidden: DRIFT FIX -> OFF
    assert mem.read(SETTINGS + 0x17, 1) == b"\x53" and _calls(mem, 0) == 2
    mem.write(CURSOR, b"\x06")
    assert _draw(p, mem) == ("UTILITY", "DRIFT FIX OFF")
    _call(p, mem, "c350_ui_utility_select", 3, 5)         # QUANTIZE -> ON, drift kept off
    assert mem.read(SETTINGS + 0x17, 1) == b"\x52"
    _call(p, mem, "c350_ui_utility_select", 3, 7)         # Q SIZE shown again: DRIFT FIX is 7
    assert mem.read(SETTINGS + 0x17, 1) == b"\x00"        # all on: the defaults' zero again
    assert mem.read(SETTINGS, 4) == b"\xC3\x02\x10\x00"

    mem.write(CURSOR, b"\x06")                            # Q SIZE: 1 -> 1/2 -> 1/4 -> 1
    for byte, row in ((0x60, "Q SIZE    1/2"), (0x70, "Q SIZE    1/4"), (0x00, "Q SIZE      1")):
        _call(p, mem, "c350_ui_utility_select", 3, 6)
        assert mem.read(SETTINGS + 0x17, 1) == bytes([byte]) and _draw(p, mem) == ("UTILITY", row)
    _call(p, mem, "c350_ui_utility_select", 3, 6)         # 1/2, then DRIFT FIX OFF keeps it
    _call(p, mem, "c350_ui_utility_select", 3, 7)
    assert mem.read(SETTINGS + 0x17, 1) == b"\x62"
    _call(p, mem, "c350_ui_utility_select", 3, 5)         # QUANTIZE OFF keeps it too
    assert mem.read(SETTINGS + 0x17, 1) == b"\x63"

    _call(p, mem, "c350_ui_utility_select", 3, 1)         # a stock item: utility_set_level(3, 1)
    assert _calls(mem, 2) == 1 and mem.u32(LOG + 0x80) == 3 and mem.u32(LOG + 0x84) == 1
    _call(p, mem, "c350_ui_utility_select", 3, 4)         # Ver: stock too
    assert _calls(mem, 2) == 2


@needs_toolchain
def test_the_flags_gate_snapping_and_drift():
    p = payloadbuild.build(["quant_loops"], "cdj350")
    mem = _mem(p)
    for byte, quant, drift in ((0x00, 1, 1), (0x51, 0, 1), (0x52, 1, 0), (0x53, 0, 0),
                               (0x03, 1, 1), (0x13, 1, 1), (0xF3, 1, 1), (0x5C, 1, 1),
                               (0x61, 0, 1), (0x72, 1, 0), (0x83, 1, 1), (0x43, 1, 1)):
        mem.write(SETTINGS + 0x17, bytes([byte]))
        div = {0x5: 1, 0x6: 2, 0x7: 4}.get(byte >> 4, 1)
        assert _call(p, mem, "c350_ui_quant_div") == div, hex(byte)
        assert _call(p, mem, "c350_ui_quantize") == quant, hex(byte)
        assert _call(p, mem, "c350_ui_drift") == drift, hex(byte)


# ---- bpm999 ----

@needs_main
def test_bpm999_raises_the_three_immediates_only():
    stock = MAIN.read_bytes()
    names = ["bpm999grid", "bpm999range", "bpm999lock"]
    report, spans = {}, {}
    out = patch_main.patch(stock, names, "cdj350", report, spans)
    assert {report[n][0] for n in names} == {"applied"}, report
    diff = [BASE + i for i in range(len(stock)) if stock[i] != out[i]]
    # movi20 #imm: only the 16-bit immediate changes (0x0708 / 0x07CF -> 0x2706)
    assert diff == [0x0C06B868, 0x0C06B869, 0x0C0C6066, 0x0C0C6067, 0x0C0C61D2, 0x0C0C61D3], \
        [hex(a) for a in diff]
    for a in (0x0C06B866, 0x0C0C6064, 0x0C0C61D0):
        assert out[a - BASE + 2:a - BASE + 4] == b"\x27\x06"
