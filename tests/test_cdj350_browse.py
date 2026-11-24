# SPDX-License-Identifier: GPL-2.0-or-later
"""browse on the CDJ-350 MAIN 1.14: played markers and TRACK sort.

* static: every redirect applies, alone and with quant_loops and info_order (the UTILITY words
  are shared), the browse pool words are outside the boot-critical closure, and the image passes
  the validator;
* the payload on the SH-2A interpreter: the 1 ms tick counts the loaded track's play time only
  while it plays, a title played more than 20 s gets a '*' in the reply, the TRACK request's
  cache key carries the chosen order, the cursor sort takes BPM or key columns only inside the
  TRACK query, the results comparator keeps row order only for a sorted list, and UTILITY SORT
  cycles DEFAULT, BPM, KEY in the mod flags.
Live (QEMU, by hand, docs/cdj350.md): UTILITY SORT BPM lists "BBB 128", "AAA 200", "CCC 400";
after 20 s of play the list shows "*BBB 128".
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
BROWSE = ["browse_played", "browse_rows", "browse_mount", "browse_unmount", "browse_sort",
          "browse_sort_cache", "browse_sort_fields", "browse_sort_order", "browse_utility_scroll",
          "browse_utility_select", "browse_utility_draw"]
OTHERS = ["quant_loops", "quant_loops_utility_scroll", "quant_loops_utility_select", "quant_loops_utility_draw",
          "info_order_page0", "info_order_page1", "info_order_scroll", "info_order_meta"]
POOLS = [0x0C0AE44C, 0x0C02CE98, 0x0C053D94, 0x0C0542E4, 0x0C06CB34, 0x0C04F478, 0x0C07411C, 0x0C0AFAB4]
SETTINGS = 0x0C9BE004
LOG = 0x0DDFE000
REC, CACHE, ARGS = 0x0F100000, 0x0F101000, 0x0F102000

needs_toolchain = pytest.mark.skipif(not payloadbuild.have_toolchain(), reason="sh-elf toolchain not installed")
needs_main = pytest.mark.skipif(not MAIN.is_file(), reason="extract/cdj350/c350_main_unpacked.bin is not present")


@needs_main
def test_pool_words_outside_the_boot_closure():
    import bootcrit
    img = MAIN.read_bytes()
    bc = bootcrit.closure(img, BASE, byteorder=">")
    for pool in POOLS:
        assert not bc.hit(pool - BASE, pool - BASE + 4), hex(pool)


@needs_main
@needs_toolchain
@pytest.mark.parametrize("names", [BROWSE, BROWSE + OTHERS])
def test_redirects_apply_and_validate(names):
    import c850loader
    import validate
    from players.cdj350 import GATED_BOOT_POOLS, LOADER_STARTUP, MAIN_PROFILE
    stock = MAIN.read_bytes()[:LOADER_STARTUP.image_len]
    report, spans = {}, {}
    out = patch_main.patch(stock, patch_main.with_loader(names, "cdj350"), "cdj350", report, spans)
    assert {report[n][0] for n in names} == {"applied"}, report
    if "quant_loops_utility_scroll" in names:
        shared = [report[n][1] for n in ("browse_utility_scroll", "quant_loops_utility_scroll")]
        assert any(w.startswith("shared with") for w in shared), report
    assert c850loader.verify(stock, out, startup=LOADER_STARTUP) == []
    rep = validate.Report()
    validate.check_main_image(stock, out, MAIN_PROFILE, spans, rep, startup=LOADER_STARTUP,
                              gated_boot_pools=GATED_BOOT_POOLS)
    assert rep.exit_code == 0, rep.text()


# ---- interpreter ----

@pytest.fixture(scope="module")
def p():
    if not payloadbuild.have_toolchain():
        pytest.skip("sh-elf toolchain not installed")
    return payloadbuild.build(["browse"], "cdj350")


def _sym(p, name):
    a = payloadbuild.lookup(p.symbols, name)
    assert a is not None, name
    return a


def _log(k, ret=0):
    """Record r4..r7 and the call count in log slot k; return `ret`."""
    return """
        mov.l   1f,r1
        mov.l   r4,@r1
        mov.l   r5,@(4,r1)
        mov.l   r6,@(8,r1)
        mov.l   r7,@(12,r1)
        mov.l   @(28,r1),r0
        add     #1,r0
        mov.l   r0,@(28,r1)
        mov.l   2f,r0
        rts
        nop
        .align 2
