# SPDX-License-Identifier: GPL-2.0-or-later
"""info_order on the CDJ-350 MAIN 1.14: INFO pages "Title - Artist", "128.23 8A", "RATING ***".

* static: the four pool words have one reader each, outside the boot-critical closure; the
  redirects apply and the image passes the validator;
* the payload on the SH-2A interpreter: the BPM/key and rating texts, Camelot from classic,
  Camelot and Open Key texts with the pitch shift, the UTF-16 key name, the DB row hook noting
  rating and key by track id, the page getters and the rating sub-page on the rotary.
Live (QEMU, by hand, docs/cdj350.md): GRID128 by make_test_stick in Am, four stars: page 0
"GRID128 - make_test_stick" (scrolls), page 1 "128.00 8A", one more step "RATING ****", fader +10 %
"140.80 10A", MASTER TEMPO on "140.80 8A".
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
NAMES = ["info_order_page0", "info_order_page1", "info_order_scroll", "info_order_meta"]
POOLS = [(0x0C068428, 0x0C06C3DC, 0x0C06833A), (0x0C06842C, 0x0C06C3F8, 0x0C068392),
         (0x0C0C9048, 0x0C0D664A, 0x0C0C8F8A), (0x0C051AEC, 0x0C0719A4, 0x0C051A32)]
REC, ROW, KEYTXT, OUT = 0x0F100000, 0x0F101000, 0x0F102000, 0x0F103000
PAGE, PITCH, MT = 0x0CD48BE2, 0x0CD4853E, 0x0CD48BC4
LOG = 0x0DDFE000
NEXT_PAGE = 0x0F103F80

needs_toolchain = pytest.mark.skipif(not payloadbuild.have_toolchain(), reason="sh-elf toolchain not installed")
needs_main = pytest.mark.skipif(not MAIN.is_file(), reason="extract/cdj350/c350_main_unpacked.bin is not present")


@needs_main
def test_pool_words_have_one_reader_outside_the_boot_closure():
    import bootcrit
    img = MAIN.read_bytes()
    bc = bootcrit.closure(img, BASE, byteorder=">")
    readers = {pool: [] for pool, _, _ in POOLS}
    for pc in range(BASE, BASE + len(img) - 1, 2):
        w = struct.unpack_from(">H", img, pc - BASE)[0]
        if (w & 0xF000) == 0xD000:
            pool = (pc & ~3) + 4 + (w & 0xFF) * 4
            if pool in readers:
                readers[pool].append(pc)
    for pool, stock, reader in POOLS:
        assert struct.unpack_from(">I", img, pool - BASE)[0] == stock
        assert readers[pool] == [reader], hex(pool)
        assert not bc.hit(pool - BASE, pool - BASE + 4), hex(pool)
        assert not bc.hit(reader - BASE, reader - BASE + 2), hex(reader)


@needs_main
@needs_toolchain
def test_redirects_apply_and_validate():
    import c850loader
    import validate
    from players.cdj350 import GATED_BOOT_POOLS, LOADER_STARTUP, MAIN_PROFILE
    stock = MAIN.read_bytes()[:LOADER_STARTUP.image_len]
    report, spans = {}, {}
    out = patch_main.patch(stock, patch_main.with_loader(NAMES, "cdj350"), "cdj350", report, spans)
    assert {report[n][0] for n in NAMES} == {"applied"}, report
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
    return payloadbuild.build(["info_order"], "cdj350")


def _sym(p, name):
    a = payloadbuild.lookup(p.symbols, name)
    assert a is not None, name
    return a


def _ret(k, value):
    return """
        mov.l   1f,r1
        mov.l   r4,@r1
        mov.l   r5,@(4,r1)
        mov.l   @(28,r1),r0
        add     #1,r0
        mov.l   r0,@(28,r1)
        mov.l   2f,r0
        rts
        nop
        .align 2
