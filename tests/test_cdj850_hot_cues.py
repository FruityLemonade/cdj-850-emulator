# SPDX-License-Identifier: GPL-2.0-or-later
"""hot_cues on the CDJ-850 MAIN 1.13: BEAT 1/2/4 as rekordbox hot cues A/B/C.

* static: the five pool-word redirects (stock words, the one instruction that reads each, all
  outside the boot-critical closure, as is every MAIN routine the payload calls), nothing else
  written outside the loader, composition with every mod on the branch, the spare settings bits;
* the compiled payload on the SH-2A interpreter (tests/sh2a.py) with small stand-ins for the MAIN
  routines: the key filter (BEAT SELECT + RELOOP toggles and saves the mode and undoes the fraction
  toggle, pads are kept from stock and queued, 8 + pad = delete, stock without ANLZ), the CALL
  selection, the ANLZ delete/count fix and the LED inputs around wai_flg;
* live (QEMU + a baken stick: GRID128 with hot cues A = point, B = 1-bar loop, C empty; GRID96
  without ANLZ), every branch mod built in: toggle, LEDs, jump while playing (keeps playing),
  record C, jump to the loop B, delete A, record A as the playing loop, jump to it, jump while
  paused (cued), mode off = stock BEAT loop, GRID96 = stock, the ANLZ file on the stick image
  afterwards, and the mode bit after a reboot from the written flash.
"""
import json
import os
import shutil
import struct
import subprocess
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

MAIN = ROOT / "extract" / "cdj850" / "main_unpacked.bin"
FLASH = ROOT / "extract" / "cdj850" / "flash_cdj850.bin"
BAKEN = ROOT / "tools" / "baken" / "baken"
BASE = 0x0C000000
MODS = ["hot_cues", "hot_cues_tick", "hot_cues_select", "hot_cues_select2", "hot_cues_leds", "hot_cues_anlz"]
# (pool word, stock value, the instruction that reads it, payload symbol)
POOLS = [(0x0C0ED038, 0x0C0DD492, 0x0C0ECEFC, "hot_cues_keys"),
         (0x0C0EC838, 0x0C0EB388, 0x0C0EC73E, "hot_cues_tick"),
         (0x0C0D41BC, 0x0C0D275A, 0x0C0D3F5C, "hot_cues_select"),
         (0x0C0D3CC8, 0x0C0D275A, 0x0C0D3B94, "hot_cues_select"),
         (0x0C0ED244, 0x0C0C9E7C, 0x0C0ED1B0, "hot_cues_wait"),
         (0x0C0C195C, 0x0C0C5960, 0x0C0C17CE, "hot_cues_anlz_write")]
# MAIN routines the payload calls (not patched). Two are reached from the boot-critical roots
# too: the settings save FUN_0C08E9B0 and the kernel's twai_flg; the payload only calls them,
# as stock code does from playertask.
CALLED_BOOTCRIT = [0x0C08E9B0, 0x0C0C9E7C]
CALLED = [0x0C0746BC, 0x0C0C12B2, 0x0C0C1442, 0x0C0C5960, 0x0C0C3550, 0x0C0C359A,
          0x0C01DE1C, 0x0C01DD8C, 0x0C0D275A, 0x0C0D5978, 0x0C0DD492, 0x0C0EB388,
          0x0C0D3B1A]
K0, K1, K2 = 0x0CE6CA08, 0x0CE6CA0A, 0x0CE6CA0C
SETTINGS = 0x0C9DA23C
D154, D156, UI_LOCK, USB_MODE, SOURCE, PSTATE = 0x0CE6D154, 0x0CE6D156, 0x0CE6D151, 0x0CE6D25A, \
    0x0CE6D252, 0x0CE6D25D
BEATS = 0x0C1E1088
CTX_PTR = 0x0CE649D0
CTX = 0x0D000000
TARGET = 0x0CE65C0C
BEAT_SELECT, RELOOP, PLAY = 0x0040, 0x0400, 0x2000
B1, B2, B4, B8 = 0x10, 0x20, 0x40, 0x80

needs_toolchain = pytest.mark.skipif(not payloadbuild.have_toolchain(), reason="sh-elf toolchain not installed")
needs_main = pytest.mark.skipif(not MAIN.is_file(), reason="extract/cdj850/main_unpacked.bin is not present")


