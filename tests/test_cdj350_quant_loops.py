# SPDX-License-Identifier: GPL-2.0-or-later
"""quant_loops on the CDJ-350 MAIN 1.14: loops snap to the rekordbox beat grid.

* static: the eight pool-word redirects (stock words, the instructions that
  read them, all outside the boot-critical closure), composition with flac,
  no FPU code and small stack frames;
* the compiled payload on the SH-2A interpreter (tests/sh2a.py) against a
  Python reference: frame rounding, beat search, auto loop points (4 beats,
  variable tempo, window edge), LOOP IN/OUT points, and all entry points end
  to end including state-6 cued auto loop and per-pass drift correction;
* live (QEMU cdj350 machine + baken stick): auto 4-beat loop (`beat_loop`)
  and manual LOOP IN/OUT (`loop_in` / `loop_out`) pressed off-beat on a
  128.00 BPM track, verified against the grid entries in RAM.
"""
import os
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

MAIN = ROOT / "extract" / "cdj350" / "c350_main_unpacked.bin"
FLASH = ROOT / "extract" / "cdj350" / "flash_cdj350.bin"
BAKEN = ROOT / "tools" / "baken" / "baken"
BASE = 0x0C000000
MODS = [
    "quant_loops", "quant_loops_auto_send",
    "quant_loops_loop_in", "quant_loops_loop_in2", "quant_loops_loop_in3",
    "quant_loops_loop_out", "quant_loops_loop_out2", "quant_loops_loop_out3",
    "quant_loops_drift",
]
NEXT_SECTOR = 0x0C006A92
AUDIO_SEND_CMD = 0x0C0C7850
STATE4_KEYS = 0x0C0D28AC

POOLS = [
    (0x0C0CE8B4, NEXT_SECTOR, [0x0C0CE696, 0x0C0CE848], "quant_loops_auto_in"),
    (0x0C0CEBCC, AUDIO_SEND_CMD, [
        0x0C0CE7E0, 0x0C0CE7FC, 0x0C0CE82E, 0x0C0CEA0E, 0x0C0CEA3C,
        0x0C0CEA5A, 0x0C0CEA7E, 0x0C0CEAC8, 0x0C0CEAE6, 0x0C0CEB0A, 0x0C0CEB70,
    ], "quant_loops_auto_send"),
    (0x0C0D1380, NEXT_SECTOR, [0x0C0D1270], "quant_loops_loop_in"),
    (0x0C0D2728, NEXT_SECTOR, [0x0C0D260A], "quant_loops_loop_in"),
    (0x0C0D31B8, NEXT_SECTOR, [0x0C0D30C2], "quant_loops_loop_in"),
    (0x0C0D1688, AUDIO_SEND_CMD, [
        0x0C0D1410, 0x0C0D15D4, 0x0C0D15E2, 0x0C0D1628, 0x0C0D1636, 0x0C0D1668,
    ], "quant_loops_loop_out"),
    (0x0C0D1984, AUDIO_SEND_CMD, [
        0x0C0D172A, 0x0C0D1782, 0x0C0D178E, 0x0C0D18D8, 0x0C0D18E4,
        0x0C0D1930, 0x0C0D193A, 0x0C0D1964, 0x0C0D196E,
    ], "quant_loops_loop_out"),
    (0x0C0D1BB8, AUDIO_SEND_CMD, [0x0C0D1A3C, 0x0C0D1A46], "quant_loops_loop_out"),
    (0x0C0C7F7C, STATE4_KEYS, [0x0C0C7D92], "quant_loops_drift_entry"),
]