1:      .long   %#x
2:      .long   %#x
""" % (LOG + 0x40 * k, value)


def _mem(p):
    m = Memory()
    m.add(0x0DE00020, p.blob)
    m.add(0x0DDFD000, bytes(0x3000))
    m.add(0x0F100000, bytes(0x4000))
    m.add(0x0C9B8F34, bytes(0x7C + 8 * 16))               # grid window: none
    m.add(0x0C7E4D30, bytes(4))
    m.add(0x0CD48D0E, b"\x01")
    m.add(0x0CD48500, bytes(0x80))                        # pitch
    m.add(0x0CD48BC0, bytes(0x40))                        # MASTER TEMPO, INFO page
    _stub(m, 0x0C06BD38, _ret(0, REC))                    # record_of_track
    _stub(m, 0x0C06C3DC, _ret(1, 0x0F10FF00))             # artist getter (stock)
    _stub(m, 0x0C06C3F8, _ret(2, 0x0F10FF40))             # album getter (stock)
    _stub(m, 0x0C0D664A, """
        mov.l   2f,r1
        mov.b   @r1,r0
        mov.l   1f,r1
        mov.b   r0,@r1
        mov.l   3f,r1
        mov.l   @r1,r0
        add     #1,r0
        rts
        mov.l   r0,@r1
        .align 2
1:      .long   %#x
2:      .long   %#x
3:      .long   %#x
""" % (PAGE, NEXT_PAGE, LOG + 0x40 * 3 + 28))           # info_page_scroll: page = NEXT_PAGE
    _stub(m, 0x0C0719A4, _ret(4, 0))                      # DB track row: ok
    _stub(m, 0x0C07D14C, """
        mov.l   1f,r0
        mov.l   r0,@r5
        rts
        mov     #0,r0
        .align 2
