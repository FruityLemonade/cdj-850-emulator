# SPDX-License-Identifier: GPL-2.0-or-later
"""info_order on the CDJ-850 MAIN 1.13: the INFO screens in the order Track, "128.23 12A *****",
waveform / Artist, Rating, Key / Album, Comment.

* static: the hook on display_stage_bpm's tail (the bpm999 span), outside the boot-critical
  closure, ahead of bpm_decimals' routine, and the composition with the other mods;
* the compiled payload on the SH-2A interpreter (tests/sh2a.py) against a Python reference: the
  list order, the key in Camelot notation (classic, Camelot and Open Key texts), the semitones a
  pitch moves the key with MASTER TEMPO off, the title-view text and the row rewrite;
* live (QEMU + a baken stick): the record's list and the three screens while scrolling INFO.
"""
import math
import os
import socket
import struct
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "mods"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import bootcrit  # noqa: E402
import patch_main  # noqa: E402
import payloadbuild  # noqa: E402
from sh2a import Memory, Sh2a  # noqa: E402

MAIN = ROOT / "extract" / "cdj850" / "main_unpacked.bin"
FLASH = ROOT / "extract" / "cdj850" / "flash_cdj850.bin"
BAKEN = ROOT / "tools" / "baken" / "baken"
BASE = 0x0C000000
HOOK_SITE = 0x0C0642AA
RECORDS = 0x0C9D44D4          # 3 DB records of loaded tracks, 0x864 bytes each
ROWS = 0x0C9D0B18
SOURCES = 0x0C9D196F
MODE = 0x0C9D0AF8
TRACK = 0x0C9D1D96
PITCH = 0x0CE6CA50
MASTER_TEMPO = 0x0CE6D0E0
USB_MODE = 0x0CE6D25A
CODE_STOP = Sh2a.RET
STOCK = [0x04, 0x07, 0x02, 0x0D, 0x23, 0x0F]          # the test stick's list, stock order

needs_toolchain = pytest.mark.skipif(not payloadbuild.have_toolchain(), reason="sh-elf toolchain not installed")
needs_main = pytest.mark.skipif(not MAIN.is_file(), reason="extract/cdj850/main_unpacked.bin is not present")


# ---- Python reference of payload/info_order.c ----

ORDER = [0x04, 0x0D, 0x07, 0x0A, 0x0F, 0x02, 0x23]


def ref_sort(items):
    if 0x04 not in items or 0x0D not in items:
        return None
    out, left = [], list(items)
    for c in ORDER:
        if c in left:
            out.append(c)
            left.remove(c)
    return out + left


def ref_semitones(pitch):
    """Nearest semitone of the ratio (10000 + pitch) / 10000, -12..12."""
    if pitch <= -10000:
        return -12
    return max(-12, min(12, round(12 * math.log2((10000 + pitch) / 10000))))


PC = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}


def ref_camelot(text):
    t = text.strip()
    if t[:1].isdigit():
        n = int("".join(c for c in t if c.isdigit())[:3])
        s = t.lstrip("0123456789")[:1].lower()
        if not 1 <= n <= 12:
            return None
        if s in ("a", "b"):
            return n, s == "a"
        if s in ("m", "d"):
            return (n - 1 + 7) % 12 + 1, s == "m"
        return None
    if not t or t[0].upper() not in PC:
        return None
    pc, rest = PC[t[0].upper()], t[1:]
    if rest[:1] == "#":
        pc, rest = pc + 1, rest[1:]
    elif rest[:1] == "b":
        pc, rest = pc + 11, rest[1:]
    minor = rest[:1] == "m" and rest[1:2] != "a"
    return (7 + 7 * (pc % 12 - (9 if minor else 0))) % 12 + 1, minor


def ref_shift(n, s):
    return (n - 1 + 7 * s) % 12 + 1