GRID_CTX = 0x0C9B8F34
BASE_TENTHS = 0x0C9B8E24
USB_MODE = 0x0CD48D0E
SOURCE = 0x0CD48D06
PLAYER_STATE = 0x0CD48D11     # + source
LOOP_ACTIVE = 0x0CD48BD1
LOOP_OUT_CMD = 0x0CD48CCC
LOOP_D1C0 = 0x0CD48574
CUE_FRAME = 0x0C7E4D4C
CUE_TRACK = 0x0CD48D5A
IN_SECTOR = 0x0C7E4D1C
PLAY_FRAME = 0x0C7E4D30
LOOP_IN = 0x0C7E4D4C
LOOP_OUT = 0x0C7E4D74
OUT_REQ = 0x0C7E4D8C
TRACK_LENGTH = 0x0C7E4DBC
SECTOR_TABLE = 0x0C73F4F0     # 16 bytes per ring sector, +0 = frame
TRACK_TABLE = 0x0C73F4F8      # +8 = track
RING_ALT = 0x0C7E4F78
TRACKLEN_TABLE = 0x0CD3D7E0   # + slot * 0x33C

needs_toolchain = pytest.mark.skipif(not payloadbuild.have_toolchain(), reason="sh-elf toolchain not installed")
needs_main = pytest.mark.skipif(not MAIN.is_file(), reason="extract/cdj350/c350_main_unpacked.bin is not present")


def ms_to_frames(ms):
    return (ms * 75 + 500) // 1000


