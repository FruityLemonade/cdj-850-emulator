# SPDX-License-Identifier: GPL-2.0-or-later
"""bpm_decimals on the CDJ-850 MAIN 1.13: the actual BPM with two decimals next to the rekordbox
BPM in the INFO view ("128.0 bpm" -> "132.25") while a beat grid gave the BPM.

* static: the two patch sites (display_stage_bpm tail hook at 0x0C0642AA, grid_find_beat pool
  redirect at 0x0C073830), stock bytes, both outside the boot-critical closure, and the
  composition with the mods already on the branch;
* the compiled payload on the SH-2A interpreter (tests/sh2a.py) against a Python reference: the
  rounding, the beat search in the grid window, the text format and the row rewrite;
* live (QEMU + a baken stick with 127.96 / 128.00 / 174.50 BPM grids and a 120.00 -> 133.33
  variable grid): the INFO view's BPM row record, pitch moved with the fader.
"""
import json
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

import patch_main  # noqa: E402
import payloadbuild  # noqa: E402
from sh2a import Memory, Sh2a  # noqa: E402

MAIN = ROOT / "extract" / "cdj850" / "main_unpacked.bin"
FLASH = ROOT / "extract" / "cdj850" / "flash_cdj850.bin"
BAKEN = ROOT / "tools" / "baken" / "baken"
BASE = 0x0C000000
HOOK_SITE = 0x0C0642AA        # display_stage_bpm tail: span 0x0C0642AA..0x0C0642B8 (bpm999's)
GRID_POOL = 0x0C073830        # FUN_0C073788's pool word for grid_find_beat
GRID_FIND_BEAT = 0x0C0012E4
ROWS = 0x0C9D0B18             # three row records, 0x105 apart
SOURCES = 0x0C9D196F          # their text sources, 0x100 apart
MODE = 0x0C9D0AF8             # display mode word, 10 = loaded track
BASE_TENTHS = 0x0C9D23EC
PITCH = 0x0CE6CA50
PLAY_FRAME = 0x0C7F647C
USB_MODE = 0x0CE6D25A
GRID_CTX = 0x0C9D24FC
MODS = ["bpm_decimals", "bpm_decimals_grid"]

needs_toolchain = pytest.mark.skipif(not payloadbuild.have_toolchain(), reason="sh-elf toolchain not installed")
needs_main = pytest.mark.skipif(not MAIN.is_file(), reason="extract/cdj850/main_unpacked.bin is not present")


# ---- Python reference of payload/bpm_decimals.c ----

