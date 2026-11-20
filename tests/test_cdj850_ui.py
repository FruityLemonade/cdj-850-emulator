# SPDX-License-Identifier: GPL-2.0-or-later
"""c850_ui (mods/payload/common/c850_ui.c) on the CDJ-850 MAIN 1.13: the shared key filter chain, the
mod flags byte and the display flash, with the quantize switch of quant_loops (BEAT SELECT + LOOP IN)
and the hot-cue switch of hot_cues (BEAT SELECT + RELOOP).

* static: quant_loops_keys and hot_cues redirect the same pool word to the same routine (the second
  is 'applied', shared), each mod alone brings it, the flash hooks sit on display_stage_bpm's tail;
* the compiled payload on the SH-2A interpreter (tests/sh2a.py): the quantize combo (saved, the
  fraction toggle undone, keys hidden until released, QUANTIZE ON/OFF requested), quant_loops and
  loop_resize stock while quantize is off, both combos with both mods linked, the flags byte (0x5A
  from earlier hot_cues builds still reads as hot cues on, quantize on), and the flash: title line
  in the loaded-track view, single middle line elsewhere, rows the view rewrites while it lasts
  come back, the rows return after C850_UI_FLASH_MS.
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
KEY_POOL = 0x0C0ED038
K0, K1, K2 = 0x0CE6CA08, 0x0CE6CA0A, 0x0CE6CA0C
D154, D156, UI_LOCK = 0x0CE6D154, 0x0CE6D156, 0x0CE6D151
SETTINGS = 0x0C9DA23C
FLAGS = SETTINGS + 0x22
ROWS, MODE, TICK = 0x0C9D0B18, 0x0C9D0AF8, 0x0CE49F54
LOG = 0x0DDFE000
TEXT = 0x0DDFD000                    # strings handed to c850_ui_flash in the tests
BEAT_SELECT, RELOOP, LOOP_IN = 0x0040, 0x0400, 0x1000
QUANT = ["quant_loops", "quant_loops_auto_send", "quant_loops_auto_send2", "quant_loops_auto_keep",
         "quant_loops_loop_in", "quant_loops_loop_out", "quant_loops_loop_out2", "quant_loops_keys",
         "quant_loops_flash"]
HOT = ["hot_cues", "hot_cues_tick", "hot_cues_select", "hot_cues_select2", "hot_cues_leds", "hot_cues_anlz",
       "hot_cues_flash"]

needs_toolchain = pytest.mark.skipif(not payloadbuild.have_toolchain(), reason="sh-elf toolchain not installed")
needs_main = pytest.mark.skipif(not MAIN.is_file(), reason="extract/cdj850/main_unpacked.bin is not present")


# ---- static ----

@needs_main
@needs_toolchain
@pytest.mark.parametrize("names,folders", [(QUANT, ["quant_loops"]), (HOT, ["hot_cues"]),
                                           (QUANT + HOT, ["hot_cues", "quant_loops"])])
def test_key_word_and_flash_hook(names, folders):
    stock = MAIN.read_bytes()
    report = {}
    out = patch_main.patch(stock, names, "cdj850", report)
    assert {n: report[n][0] for n in names} == {n: "applied" for n in names}, report
    p = payloadbuild.build(folders, "cdj850")
    gate = payloadbuild.lookup(p.symbols, payloadbuild.gate_name("c850_ui_keys"))
    assert struct.unpack_from(">I", stock, KEY_POOL - BASE)[0] == 0x0C0DD492
    assert struct.unpack_from(">I", out, KEY_POOL - BASE)[0] == gate
    if len(folders) == 2:                                 # whichever comes second shares the word
        assert sorted((report["hot_cues"], report["quant_loops_keys"])) == [
            ("applied", ""), ("applied", "shared with quant_loops_keys")]
    # the flash hooks: the display_stage_bpm tail span now jumps to the shared trampoline
    assert out[0x0C0642AA - BASE:0x0C0642B8 - BASE] != stock[0x0C0642AA - BASE:0x0C0642B8 - BASE]
    hook = payloadbuild.lookup(p.symbols, "c850_ui_flash_hook")
    assert struct.pack(">I", hook) in out[0x0C017200 - BASE:0x0C017480 - BASE]


@needs_toolchain
def test_filter_table_has_the_linked_mods():
    for folders, want in ((["quant_loops"], ["quant_loops_key_filter"]), (["hot_cues"], ["hot_cues_key_filter"]),
                          (["loop_resize"], []),
                          (["hot_cues", "quant_loops"], ["hot_cues_key_filter", "quant_loops_key_filter"])):
        p = payloadbuild.build(folders, "cdj850")
        a, b = (payloadbuild.lookup(p.symbols, n) for n in ("c850_ui_filters_start", "c850_ui_filters_end"))
        words = [struct.unpack_from(">I", p.blob, x - payloadbuild.BASE)[0] for x in range(a, b + 4, 4)]
        names = {v: k.lstrip("_") for k, v in p.symbols.items()}
        assert words[0] == 0 and words[-1] == 0
        assert [names[w] for w in words[1:-1]] == want, folders


# ---- interpreter ----

def _sym(p, name):
    a = payloadbuild.lookup(p.symbols, name)
    assert a is not None, name
    return a


def _settings(flags=0, name=b"HISTORY"):
    rec = bytearray(0x58)
    rec[0:3] = b"\xC3\x02\x10"
    rec[3:3 + len(name)] = name
    rec[0x23:0x2B] = b"TAG LIST"
    rec[0x22] = flags
    return bytes(rec)


def _row(tag, attr, text):
    return bytes([tag, attr, 0, 0, len(text)]) + text + bytes(0x105 - 5 - len(text))


def _mem(p, flags=0, mode=10, rows=(b"GRID128", b"make_test_stick", b"")):
    mem = Memory()
    mem.add(0x0DE00020, p.blob)
    mem.add(0x0DDFD000, bytes(0x3000))                    # strings, log, stack
    mem.add(0x0CE6CA00, bytes(0x40))                      # K words
    mem.add(0x0CE6D0E0, bytes(0x200))                     # player bytes (D154, D156, UI lock, USB mode)
    mem.add(0x0CE49F50, bytes(8))                         # 1 ms tick
    mem.add(SETTINGS, _settings(flags))
    mem.add(0x0C9D0AF0, bytes(0x10))                      # popup block, display mode word
    mem.add(ROWS, b"".join(_row(0x25 if r == 0 else r + 1, (4, 7, 0)[r], t) for r, t in enumerate(rows)))
    mem.put32(MODE, mode)
    mem.write(0x0CE6D25A, b"\x01")                        # USB
    _stub(mem, 0x0C0DD492, "rts\nmov #0x55,r0\n")         # the word's stock function (jog), r0 = 0x55
    _stub(mem, 0x0C08E9B0, _logger(0, 1))                 # settings save: log 0
    return mem


def _call(p, mem, name, *args):
    """Args 1..4 in r4..r7, the rest on the stack (Renesas ABI)."""
    cpu = Sh2a(mem)
    cpu.r[15] = 0x0DDFFF00
    for i, a in enumerate(args):
        if i < 4:
            cpu.r[4 + i] = a & 0xFFFFFFFF
        else:
            mem.put32(0x0DDFFF00 + 4 * (i - 4), a)
    cpu.run(_sym(p, name), Sh2a.RET)
    return cpu.r[0]


def _keys(p, mem, k0=0, k1=0, k2=0):
    mem.write(K0, struct.pack(">HHH", k0, k1, k2))
    assert _call(p, mem, "c850_ui_keys") == 0x55
    return struct.unpack(">HHH", mem.read(K0, 6))


def _u8(mem, a):
    return mem.read(a, 1)[0]


def _flash_text(p, mem):
    """The text of the last c850_ui_flash request."""
    a = mem.u32(_sym(p, "fl_text"))
    return bytes(mem.read(a, 16)).split(b"\0")[0].decode() if a else None


def _combo(p, mem, key):
    _keys(p, mem, k0=BEAT_SELECT)
    mem.write(D154, b"\x01")                              # stock toggles fraction mode on the press
    mem.write(D156, b"\x01")
    seen = _keys(p, mem, k0=BEAT_SELECT | key)
    _keys(p, mem)
    return seen


@needs_toolchain
def test_quantize_combo_saves_and_flashes():
    p = payloadbuild.build(["quant_loops"], "cdj850")
    mem = _mem(p)
    assert _call(p, mem, "c850_ui_quantize") == 1                         # fresh deck: on
    assert _combo(p, mem, LOOP_IN) == (0, 0, 0)                           # the combo is ours
    assert _u8(mem, FLAGS) == 0x53 and _call(p, mem, "c850_ui_quantize") == 0
    assert mem.u32(LOG) == 2 and mem.u32(LOG + 4) == SETTINGS and mem.u32(LOG + 28) == 1   # saved
    assert (_u8(mem, D154), _u8(mem, D156)) == (0, 0)                      # fraction toggle undone
    assert mem.read(SETTINGS, 3) == b"\xC3\x02\x10"                        # flag bytes untouched
    assert _flash_text(p, mem) == "QUANTIZE OFF"
    assert mem.u32(_sym(p, "quant_loops_toggles")) == 1
    # back on: the byte goes back to 0 (no flag set), as a fresh deck has it
    _combo(p, mem, LOOP_IN)
    assert _u8(mem, FLAGS) == 0 and _call(p, mem, "c850_ui_quantize") == 1
    assert _flash_text(p, mem) == "QUANTIZE ON" and mem.u32(LOG + 28) == 2
    # LOOP IN alone and LOOP IN before BEAT SELECT stay stock
    assert _keys(p, mem, k0=LOOP_IN) == (LOOP_IN, 0, 0)
    assert _keys(p, mem, k0=LOOP_IN | BEAT_SELECT) == (LOOP_IN | BEAT_SELECT, 0, 0)
    _keys(p, mem)
    assert _u8(mem, FLAGS) == 0
    # BEAT SELECT + RELOOP is not quant_loops' (hot_cues not linked): stock
    _keys(p, mem, k0=BEAT_SELECT)
    assert _keys(p, mem, k0=BEAT_SELECT | RELOOP) == (BEAT_SELECT | RELOOP, 0, 0)
    _keys(p, mem)
    # held after the combo: hidden until released
    _keys(p, mem, k0=BEAT_SELECT)
    _keys(p, mem, k0=BEAT_SELECT | LOOP_IN)
    assert _keys(p, mem, k0=LOOP_IN) == (0, 0, 0)
    assert _keys(p, mem) == (0, 0, 0)
    assert _keys(p, mem, k0=LOOP_IN) == (LOOP_IN, 0, 0)
    _keys(p, mem)
    # UI lock: no combo
    _call(p, mem, "c850_ui_set_flag", 1, 0)
    mem.write(UI_LOCK, b"\x01")
    _keys(p, mem, k0=BEAT_SELECT)
    assert _keys(p, mem, k0=BEAT_SELECT | LOOP_IN) == (BEAT_SELECT | LOOP_IN, 0, 0)
    _keys(p, mem)
    mem.write(UI_LOCK, b"\x00")
    assert _u8(mem, FLAGS) == 0


@needs_toolchain
def test_long_history_name_keeps_the_state_and_says_so():
    p = payloadbuild.build(["quant_loops"], "cdj850")
    mem = _mem(p)
    mem.write(SETTINGS + 3, b"H" * 31)                    # the name's NUL is the flags byte
    assert _combo(p, mem, LOOP_IN) == (0, 0, 0)
    assert _u8(mem, FLAGS) == 0 and mem.u32(LOG + 28) == 0                # nothing written or saved
    assert _call(p, mem, "c850_ui_quantize") == 1 and _flash_text(p, mem) == "QUANTIZE ON"
    assert mem.u32(_sym(p, "quant_loops_toggle_failed")) == 1


@needs_toolchain
@pytest.mark.parametrize("byte,hot,quant", [(0, 0, 1), (0x5A, 1, 1), (0x5B, 1, 0), (0x53, 0, 0),
                                            (0x52, 0, 1), (0x41, 0, 1), (0xFF, 0, 1)])
def test_flags_byte(byte, hot, quant):
    """0x5A is what hot_cues wrote before c850_ui: hot-cue mode on, quantize on. Only 0x50..0x5F
    are flags (a name change or factory reset leaves 0)."""
    p = payloadbuild.build(["hot_cues", "quant_loops"], "cdj850")
    mem = _mem(p, flags=byte)
    assert _call(p, mem, "hot_cues_mode") == hot
    assert _call(p, mem, "c850_ui_quantize") == quant


@needs_toolchain
def test_both_combos_with_both_mods():
    p = payloadbuild.build(["hot_cues", "quant_loops"], "cdj850")
    mem = _mem(p)
    _combo(p, mem, RELOOP)
    assert _u8(mem, FLAGS) == 0x5A and _flash_text(p, mem) == "HOT CUE ON"
    _combo(p, mem, LOOP_IN)
    assert _u8(mem, FLAGS) == 0x5B and _flash_text(p, mem) == "QUANTIZE OFF"
    _combo(p, mem, RELOOP)
    assert _u8(mem, FLAGS) == 0x53 and _flash_text(p, mem) == "HOT CUE OFF"
    _combo(p, mem, LOOP_IN)
    assert _u8(mem, FLAGS) == 0 and _flash_text(p, mem) == "QUANTIZE ON"
    assert mem.u32(LOG + 28) == 4
    assert mem.u32(_sym(p, "c850_ui_shifts")) == 4


@needs_toolchain
def test_quantize_off_leaves_the_grid():
    """quant_loops_grid_ok is the gate of every snapping path; loop_resize falls back to frames."""
    p = payloadbuild.build(["loop_resize", "quant_loops"], "cdj850")
    mem = _mem(p)
    ctx = bytearray(0x7C + 8 * 32)
    struct.pack_into(">IIH", ctx, 0x70, 1, 32, 0)
    for i in range(32):
        struct.pack_into(">HHI", ctx, 0x7C + 8 * i, i % 4 + 1, 12800, 10 + i * 60000 // 128)
    mem.add(0x0C9D24FC, bytes(ctx))
    mem.add(0x0C9D23E0, bytes(0x20))
    mem.write(0x0C9D23EC, struct.pack(">H", 1280))       # the base BPM came from this grid
    t4, t8 = (struct.unpack_from(">I", ctx, 0x7C + 8 * i + 4)[0] for i in (4, 8))
    loop_in, loop_out = (_call(p, mem, "loop_resize_ms_to_frames", t) for t in (t4, t8))

    def resize_mode():                                   # CALL < on the 4-beat loop: 1 grid, 2 frames
        _call(p, mem, "loop_resize_new_out", loop_in, loop_out, 100000, 0, LOG + 0x80, LOG + 0x84)
        return mem.u32(LOG + 0x84)

    assert _call(p, mem, "quant_loops_grid_ok", 4) == 1 and resize_mode() == 1
    mem.write(FLAGS, b"\x53")                            # quantize off
    assert _call(p, mem, "quant_loops_grid_ok", 4) == 0 and resize_mode() == 2


# ---- flash ----

def _rows(mem):
    out = []
    for r in range(3):
        b = mem.read(ROWS + r * 0x105, 5 + 40)
        out.append((bytes(b[:4]).hex(), bytes(b[5:5 + b[4]]).decode()))
    return out


def _flash(p, mem, text):
    mem.write(TEXT, text.encode() + b"\0")
    _call(p, mem, "c850_ui_flash", TEXT)


def _pass(p, mem, ms=None, twice=False):
    """One DisplayTask pass at tick `ms` (the hook runs once per flash hook mod: twice with both)."""
    if ms is not None:
        mem.put32(TICK, ms)
    for _ in range(2 if twice else 1):
        _call(p, mem, "c850_ui_flash_hook", 0, 0)
    return _rows(mem)


@needs_toolchain
def test_flash_in_the_loaded_track_view_takes_the_title_line():
    p = payloadbuild.build(["quant_loops"], "cdj850")
    mem = _mem(p, mode=10)
    stock = _rows(mem)
    assert _pass(p, mem, 1000) == stock                                   # nothing asked: untouched
    _flash(p, mem, "QUANTIZE OFF")
    shown = _pass(p, mem, 1000, twice=True)
    assert shown[0] == (stock[0][0], "QUANTIZE OFF") and shown[1:] == stock[1:]   # tag/attrs kept
    assert _pass(p, mem, 2400, twice=True) == shown                       # 1.4 s: still up
    assert _pass(p, mem, 2500, twice=True) == stock                       # 1.5 s: the title is back
    assert mem.u32(_sym(p, "c850_ui_flash_restores")) == 1
    assert _pass(p, mem, 9000) == stock


@needs_toolchain
def test_flash_elsewhere_is_one_middle_line_and_rows_rewritten_meanwhile_come_back():
    p = payloadbuild.build(["quant_loops"], "cdj850")
    mem = _mem(p, mode=1, rows=(b"GRID128", b"GRID96", b"TRACK3"))
    stock = _rows(mem)
    _flash(p, mem, "QUANTIZE ON")
    shown = _pass(p, mem, 500)
    assert [t for _, t in shown] == ["", "QUANTIZE ON", ""]
    assert [h for h, _ in shown] == [h for h, _ in stock]
    # the browser scrolls while the message is up: the view rewrites row 2, the flash covers it again
    mem.write(ROWS + 2 * 0x105 + 4, bytes([6]) + b"TRACK4")
    assert [t for _, t in _pass(p, mem, 900)] == ["", "QUANTIZE ON", ""]
    # a second request while up restarts the time with the new text
    _flash(p, mem, "QUANTIZE OFF")
    assert [t for _, t in _pass(p, mem, 1200)] == ["", "QUANTIZE OFF", ""]
    assert [t for _, t in _pass(p, mem, 2650)] == ["", "QUANTIZE OFF", ""]
    back = _pass(p, mem, 2700)
    assert [t for _, t in back] == ["GRID128", "GRID96", "TRACK4"] and [h for h, _ in back] == [h for h, _ in stock]


@needs_toolchain
def test_flash_follows_a_view_change():
    """Loaded-track view -> browser while the message is up: the title line goes back, the browser
    rows take the message, and after the time every row is what the views last wrote."""
    p = payloadbuild.build(["quant_loops"], "cdj850")
    mem = _mem(p, mode=10)
    _flash(p, mem, "QUANTIZE ON")
    assert _pass(p, mem, 0)[0][1] == "QUANTIZE ON"
    mem.put32(MODE, 1)
    for r, t in enumerate((b"GRID128", b"GRID96", b"")):     # the browser fills its rows
        mem.write(ROWS + r * 0x105 + 4, bytes([len(t)]) + t)
    assert [t for _, t in _pass(p, mem, 100)] == ["", "QUANTIZE ON", ""]
    assert [t for _, t in _pass(p, mem, 1600)] == ["GRID128", "GRID96", ""]


# ---- live ----

FLASH_BIN = ROOT / "extract" / "cdj850" / "flash_cdj850.bin"
STATE_USB, ACTIVE, BEATS = 0x0CE6D25F, 0x0CE6D0ED, 0x0C1E1088


def drive(stick, flash, syms, work):
    import time
    sys.path.insert(0, str(ROOT / "scripts" / "debug"))
    import cdj850_trace as tr
    S = lambda n: payloadbuild.lookup(syms, n)
    res = {}

    def snap(d, tag):
        d.q("stop")
        v = {"mode": d.rw(MODE), "flags": d.rb(FLAGS), "state": d.rb(STATE_USB), "active": d.rb(ACTIVE),
             "beats": d.rb(BEATS), "d154": d.rb(D154),
             "rows": [d.dump(ROWS + r * 0x105 + 5, d.rb(ROWS + r * 0x105 + 4)).decode("latin1") for r in range(3)]}
        for n in ("quant_loops_auto_count", "quant_loops_toggles", "hot_cues_toggles", "c850_ui_flashes",
                  "c850_ui_flash_restores"):
            v[n] = d.rw(S(n))
        d.q("cont")
        res[tag] = v
        return v

    def ensure_playing(d):
        for _ in range(6):
            st = d.rb(STATE_USB)
            if st == 3:
                return
            d.key("reloop" if st == 4 else "play")
            time.sleep(2.5)

    def wait_deck_ms(d, ms):
        """The emulator runs slower than wall time (about 400-600 deck ms per second): wait on
        the deck's 1 ms tick, which the flash times itself by."""
        t0, end = d.rw(TICK), time.monotonic() + 60
        while (d.rw(TICK) - t0) & 0xFFFFFFFF < ms and time.monotonic() < end:
            time.sleep(0.1)

    def combo(d, key, tag):
        d.hold("beat_select")
        time.sleep(0.6)
        d.key(key)
        time.sleep(0.4)
        snap(d, tag)                                  # the message is up (1.5 s of deck time)
        d.release("beat_select")
        wait_deck_ms(d, 1700)
        time.sleep(0.5)                               # a display pass and a panel burst
        snap(d, tag + "_after")

    log = open(work / "qemu.log", "w")
    with tr.Deck(stick, flash=flash, log=log, writable=True) as d:
        d.wait_line("mode=0x0a", "[GRID128]", timeout=300)
        time.sleep(1.5)
        ensure_playing(d)
        time.sleep(1.0)
        snap(d, "start")
        d.key("beat_4")                               # quantize on: a grid loop
        time.sleep(1.5)
        snap(d, "beat4_on")
        d.key("reloop")
        time.sleep(1.5)
        combo(d, "loop_in", "q_off")                  # QUANTIZE OFF on the title line
        d.key("beat_4")                               # quantize off: the stock loop
        time.sleep(1.5)
        snap(d, "beat4_off")
        d.key("reloop")
        time.sleep(1.5)
        combo(d, "reloop", "hot_on")                  # HOT CUE ON on the title line: 0x5B
        d.key("browse")                               # the browser: one middle line
        time.sleep(2.0)
        snap(d, "browse")
        combo(d, "loop_in", "q_on_browse")            # quantize on again: 0x5A
        time.sleep(2.0)
    with tr.Deck(stick, flash=flash, log=log, writable=True) as d:
        d.wait_line("mode=0x0a", "[GRID128]", timeout=300)
        time.sleep(3.0)
        snap(d, "reboot")
    return res