class Grid:
    def __init__(self, entries, w=0, total=None, base=None):
        self.e, self.w = entries, w
        self.total = len(entries) + 500 * w if total is None else total
        self.base = (entries[0][1] + 5) // 10 if base is None else base

    def ok(self, i):
        if not self.e or self.base == 0:
            return False
        for j in range(max(0, i - 8), min(len(self.e), i + 9)):
            t = (self.e[j][1] + 5) // 10
            if t == self.base or (t > self.base and self.base == 1800):
                return True
        return False

    def f(self, i):
        return ms_to_frames(self.e[i][2])

    def at_or_before(self, frame):
        if not self.e:
            return -2
        if frame < self.f(0):
            return -1 if self.w == 0 else -2
        if frame >= self.f(len(self.e) - 1):
            return -2 if 500 * self.w + len(self.e) < self.total else len(self.e) - 1
        lo, hi = 0, len(self.e) - 1
        while hi - lo > 1:
            mid = (lo + hi) // 2
            if self.f(mid) <= frame:
                lo = mid
            else:
                hi = mid
        return lo

    def nearest(self, frame):
        i = self.at_or_before(frame)
        if i == -2:
            return -2
        if i == -1:
            return 0
        if i + 1 >= len(self.e):
            return i
        return i if frame - self.f(i) <= self.f(i + 1) - frame else i + 1

    def ms_after(self, i, k):
        if i + k < len(self.e):
            return self.e[i + k][2]
        bpm100 = self.e[i][1] or 13000
        return self.e[i][2] + (k * 6000000 + bpm100 // 2) // bpm100

    def auto_points(self, pos, beats=4):
        i = self.nearest(pos)
        if i < 0 or not self.ok(i):
            return None
        fin = ms_to_frames(self.e[i][2])
        fout = max(fin + 1, ms_to_frames(self.ms_after(i, beats)))
        return None if fout <= pos else (fin, fout)

    def in_point(self, pos):
        i = self.nearest(pos)
        return pos if i < 0 or not self.ok(i) else self.f(i)

    def out_point(self, loop_in, pos):
        i = self.nearest(pos)
        if i < 0 or not self.ok(i):
            return pos
        if self.f(i) <= loop_in:
            i = self.at_or_before(loop_in)
            if i == -2:
                return pos
            i = 0 if i == -1 else i + 1
            if i >= len(self.e) or self.f(i) <= loop_in:
                return pos
        return self.f(i)


def _grid(n, bpm100, start_ms=0):
    if callable(bpm100):
        out, t = [], start_ms
        for k in range(n):
            b = bpm100(k)
            out.append((k % 4 + 1, b, round(t)))
            t += 6000000.0 / b
        return out
    return [(k % 4 + 1, bpm100, round(start_ms + k * 6000000.0 / bpm100)) for k in range(n)]


GRIDS = {
    "128 @0.35": _grid(40, 12800, 350),
    "127.96 @0.35": _grid(40, 12796, 350),
    "120->133.33": _grid(80, lambda k: 12000 if k < 32 else 13333, 0),
    "200 clamp": _grid(20, 20000, 100),
}


# ---- static ----

@needs_main
def test_static_pool_words_outside_the_boot_critical_closure():
    import bootcrit
    img = MAIN.read_bytes()
    bc = bootcrit.closure(img, BASE, byteorder=">")
    readers_of = {p: [] for p, _, _, _ in POOLS}
    for pc in range(BASE, BASE + len(img) - 1, 2):
        w = struct.unpack(">H", img[pc - BASE:pc - BASE + 2])[0]
        if (w & 0xF000) == 0xD000:
            pool = (pc & ~3) + 4 + (w & 0xFF) * 4
            if pool in readers_of:
                readers_of[pool].append(pc)
    for pool, stock, want_readers, _ in POOLS:
        assert struct.unpack(">I", img[pool - BASE:pool - BASE + 4])[0] == stock
        assert readers_of[pool] == want_readers
        assert not bc.hit(pool - BASE, pool - BASE + 4), hex(pool)
        for pc in want_readers:
            assert not bc.hit(pc - BASE, pc - BASE + 2), hex(pc)


@needs_toolchain
@needs_main
def test_composes_with_flac(tmp_path):
    img = MAIN.read_bytes()
    both = ["flac", "flac_header", "flac_pcm", "flac_size", "flac_file_close",
            "flac_audio_open", "flac_audio_open2"] + MODS
    out = patch_main.patch(img, both, model="cdj350")
    assert len(out) == len(img)
    p = payloadbuild.build(["flac", "quant_loops"], "cdj350")
    assert payloadbuild.lookup(p.symbols, "quant_loops_auto_in") != 0


@needs_toolchain
def test_payload_has_no_fpu_code_and_small_frames(tmp_path):
    p = payloadbuild.build(["quant_loops"], "cdj350")
    assert all(bytes_ <= 512 for bytes_ in p.stack.values())
    blob = tmp_path / "payload.bin"
    blob.write_bytes(p.blob)
    dis = subprocess.run(
        [payloadbuild.tool_prefix() + "objdump", "-d", "-b", "binary", "-m", "sh2a", "-EB", str(blob)],
        capture_output=True, check=True,
    ).stdout.decode()
    assert not any(op in dis for op in ("fmov", "fadd", "fmul", "fdiv", "flds", "fsts"))


# ---- SH-2A interpreter ----

def _payload():
    return payloadbuild.build(["quant_loops"], "cdj350")


def _mem(p):
    m = Memory()
    m.add(payloadbuild.BASE, p.blob)
    m.add(0x0F000000, bytes(0x4000))
    m.add(GRID_CTX, bytes(0x7C + 8 * 1000))
    m.add(BASE_TENTHS, bytes(2))
    m.add(USB_MODE, bytes(1))
    m.add(SOURCE, bytes(1))
    m.add(PLAYER_STATE, bytes(4))
    m.add(LOOP_ACTIVE, bytes(1))
    m.add(LOOP_OUT_CMD, bytes(4))
    m.add(LOOP_D1C0, bytes(4))
    m.add(IN_SECTOR, bytes(0xA4))     # covers 0x0C7E4D1C..0x0C7E4DC0
    m.add(CUE_TRACK, bytes(2))
    m.add(RING_ALT, bytes(1))
    m.add(TRACKLEN_TABLE, bytes(0x33C * 4))
    m.add(SECTOR_TABLE, bytes(2400 * 16))
    m.add(0x0DDFE000, bytes(0x400))
    m.add(0x0C9BE004, b"\xC3\x02\x10" + bytes(0x15))   # settings record: quantize and drift on
    return m


def _call(p, m, sym, *args, stack=()):
    cpu = Sh2a(m)
    cpu.r[15] = 0x0F003800
    for k, v in enumerate(args):
        cpu.r[4 + k] = v & 0xFFFFFFFF
    for k, v in enumerate(stack):
        m.put32(cpu.r[15] + k * 4, v & 0xFFFFFFFF)
    cpu.run(payloadbuild.lookup(p.symbols, sym), Sh2a.RET)
    return cpu.r[0]


def _put_grid(m, g, w=0, total=None, base=None, ok=1):
    m.put32(GRID_CTX + 0x70, ok)
    m.put32(GRID_CTX + 0x74, len(g) + 500 * w if total is None else total)
    m.write(GRID_CTX + 0x78, struct.pack(">HH", w, 2 if total and total > 1000 else 1))
    for k, (bib, bpm100, ms) in enumerate(g):
        m.write(GRID_CTX + 0x7C + k * 8, struct.pack(">HHI", bib, bpm100, ms))
    b = min((g[0][1] + 5) // 10, 1800) if g and base is None else (base or 0)
    m.write(BASE_TENTHS, struct.pack(">H", b))


def _s32(u):
    return u - 0x100000000 if u >= 0x80000000 else u


@needs_toolchain
def test_ms_to_frames_rounds():
    p = _payload()
    m = _mem(p)
    for ms in (0, 1, 6, 7, 13, 14, 350, 12345, 600000, 3600000):
        assert _call(p, m, "quant_loops_ms_to_frames", ms) == ms_to_frames(ms)


@needs_toolchain
def test_beat_search_on_the_interpreter():
    p = _payload()
    m = _mem(p)
    entries = GRIDS["128 @0.35"]
    _put_grid(m, entries)
    g = Grid(entries)
    for frame in (0, 26, 27, 44, 45, 61, 62, 600, 1400, 2000):
        assert _s32(_call(p, m, "quant_loops_beat_at_or_before", frame)) == g.at_or_before(frame)
        assert _s32(_call(p, m, "quant_loops_beat_nearest", frame)) == g.nearest(frame)
    entries1 = _grid(1000, 12800, 15000)
    _put_grid(m, entries1, w=1, total=2000)
    g1 = Grid(entries1, w=1, total=2000)
    for frame in (0, 1125, 1500, 50000):
        assert _s32(_call(p, m, "quant_loops_beat_at_or_before", frame)) == g1.at_or_before(frame)
        assert _s32(_call(p, m, "quant_loops_beat_nearest", frame)) == g1.nearest(frame)


@needs_toolchain
def test_auto_points_on_the_interpreter():
    p = _payload()
    m = _mem(p)
    m.write(USB_MODE, b"\x01")
    for name, entries in GRIDS.items():
        _put_grid(m, entries)
        g = Grid(entries)
        for pos in (0, 15, 26, 30, 44, 45, 1120, 1135, 1390, 2000):
            ok = _call(p, m, "quant_loops_auto_points", pos, 4, 0x0F002000, 0x0F002004)
            want = g.auto_points(pos, 4)
            assert (ok, m.u32(0x0F002000), m.u32(0x0F002004))[:1 if want is None else 3] == (
                (0,) if want is None else (1, want[0], want[1])
            ), (name, pos)
    # no grid / CD mode -> 0
    m.write(USB_MODE, b"\x00")
    assert _call(p, m, "quant_loops_auto_points", 100, 4, 0x0F002000, 0x0F002004) == 0


def _stub(m, addr, insns):
    m.write(addr, b"".join(struct.pack(">H", w) for w in insns))


def _entry_mem(p, entries, state=3, tracklen=30000):
    m = _mem(p)
    _put_grid(m, entries)
    m.write(USB_MODE, b"\x01")
    m.write(SOURCE, b"\x02")
    m.write(PLAYER_STATE + 2, bytes([state]))
    m.write(RING_ALT, b"\x00")
    m.put32(TRACKLEN_TABLE + 0x33C, tracklen)
    m.put32(TRACK_LENGTH, tracklen)

    # Stand-in for c350_next_sector (0x0C006A92, 2 mod 4): copies 0x0DDFE100 -> IN_SECTOR
    # mov.l @(10,pc),r1; mov.w @r1,r0; mov.l @(10,pc),r2; rts; mov.w r0,@r2
    m.add(NEXT_SECTOR, bytes(32))
    _stub(m, NEXT_SECTOR, [0xD102, 0x6011, 0xD202, 0x000B, 0x2201, 0x0009, 0x0009])
    m.put32(NEXT_SECTOR + 10, 0x0DDFE100)
    m.put32(NEXT_SECTOR + 14, IN_SECTOR)
    # Stand-in for c350_ring_sector_to_frame (0x0C0090D6, 2 mod 4): SECTOR_TABLE[r4 * 16]
    m.add(0x0C0090D6, bytes(32))
    _stub(m, 0x0C0090D6, [0x644F, 0x4408, 0x4408, 0xD102, 0x341C, 0x000B, 0x6042, 0x0009, 0x0009])
    m.put32(0x0C0090D6 + 18, SECTOR_TABLE)
    # Stand-in for c350_ring_add (0x0C008E38, 0 mod 4): (r4 + r5) % 2400
    m.add(0x0C008E38, bytes(32))
    _stub(m, 0x0C008E38, [0x345C, 0x4411, 0x8901, 0xD002, 0x340C, 0x000B, 0x6043, 0x0009, 2400 >> 16, 2400 & 0xFFFF])
    # Stand-in for c350_sector_track (0x0C006950, 0 mod 4): TRACK_TABLE[r4 * 16]
    m.add(0x0C006950, bytes(32))
    _stub(m, 0x0C006950, [0x644F, 0x4408, 0x4408, 0xD102, 0x341C, 0x000B, 0x6042, 0x0009])
    m.put32(0x0C006950 + 16, TRACK_TABLE)
    # Stand-in for c350_audio_send_cmd (0x0C0C7850): records 8 args at 0x0DDFE200
    log = 0x0DDFE200
    m.add(AUDIO_SEND_CMD, bytes(64))
    _stub(m, AUDIO_SEND_CMD, [
        0xD007, 0x1040, 0x1051, 0x1062, 0x1073,
        0x61F2, 0x1014, 0x51F1, 0x1015, 0x51F2, 0x1016, 0x51F3, 0x1017,
        0x000B, 0x0009, 0x0009,
        log >> 16, log & 0xFFFF,
    ])
    m.write(log, bytes(32))
    return m, log


def _ring(m, first_frame, track=1):
    for s in range(2400):
        m.put32(SECTOR_TABLE + s * 16, first_frame + s)
        m.put32(TRACK_TABLE + s * 16, track)


def _log(m, log):
    return [m.u32(log + k * 4) for k in range(8)]


@needs_toolchain
def test_auto_loop_entry_points_end_to_end():
    p = _payload()
    entries = GRIDS["128 @0.35"]
    g = Grid(entries)
    for pos in (225, 231, 246):
        m, log = _entry_mem(p, entries)
        _ring(m, first_frame=pos - 1000)
        m.write(0x0DDFE100, struct.pack(">h", 1000))
        _call(p, m, "quant_loops_auto_in")
        want = g.auto_points(pos, 4)
        sector = struct.unpack(">h", m.read(IN_SECTOR, 2))[0]
        assert sector == 1000 + (want[0] - pos)
        # send
        _call(p, m, "quant_loops_auto_send", 0x1E, 0, pos + 138, 0, stack=(0, 0, 0, 2))
        assert _log(m, log)[2] == want[1]
        assert m.u32(LOOP_OUT_CMD) == want[1]
        # post-send keep: must NOT overwrite IN_SECTOR with NEXT_SECTOR (say 1005)
        m.write(0x0DDFE100, struct.pack(">h", 1005))
        _call(p, m, "quant_loops_auto_in")
        assert struct.unpack(">h", m.read(IN_SECTOR, 2))[0] == sector
    # state 6 (cued): skips auto_in, quant_loops_auto_send snaps from CUE_FRAME
    m, log = _entry_mem(p, entries, state=6)
    m.put32(CUE_FRAME, 231)
    m.write(CUE_TRACK, struct.pack(">H", 1))
    _call(p, m, "quant_loops_auto_send", 0x1E, 0, 231 + 138, 0, stack=(0, 0, 0, 2))
    want = g.auto_points(231, 4)
    assert _log(m, log)[2] == 231 + (want[1] - want[0])
    # no grid tempo -> stock
    m, log = _entry_mem(p, entries)
    m.write(BASE_TENTHS, struct.pack(">H", 1300))
    _ring(m, first_frame=231 - 1000)
    m.write(0x0DDFE100, struct.pack(">h", 1000))
    _call(p, m, "quant_loops_auto_in")
    _call(p, m, "quant_loops_auto_send", 0x1E, 0, 369, 0, stack=(0, 0, 0, 2))
    assert _log(m, log)[2] == 369


@needs_toolchain
def test_loop_in_out_and_drift_on_the_interpreter():
    p = _payload()
    entries = GRIDS["127.96 @0.35"]
    g = Grid(entries)
    for pos in (215, 231, 600):
        m, log = _entry_mem(p, entries)
        _ring(m, first_frame=pos - 1000)
        m.write(0x0DDFE100, struct.pack(">h", 1000))
        _call(p, m, "quant_loops_loop_in")
        loop_in = g.in_point(pos)
        assert struct.unpack(">h", m.read(IN_SECTOR, 2))[0] == 1000 + loop_in - pos
        m.put32(LOOP_IN, loop_in)
        out_pos = loop_in + 40
        sector = 1000 + out_pos - pos
        _call(p, m, "quant_loops_loop_out", 1, 1, 0, 3, stack=(sector, 1, 0, 2))
        want = g.out_point(loop_in, out_pos)
        assert _log(m, log) == [1, 1, 0, 3, sector + want - out_pos, 1, 0, 2]

    # drift correction: 128.00 BPM, 4 beats = 140.625 frames -> alternates 140 and 141
    m, log = _entry_mem(p, GRIDS["128 @0.35"], state=4)
    m.write(LOOP_ACTIVE, b"\x01")
    m.put32(LOOP_IN, 26)
    m.put32(LOOP_OUT, 167)      # 141 frames
    m.put32(OUT_REQ, 167)
    m.put32(payloadbuild.lookup(p.symbols, "quant_loops_drift_owner"), 26)
    m.put32(PLAY_FRAME, 30)
    _call(p, m, "quant_loops_drift_tick")
    m.put32(PLAY_FRAME, 165)
    _call(p, m, "quant_loops_drift_tick")
    m.put32(PLAY_FRAME, 27)     # wrap!
    _call(p, m, "quant_loops_drift_tick")
    assert _log(m, log)[:4] == [0x1E, 0, 166, 1]   # moved out to 166 (140 frames)


def test_live_loops_on_the_grid(tmp_path):
    """Live QEMU cdj350 + baken stick: beat_loop and manual loop_in/loop_out snap to grid."""
    import json
    import os
    import socket
    import subprocess
    import time

    qemu = os.environ.get("GUI_QEMU") or str(ROOT / "qemu-build-eb" / "qemu-system-sh4eb")
    flash_src = ROOT / "extract" / "cdj350" / "flash_cdj350.bin"
    baken = ROOT / "tools" / "baken" / "baken"
    if not (os.path.isfile(qemu) and flash_src.is_file() and baken.is_file()):
        pytest.skip("cdj350 qemu, flash_cdj350.bin, or baken not available")

    patched_flash = tmp_path / "flash_cdj350_quant.bin"
    quant_mods = [
        "quant_loops", "quant_loops_auto_send",
        "quant_loops_loop_in", "quant_loops_loop_in2", "quant_loops_loop_in3",
        "quant_loops_loop_out", "quant_loops_loop_out2", "quant_loops_loop_out3",
        "quant_loops_drift",
    ]
    subprocess.run(
        [sys.executable, str(ROOT / "mods" / "patch_main.py"),
         "--flash", "--model", "cdj350",
         str(flash_src), str(patched_flash), *quant_mods],
        cwd=str(ROOT), check=True,
    )

    spec_path = tmp_path / "spec.json"
    spec_path.write_text(json.dumps({
        "device_name": "QL350",
        "playlist": "QL",
        "tracks": [{"title": "GRID128", "bpm": 128, "first_downbeat": 0.35, "bars": 32}],
    }))
    usb_img = tmp_path / "usb.img"
    builder = (
        "import sys, wave, numpy as np\n"
        "sys.path.insert(0, sys.argv[1])\n"
        "import make_test_stick as stick\n"
        "def render(track, path):\n"
        "    rate = 44100\n"
        "    t = np.arange(round(stick.track_length(track) * rate)) / rate\n"
        "    phase = t % (60 / track['bpm'])\n"
        "    sig = 0.5 * np.sin(2 * np.pi * 55 * phase) * np.exp(-25 * phase)\n"
        "    s = np.rint(sig * 32767).astype('<i2')\n"
        "    with wave.open(path, 'wb') as w:\n"
        "        w.setparams((2, 2, rate, 0, 'NONE', 'not compressed'))\n"
        "        w.writeframes(np.repeat(s, 2).tobytes())\n"
        "    return True\n"
        "stick.have_ffmpeg = lambda: True\n"
        "stick.render_audio = render\n"
        "stick.file_name = lambda n, title: f'{title}.wav'\n"
        "raise SystemExit(stick.main(['--spec', sys.argv[2], '--out', sys.argv[3],\n"
        "    '--image', sys.argv[4], '--size-mb', '32', '--baken', sys.argv[5]]))\n"
    )
    subprocess.run(
        [sys.executable, "-c", builder, str(ROOT / "scripts" / "media"),
         str(spec_path), str(tmp_path / "stick"), str(usb_img), str(baken)],
        check=True, timeout=180,
    )

    env = os.environ.copy()
    env["CDJ850_PANEL_SCRIPT"] = (
        "9000:usb:1500,13000:rot+2,15000:push:200,17000:push:200,"
        "21000:beat_loop:200,25000:reloop:200,"
        "27000:loop_in:200,29000:loop_out:200"
    )
    qs, qc = socket.socketpair()
    proc = subprocess.Popen(
        [qemu, "-M", "cdj350", "-display", "none", "-monitor", "none", "-serial", "none",
         "-icount", "shift=2,align=off,sleep=off",
         "-drive", f"if=pflash,format=raw,file={patched_flash},snapshot=on",
         "-drive", f"if=none,id=usbstk,format=raw,file={usb_img},snapshot=on",
         "-device", "usb-storage,drive=usbstk,port=1",
         "-chardev", f"socket,id=qmp,fd={qc.fileno()}",
         "-mon", "chardev=qmp,mode=control"],
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
        pass_fds=(qc.fileno(),), env=env,
    )
    qc.close()
    qs.settimeout(60)
    qmp = qs.makefile("rwb", buffering=0)

    def q(cmd, **args):
        qmp.write(json.dumps({"execute": cmd, "arguments": args}).encode() + b"\n")
        while True:
            r = json.loads(qmp.readline())
            if "error" in r:
                raise RuntimeError((cmd, r))
            if "return" in r:
                return r["return"]

    def x(addr, unit):
        return int(q("human-monitor-command", **{"command-line": f"xp /1{unit}x {addr:#x}"}).split(":")[-1].strip(), 16)

    rb = lambda a: x(a, "b")
    rh = lambda a: x(a, "h")
    rw = lambda a: x(a, "w")

    def dump(addr, size):
        p = tmp_path / f"d_{addr:x}.bin"
        q("pmemsave", val=addr, size=size, filename=str(p))
        return p.read_bytes()

    def wait_until(pred, timeout=60.0):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            val = pred()
            if val:
                return val
            assert proc.poll() is None, f"qemu exited early: {proc.stderr.read().decode(errors='replace')}"
            time.sleep(0.15)
        src = rb(SOURCE)
        raise TimeoutError(
            f"condition timed out: win_ok={rw(GRID_CTX + 0x70):#x} "
            f"beats={rw(GRID_CTX + 0x74)} src={src} usb_mode={rb(USB_MODE)} "
            f"state={rb(PLAYER_STATE + src)} active={rb(LOOP_ACTIVE)} "
            f"bpm={rw(BASE_TENTHS)} play_frame={rw(PLAY_FRAME)} "
            f"loop_in={rw(LOOP_IN)} loop_out={rw(LOOP_OUT)} out_req={rw(OUT_REQ)}"
        )

    try:
        assert b"QMP" in qmp.readline()
        q("qmp_capabilities")

        # Wait for track load + beat grid window_ok
        wait_until(lambda: rw(GRID_CTX + 0x70) != 0 and rw(GRID_CTX + 0x74) > 10, timeout=90.0)
        n = rw(GRID_CTX + 0x74)
        raw = dump(GRID_CTX + 0x7C, 8 * min(n, 200))
        entries = [struct.unpack_from(">HHI", raw, 8 * i) for i in range(min(n, 200))]
        g = Grid(entries, total=n)

        p = _payload()
        sym = lambda name: rw(payloadbuild.lookup(p.symbols, name))

        # Wait for scripted play + beat_loop
        trace = []
        end = time.monotonic() + 60.0
        while time.monotonic() < end:
            src = rb(SOURCE)
            st = rb(PLAYER_STATE + src)
            act = rb(LOOP_ACTIVE)
            trace.append((
                st, act, rw(PLAY_FRAME), rw(LOOP_IN), rw(LOOP_OUT), rw(OUT_REQ),
                sym("quant_loops_auto_count"), sym("quant_loops_in_count"), sym("quant_loops_out_count"),
            ))
            if st == 4 and act == 1:
                break
            assert proc.poll() is None, f"qemu exited early: {proc.stderr.read().decode(errors='replace')}"
            time.sleep(0.1)
        else:
            raise TimeoutError(f"never entered state 4; trace[-10:]={trace[-10:]}")

        lin = rw(LOOP_IN)
        lout = rw(OUT_REQ) or rw(LOOP_OUT)
        diag = (
            f"lin={lin} lout={lout} loop_out={rw(LOOP_OUT)} out_req={rw(OUT_REQ)} play={rw(PLAY_FRAME)} "
            f"auto={sym('quant_loops_auto_count')} in_cnt={sym('quant_loops_in_count')} "
            f"out_cnt={sym('quant_loops_out_count')} refused={sym('quant_loops_refused')} "
            f"last_pos={sym('quant_loops_last_pos')} last_in={sym('quant_loops_last_in')} "
            f"last_out={sym('quant_loops_last_out')} trace_tail={trace[-8:]}"
        )
        grid_frames = [g.f(i) for i in range(len(g.e))]
        snapped_beat = min(range(len(grid_frames)), key=lambda i: abs(grid_frames[i] - lin))
        assert lin == grid_frames[snapped_beat], (diag, grid_frames[snapped_beat])
        if sym("quant_loops_auto_count") > 0:
            assert lout == grid_frames[snapped_beat + 4], (diag, grid_frames[snapped_beat + 4])
            # Wait for scripted reloop exit
            wait_until(lambda: rb(PLAYER_STATE + rb(SOURCE)) == 3, timeout=60.0)
            # Wait for scripted manual loop_in + loop_out
            wait_until(lambda: rb(PLAYER_STATE + rb(SOURCE)) == 4, timeout=60.0)
            man_in = rw(LOOP_IN)
            man_out = rw(LOOP_OUT)
            assert man_in in grid_frames, (man_in, grid_frames[:24])
            assert man_out == g.out_point(man_in, man_out), (man_in, man_out)
        else:
            assert sym("quant_loops_in_count") >= 1 and sym("quant_loops_out_count") >= 1, diag
            assert lout == g.out_point(lin, lout), (diag, lout)
    finally:
        try:
            q("quit")
        except Exception:
            pass
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        qmp.close()
        qs.close()