1:      .long   %#x
""" % KEYTXT)                                             # key name -> KEYTXT
    return m


def _call(p, m, name, *args):
    cpu = Sh2a(m)
    cpu.r[15] = 0x0DDFFF00
    for i, a in enumerate(args):
        cpu.r[4 + i] = a & 0xFFFFFFFF
    cpu.run(_sym(p, name), Sh2a.RET)
    return cpu.r[0]


def _cstr(m, a):
    raw = bytes(m.read(a, 120))
    return raw[:raw.index(0)].decode()


def _record(m, title=b"GRID128", artist=b"make_test_stick", bpm100=12800, rid=7):
    m.write(REC, bytes(0x100))
    m.put32(REC + 0x10, rid)
    m.put32(REC + 0x18, 1 if bpm100 else 0)
    m.put32(REC + 0x1C, bpm100)
    m.write(REC + 0x38, title + b"\0")
    m.write(REC + 0x98, artist + b"\0")


def _note(p, m, rid, rating, key):
    m.write(ROW, bytes(0x40))
    m.write(ROW + 0x1C, bytes([rating]))
    m.put32(ROW + 0x2C, 3 if key else 0)
    m.write(KEYTXT, key.encode("utf-16-be") + b"\0\0")
    assert _call(p, m, "info_order_track_row", rid, ROW) == 0


@pytest.mark.parametrize("key,mt,pitch,want", [
    ("Am", 1, 0, "128.00 8A"), ("Am", 0, 0, "128.00 8A"), ("Am", 0, 1000, "140.80 10A"),
    ("Am", 1, 1000, "140.80 8A"), ("F#m", 1, 0, "128.00 11A"), ("C", 1, 0, "128.00 8B"),
    ("Db", 1, 0, "128.00 3B"), ("8A", 1, 0, "128.00 8A"), ("1m", 1, 0, "128.00 8A"),
    ("Am", 0, -300, "124.16 1A"), ("Xyz", 1, 0, "128.00 Xyz"),
])
def test_bpm_key_row(p, key, mt, pitch, want):
    """The page-1 row through the payload: rekordbox BPM (no grid), key noted by the DB hook,
    MASTER TEMPO and pitch from RAM."""
    m = _mem(p)
    _record(m)
    _note(p, m, 7, 0, key)
    m.write(PITCH, struct.pack(">h", pitch))
    m.write(MT, bytes([mt]))
    assert _cstr(m, _call(p, m, "info_order_page1", 1)) == want


def test_bpm_alone_key_alone_and_rating(p):
    m = _mem(p)
    m.write(OUT - 0x100, b"\0")
    _call(p, m, "info_order_bpm_key_text", OUT, 14080, OUT - 0x100, 0, 0)
    assert _cstr(m, OUT) == "140.80"
    m.write(OUT - 0x100, b"Am\0")
    _call(p, m, "info_order_bpm_key_text", OUT, 0, OUT - 0x100, 1, 0)
    assert _cstr(m, OUT) == "8A"
    for stars, want in ((0, "NO RATING"), (1, "RATING *"), (5, "RATING *****"), (6, "NO RATING")):
        _call(p, m, "info_order_rating_text", OUT, stars)
        assert _cstr(m, OUT) == want


def test_x100_rounds_and_clamps(p):
    m = _mem(p)
    assert _call(p, m, "info_order_x100", 12800, 1000) == 14080
    assert _call(p, m, "info_order_x100", 12800, -10000 - 5) == 0
    assert _call(p, m, "info_order_x100", 12796, 0) == 12796


def test_pages_and_rotary(p):
    m = _mem(p)
    _record(m)
    _note(p, m, 7, 4, "Am")
    assert _cstr(m, _call(p, m, "info_order_page0", 1)) == "GRID128 - make_test_stick"
    assert _cstr(m, _call(p, m, "info_order_page1", 1)) == "128.00 8A"
    m.write(PITCH, struct.pack(">h", 1000))
    assert _cstr(m, _call(p, m, "info_order_page1", 1)) == "140.80 10A"
    m.write(MT, b"\x01")
    assert _cstr(m, _call(p, m, "info_order_page1", 1)) == "140.80 8A"

    m.write(PAGE, b"\x00")                                # page 0 -> 1: BPM/key first
    m.write(NEXT_PAGE, b"\x01")
    _call(p, m, "info_order_scroll", 1, 1)
    assert m.read(PAGE, 1) == b"\x01" and m.u32(LOG + 0x40 * 3 + 28) == 1
    assert _cstr(m, _call(p, m, "info_order_page1", 1)) == "140.80 8A"
    _call(p, m, "info_order_scroll", 1, 1)                # page 1, CW: the rating sub-page
    assert m.u32(LOG + 0x40 * 3 + 28) == 1
    assert _cstr(m, _call(p, m, "info_order_page1", 1)) == "RATING ****"
    m.write(NEXT_PAGE, b"\x02")
    _call(p, m, "info_order_scroll", 1, 1)                # on to the stock page 2
    assert m.read(PAGE, 1) == b"\x02" and m.u32(LOG + 0x40 * 3 + 28) == 2
    m.write(NEXT_PAGE, b"\x01")
    _call(p, m, "info_order_scroll", 0, 1)                # CCW from 2 lands on the rating
    assert _cstr(m, _call(p, m, "info_order_page1", 1)) == "RATING ****"
    _call(p, m, "info_order_scroll", 0, 1)                # back to BPM/key, page stays 1
    assert m.u32(LOG + 0x40 * 3 + 28) == 3
    assert _cstr(m, _call(p, m, "info_order_page1", 1)) == "140.80 8A"


def test_unknown_track_and_mod_off_are_stock(p):
    m = _mem(p)
    _record(m, rid=9, artist=b"")
    assert _cstr(m, _call(p, m, "info_order_page0", 1)) == "GRID128"
    m.write(REC + 0x18, bytes(8))                          # no BPM, no noted key: the album
    assert _call(p, m, "info_order_page1", 1) == 0x0F10FF40
    m.put32(_sym(p, "info_order_on"), 0)
    assert _call(p, m, "info_order_page0", 1) == 0x0F10FF00
    assert _call(p, m, "info_order_page1", 1) == 0x0F10FF40


def test_row_hook_keeps_four_tracks(p):
    m = _mem(p)
    for rid in range(1, 6):
        _note(p, m, rid, rid % 6, "Am")
    meta = _sym(p, "io_cache")
    ids = [m.u32(meta + 16 * i) for i in range(4)]
    assert sorted(ids) == [2, 3, 4, 5]
    _note(p, m, 3, 2, "")                                  # a re-read updates in place
    assert sorted(m.u32(meta + 16 * i) for i in range(4)) == [2, 3, 4, 5]