def ref_x100(bpm100, pitch):
    pitch = max(-10000, min(10000, pitch))
    return min(99999, (bpm100 * (10000 + pitch) + 5000) // 10000)


def ref_title(x100, bpm_ok, key, mt_on, pitch):
    parts = []
    if bpm_ok:
        parts.append("%d.%02d" % (x100 // 100, x100 % 100))
    if key:
        c = ref_camelot(key)
        if c:
            n, minor = c
            if not mt_on:
                n = ref_shift(n, ref_semitones(pitch))
            parts.append("%d%s" % (n, "A" if minor else "B"))
        else:
            parts.append(key[:16])
    return " ".join(parts)


def test_reference_examples():
    assert ref_sort(STOCK) == [0x04, 0x0D, 0x07, 0x0F, 0x02, 0x23]
    assert ref_sort(STOCK + [0x0A]) == [0x04, 0x0D, 0x07, 0x0A, 0x0F, 0x02, 0x23]
    assert ref_sort([0x04, 0x06, 0x07, 0x0E, 0x0D]) == [0x04, 0x0D, 0x07, 0x06, 0x0E]   # others follow
    assert ref_sort([0x04, 0x07, 0x02]) is None
    camelot = {"Am": (8, True), "F#m": (11, True), "Dbm": (12, True), "C#m": (12, True), "Abm": (1, True),
               "C": (8, False), "Eb": (5, False), "Bb": (6, False), "B": (1, False), "E": (12, False),
               "Amin": (8, True), "Ebmaj": (5, False), "8A": (8, True), "12b": (12, False),
               "1m": (8, True), "6d": (1, False)}
    for text, want in camelot.items():
        assert ref_camelot(text) == want, text
    assert ref_semitones(293) == 0 and ref_semitones(294) == 1 and ref_semitones(-285) == -1
    assert ref_semitones(600) == 1 and ref_semitones(1600) == 3 and ref_semitones(10000) == 12
    assert ref_shift(8, 1) == 3 and ref_shift(8, -1) == 1       # Am + 1 = Bbm (3A), - 1 = G#m (1A)
    assert ref_title(12823, True, "Am", True, 300) == "128.23 8A"
    assert ref_title(12823, True, "Am", False, 300) == "128.23 3A"


# ---- static ----

@needs_main
@needs_toolchain
def test_hook_is_outside_the_boot_critical_closure_and_runs_before_bpm_decimals():
    stock = MAIN.read_bytes()
    closure = bootcrit.closure(stock, BASE, byteorder=">")
    assert not closure.hit(HOOK_SITE - BASE, HOOK_SITE + 14 - BASE)
    rating_pool = 0x0C052D80 - BASE
    assert not closure.hit(rating_pool, rating_pool + 4)
    mods = ["info_order", "info_order_rating", "bpm_decimals", "bpm_decimals_grid"]
    report = {}
    out = patch_main.patch(stock, mods, "cdj850", report)
    assert {n: st for n, (st, _) in report.items() if n in mods} == {n: "applied" for n in mods}
    p = payloadbuild.build(["info_order", "bpm_decimals"], "cdj850")
    assert int.from_bytes(out[rating_pool:rating_pool + 4], "big") == payloadbuild.lookup(
        p.symbols, payloadbuild.gate_name("info_order_track_info"))
    # call site: mov.l @(lit),r1; jmp @r1; nop; .long trampoline; the trampoline calls
    # info_order_hook before bpm_decimals_hook
    h = HOOK_SITE - BASE
    assert out[h + 2:h + 6].hex() == "412b0009"
    lit = ((HOOK_SITE + 9) & ~3) - BASE
    tramp = struct.unpack(">I", out[lit:lit + 4])[0] - BASE
    seg = out[tramp:tramp + 0x100]
    a = seg.find(payloadbuild.lookup(p.symbols, "info_order_hook").to_bytes(4, "big"))
    b = seg.find(payloadbuild.lookup(p.symbols, "bpm_decimals_hook").to_bytes(4, "big"))
    assert 0 <= a < b, (a, b)


@needs_toolchain
@pytest.mark.skipif(not FLASH.is_file(), reason="extract/cdj850/flash_cdj850.bin is not present")
def test_composes_with_the_other_mods():
    mods = ["info_order", "info_order_rating", "bpm_decimals", "bpm_decimals_grid", "bpm999", "hot_cues", "quant_loops"]
    patch_main.patch_flash(FLASH.read_bytes(), mods, "cdj850")


# ---- the payload on the interpreter ----

def _payload(mods=("info_order",)):
    return payloadbuild.build(list(mods), "cdj850")


def _mem(p):
    mem = Memory()
    mem.add(0x0DE00020, p.blob)
    mem.add(0x0DDFF000, bytes(0x1000))           # stack
    mem.add(0x0DDF0000, bytes(0x1000))           # scratch for arguments
    return mem


def _call(p, mem, name, *args):
    """Arguments 1..4 in r4..r7, the rest on the stack (Renesas ABI)."""
    cpu = Sh2a(mem)
    cpu.r[15] = 0x0DDFFF00
    for i, a in enumerate(args[:4]):
        cpu.r[4 + i] = a & 0xFFFFFFFF
    for i, a in enumerate(args[4:]):
        mem.put32(cpu.r[15] + 4 * i, a & 0xFFFFFFFF)
    cpu.run(payloadbuild.lookup(p.symbols, name), CODE_STOP)
    return cpu.r[0]


def _s32(v):
    return v - (1 << 32) if v & 0x80000000 else v


SCRATCH = 0x0DDF0000


@needs_toolchain
def test_sort_on_the_interpreter():
    p = _payload()
    mem = _mem(p)
    for items in (STOCK, STOCK + [0x0A], [0x04, 0x06, 0x07, 0x0E, 0x0D], [0x04, 0x07, 0x02], [0x0D, 0x07],
                  [0x23, 0x0F, 0x0D, 0x0A, 0x02, 0x07, 0x04, 0x29, 0x28, 0x11], [], [0x04, 0x0D]):
        mem.write(SCRATCH, bytes(items).ljust(18, b"\0"))
        ok = _call(p, mem, "info_order_sort", SCRATCH, len(items), SCRATCH + 0x40)
        want = ref_sort(items)
        assert bool(ok) == (want is not None), items
        if want is not None:
            assert list(mem.read(SCRATCH + 0x40, len(items))) == want, items


@needs_toolchain
def test_key_semitones_and_text_on_the_interpreter():
    p = _payload()
    mem = _mem(p)
    keys = ["Am", "F#m", "Dbm", "C#m", "Abm", "C", "Eb", "Bb", "B", "E", "Amin", "Ebmaj", "8A", "12b", "1m",
            "6d", "G#", "Gm", " Fm", "13A", "H", "x", "0A"]
    for k in keys:
        mem.write(SCRATCH, k.encode() + b"\0")
        n = _call(p, mem, "info_order_camelot", SCRATCH, SCRATCH + 0x40)
        want = ref_camelot(k)
        if want is None:
            assert n == 0, k
        else:
            assert (n, mem.u32(SCRATCH + 0x40) == 1) == want, (k, n)
    for pitch in list(range(-10000, 10001, 37)) + [293, 294, -284, -285, 905, 906, 9430, 9431]:
        assert _s32(_call(p, mem, "info_order_semitones", pitch)) == ref_semitones(pitch), pitch
    for n in range(1, 13):
        for s in range(-12, 13):
            assert _call(p, mem, "info_order_camelot_shift", n, s) == ref_shift(n, s), (n, s)
    for x100, ok, key, mt, pitch in ((12823, 1, "Am", 1, 300), (12823, 1, "Am", 0, 300), (12823, 1, "", 0, 0),
                                     (0, 0, "F#m", 0, -600), (17450, 1, "foo", 0, 0), (99999, 1, "12B", 0, 10000),
                                     (6000, 1, "Dbm", 0, -1600)):
        mem.write(SCRATCH, key.encode() + b"\0")
        n = _call(p, mem, "info_order_title_text", SCRATCH + 0x40, x100, ok, SCRATCH, mt, pitch, 0)
        assert mem.read(SCRATCH + 0x40, n + 1) == ref_title(x100, ok, key, mt, pitch).encode() + b"\0"
    for bpm100, pitch in ((12800, 0), (12796, 327), (12800, -1600), (65535, 10000)):
        assert _call(p, mem, "info_order_x100", bpm100, pitch) == ref_x100(bpm100, pitch)


def _screen_mem(p, items, mode=10, key="Am", bpm100=12823, bpm_ok=1, pitch=0,
                mt=1, usb=1, track=7, rating=None):
    mem = _mem(p)
    recs = bytearray(3 * 0x864)
    struct.pack_into(">I", recs, 0, 3)                         # slot 0: in use
    struct.pack_into(">h", recs, 0x14, track)
    struct.pack_into(">II", recs, 0x18, bpm_ok, bpm100)
    if rating is not None:
        recs[0x5A8:0x5AD] = b"\x04" * rating + b"\x05" * (5 - rating)
    recs[0x730:0x730 + len(key) + 1] = key.encode() + b"\0"
    recs[0x851] = len(items)
    recs[0x852:0x852 + len(items)] = bytes(items)
    mem.add(RECORDS, recs)
    rows, src = bytearray(3 * 0x105), bytearray(0x300)
    for r, (attr, text) in enumerate([(0x04, "GRID128"), (0x07, "make_test_stick"), (0x00, "")]):
        t = text.encode()
        rows[r * 0x105:r * 0x105 + 5 + len(t)] = bytes([r + 1, attr, 0, 0, len(t)]) + t
        src[r * 0x100:r * 0x100 + len(t)] = t
    mem.add(ROWS, rows)
    mem.add(SOURCES, src)
    mem.add(MODE, struct.pack(">I", mode))
    mem.add(TRACK, struct.pack(">h", 7))
    mem.add(PITCH, struct.pack(">h", pitch))
    mem.add(MASTER_TEMPO, bytes([mt]))
    mem.add(USB_MODE, bytes([usb]))
    return mem


def _row(mem, r):
    rec = mem.read(ROWS + r * 0x105, 0x30)
    return rec[1], rec[5:5 + rec[4]].decode()


def _src(mem, r):
    return mem.read(SOURCES + r * 0x100, 0x40).split(b"\0")[0].decode()


@needs_toolchain
def test_pass_reorders_and_writes_the_title_row():
    p = _payload()
    rows = payloadbuild.lookup(p.symbols, "info_order_rows")
    lists = payloadbuild.lookup(p.symbols, "info_order_lists")
    mem = _screen_mem(p, STOCK + [0x0A], pitch=300, mt=0)
    _call(p, mem, "info_order_pass")
    assert list(mem.read(RECORDS + 0x852, 7)) == [0x04, 0x0D, 0x07, 0x0A, 0x0F, 0x02, 0x23]
    assert _row(mem, 1) == (0x0D, "132.08 3A") and _src(mem, 1) == "132.08 3A"   # 128.23 * 1.03, Am + 1
    assert _row(mem, 0) == (0x04, "GRID128") and _row(mem, 2) == (0x00, "")
    assert (mem.u32(rows), mem.u32(lists)) == (1, 1)
    _call(p, mem, "info_order_pass")                    # unchanged: nothing written again
    assert (mem.u32(rows), mem.u32(lists)) == (1, 1)
    mem.write(MASTER_TEMPO, b"\x01")                    # MASTER TEMPO on: the key as analysed
    _call(p, mem, "info_order_pass")
    assert _row(mem, 1) == (0x0D, "132.08 8A") and mem.u32(rows) == 2
    # stock cases: the INFO list view, no USB, a list without a BPM entry
    for kw, want_list in ((dict(mode=11), [0x04, 0x0D]), (dict(usb=0), STOCK[:2]), (dict(items=[4, 7, 2]), [4, 7])):
        args = dict(items=STOCK, mode=10, usb=1)
        args.update(kw)
        items = args.pop("items")
        mem = _screen_mem(p, items, **args)
        _call(p, mem, "info_order_pass")
        assert _row(mem, 1) == (0x07, "make_test_stick"), kw
        assert list(mem.read(RECORDS + 0x852, 2)) == want_list, kw
    # no key: the BPM alone; no BPM in the record: the key alone
    mem = _screen_mem(p, STOCK, key="")
    _call(p, mem, "info_order_pass")
    assert _row(mem, 1) == (0x0D, "128.23")
    mem = _screen_mem(p, STOCK, bpm_ok=0, key="F#m")
    _call(p, mem, "info_order_pass")
    assert _row(mem, 1) == (0x0D, "11A")


@needs_toolchain
@pytest.mark.parametrize("rating", range(6))
def test_title_row_rating_on_the_interpreter(rating):
    p = _payload()
    mem = _screen_mem(p, STOCK + [0x0A], key="12A", bpm100=12345, rating=rating)
    _call(p, mem, "info_order_pass")
    want = "123.45 12A" + (" " + "*" * rating if rating else "")
    assert _row(mem, 1) == (0x0D, want)
    assert _src(mem, 1) == want
    assert len(want) <= 16


# ---- live ----

_STICK = r'''
import json, sys, wave, pathlib
import numpy as np
root, out_dir, image, baken = sys.argv[1:5]
sys.path.insert(0, root)
import make_test_stick as stick
def render(track, path):
    rate = 44100
    t = np.arange(round(stick.track_length(track) * rate)) / rate
    phase = t % (60 / track['bpm'])
    s = np.rint(0.65 * np.sin(2 * np.pi * 55 * phase) * np.exp(-25 * phase) * 32767).astype('<i2')
    with wave.open(path, 'wb') as w:
        w.setparams((2, 2, rate, 0, 'NONE', 'not compressed'))
        w.writeframes(np.repeat(s, 2).tobytes())
    return True
stick.have_ffmpeg = lambda: True
stick.render_audio = render
stick.file_name = lambda n, title: f'{title}.wav'
pathlib.Path(out_dir).mkdir(parents=True, exist_ok=True)
spec = {"device_name": "INFOORD", "playlist": "Info", "tracks": [
    {"title": "KEYAM", "bpm": 128.0, "first_downbeat": 0, "bars": 32, "key": "Am", "rating": 5}]}
sp = pathlib.Path(out_dir) / 'spec.json'
sp.write_text(json.dumps(spec))
raise SystemExit(stick.main(['--spec', str(sp), '--out', out_dir + '/wav-stick', '--image', image,
                             '--size-mb', '32', '--baken', baken]))
'''


def _udp(deck, msg):
    import cdj_panelsock
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.sendto(msg.encode(), (cdj_panelsock.HOST, cdj_panelsock.port_for(deck.keysock)))
    s.close()


@needs_toolchain
@pytest.mark.skipif(not FLASH.is_file(), reason="extract/cdj850/flash_cdj850.bin is not present")
def test_live_info_screens(tmp_path):
    import importlib.util
    if os.name == "nt":
        pytest.skip("QMP socket descriptor inheritance requires POSIX")
    if not BAKEN.is_file() or importlib.util.find_spec("pyfatfs") is None or importlib.util.find_spec("numpy") is None:
        pytest.skip("tools/baken/baken, pyfatfs and numpy are needed for the stick")
    image = tmp_path / "info.img"
    subprocess.run([sys.executable, "-c", _STICK, str(ROOT / "scripts" / "media"), str(tmp_path / "stick"),
                    str(image), str(BAKEN)], check=True, capture_output=True, text=True, timeout=300)
    flash = tmp_path / "flash.bin"
    flash.write_bytes(patch_main.patch_flash(FLASH.read_bytes(),
                                             ["info_order", "info_order_rating", "bpm_decimals", "bpm_decimals_grid"], "cdj850"))
    sys.path.insert(0, str(ROOT / "scripts" / "debug"))
    import cdj850_trace as tr

    def screen(d):
        d.q("stop")
        v = {"mode": d.rw(MODE), "list": list(d.dump(RECORDS + 0x851, 8)), "mt": d.rb(MASTER_TEMPO)}
        for r in range(3):
            rec = d.dump(ROWS + r * 0x105, 0x30)
            v["r%d" % r] = (rec[1], rec[5:5 + rec[4]].decode("latin1"))
        d.q("cont")
        return v

    res = {}
    with tr.Deck(image, flash=flash) as d:
        d.wait_line("mode=0x0a", "[KEYAM]", timeout=300)
        time.sleep(1.5)
        for _ in range(4):
            if d.rb(0x0CE6D25F) == 3:
                break
            d.key("play")
            time.sleep(2.5)
        time.sleep(1.0)
        res["title"] = screen(d)
        d.key("info")
        time.sleep(1.5)
        for k in range(1, 6):
            _udp(d, "0x03:1:0:rot")
            time.sleep(1.2)
            res["rot%d" % k] = screen(d)
        for _ in range(5):                          # back to the title view
            _udp(d, "0x03:-1:0:rot")
            time.sleep(1.2)
        _udp(d, "0x05:%d:0:lvl" % 0x56)             # pitch fader to about +3.35 %, MASTER TEMPO off
        _udp(d, "0x06:%d:0:lvl" % 0x00)
        time.sleep(2.5)
        res["pitch"] = screen(d)
        res["pitch"]["pitch"] = d.rh(PITCH)
        d.key("master_tempo")                       # MT on: the key as analysed
        time.sleep(1.5)
        res["mt"] = screen(d)
    print("info_order live:", res)
    t = res["title"]
    n = t["list"][0]
    assert t["list"][1:1 + n] == [0x04, 0x0D, 0x07, 0x0A, 0x0F, 0x02, 0x23], t
    assert t["mode"] == 10 and t["r0"] == (0x04, "KEYAM") and t["r2"][0] == 0x00, t
    assert t["r1"] == (0x0D, "128.00 8A *****"), t                       # grid 128, Am, pitch 0
    r = res["rot2"]
    assert r["mode"] == 11 and [r["r0"][0], r["r1"][0], r["r2"][0]] == [0x07, 0x0A, 0x0F], r
    r = res["rot5"]
    assert r["mode"] == 11 and [r["r0"][0], r["r1"][0]] == [0x02, 0x23], r
    v = res["pitch"]
    pitch = v["pitch"] - 0x10000 if v["pitch"] & 0x8000 else v["pitch"]
    assert v["mode"] == 10 and v["mt"] == 0 and 294 <= pitch < 906, v          # one semitone up
    assert v["r1"] == (0x0D, ref_title(ref_x100(12800, pitch), True, "Am", False, pitch) + " *****"), v
    assert v["r1"][1].endswith(" 3A *****"), v                                        # Am + 1 = Bbm = 3A
    v = res["mt"]
    assert v["mt"] == 1 and v["r1"][1].endswith(" 8A *****"), v