@needs_toolchain
@pytest.mark.skipif(not FLASH_BIN.is_file(), reason="extract/cdj850/flash_cdj850.bin is not present")
def test_live_quantize_and_hot_cue_switches_on_the_display(tmp_path):
    import json
    import os
    from test_cdj850_hot_cues import ALL_FOLDERS, ALL_MODS, MODS as HOT_MODS, build_stick
    if os.name == "nt":
        pytest.skip("QMP socket descriptor inheritance requires POSIX")
    stick = build_stick(tmp_path)
    flash = tmp_path / "flash.bin"
    names = ALL_MODS + HOT_MODS + ["hot_cues_flash", "quant_loops_keys", "quant_loops_flash"]
    flash.write_bytes(patch_main.patch_flash(FLASH_BIN.read_bytes(), names, "cdj850"))
    syms = payloadbuild.build(ALL_FOLDERS, "cdj850").symbols
    r = drive(stick, flash, syms, tmp_path)
    print("c850_ui live:", json.dumps(r))
    s = r["start"]
    assert s["flags"] == 0 and s["mode"] == 10 and s["rows"][0] == "GRID128" and s["state"] == 3, s
    on = r["beat4_on"]
    assert on["state"] == 4 and on["quant_loops_auto_count"] == s["quant_loops_auto_count"] + 1, on
    q = r["q_off"]
    assert q["rows"][0] == "QUANTIZE OFF" and q["rows"][1:] == s["rows"][1:], q
    assert q["flags"] == 0x53 and q["quant_loops_toggles"] == 1 and q["d154"] == 0, q
    assert q["state"] == 3 and q["active"] == 0, q                       # LOOP IN did not set a loop
    qa = r["q_off_after"]
    assert qa["rows"] == s["rows"], qa                  # (the view rewrites its title row itself)
    off = r["beat4_off"]
    assert off["state"] == 4 and off["beats"] == 4, off
    assert off["quant_loops_auto_count"] == on["quant_loops_auto_count"], off   # not snapped
    h = r["hot_on"]
    assert h["rows"][0] == "HOT CUE ON" and h["flags"] == 0x5B and h["hot_cues_toggles"] == 1, h
    assert r["hot_on_after"]["rows"] == s["rows"]
    b, qb, qba = r["browse"], r["q_on_browse"], r["q_on_browse_after"]
    assert b["mode"] == 1 and qb["mode"] == 1 and qba["mode"] == 1, (b, qb, qba)
    assert qb["rows"] == ["", "QUANTIZE ON", ""] and qb["flags"] == 0x5A, qb
    assert qba["rows"] == b["rows"], (b, qba)
    rb = r["reboot"]
    assert rb["flags"] == 0x5A and rb["rows"][0] == "GRID128" and rb["c850_ui_flashes"] == 0, rb
    # what the panel got (the emulated panel CPU logs every text burst): each message, then the
    # view's rows, nothing in between
    texts = [ln.split("] (")[0].split(" [", 1)[1] for ln in (tmp_path / "qemu.log").read_text("latin1").splitlines()
             if "cdj850-panel: display mode=" in ln]
    seq = [t for i, t in enumerate(texts) if i == 0 or t != texts[i - 1]]
    for msg, view in (("QUANTIZE OFF] | [make_test_stick] | [", "GRID128] | [make_test_stick] | ["),
                      ("HOT CUE ON] | [make_test_stick] | [", "GRID128] | [make_test_stick] | ["),
                      ("] | [QUANTIZE ON] | [", "GRID128] | [GRID96] | [")):
        assert msg in seq and seq[seq.index(msg) + 1] == view, (msg, seq)