def _pool_readers(stock, pool):
    out = []
    for k in range(len(stock) // 2):
        op = struct.unpack_from(">H", stock, 2 * k)[0]
        if op & 0xF000 == 0xD000:
            pc = BASE + 2 * k
            if (pc & ~3) + 4 + (op & 0xFF) * 4 == pool:
                out.append(pc)
    return out


# ---- static ----

@needs_main
@needs_toolchain
def test_static_pool_words_outside_the_boot_critical_closure():
    import bootcrit
    stock = MAIN.read_bytes()
    report = {}
    out = patch_main.patch(stock, MODS, "cdj850", report)
    assert {n: report[n][0] for n in MODS} == {n: "applied" for n in MODS}
    p = payloadbuild.build(["hot_cues"], "cdj850")
    closure = bootcrit.closure(stock, BASE, byteorder=">")
    for pool, old, reader, symbol in POOLS:
        o = pool - BASE
        assert struct.unpack_from(">I", stock, o)[0] == old, hex(pool)
        assert struct.unpack_from(">I", out, o)[0] == payloadbuild.lookup(p.symbols, symbol), hex(pool)
        assert _pool_readers(stock, pool) == [reader], hex(pool)
        assert not closure.hit(o, o + 4), hex(pool)
        assert not closure.hit(reader - BASE, reader - BASE + 2), hex(reader)
    for a in CALLED:
        assert not closure.hit(a - BASE, a - BASE + 2), hex(a)
    for a in CALLED_BOOTCRIT:
        assert closure.hit(a - BASE, a - BASE + 2), hex(a)
    # the readers: a jsr through the loaded register follows (the wai_flg call pushes a 5th argument)
    assert struct.unpack_from(">2H", stock, 0x0C0ECEFC - BASE) == (0xDE4E, 0x4E0B)
    assert struct.unpack_from(">2H", stock, 0x0C0EC73E - BASE) == (0xD33E, 0x434B)
    assert struct.unpack_from(">3H", stock, 0x0C0D3F5C - BASE) == (0xD397, 0x430B, 0xE501)
    assert struct.unpack_from(">3H", stock, 0x0C0D3B94 - BASE) == (0xD34C, 0x430B, 0x6413)
    assert struct.unpack_from(">2H", stock, 0x0C0ED1A6 - BASE) == (0x2F86, 0x67F3)   # push r8; r7 = r15
    assert struct.unpack_from(">3H", stock, 0x0C0ED1B0 - BASE) == (0xD224, 0x420B, 0xE406)
    assert struct.unpack_from(">3H", stock, 0x0C0C17CE - BASE) == (0xDE63, 0x4E0B, 0x67B3)
    diff = [a for a in range(0x0C000100, 0x0C1E2000) if stock[a - BASE] != out[a - BASE]]
    assert all(any(pool <= a < pool + 4 for pool, *_ in POOLS) for a in diff), [hex(a) for a in diff]


ALL_MODS = ["autostandby", "bpm999", "bpm999grid", "bpm999range", "bpm_probe", "dspprobe",
            "payload_demo", "payload_demo_redirect", "tempowide", "tempowidemath1", "tempowidemath2",
            "bpm_decimals", "bpm_decimals_grid", "quant_loops", "quant_loops_auto_send",
            "quant_loops_auto_send2", "quant_loops_auto_keep", "quant_loops_loop_in", "quant_loops_loop_out",
            "quant_loops_loop_out2", "loop_resize", "loop_resize_tick", "loop_resize_memory"]
ALL_FOLDERS = ["bpm_decimals", "hot_cues", "loop_resize", "payload_demo", "quant_loops"]


@needs_main
@needs_toolchain
def test_composes_with_the_branch_mods():
    names = ALL_MODS + MODS
    report = {}
    out = patch_main.patch(MAIN.read_bytes(), names, "cdj850", report)
    assert all(report[n][0] == "applied" for n in names), report
    p = payloadbuild.build(ALL_FOLDERS, "cdj850")
    for pool, _, _, symbol in POOLS:
        assert struct.unpack_from(">I", out, pool - BASE)[0] == payloadbuild.lookup(p.symbols, symbol)


@needs_main
def test_settings_mode_byte():
    """The settings log scan at boot (0x0C08EF60) ends at the first record with byte +0 bit 4,
    byte +1 bits 4..6 or byte +0x43 bits 4..7 set (erased flash), so those bits are not free. The
    mode byte is +0x22, the last byte of the 32-byte HISTORY name (memset + strncpy of 32 bytes at
    +3; read with strlen/strcmp), which the scan does not look at."""
    img = MAIN.read_bytes()
    scan = struct.unpack_from(">10H", img, 0x0C08EF66 - BASE)
    assert scan == (0x3549, 0x3000, 0x890A, 0x8451, 0xC970, 0x2008, 0x8B06, 0x3051, 0x4043, 0xC8F0)
    # the HISTORY name setter: memset(+3, 0, 32); strncpy(+3, s, 32)
    assert struct.unpack_from(">I", img, 0x0C08F124 - BASE)[0] == SETTINGS + 3
    assert struct.unpack_from(">4H", img, 0x0C08F0E0 - BASE) == (0xE620, 0x6E43, 0xD40F, 0xD110)
    assert struct.unpack_from(">I", img, 0x0C08F128 - BASE)[0] == 0x0C1106EC   # memset
    assert struct.unpack_from(">I", img, 0x0C08F12C - BASE)[0] == 0x0C110918   # strncpy
    # no literal names a byte of the record past +3 other than the two name fields and the error log
    lits = sorted(struct.unpack_from(">I", img, o)[0] for o in range(0, len(img) - 3, 4)
                  if SETTINGS <= struct.unpack_from(">I", img, o)[0] < SETTINGS + 0x58)
    assert set(lits) == {SETTINGS, SETTINGS + 1, SETTINGS + 2, SETTINGS + 3, SETTINGS + 0x23,
                         SETTINGS + 0x43, SETTINGS + 0x44, SETTINGS + 0x54}, [hex(v) for v in set(lits)]


@needs_toolchain
def test_payload_has_no_fpu_code_and_small_frames():
    p = payloadbuild.build(["hot_cues"], "cdj850")
    text = p.blob[:p.symbols["__bss_start"] - 0x0DE00020]
    words = struct.unpack(">%dH" % (len(text) // 2), text[:len(text) & ~1])
    # FPU ops are 0xFxxx except the SH-2A 32-bit forms; none of these opcodes may appear as code
    fpu = [w for w in words if w >> 12 == 0xF and (w & 0xF) not in (0x0D, 0x0F)]
    dis = subprocess.run([payloadbuild.tool_prefix() + "objdump", "-d", "-b", "binary", "-m", "sh2a", "-EB",
                          "--adjust-vma=0x0DE00020", "/dev/stdin"], input=text, capture_output=True).stdout
    assert b"fmov" not in dis and b"fsts" not in dis and b"flds" not in dis and b"fpscr" not in dis, fpu
    assert max(p.stack.values()) <= 96


# ---- interpreter ----

LOG = 0x0DDFE000
STUB_BASE = {}


def _asm(addr, src):
    """Assemble SH-2A source at addr with the toolchain (stand-ins for MAIN routines)."""
    pre = payloadbuild.tool_prefix()
    with tempfile.TemporaryDirectory() as tmp:
        s, o, e, b = (os.path.join(tmp, n) for n in ("s.s", "s.o", "s.elf", "s.bin"))
        Path(s).write_text(src)
        subprocess.run([pre + "as", "-isa=sh2a", "-big", "-o", o, s], check=True)
        subprocess.run([pre + "ld", "-EB", "-Ttext=%#x" % addr, "-o", e, o], check=True, capture_output=True)
        subprocess.run([pre + "objcopy", "-O", "binary", "-j", ".text", e, b], check=True)
        return Path(b).read_bytes()


def _stub(mem, addr, body):
    """A stand-in at addr; addr may be 2 mod 4 (the routine then starts after a nop)."""
    pad = addr & 2
    code = _asm(addr - pad, ("nop\n" if pad else "") + body)
    mem.add(addr - pad, code)


# log slot k: LOG + 0x40 * k; each stand-in records its arguments there
def _logger(k, ret=0):
    return """
        mov.l   1f,r0
        mov.l   r4,@r0
        mov.l   r5,@(4,r0)
        mov.l   r6,@(8,r0)
        mov.l   r7,@(12,r0)
        mov.l   @(28,r0),r1
        add     #1,r1
        mov.l   r1,@(28,r0)
        rts
        mov     #%d,r0
        .align 2
1:      .long   %#x
""" % (ret, LOG + 0x40 * k)


def _payload():
    return payloadbuild.build(["hot_cues"], "cdj850")


def _sym(p, name):
    a = payloadbuild.lookup(p.symbols, name)
    assert a is not None, name
    return a


def _mem(p, mode=0, avail=1, slots=(1, 2, 0), state=3, frac=0, d156=0, beats=0, extras=0):
    mem = Memory()
    mem.add(0x0DE00020, p.blob)
    mem.add(0x0DDFE000, bytes(0x2000))                    # log, stack
    mem.add(0x0CE6CA00, bytes(0x40))                      # K words
    mem.add(0x0CE6D0E0, bytes(0x200))                     # player bytes
    mem.add(0x0CE649C0, bytes(0x40))                      # ANLZ ctx pointer
    mem.add(0x0CE65C00, bytes(0x40))                      # jump target
    mem.add(0x0CE65840, bytes(0x20))                      # cue list state (0x0CE65859)
    mem.add(0x0CE5A9F0, bytes(0x10))
    mem.add(0x0CE64570, bytes(8))
    mem.add(0x0C7F6480, bytes(0x300))                     # audio records
    rec = bytearray(0x58)
    rec[0:3] = b"\xC3\x02\x10"
    rec[3:10] = b"HISTORY"
    rec[0x23:0x2B] = b"TAG LIST"
    rec[0x22] = 0x5A if mode else 0
    mem.add(SETTINGS, bytes(rec))
    mem.add(BEATS, bytes([beats, 0, 0, 0]))
    ctx = bytearray(0x1500)
    struct.pack_into(">I", ctx, 0, 0xBFA14C7E)
    struct.pack_into(">I", ctx, 0x1414, 0x9768A153)
    struct.pack_into(">II", ctx, 0x488, 0x11111111, 0x22222222)
    struct.pack_into(">I", ctx, 0xF74, extras)
    for i, n in enumerate(slots):
        struct.pack_into(">H", ctx, 0xF90 + i * 0x38 + 0xE, n)
        if n:
            ctx[0xF90 + i * 0x38 + 0x1C] = 1
    mem.add(CTX, ctx)
    mem.put32(CTX_PTR, CTX)
    mem.write(USB_MODE, b"\x01")
    mem.write(SOURCE, b"\x02")
    mem.write(PSTATE + 2, bytes([state]))
    mem.write(D154, bytes([frac]))
    mem.write(D156, bytes([d156]))
    mem.put32(_sym(p, "hot_cues_avail"), avail)
    mem.put32(_sym(p, "hc_h1"), 0x11111111)
    mem.put32(_sym(p, "hc_h2"), 0x22222222)
    # stand-ins: FUN_0C0DD492 (jog, r0 = 0x55), settings save (log 0), FUN_0C0D5978 (log 1),
    # cue_select_from_list (log 2, r0 = 7), FUN_0C0C5960 (log 3), wai_flg (log 4 + LED bytes)
    _stub(mem, 0x0C0DD492, "rts\nmov #0x55,r0\n")
    _stub(mem, 0x0C08E9B0, _logger(0, 1))
    _stub(mem, 0x0C0D5978, _logger(1))
    _stub(mem, 0x0C0D275A, _logger(2, 7))
    _stub(mem, 0x0C0C5960, _logger(3, 0))
    _stub(mem, 0x0C0C9E7C, """
        mov.l   1f,r2
        mov.l   r4,@r2
        mov.l   4f,r2
        mov.l   2f,r1
        mov.b   @r1,r0
        mov.b   r0,@r2
        mov.l   3f,r1
        mov.b   @r1,r0
        mov.b   r0,@(1,r2)
        mov.l   @r15,r0
        mov.l   r0,@(4,r2)
        rts
        mov     #0,r0
        .align 2
1:      .long   %#x
2:      .long   %#x
3:      .long   %#x
4:      .long   %#x
""" % (LOG + 0x100, BEATS, D154, LOG + 0x110))
    return mem


def _call(p, mem, name, *args, stack=()):
    cpu = Sh2a(mem)
    cpu.r[15] = 0x0DDFFF00
    for i, a in enumerate(args):
        cpu.r[4 + i] = a & 0xFFFFFFFF
    for i, a in enumerate(stack):
        mem.put32(0x0DDFFF00 + 4 * i, a)
    cpu.run(_sym(p, name), Sh2a.RET)
    return cpu.r[0]


def _keys(p, mem, k0=0, k1=0, k2=0):
    """The remap built k0..k2; hot_cues_keys filters them. Returns the words stock then sees."""
    mem.write(K0, struct.pack(">HHH", k0, k1, k2))
    assert _call(p, mem, "hot_cues_keys") == 0x55
    return struct.unpack(">HHH", mem.read(K0, 6))


def _u8(mem, a):
    return mem.read(a, 1)[0]


@needs_toolchain
def test_toggle_saves_the_mode_and_undoes_the_fraction_toggle():
    p = _payload()
    mem = _mem(p, mode=0, frac=0, d156=0)
    assert _keys(p, mem, k0=BEAT_SELECT) == (BEAT_SELECT, 0, 0)           # stock sees BEAT SELECT
    mem.write(D154, b"\x01")                                               # ... and toggles (latch)
    mem.write(D156, b"\x01")
    assert _keys(p, mem, k0=BEAT_SELECT | RELOOP) == (0, 0, 0)             # the combo is ours
    assert _u8(mem, SETTINGS + 0x22) == 0x5A and mem.u32(_sym(p, "hot_cues_toggles")) == 1
    assert mem.read(SETTINGS, 3) == b"\xC3\x02\x10"                           # flag bytes untouched
    assert mem.u32(LOG) == 2 and mem.u32(LOG + 4) == SETTINGS and mem.u32(LOG + 28) == 1   # saved
    assert (_u8(mem, D154), _u8(mem, D156)) == (0, 0)                      # fraction toggle undone
    assert _keys(p, mem, k0=BEAT_SELECT) == (0, 0, 0)                      # still held: hidden
    assert _keys(p, mem) == (0, 0, 0)
    # toggling back
    _keys(p, mem, k0=BEAT_SELECT)
    _keys(p, mem, k0=BEAT_SELECT | RELOOP)
    assert _u8(mem, SETTINGS + 0x22) == 0 and mem.u32(LOG + 28) == 2
    _keys(p, mem)
    # RELOOP alone and BEAT SELECT alone stay stock
    assert _keys(p, mem, k0=RELOOP) == (RELOOP, 0, 0)
    assert _keys(p, mem) == (0, 0, 0)
    assert _keys(p, mem, k0=BEAT_SELECT) == (BEAT_SELECT, 0, 0)
    assert _keys(p, mem) == (0, 0, 0)
    # RELOOP held first, then BEAT SELECT: not the combo (stock order)
    _keys(p, mem, k0=RELOOP)
    assert _keys(p, mem, k0=RELOOP | BEAT_SELECT) == (RELOOP | BEAT_SELECT, 0, 0)
    assert _u8(mem, SETTINGS + 0x22) == 0
    _keys(p, mem)
    # a 31-character HISTORY name leaves no room: no toggle (the keys are still taken)
    mem.write(SETTINGS + 3, b"H" * 31)
    _keys(p, mem, k0=BEAT_SELECT)
    assert _keys(p, mem, k0=BEAT_SELECT | RELOOP) == (0, 0, 0)
    assert _u8(mem, SETTINGS + 0x22) == 0 and mem.u32(LOG + 28) == 2


@needs_toolchain
def test_pads_are_hot_cues_only_in_mode_with_anlz():
    p = _payload()
    q = _sym(p, "hc_queue")
    # mode off: stock
    mem = _mem(p, mode=0)
    assert _keys(p, mem, k1=B1) == (0, B1, 0)
    # mode on, no ANLZ: stock
    mem = _mem(p, mode=1, avail=0)
    assert _keys(p, mem, k1=B4) == (0, B4, 0)
    # UI lock: stock (and stock masks the beat keys itself)
    mem = _mem(p, mode=1)
    mem.write(UI_LOCK, b"\x01")
    assert _keys(p, mem, k1=B1) == (0, B1, 0)
    # mode on with ANLZ: every BEAT key is ours; pads queue a trigger, 8 + pad a delete
    mem = _mem(p, mode=1)
    assert _keys(p, mem, k1=B1 | 0x0800) == (0, 0x0800, 0)               # BROWSE untouched
    assert mem.read(q, 3) == b"\x01\x00\x00"
    assert _keys(p, mem, k1=0x0800) == (0, 0x0800, 0)
    assert _keys(p, mem, k1=B8) == (0, 0, 0)
    assert _keys(p, mem, k1=B8 | B2) == (0, 0, 0)
    assert mem.read(q, 3) == b"\x01\x02\x00"
    assert _keys(p, mem, k1=B2) == (0, 0, 0)                             # still held: hidden
    assert _keys(p, mem, k1=B4) == (0, 0, 0)
    assert mem.read(q, 3) == b"\x01\x02\x01"
    assert _keys(p, mem) == (0, 0, 0)
    # a key held when the mode came on stays stock until released
    mem = _mem(p, mode=0)
    _keys(p, mem, k1=B4)
    mem.write(SETTINGS + 0x22, b"\x5A")
    assert _keys(p, mem, k1=B4) == (0, B4, 0)
    assert mem.read(q, 3) == b"\x00\x00\x00"


def _entry(mem, p, words):
    a = _sym(p, "hc_entry")
    for i, w in enumerate(words):
        mem.put32(a + 4 * i, w)


@needs_toolchain
@pytest.mark.parametrize("loop", [0, 1])
def test_select_fills_the_jump_target_like_the_stock_usb_branch(loop):
    p = _payload()
    mem = _mem(p)
    mem.write(0x0C7F6488, struct.pack(">H", 2))                           # track number
    e = [(2 << 16) | loop, 2, 2, 1125, 1265 if loop else 0, 11, 12, 13, 14]
    _entry(mem, p, e)
    # not armed: the stock routine with the caller's arguments
    assert _call(p, mem, "hot_cues_select", 2, 1, 9) == 7
    assert (mem.u32(LOG + 0x80), mem.u32(LOG + 0x84), mem.u32(LOG + 0x88)) == (2, 1, 9)
    mem.write(_sym(p, "hc_sel"), b"\x01")
    assert _call(p, mem, "hot_cues_select", 2, 1, 9) == 0
    assert mem.u32(LOG + 0x80 + 28) == 1                                  # stock not called again
    assert mem.u32(LOG + 0x40 + 28) == 1                                  # FUN_0C0D5978 called
    assert _u8(mem, _sym(p, "hc_sel")) == 0
    assert mem.u32(0x0CE6D158) == 1125 and mem.u32(TARGET + 0xC) == 1125
    assert mem.u16(TARGET + 4) == 2 and mem.u16(0x0CE65C04) == 2
    assert mem.u32(TARGET + 0x14) == 11 and mem.u32(TARGET + 0x1C) == 13
    assert mem.u32(0x0CE5A9F8) == 11 and mem.u32(0x0CE5A9FC) == 13 and mem.u32(0x0CE64574) == 1
    assert mem.read(TARGET + 1, 2) == bytes([loop, loop]) and _u8(mem, TARGET) == 0
    assert _u8(mem, 0x0CE6D0ED) == loop and _u8(mem, 0x0CE6D0EE) == loop
    if loop:
        assert mem.u32(TARGET + 0x10) == 1265 and mem.u32(0x0C7F66F4) == 1265
        assert mem.u32(0x0C7F6758) == 12 and mem.u32(0x0C7F675C) == 14
    else:
        assert mem.u32(TARGET + 0x10) == 0 and mem.u32(0x0C7F66F4) == 0


@needs_toolchain
@pytest.mark.parametrize("delete, extras", [(0, 0), (2, 0), (1, 2), (0, 1)])
def test_anlz_write_empties_the_slot_and_counts_the_cues(delete, extras):
    p = _payload()
    mem = _mem(p, slots=(1, 2, 3), extras=extras)
    struct.pack_into(">H", bytearray(2), 0, 0)
    mem.write(CTX + 0xF8A, struct.pack(">H", 3))
    mem.put32(CTX + 0xF80, 3 * 0x38 + 0x18)
    # a stock (non hot_cues) write is untouched
    assert _call(p, mem, "hot_cues_anlz_write", 0xF11E, CTX, 3, 0xB0F) == 0
    assert mem.u16(CTX + 0xF8A) == 3
    assert (mem.u32(LOG + 0xC0), mem.u32(LOG + 0xC4), mem.u32(LOG + 0xC8), mem.u32(LOG + 0xCC)) == \
        (0xF11E, CTX, 3, 0xB0F)
    mem.write(_sym(p, "hc_fixlen"), b"\x01")
    mem.write(_sym(p, "hc_del"), bytes([delete]))
    _call(p, mem, "hot_cues_anlz_write", 0xF11E, CTX, 3, 0xB0F)
    n = 3 - (1 if delete else 0) + extras
    assert mem.u16(CTX + 0xF8A) == n and mem.u32(CTX + 0xF80) == n * 0x38 + 0x18
    for i in range(3):
        slot = mem.read(CTX + 0xF90 + i * 0x38, 0x38)
        assert (slot == bytes(0x38)) == (i + 1 == delete)
    assert mem.u32(LOG + 0xC0 + 28) == 2


@needs_toolchain
@pytest.mark.parametrize("mode, slots, want", [(1, (1, 2, 0), (4, 0)), (1, (0, 0, 0), (7, 0)),
                                               (1, (1, 2, 3), (0, 0)), (0, (1, 0, 0), None)])
def test_leds_are_faked_only_while_playertask_waits(mode, slots, want):
    p = _payload()
    mem = _mem(p, mode=mode, slots=slots, frac=1, beats=4)
    assert _call(p, mem, "hot_cues_wait", 6, 0x37, 1, 0x0DDFF800, stack=(0x1234,)) == 0
    assert mem.u32(LOG + 0x100) == 6 and mem.u32(LOG + 0x114) == 0x1234        # args, 5th on the stack
    seen = (_u8(mem, LOG + 0x110), _u8(mem, LOG + 0x111))
    assert seen == (want or (4, 1))
    assert (_u8(mem, BEATS), _u8(mem, D154)) == (4, 1)                          # real values back


@needs_toolchain
def test_effective_out_follows_a_resize():
    p = _payload()
    mem = _mem(p)
    mem.put32(0x0C7F64B0, 741)
    mem.put32(0x0C7F64DC, 500)
    assert _call(p, mem, "hot_cues_effective_out", 601) == 741                # new loop: the record
    mem.put32(0x0C7F64DC, 882)                                                 # resize
    assert _call(p, mem, "hot_cues_effective_out", 601) == 882
    mem.put32(0x0C7F64B0, 900)                                                 # a new loop
    assert _call(p, mem, "hot_cues_effective_out", 601) == 900


# ---- live ----

# Stick: GRID128 (128 BPM, 32 bars, memory cue bar 13, hot cue A = bar 3 point, B = bar 9 one-bar
# loop, C empty) and GRID96 (no ANLZ files: removed after the export).
_STICK_BUILDER = r'''
import json, os, subprocess, sys, wave, pathlib
import numpy as np
root, out_dir, image, baken = sys.argv[1:5]
sys.path.insert(0, root)
import make_test_stick as stick
import collection_xml
_orig = collection_xml.position_mark_xml
def position_mark_xml(start, num=collection_xml.MEMORY_CUE):
    if num == 1:      # hot cue B: a one-bar loop
        return ('      <POSITION_MARK Name="" Type="4" Start="%.3f" End="%.3f" Num="1"/>\n'
                % (start, start + 4 * 60 / 128.0))
    return _orig(start, num)
collection_xml.position_mark_xml = position_mark_xml
stick.collection_xml = collection_xml
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
spec = {"device_name": "HOTCUES", "playlist": "Hot Cues", "tracks": [
    {"title": "GRID128", "bpm": 128, "first_downbeat": 0, "bars": 32, "memory_cues": [13], "hot_cues": [3, 9]},
    {"title": "GRID96", "bpm": 96, "first_downbeat": 0, "bars": 16}]}
sp = pathlib.Path(out_dir) / 'spec.json'
sp.write_text(json.dumps(spec))
rc = stick.main(['--spec', str(sp), '--out', out_dir + '/wav-stick', '--image', image, '--size-mb', '32',
                 '--baken', baken])
if rc:
    raise SystemExit(rc)
device = os.path.join(out_dir, 'wav-stick', 'device')
gone = []
for d, _, files in os.walk(os.path.join(device, 'PIONEER', 'USBANLZ')):
    if 'ANLZ0000.DAT' in files and 'GRID96'.encode('utf-16-be') in open(os.path.join(d, 'ANLZ0000.DAT'), 'rb').read():
        for m in files:
            os.remove(os.path.join(d, m))
            gone.append(m)
assert gone
os.remove(image)
subprocess.run([sys.executable, os.path.join(root, 'make_usb_image.py'), device, image, '32'], check=True,
               capture_output=True)
'''

STATE_USB, FRAME, LOOP_IN, OUT_REC, ACTIVE = 0x0CE6D25F, 0x0C7F647C, 0x0C7F6498, 0x0C7F64B0, 0x0CE6D0ED
LED7, MODE_BYTE = 0x0C9D0AEB, SETTINGS + 0x22
EV = ["toggles", "jumps", "records", "deletes", "plays", "failed", "last_slot", "last_in", "last_out",
      "last_result", "avail", "mask"]


def build_stick(path):
    import importlib.util
    if not BAKEN.is_file() or importlib.util.find_spec("pyfatfs") is None or importlib.util.find_spec("numpy") is None:
        pytest.skip("tools/baken/baken, pyfatfs and numpy are needed for the hot-cue stick")
    image = path / "hotcues.img"
    subprocess.run([sys.executable, "-c", _STICK_BUILDER, str(ROOT / "scripts" / "media"), str(path / "stick"),
                    str(image), str(BAKEN)], check=True, capture_output=True, text=True, timeout=300)
    return image


def pcob(data):
    """The PCOB tags of an ANLZ file: [(type, count, length, [(num, type, divisor, time, loop)])]."""
    out, o = [], struct.unpack_from(">I", data, 4)[0]
    while o + 12 <= len(data):
        tag, h, n = data[o:o + 4], *struct.unpack_from(">II", data, o + 4)
        if n == 0:
            break
        if tag == b"PCOB":
            typ, cnt = struct.unpack_from(">I", data, o + 12)[0], struct.unpack_from(">H", data, o + 18)[0]
            cues, q = [], o + h
            while q < o + n:
                assert data[q:q + 4] == b"PCPT"
                num, = struct.unpack_from(">I", data, q + 12)
                cues.append((num, data[q + 0x1C], *struct.unpack_from(">HII", data, q + 0x1E)))
                q += struct.unpack_from(">I", data, q + 8)[0]
            out.append((typ, cnt, n, sorted(cues)))
        o += n
    return out


def stick_hot_cues(image):
    from pyfatfs.PyFatFS import PyFatFS
    with tempfile.TemporaryDirectory() as tmp:
        part = Path(tmp) / "part.img"
        part.write_bytes(Path(image).read_bytes()[63 * 512:])
        f = PyFatFS(str(part))
        try:
            for p in f.walk.files("/PIONEER/USBANLZ"):
                if p.upper().endswith("ANLZ0000.DAT"):
                    data = f.readbytes(p)
                    if "GRID128".encode("utf-16-be") in data:
                        return [t for t in pcob(data) if t[0] == 1][0]
        finally:
            f.close()


def drive(stick, flash, syms, work):
    sys.path.insert(0, str(ROOT / "scripts" / "debug"))
    import cdj850_trace as tr
    S = lambda n: payloadbuild.lookup(syms, "hot_cues_" + n)
    res = {}

    def snap(d, tag):
        d.q("stop")
        v = {"state": d.rb(STATE_USB), "frame": d.rw(FRAME), "in": d.rw(LOOP_IN), "out": d.rw(OUT_REC),
             "active": d.rb(ACTIVE), "mode": d.rb(MODE_BYTE), "d154": d.rb(D154), "beats": d.rb(BEATS),
             "set": d.dump(SETTINGS, 3).hex()}
        v.update({n: d.rw(S(n)) for n in EV})
        ctx = d.rw(CTX_PTR)
        raw = d.dump(ctx + 0xF90, 3 * 0x38)
        v["slots"] = [(struct.unpack_from(">H", raw, i * 0x38 + 0xE)[0], raw[i * 0x38 + 0x1C],
                       *struct.unpack_from(">HII", raw, i * 0x38 + 0x1E)) for i in range(3)]
        d.q("cont")
        res[tag] = v
        return v

    def frames(d, secs):
        out, end = [], time.monotonic() + secs
        while time.monotonic() < end:
            out.append(d.rw(FRAME))
            time.sleep(0.05)
        return out

    def leds(d, secs=2.0):
        """Beat-LED nibbles seen in TX staging. An all-off sample (0) now and then shows up in stock
        too (mode off, run of 2026-11-20), so 0 is left out."""
        out, end = set(), time.monotonic() + secs
        while time.monotonic() < end:
            out.add(d.rb(LED7) & 0xF)
            time.sleep(0.03)
        res.setdefault("led_zero", []).append(0 in out)
        return sorted(out - {0})

    def ensure_playing(d):
        for _ in range(6):
            st = d.rb(STATE_USB)
            if st == 3:
                return
            d.key("reloop" if st == 4 else "play")
            time.sleep(2.5)

    def wait_frame(d, f, timeout=60):
        end = time.monotonic() + timeout
        while d.rw(FRAME) < f and time.monotonic() < end:
            time.sleep(0.05)

    def toggle(d):
        d.hold("beat_select")
        time.sleep(0.6)
        d.key("reloop")
        time.sleep(0.8)
        d.release("beat_select")
        time.sleep(1.0)

    log = open(work / "qemu.log", "w")
    with tr.Deck(stick, flash=flash, log=log, writable=True) as d:
        written = d.usb
        d.wait_line("mode=0x0a", "[GRID128]", timeout=300)
        time.sleep(1.5)
        ensure_playing(d)
        time.sleep(1.0)
        snap(d, "loaded")
        res["leds_off"] = leds(d)
        d.key("beat_4")                                   # stock: a 4-beat loop
        time.sleep(1.5)
        snap(d, "stock_beat4")
        d.key("reloop")
        time.sleep(1.5)
        snap(d, "exit")
        toggle(d)                                         # mode on; RELOOP must not re-enter the loop
        snap(d, "on")
        res["leds_on"] = leds(d)
        wait_frame(d, d.rw(FRAME) + 150)
        d.key("beat_1")                                   # A while playing
        time.sleep(0.4)
        res["jump_a_frames"] = frames(d, 2.0)
        snap(d, "jump_a")
        wait_frame(d, 600)
        d.key("beat_4")                                   # C is empty: record while playing
        time.sleep(2.5)
        snap(d, "record_c")
        res["leds_c"] = leds(d)
        d.key("beat_2")                                   # B: the loop
        time.sleep(0.5)
        res["jump_b_frames"] = frames(d, 4.0)
        snap(d, "jump_b")
        d.hold("beat_8")                                  # delete A
        time.sleep(0.5)
        d.key("beat_1")
        time.sleep(2.5)
        d.release("beat_8")
        time.sleep(0.5)
        snap(d, "delete_a")
        res["leds_del"] = leds(d)
        d.key("beat_1")                                   # A empty, in loop B: record the loop
        time.sleep(2.5)
        snap(d, "record_a_loop")
        d.key("reloop")                                   # RELOOP alone: stock exit
        time.sleep(1.5)
        snap(d, "exit2")
        d.key("beat_1")                                   # A (the loop) while playing
        time.sleep(0.5)
        res["jump_a_loop_frames"] = frames(d, 4.0)
        snap(d, "jump_a_loop")
        d.key("reloop")
        time.sleep(1.5)
        d.key("play")                                     # pause
        time.sleep(1.5)
        snap(d, "paused")
        d.key("beat_4")                                   # C while paused: cued there
        time.sleep(1.5)
        res["paused_frames"] = frames(d, 1.5)
        snap(d, "jump_c_paused")
        d.key("play")
        time.sleep(1.5)
        toggle(d)                                         # mode off: BEAT 4 is stock again
        snap(d, "off")
        ensure_playing(d)
        d.key("beat_4")
        time.sleep(1.5)
        snap(d, "off_beat4")
        d.key("reloop")
        time.sleep(1.5)
        toggle(d)                                         # on again, GRID96 (no ANLZ): stock
        snap(d, "on_again")
        d.key("next")
        d.wait_line("mode=0x0a", "[GRID96]", timeout=120)
        time.sleep(2.0)
        ensure_playing(d)
        time.sleep(1.0)
        snap(d, "grid96")
        d.key("beat_4")
        time.sleep(1.5)
        snap(d, "grid96_beat4")
        time.sleep(3.0)
    res["anlz"] = stick_hot_cues(written)
    # reboot from the written flash with the written stick: mode and hot cues are back
    with tr.Deck(written, flash=flash, log=log, writable=True) as d:
        d.wait_line("mode=0x0a", "[GRID128]", timeout=300)
        time.sleep(3.0)
        ensure_playing(d)
        time.sleep(1.0)
        snap(d, "reboot")
        res["leds_reboot"] = leds(d)
    return res


def _moved_from(fr, start):
    """The frames after the jump: the first sample at or after `start`, and the deck plays on from there."""
    i = next(i for i, f in enumerate(fr) if start <= f <= start + 10)
    return fr[i:], fr[-1] > fr[i] + 5             # the emulator plays slower than wall time


@needs_toolchain
@pytest.mark.skipif(not FLASH.is_file(), reason="extract/cdj850/flash_cdj850.bin is not present")
def test_live_hot_cues_with_every_branch_mod(tmp_path):
    if os.name == "nt":
        pytest.skip("QMP socket descriptor inheritance requires POSIX")
    stick = build_stick(tmp_path)
    flash = tmp_path / "flash.bin"
    flash.write_bytes(patch_main.patch_flash(FLASH.read_bytes(), ALL_MODS + MODS, "cdj850"))
    syms = payloadbuild.build(ALL_FOLDERS, "cdj850").symbols
    r = drive(stick, flash, syms, tmp_path)
    print("hot_cues live:", json.dumps(r))
    A, B = 281, 1125                                     # 3.750 s, 15.000 s (end 16.875 s = 1265)
    l0 = r["loaded"]
    assert l0["mode"] == 0 and l0["avail"] == 1 and l0["mask"] == 3, l0
    assert l0["slots"][:2] == [(1, 1, 1000, 3750, 0xFFFFFFFF), (2, 2, 1000, 15000, 16875)], l0
    assert r["leds_off"] == [0xF]
    assert r["stock_beat4"]["state"] == 4 and r["stock_beat4"]["beats"] == 4
    on = r["on"]
    assert on["mode"] == 0x5A and on["toggles"] == 1 and on["set"] == l0["set"]        # flag bytes untouched
    assert on["d154"] == 0 and on["state"] == 3                                          # no fraction, no RELOOP
    assert r["leds_on"] == [0xB, 0xF]                                                    # C blinks
    # A while playing: plays on from A
    ja = r["jump_a"]
    assert ja["jumps"] == 1 and ja["last_in"] == A and ja["state"] == 3 and ja["plays"] >= 1, ja
    _, plays = _moved_from(r["jump_a_frames"], A)
    assert plays
    # record C while playing (about frame 600), the deck plays on
    rc = r["record_c"]
    assert rc["records"] == 1 and rc["last_result"] == 0 and rc["state"] == 3 and rc["mask"] == 7, rc
    c = rc["last_in"]
    assert 600 <= c <= 640 and rc["slots"][2] == (3, 1, 150, 2 * c, 1), rc
    assert r["leds_c"] == [0xF]
    # B: the loop plays
    jb = r["jump_b"]
    assert jb["state"] == 4 and jb["active"] == 1 and (jb["in"], jb["out"]) == (B, 1265), jb
    fr, plays = _moved_from(r["jump_b_frames"], B)
    assert plays and max(fr) <= 1265
    # delete A
    da = r["delete_a"]
    assert da["deletes"] == 1 and da["mask"] == 6 and da["slots"][0][0] == 0 and da["state"] == 4, da
    assert r["leds_del"] == [0xE, 0xF]                                                   # A blinks
    # record A as the playing loop B
    ra = r["record_a_loop"]
    assert ra["records"] == 2 and ra["slots"][0] == (1, 2, 150, 2 * B, 2 * 1265 + 1), ra
    # jump to A (a loop) while playing: the loop plays
    jl = r["jump_a_loop"]
    assert jl["state"] == 4 and (jl["in"], jl["out"]) == (B, 1265), jl
    # C while paused: cued at C, not playing
    jc = r["jump_c_paused"]
    assert r["paused"]["state"] == 5 and jc["state"] == 6 and jc["frame"] == c and set(r["paused_frames"]) == {c}, jc
    # mode off: stock BEAT 4 loop, LEDs stock
    assert r["off"]["mode"] == 0 and r["off_beat4"]["state"] == 4 and r["off_beat4"]["beats"] == 4
    # GRID96 has no ANLZ: BEAT 4 is stock in hot-cue mode
    g = r["grid96_beat4"]
    assert r["grid96"]["avail"] == 0 and g["state"] == 4 and g["beats"] == 4 and g["jumps"] == jc["jumps"], g
    assert g["failed"] == 0
    # the stick's ANLZ: A = the loop, B as exported, C = the point (deck units: 1/150 s)
    typ, cnt, length, cues = r["anlz"]
    assert (cnt, length) == (3, 3 * 0x38 + 0x18)
    assert cues == [(1, 2, 150, 2 * B, 2 * 1265 + 1), (2, 2, 1000, 15000, 16875), (3, 1, 150, 2 * c, 1)], cues
    # reboot: the mode is still on and the cues come back from the stick
    rb = r["reboot"]
    assert rb["mode"] == 0x5A and rb["avail"] == 1 and rb["mask"] == 7, rb
    assert r["leds_reboot"] == [0xF]