def ref_x100(bpm100, pitch):
    pitch = max(-10000, min(10000, pitch))
    return min(99999, (bpm100 * (10000 + pitch) + 5000) // 10000)


def ref_beat_bpm100(window, w, total, ms):
    """window = [(beat_in_bar, bpm100, time_ms)] of window w; 0 = the window cannot say."""
    first = w * 500
    if total <= first:
        return 0
    n = min(total - first, 1000)
    if ms < window[0][2]:
        return 0 if w else window[0][1]
    if ms >= window[n - 1][2] and first + n < total:
        return 0
    lo = max(i for i in range(n) if window[i][2] <= ms)
    return window[lo][1]


def ref_base_from_grid(base, bpm100):
    t = (bpm100 + 5) // 10
    return base == t or (t > base and base in (1800, 9990))


# ---- static ----

@needs_main
@needs_toolchain
def test_static_sites_and_stock_bytes_outside_the_boot_critical_closure():
    import bootcrit
    stock = MAIN.read_bytes()
    report = {}
    out = patch_main.patch(stock, MODS, "cdj850", report)
    assert {n: s for n, (s, _) in report.items() if n in MODS} == {n: "applied" for n in MODS}
    p = payloadbuild.build(["bpm_decimals"], "cdj850")
    h = HOOK_SITE - BASE
    assert stock[h:h + 14].hex() == "d62e626022288902d62a60618154"
    # call site: mov.l @(lit),r1; jmp @r1; nop; .long trampoline (in the cave)
    assert out[h + 2:h + 6].hex() == "412b0009"
    lit = ((HOOK_SITE + 9) & ~3) - BASE
    tramp = struct.unpack(">I", out[lit:lit + 4])[0]
    assert 0x0C0172B4 <= tramp < 0x0C0172B4 + 1118
    hook = payloadbuild.lookup(p.symbols, "bpm_decimals_hook")
    assert hook.to_bytes(4, "big") in out[tramp - BASE:tramp - BASE + 0x80]
    g = GRID_POOL - BASE
    assert struct.unpack(">I", stock[g:g + 4])[0] == GRID_FIND_BEAT
    assert struct.unpack(">I", out[g:g + 4])[0] == payloadbuild.lookup(p.symbols, "__gate_bpm_decimals_find_beat")
    # every byte the mod changes outside the cave and the loader is in one of the two sites,
    # and neither site (nor the code reading the pool word, 0x0C0737B8) is boot-critical
    closure = bootcrit.closure(stock, BASE, byteorder=">")
    for lo, hi in ((HOOK_SITE, HOOK_SITE + 14), (GRID_POOL, GRID_POOL + 4), (0x0C0737B8, 0x0C0737BA),
                   (0x0C064118, 0x0C064120), (0x0C073788, 0x0C073790)):
        assert not closure.hit(lo - BASE, hi - BASE), hex(lo)
    for lo, hi in ((0x0C064118, 0x0C064300), (0x0C073788, 0x0C073838)):
        diff = [a for a in range(lo, hi) if stock[a - BASE] != out[a - BASE]]
        assert all(GRID_POOL <= a < GRID_POOL + 4 or HOOK_SITE <= a < HOOK_SITE + 14 for a in diff), diff
    # the frame-digit / TX ISR path of the first version is gone
    assert stock[0x0C0FA05E - BASE:0x0C0FA13C - BASE] == out[0x0C0FA05E - BASE:0x0C0FA13C - BASE]


@needs_main
@needs_toolchain
def test_composes_with_the_branch_mods():
    names = ["autostandby", "bpm999", "bpm999grid", "bpm999range", "bpm_probe", "dspprobe",
             "payload_demo", "payload_demo_redirect", "tempowide", "tempowidemath1", "tempowidemath2"] + MODS
    report = {}
    patch_main.patch(MAIN.read_bytes(), names, "cdj850", report)
    assert all(report[n][0] == "applied" for n in names if n in report), report
    assert set(MODS) <= set(report)


@needs_toolchain
def test_payload_has_no_fpu_code_and_small_frames():
    p = payloadbuild.build(["bpm_decimals"], "cdj850")
    text = p.blob[:p.symbols["__bss_start"] - 0x0DE00020]
    # objdump-checked: functions are followed by their pools; scan only the code words up to each pool
    for name in ("bpm_decimals_x100", "bpm_decimals_find_beat", "bpm_decimals_hook", "bpm_decimals_format"):
        at = payloadbuild.lookup(p.symbols, name) - 0x0DE00020
        words = struct.unpack(">8H", text[at:at + 16])
        assert not [w for w in words if w >> 12 == 0xF], name
    assert max(p.stack.values()) <= 64           # bpm_decimals_rows: 24-byte text buffer


# ---- interpreter ----

CODE_STOP = Sh2a.RET


def _payload_mem(p):
    mem = Memory()
    mem.add(0x0DE00020, p.blob)
    mem.add(0x0DDFF000, bytes(0x1000))           # stack
    return mem


def _call(p, mem, name, *args):
    cpu = Sh2a(mem)
    cpu.r[15] = 0x0DE00000
    for i, a in enumerate(args):
        cpu.r[4 + i] = a & 0xFFFFFFFF
    cpu.run(payloadbuild.lookup(p.symbols, name), CODE_STOP)
    return cpu.r[0]


@needs_toolchain
def test_x100_matches_the_reference_on_the_interpreter():
    p = payloadbuild.build(["bpm_decimals"], "cdj850")
    mem = _payload_mem(p)
    cases = [(12796, 0), (12800, 0), (17450, 0), (12796, 327), (12800, 327), (17450, -1600),
             (60000, 10000), (65535, 10000), (12000, -10000), (12796, 12000), (13333, 50), (9999, 1)]
    for bpm100, pitch in cases:
        got = _call(p, mem, "bpm_decimals_x100", bpm100, pitch)
        assert got == ref_x100(bpm100, pitch), (bpm100, pitch, got)
    assert ref_x100(12796, 327) == 13214            # 127.96 * 1.0327 = 132.1446 -> 132.14
    assert ref_x100(12800, 327) == 13219            # 128 * 1.0327 = 132.1856 -> 132.19
    assert ref_x100(65535, 10000) == 99999          # never more than 999.99


def _grid_mem(p, entries, w=0, total=None):
    mem = _payload_mem(p)
    ctx = bytearray(0x7C + 8 * 1000)
    struct.pack_into(">I", ctx, 0x74, len(entries) + 500 * w if total is None else total)
    struct.pack_into(">H", ctx, 0x78, w)
    for i, e in enumerate(entries):
        struct.pack_into(">HHI", ctx, 0x7C + 8 * i, *e)
    mem.add(GRID_CTX, ctx)
    return mem


@needs_toolchain
def test_beat_search_follows_a_variable_grid():
    p = payloadbuild.build(["bpm_decimals"], "cdj850")
    # 120.00 for 32 beats (500 ms), then 133.33 (450 ms)
    entries = [((i % 4) + 1, 12000, 500 * i) for i in range(32)]
    entries += [((i % 4) + 1, 13333, 16000 + 450 * (i - 32)) for i in range(32, 64)]
    mem = _grid_mem(p, entries)
    for ms in (0, 1, 499, 500, 15999, 16000, 16001, 16449, 30000, 99999):
        got = _call(p, mem, "bpm_decimals_beat_bpm100", ms)
        assert got == ref_beat_bpm100(entries, 0, len(entries), ms), (ms, got)
    assert ref_beat_bpm100(entries, 0, 64, 15999) == 12000 and ref_beat_bpm100(entries, 0, 64, 16000) == 13333
    # a later window that does not reach `ms` cannot say (another window holds it)
    later = entries[32:]                                              # window 1 starts at 16000 ms
    mem = _grid_mem(p, later, w=1, total=500 + len(later))
    assert _call(p, mem, "bpm_decimals_beat_bpm100", 0) == 0          # before window 1: window 0 holds it
    assert _call(p, mem, "bpm_decimals_beat_bpm100", 16500) == 13333
    mem = _grid_mem(p, later, w=1, total=500 + len(later) + 10)
    assert _call(p, mem, "bpm_decimals_beat_bpm100", 99999) == 0      # past its last entry, more beats follow


def ref_format(number, x100):
    return "%d.%02d" % (x100 // 100, x100 % 100)     # the DB number is not shown


def _rows_mem(p, base, pitch, frame, row_items, entries, grid_ok=1, grid_bpm100=12796, usb=1, mode=11):
    """row_items: three (attr0, text) for the row records and their text sources."""
    mem = _grid_mem(p, entries)
    s = p.symbols
    for name, val in (("bpm_decimals_grid_ok", grid_ok), ("bpm_decimals_grid_bpm100", grid_bpm100)):
        mem.put32(payloadbuild.lookup(s, name), val)
    rows, src = bytearray(3 * 0x105), bytearray(0x300)
    for r, (attr, text) in enumerate(row_items):
        t = text.encode()
        rows[r * 0x105:r * 0x105 + 5 + len(t)] = bytes([r + 1, attr, 0, 0, len(t)]) + t
        src[r * 0x100:r * 0x100 + len(t)] = t
    mem.add(ROWS, rows)
    mem.add(SOURCES, src)
    mem.add(MODE, struct.pack(">I", mode))
    mem.add(BASE_TENTHS, struct.pack(">H", base))
    mem.add(PITCH, struct.pack(">h", pitch))
    mem.add(PLAY_FRAME, struct.pack(">I", frame))
    mem.add(USB_MODE, bytes([usb]))
    return mem


def _row_texts(mem):
    out = []
    for r in range(3):
        rec = mem.read(ROWS + r * 0x105, 0x20)
        out.append((rec[1], rec[5:5 + rec[4]].decode()))
    return out


INFO_ROWS = [(0x02, "Test stick"), (0x0D, "128.0 bpm"), (0x00, "")]


@needs_toolchain
def test_format_and_stock_number_on_the_interpreter():
    p = payloadbuild.build(["bpm_decimals"], "cdj850")
    for number, x100 in (("128.0", 13225), ("128.0", 12796), ("174.5", 18035), ("99.9", 9999),
                         ("999.0", 99999), ("60.0", 6000), ("1.0", 5)):
        mem = _payload_mem(p)
        mem.add(0x0DDFE000, number.encode().ljust(0x1000, b"\0"))
        n = _call(p, mem, "bpm_decimals_format", 0x0DDFE800, x100)
        assert mem.read(0x0DDFE800, n).decode() == ref_format(number, x100), (number, x100)
    assert ref_format("128.0", 13225) == "132.25"
    for text, n in (("128.0 bpm", 5), ("74.5 bpm", 4), ("999.9 bpm", 5), ("128 bpm", 0), ("128.0bpm", 0),
                    ("1x8.0 bpm", 0), ("Test stick", 0), ("128.0 bpm ", 0)):
        mem = _payload_mem(p)
        mem.add(0x0DDFE000, text.encode() + bytes(8))
        assert _call(p, mem, "bpm_decimals_stock_number", 0x0DDFE000, len(text)) == n, text


@needs_toolchain
def test_info_row_rewrite_on_the_interpreter():
    p = payloadbuild.build(["bpm_decimals"], "cdj850")
    writes = payloadbuild.lookup(p.symbols, "bpm_decimals_writes")
    grid = [((i % 4) + 1, 12796, (i * 60000) // 12796) for i in range(64)]
    # 127.96 at +3.35 %: 132.2467 -> "132.25"; the other rows are untouched
    mem = _rows_mem(p, 1280, 335, 900, INFO_ROWS, grid)
    _call(p, mem, "bpm_decimals_rows")
    assert _row_texts(mem) == [(0x02, "Test stick"), (0x0D, "132.25"), (0x00, "")]
    assert mem.u32(writes) == 1
    _call(p, mem, "bpm_decimals_rows")                 # unchanged value: the row is not rewritten
    assert mem.u32(writes) == 1
    mem.write(PITCH, struct.pack(">h", 0))             # fader back: 127.96
    _call(p, mem, "bpm_decimals_rows")
    assert _row_texts(mem)[1] == (0x0D, "127.96") and mem.u32(writes) == 2
    # the gate closes (base from the BPM counter): the row goes back to the stock text
    mem.write(BASE_TENTHS, struct.pack(">H", 1279))
    _call(p, mem, "bpm_decimals_rows")
    assert _row_texts(mem) == INFO_ROWS
    # stock cases: no grid lookup yet, CD mode, another display mode, base 0 (clears the flag)
    for kw in (dict(grid_ok=0), dict(usb=0), dict(mode=1), dict(base=0)):
        args = dict(base=1280, pitch=335, frame=900, row_items=INFO_ROWS, entries=grid)
        args.update(kw)
        mem = _rows_mem(p, **args)
        _call(p, mem, "bpm_decimals_rows")
        assert _row_texts(mem) == INFO_ROWS, kw
    assert mem.u32(payloadbuild.lookup(p.symbols, "bpm_decimals_grid_ok")) == 0
    # a 0x0D row whose source is not a stock BPM text is left alone
    odd = [(0x02, "Test stick"), (0x0D, "--- bpm"), (0x00, "")]
    mem = _rows_mem(p, 1280, 335, 900, odd, grid)
    _call(p, mem, "bpm_decimals_rows")
    assert _row_texts(mem) == odd
    assert ref_base_from_grid(1800, 20000) and ref_base_from_grid(9990, 99999) and not ref_base_from_grid(1279, 12796)


# ---- live ----

# Stick: 127.96, 128.00 and 174.50 BPM grids and a variable grid TVAR (120.00 for 16 s, then
# 133.33), built by scripts/media/make_test_stick.py with baken; TVAR gets two <TEMPO> entries.
_STICK_BUILDER = r'''
# Build a rekordbox stick with non-integer and variable-tempo grids (baken).
import json, sys, wave, pathlib
import numpy as np
root, out_dir, image, baken = sys.argv[1:5]
sys.path.insert(0, root)
import make_test_stick as stick
import collection_xml
_orig_number = stick._number
stick._number = lambda track, key, low, high=None: _orig_number(track, key, low, 999 if key == 'bpm' else high)
VAR = {"TVAR": [(0.0, 120.00), (16.0, 133.33)]}   # (start s, bpm): 120.00 for 8 bars, then 133.33
_orig_track_xml = collection_xml.track_xml
def track_xml(track_id, path, tags, audio, notes, tempo=None, key="", marks=()):
    x = _orig_track_xml(track_id, path, tags, audio, notes, tempo=tempo, key=key, marks=marks)
    if tags["title"] in VAR:
        import re
        lines = "".join('      <TEMPO Inizio="%.3f" Bpm="%.2f" Metro="4/4" Battito="1"/>\n' % (t, b)
                        for t, b in VAR[tags["title"]])
        x, n = re.subn(r'      <TEMPO [^\n]*\n', lines, x, count=1)
        assert n == 1
    return x
collection_xml.track_xml = track_xml
stick.collection_xml = collection_xml
def render(track, path):
    rate = 44100
    t = np.arange(round(stick.track_length(track) * rate)) / rate
    phase = t % (60 / track['bpm'])
    sig = 0.65 * np.sin(2 * np.pi * 55 * phase) * np.exp(-25 * phase)
    s = np.rint(sig * 32767).astype('<i2')
    with wave.open(path, 'wb') as w:
        w.setparams((2, 2, rate, 0, 'NONE', 'not compressed'))
        w.writeframes(np.repeat(s, 2).tobytes())
    return True
stick.have_ffmpeg = lambda: True
stick.render_audio = render
stick.file_name = lambda n, title: f'{title}.wav'
pathlib.Path(out_dir).mkdir(parents=True, exist_ok=True)
tracks = [("T12796", 127.96, 16), ("T12800", 128.00, 16), ("T17450", 174.50, 16), ("TVAR", 120.0, 24)]
spec = {"device_name": "BPMDEC", "playlist": "Bpm Dec", "tracks": [
    {"title": t, "bpm": b, "first_downbeat": 0, "bars": n} for t, b, n in tracks]}
sp = pathlib.Path(out_dir) / 'spec.json'
sp.write_text(json.dumps(spec))
raise SystemExit(stick.main(['--spec', str(sp), '--out', out_dir + '/wav-stick',
                             '--image', image, '--size-mb', '32', '--baken', baken]))
'''


def _stick(tmp_path):
    import importlib.util
    if not BAKEN.is_file() or importlib.util.find_spec("pyfatfs") is None or importlib.util.find_spec("numpy") is None:
        pytest.skip("tools/baken/baken, pyfatfs and numpy are needed for the grid stick")
    image = tmp_path / "bpmdec.img"
    subprocess.run([sys.executable, "-c", _STICK_BUILDER, str(ROOT / "scripts" / "media"),
                    str(tmp_path / "stick"), str(image), str(BAKEN)],
                   check=True, capture_output=True, text=True, timeout=300)
    return image


def _udp(deck, msg):
    import cdj_panelsock
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.sendto(msg.encode(), (cdj_panelsock.HOST, cdj_panelsock.port_for(deck.keysock)))
    s.close()


def _set_fader(deck, value):
    """Tempo fader sample (RX1 bytes 5..6) through the live key socket's `lvl` op."""
    _udp(deck, "0x05:%d:0:lvl" % (value >> 8))
    _udp(deck, "0x06:%d:0:lvl" % (value & 0xFF))


@needs_toolchain
@pytest.mark.skipif(not FLASH.is_file(), reason="extract/cdj850/flash_cdj850.bin is not present")
def test_live_info_view_bpm_row(tmp_path):
    """One boot of the mod: per grid track, INFO view scrolled to the BPM item; the row record the
    panel gets, at pitch 0, with the fader moved, and across the variable grid's tempo change."""
    if os.name == "nt":
        pytest.skip("QMP socket descriptor inheritance requires POSIX")
    sys.path.insert(0, str(ROOT / "scripts" / "debug"))
    import cdj850_trace as tr
    stick = _stick(tmp_path)
    flash = tmp_path / "flash.bin"
    flash.write_bytes(patch_main.patch_flash(FLASH.read_bytes(), MODS, "cdj850"))
    syms = payloadbuild.build(["bpm_decimals"], "cdj850").symbols
    seen = {}

    def snap(d, label):
        d.q("stop")
        rows = []
        for r in range(3):
            b = d.dump(ROWS + r * 0x105, 0x30)
            rows.append((b[1], b[5:5 + b[4]].decode("latin1")))
        v = {"rows": rows, "mode": d.rw(MODE), "frame": d.rw(PLAY_FRAME),
             "pitch": struct.unpack(">h", struct.pack(">H", d.rh(PITCH)))[0],
             "x100": d.rw(payloadbuild.lookup(syms, "bpm_decimals_x100_last")),
             "writes": d.rw(payloadbuild.lookup(syms, "bpm_decimals_writes"))}
        d.q("cont")
        v["bpm_row"] = [t for a, t in rows if a == 0x0D]
        seen[label] = v
        return v

    def wait_next_grid(d, old_fid):
        """The next track's grid: the grid context's file id (ctx+4) changes. In the INFO view the
        emulator's log line carries no title, so the load is read from RAM."""
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            if d.rw(GRID_CTX + 4) != old_fid:
                return
            time.sleep(0.5)
        raise TimeoutError("no new grid after NEXT")

    with tr.Deck(stick, flash=flash) as d:
        d.wait_line("mode=0x0a", "[T12796]", timeout=300)
        for title in ["T12796", "T12800", "T17450", "TVAR"]:
            time.sleep(1.5)
            if d.rb(0x0CE6D25F) != 3:          # USB player state: 3 = playing
                d.key("play")
                time.sleep(2.0)
            if d.rw(MODE) != 11:
                d.key("info")
                time.sleep(1.5)
            # jog +2 from the top of the INFO list shows the BPM item (rows: album, BPM, empty); the
            # list keeps its position across a track change and goes blank past its end, so step
            # back first (a no-op at the top)
            for delta in ([] if title == "T12796" else [-1, -1]) + [1, 1]:
                _udp(d, "0x03:%d:0:rot" % delta)
                time.sleep(1.2)
            time.sleep(0.5)
            snap(d, title)
            time.sleep(2.0)
            snap(d, title + " again")
            if title in ("T12796", "T17450"):
                _set_fader(d, 0x5600)
                time.sleep(2.5)
                snap(d, title + " pitch")
                _set_fader(d, 0x8000)
                time.sleep(2.0)
            if title == "TVAR":
                deadline = time.monotonic() + 120
                while time.monotonic() < deadline and snap(d, "TVAR late")["frame"] < 1300:
                    time.sleep(2)
            else:
                fid = d.rw(GRID_CTX + 4)
                d.key("next")
                wait_next_grid(d, fid)
    print("bpm_decimals live:", json.dumps(seen))
    for title, db, x100 in (("T12796", "128.0", 12796), ("T12800", "128.0", 12800), ("T17450", "174.5", 17450)):
        v = seen[title]
        assert v["mode"] == 11 and v["pitch"] == 0, v
        assert v["bpm_row"] == [ref_format(db, x100)], v              # 127.96
        w = seen[title + " again"]
        assert w["bpm_row"] == v["bpm_row"], (v, w)
    for title, db, bpm100 in (("T12796", "128.0", 12796), ("T17450", "174.5", 17450)):
        v = seen[title + " pitch"]
        assert v["pitch"] != 0 and v["bpm_row"] == [ref_format(db, ref_x100(bpm100, v["pitch"]))], v
    assert seen["TVAR"]["bpm_row"] == [ref_format("120.0", 12000)], seen["TVAR"]
    v = seen["TVAR late"]                                              # after 16 s: 133.33
    assert v["frame"] >= 1300 and v["bpm_row"] == [ref_format("120.0", 13333)], v