1:      .long   %#x
2:      .long   %#x
""" % (LOG + 0x40 * k, ret)


def _mem(p):
    m = Memory()
    m.add(0x0DE00020, p.blob)
    m.add(0x0DDFD000, bytes(0x3000))
    m.add(0x0F100000, bytes(0x4000))
    m.add(SETTINGS, b"\xC3\x02\x10" + bytes(0x15))
    m.add(0x0CD48500, bytes(0x80))                        # view track, VIEW_SHOWN
    m.add(0x0C9B8160, bytes(0x10))
    m.add(0x0CD48D00, bytes(0x40))                        # source, USB mode, player state
    m.write(0x0CD48D06, b"\x02")
    m.write(0x0CD48D0E, b"\x01")
    _stub(m, 0x0C0C6EBC, _log(0))                         # keys_tick_1ms
    _stub(m, 0x0C02B900, _log(1))                         # reply item adder
    _stub(m, 0x0C06CC04, _log(2))                         # load sort menu
    _stub(m, 0x0C04CA9C, _log(3))                         # unmount
    _stub(m, 0x0C0B083C, _log(4))                         # menu cache
    _stub(m, 0x0C073C68, _log(5, 0x1234))                 # track query
    _stub(m, 0x0C1073C4, _log(6))                         # cursor sort
    _stub(m, 0x0C0AF72E, _log(7, 99))                     # title comparator
    _stub(m, 0x0C06BD38, _log(8, REC))                    # record_of_track
    _stub(m, 0x0C084DE4, _log(9, 1))                      # settings_save
    _stub(m, 0x0C06777A, _log(10))                        # display_request
    m.add(0x0C1CAF00, bytes(0x20))                        # UTILITY timeout, level, cursor
    return m


def _call(p, m, name, *args, stack=()):
    cpu = Sh2a(m)
    sp = 0x0DDFFF00
    for i, v in enumerate(stack):
        m.put32(sp + 4 * i, v)
    cpu.r[15] = sp
    for i, a in enumerate(args):
        cpu.r[4 + i] = a & 0xFFFFFFFF
    cpu.run(_sym(p, name), Sh2a.RET)
    return cpu.r[0]


def _calls(m, k):
    return m.u32(LOG + 0x40 * k + 28)


def _arg(m, k, i):
    return m.u32(LOG + 0x40 * k + 4 * i)


def _playing(m, rid, state=3):
    m.put32(REC + 0x10, rid)
    m.write(0x0CD48534, b"\x01")
    m.write(0x0CD48546, struct.pack(">h", 5))
    m.write(0x0CD48D11 + 2, bytes([state]))


def _entry(m, rid, title):
    m.write(CACHE, bytes(0x114))
    m.put32(CACHE, rid)
    m.write(CACHE + 0x14, title.encode("utf-16-be") + b"\0\0")


def _utf16(m, a):
    raw = bytes(m.read(a, 64))
    end = next(i for i in range(0, len(raw), 2) if raw[i:i + 2] == b"\0\0")
    return raw[:end].decode("utf-16-be")


def test_played_marker_after_20_seconds(p):
    m = _mem(p)
    _call(p, m, "browse_mount_export", 3)
    assert _calls(m, 2) == 1 and _arg(m, 2, 0) == 3
    _playing(m, 42)
    for _ in range(20002):
        _call(p, m, "browse_tick")
    assert _calls(m, 0) == 20002
    assert _call(p, m, "browse_played", 42) == 1
    assert _call(p, m, "browse_played", 43) == 0
    _entry(m, 42, "BBB 128")
    _call(p, m, "browse_add_item", 0x77, 0x04, 0x10, CACHE + 0x14)
    assert _arg(m, 1, 0) == 0x77 and _arg(m, 1, 1) == 0x04 and _arg(m, 1, 2) == 0x10
    assert _utf16(m, _arg(m, 1, 3)) == "*BBB 128"
    _entry(m, 43, "AAA 200")
    _call(p, m, "browse_add_item", 0x77, 0x04, 0x10, CACHE + 0x14)
    assert _arg(m, 1, 3) == CACHE + 0x14                  # not played: stock title
    _entry(m, 42, "BBB 128")
    _call(p, m, "browse_add_item", 0x77, 0x8001, 0, CACHE + 0x14)
    assert _arg(m, 1, 3) == CACHE + 0x14                  # a header, not a track item

    _call(p, m, "browse_mount_export", 3)                 # a new export clears the table
    _call(p, m, "browse_tick")
    assert _call(p, m, "browse_played", 42) == 0


def test_paused_or_unmounted_does_not_count(p):
    m = _mem(p)
    _call(p, m, "browse_mount_export", 3)
    _playing(m, 42, state=6)                               # cued
    for _ in range(25000):
        _call(p, m, "browse_tick")
    assert _call(p, m, "browse_played", 42) == 0
    _call(p, m, "browse_unmount_export", 0x55, 3)
    assert _calls(m, 3) == 1 and _arg(m, 3, 1) == 3
    _playing(m, 42)
    for _ in range(25000):
        _call(p, m, "browse_tick")
    assert _call(p, m, "browse_played", 42) == 0


def test_sort_setting_and_track_chain(p):
    m = _mem(p)
    assert _call(p, m, "browse_mount_export", 3) is not None
    for want in (0x54, 0x58, 0x00):                        # SORT: BPM, KEY, DEFAULT
        _call(p, m, "c350_ui_utility_select", 3, 5)        # SORT is item 5 in a browse-only build
        assert m.read(SETTINGS + 0x17, 1) == bytes([want])
    _call(p, m, "c350_ui_utility_select", 3, 5)            # -> BPM
    m.write(ARGS, bytes(16))
    _call(p, m, "browse_track_cache", 0x10, 0x00010301, 0x1004, ARGS)
    assert m.u32(ARGS + 4) == 1 and _calls(m, 4) == 1      # the order in the cache key
    # inside the cache call: the query asks for a sortable cursor and the sort takes BPM
    _call(p, m, "browse_track_query", 0x01, 4, 0, 0, stack=(0, 0x00010301))
    assert _arg(m, 5, 0) == 0x81
    cols = 0x0F103000
    m.put32(cols, 4)
    _call(p, m, "browse_sort_fields", 0x1234, 1, cols, 0)
    assert _arg(m, 6, 2) == cols                           # outside the TRACK query: stock columns
    assert _call(p, m, "browse_result_compare", 0x1000, 7, 3) == 4    # keeps row order
    assert _calls(m, 7) == 0


def test_default_order_is_stock(p):
    m = _mem(p)
    _call(p, m, "browse_mount_export", 3)
    m.write(ARGS, bytes(16))
    _call(p, m, "browse_track_cache", 0x10, 0x00010301, 0x1004, ARGS)
    assert m.u32(ARGS + 4) == 0
    _call(p, m, "browse_track_query", 0x01, 4, 0, 0, stack=(0, 0x00010301))
    assert _arg(m, 5, 0) == 0x01
    assert _call(p, m, "browse_result_compare", 0x1000, 7, 3) == 99 and _calls(m, 7) == 1
    _call(p, m, "browse_track_query", 0x01, 2, 0, 0, stack=(0, 0x00010301))   # not TRACK
    assert _arg(m, 5, 0) == 0x01
