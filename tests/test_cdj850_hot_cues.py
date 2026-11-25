# SPDX-License-Identifier: GPL-2.0-or-later
"""hot_cues on the CDJ-850 MAIN 1.13: BEAT 1/2/4 as rekordbox hot cues A/B/C.

* static: the five pool-word redirects (stock words, the one instruction that reads each, all
  outside the boot-critical closure, as is every MAIN routine the payload calls), nothing else
  written outside the loader, composition with every mod on the branch, the spare settings bits;
* the compiled payload on the SH-2A interpreter (tests/sh2a.py) with small stand-ins for the MAIN
  routines: the key filter (BEAT SELECT + RELOOP toggles and saves the mode and undoes the fraction
  toggle, pads are kept from stock and queued, 8 + pad = delete, stock without ANLZ), the CALL
  selection, the ANLZ delete/count fix, the LED inputs around wai_flg and the frame a new point cue
  is recorded at (the nearest beat with quant_loops and quantize on, else the play position);
* live (QEMU + a baken stick: GRID128 with hot cues A = point, B = 1-bar loop, C empty; GRID96
  without ANLZ), every branch mod built in: toggle, LEDs, jump while playing (keeps playing),
  record C (snapped to the nearest beat: quantize is on), jump to the loop B, delete A, record A as the playing loop, jump to it, jump while
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
# the HOT CUE ON/OFF flash: a hook on the shared display_stage_bpm span (tests/test_cdj850_quant_loops.py
# covers the flash routine itself)
SHOW = ["hot_cues_flash"]
# (pool word, stock value, the instruction that reads it, payload symbol)
POOLS = [(0x0C0ED038, 0x0C0DD492, 0x0C0ECEFC, "c850_ui_keys"),
         (0x0C0EC838, 0x0C0EB388, 0x0C0EC73E, "c850_ui_tick"),
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
        assert struct.unpack_from(">I", out, o)[0] == payloadbuild.lookup(p.symbols, payloadbuild.gate_name(symbol)), hex(pool)
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
ALL_FOLDERS = ["auto_standby", "bpm_decimals", "hot_cues", "loop_resize", "payload_demo", "quant_loops"]


# The cue prefill request of the audio task: its pool word is inside the boot-critical closure, so it
# goes through the service-mode gate (players/cdj850.GATED_BOOT_POOLS) and validate.py checks the thunk.
PREFILL = ["hot_cues_prefill"]
PREFILL_POOL, PREFILL_OLD, PREFILL_READER = 0x0C0F54D8, 0x0C008C8A, 0x0C0F534C


@needs_main
@needs_toolchain
def test_prefill_pool_is_a_gated_boot_pool():
    import bootcrit
    import validate
    from players.cdj850 import GATED_BOOT_POOLS, MAIN_PROFILE
    stock = MAIN.read_bytes()
    report, spans = {}, {}
    names = MODS + PREFILL + SHOW
    out = patch_main.patch(stock, patch_main.with_loader(names, "cdj850"), "cdj850", report, spans)
    assert all(report[n][0] == "applied" for n in MODS + PREFILL), report
    o = PREFILL_POOL - BASE
    assert struct.unpack_from(">I", stock, o)[0] == PREFILL_OLD
    assert _pool_readers(stock, PREFILL_POOL) == [PREFILL_READER]
    # the reader: mov.l @(..),r2; jsr @r2; mov r13,r6, after the pushes of 120 and 400 (0x0C0F533A..)
    assert struct.unpack_from(">5H", stock, 0x0C0F533A - BASE) == (0xEE78, 0x2FE6, 0xEE64, 0x4E08, 0x2FE6)
    assert struct.unpack_from(">3H", stock, PREFILL_READER - BASE) == (0xD262, 0x420B, 0x66D3)
    closure = bootcrit.closure(stock, BASE, byteorder=">")
    assert closure.hit(o, o + 4)                       # why it needs the gate
    assert PREFILL_POOL in GATED_BOOT_POOLS
    p = payloadbuild.build(["hot_cues"], "cdj850")
    assert struct.unpack_from(">I", out, o)[0] == _sym(p, "__gate_hot_cues_ring_request")
    rep = validate.Report()
    validate.check_main_image(stock, out, MAIN_PROFILE, spans, rep, gated_boot_pools=GATED_BOOT_POOLS)
    assert rep.exit_code == 0, rep.text()
    # without the allowance the validator refuses it
    rep = validate.Report()
    validate.check_main_image(stock, out, MAIN_PROFILE, spans, rep, gated_boot_pools=GATED_BOOT_POOLS - {PREFILL_POOL})
    assert rep.exit_code != 0


@needs_main
@needs_toolchain
def test_composes_with_the_branch_mods():
    names = ALL_MODS + MODS + SHOW
    report = {}
    out = patch_main.patch(MAIN.read_bytes(), names, "cdj850", report)
    assert all(report[n][0] == "applied" for n in names), report
    p = payloadbuild.build(ALL_FOLDERS, "cdj850")
    for pool, _, _, symbol in POOLS:
        assert struct.unpack_from(">I", out, pool - BASE)[0] == payloadbuild.lookup(p.symbols, payloadbuild.gate_name(symbol))


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
    if mode:
        _call(p, mem, "c850_ui_set_hot_cues_armed", 1)
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
    """The remap built k0..k2; c850_ui_keys (with hot_cues_key_filter) filters them. Returns the words stock
    then sees."""
    mem.write(K0, struct.pack(">HHH", k0, k1, k2))
    assert _call(p, mem, "c850_ui_keys") == 0x55
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
    assert _call(p, mem, "hot_cues_mode") == 1 and mem.u32(_sym(p, "hot_cues_toggles")) == 1
    assert mem.read(SETTINGS, 3) == b"\xC3\x02\x10"                           # flag bytes untouched
    assert mem.u32(LOG + 28) == 0                                          # RAM only: no flash save!
    assert (_u8(mem, D154), _u8(mem, D156)) == (0, 0)                      # fraction toggle undone
    assert _keys(p, mem, k0=BEAT_SELECT) == (0, 0, 0)                      # still held: hidden
    assert _keys(p, mem) == (0, 0, 0)
    # toggling back
    _keys(p, mem, k0=BEAT_SELECT)
    _keys(p, mem, k0=BEAT_SELECT | RELOOP)
    assert _call(p, mem, "hot_cues_mode") == 0 and mem.u32(LOG + 28) == 0
    _keys(p, mem)
    # RELOOP alone and BEAT SELECT alone stay stock
    assert _keys(p, mem, k0=RELOOP) == (RELOOP, 0, 0)
    assert _keys(p, mem) == (0, 0, 0)
    assert _keys(p, mem, k0=BEAT_SELECT) == (BEAT_SELECT, 0, 0)
    assert _keys(p, mem) == (0, 0, 0)
    # RELOOP held first, then BEAT SELECT: not the combo (stock order)
    _keys(p, mem, k0=RELOOP)
    assert _keys(p, mem, k0=RELOOP | BEAT_SELECT) == (RELOOP | BEAT_SELECT, 0, 0)
    assert _call(p, mem, "hot_cues_mode") == 0
    _keys(p, mem)
    # Turning HOT CUES OFF in UTILITY disables the mod and blocks BEAT SELECT + RELOOP
    _call(p, mem, "c850_ui_set_flag", 0x0800, 1)
    assert mem.u32(LOG + 28) == 0                                          # high bit, no flash save
    _keys(p, mem, k0=BEAT_SELECT)
    assert _keys(p, mem, k0=BEAT_SELECT | RELOOP) == (BEAT_SELECT | RELOOP, 0, 0)
    assert _call(p, mem, "hot_cues_mode") == 0


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
    _call(p, mem, "c850_ui_set_hot_cues_armed", 1)
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
@pytest.mark.parametrize("mode, slots, want", [(1, (1, 2, 0), (12, 0)), (1, (0, 0, 0), (15, 0)),
                                               (1, (1, 2, 3), (8, 0)), (0, (1, 0, 0), None)])
def test_leds_are_faked_only_while_playertask_waits(mode, slots, want):
    p = _payload()
    mem = _mem(p, mode=mode, slots=slots, frac=1, beats=4)
    assert _call(p, mem, "hot_cues_wait", 6, 0x37, 1, 0x0DDFF800, stack=(0x1234,)) == 0
    assert mem.u32(LOG + 0x100) == 6 and mem.u32(LOG + 0x114) == 0x1234        # args, 5th on the stack
    seen = (_u8(mem, LOG + 0x110), _u8(mem, LOG + 0x111))
    assert seen == (want or (4, 1))
    assert (_u8(mem, BEATS), _u8(mem, D154)) == (4, 1)                          # real values back


@needs_toolchain
@pytest.mark.parametrize("start_state", [5, 6])
def test_paused_or_cued_hot_cue_auto_plays(start_state):
    p = _payload()
    mem = _mem(p, mode=1, slots=(1, 0, 0), state=start_state)
    mem.add(0x0C7F6400, bytes(0x80))
    mem.add(0x0CE49F50, bytes(8))
    mem.put32(0x0C7F677C, 0xFFFFFFFF)
    mem.write(0x0C7F645C, struct.pack(">hhh", 200, 500, 0))
    mem.put32(0x0C7F6470, 0x0C1E8E5C + 200 * 0x930)
    struct.pack_into(">HII", mem.find(CTX + 0xF90, 0x38)[0], mem.find(CTX + 0xF90, 0x38)[1] + 0x1E, 150, 200, 1)
    _stub(mem, 0x0C0746BC, "mov.l 1f,r0\nrts\nnop\n.align 2\n1: .long 0x0DDFEF00\n")
    _stub(mem, 0x0C0C3550, "mov.l 1f,r0\nrts\nnop\n.align 2\n1: .long 0x11111111\n")
    _stub(mem, 0x0C0C359A, "mov.l 1f,r0\nrts\nnop\n.align 2\n1: .long 0x22222222\n")
    _stub(mem, 0x0C0C12B2, """
        mov #1,r0
        mov.l r0,@r6
        mov.w 1f,r0
        mov.w r0,@(4,r6)
        rts
        mov #0,r0
        .align 2
    1:  .short 1
        .short 0
    """)                                                   # 1 hot-cue record for slot 1
    _stub(mem, 0x0C00851E, "rts\nmov r4,r0")             # ring_sector_to_frame: identity
    _stub(mem, 0x0C01DE1C, """
        mov #0,r0
        mov.l r0,@r5
        mov #100,r0
        mov.l r0,@(12,r5)
        rts
        mov #1,r0
    """)                                                   # rec_to_entry -> point cue at frame 100
    _stub(mem, 0x0C0D3B1A, "rts\nnop")
    assert _keys(p, mem, k1=B1) == (0, 0, 0)
    _call(p, mem, "hot_cues_tick")                         # trigger queued slot 1 in paused/cued state
    assert mem.u32(_sym(p, "hot_cues_instants")) == 1
    assert mem.u16(0x0C7F645C) == 100
    assert _u8(mem, _sym(p, "hc_play")) > 0
    _call(p, mem, "hot_cues_tick")
    assert mem.u16(K0) & PLAY == PLAY
    assert mem.u32(_sym(p, "hot_cues_plays")) == 1


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


@needs_toolchain
def test_point_record_snaps_to_the_nearest_beat_with_quantize():
    """hot_cues_point: the frame a new point cue is recorded at. hot_cues alone keeps the play position (its
    weak quant_loops_in_point); with quant_loops it is LOOP IN's nearest beat while quantize is on (and the
    grid gave the deck's tempo), the play position with quantize off."""
    import test_cdj850_quant_loops as ql
    alone = _payload()
    mem = _mem(alone)
    for pos in (0, 202, 215, 231, 600):
        assert _call(alone, mem, "hot_cues_point", pos) == pos
    assert struct.unpack(">I", mem.read(_sym(alone, "hot_cues_snapped"), 4))[0] == 0

    both = payloadbuild.build(["hot_cues", "quant_loops"], "cdj850")
    for label, entries in ql.GRIDS.items():
        g = ql.Grid(entries)
        base = (entries[0][1] + 5) // 10
        on, off = ql._mem(both, entries, base=base), ql._mem(both, entries, base=base, flags=0x53)
        wrong = ql._mem(both, entries, base=base + 10)          # the grid is not the deck's tempo
        moved = 0
        for pos in range(0, g.f(len(entries) - 1) + 40, 7):
            want = g.in_point(pos)
            assert _call(both, on, "hot_cues_point", pos) == want, (label, pos)
            assert _call(both, off, "hot_cues_point", pos) == pos, (label, pos)
            assert _call(both, wrong, "hot_cues_point", pos) == pos, (label, pos)
            moved += want != pos
            assert struct.unpack(">I", on.read(_sym(both, "hot_cues_last_pos"), 4))[0] == pos
        assert moved and struct.unpack(">I", on.read(_sym(both, "hot_cues_snapped"), 4))[0] == moved, label
    g = ql.Grid(ql.GRIDS["128 @0.35"])
    assert g.in_point(231) == 237 and g.in_point(215) == 202


# ---- PCM snapshot cache and background pre-cache ----

CACHE = ["hot_cues_play_req", "hot_cues_play_tick", "hot_cues_usb_tick", "hot_cues_beba0",
         "hot_cues_pcm_init", "hot_cues_pcm_write"]
# (pool, stock word, reader, payload symbol, inside the boot-critical closure -> gated)
CACHE_POOLS = [
    (0x0C0F73A8, 0x0C008C8A, (0x0C0F7312, 0x0C0F7332), "hot_cues_play_ring_request", False),
    (0x0C014E8C, 0x0C0F65F0, (0x0C014D2E,), "hot_cues_play_tick", True),
    (0x0C014E90, 0x0C0F6E6C, (0x0C014D40,), "hot_cues_usb_tick", True),
    (0x0C0BE0CC, 0x0C0BEBA0, (0x0C0BDFB4,), "hot_cues_beba0", True),
    (0x0C0BF818, 0x0C0EFB8E, (0x0C0BF750,), "hot_cues_pcm_init", False),
    (0x0C0C056C, 0x0C0EFB9A, (0x0C0C02F8,), "hot_cues_pcm_write", False),
]
HC_CACHE_BASE, HC_CACHE_END, HC_SLOT_STRIDE, HC_SLOT_PCM = 0x0DE6BD70, 0x0DF6FFF0, 0x56B80, 150 * 0x930
RING, RING_META, SECTOR = 0x0C1E8E5C, 0x0C750C3C, 0x930
MEMCPY = """
        tst     r6,r6
        bt      2f
        shlr2   r6
1:      mov.l   @r5+,r0
        mov.l   r0,@r4
        dt      r6
        bf/s    1b
        add     #4,r4
2:      rts
        nop
"""


@needs_main
@needs_toolchain
def test_cache_pools_are_gated_where_boot_critical():
    import bootcrit
    import validate
    from players.cdj850 import GATED_BOOT_POOLS, MAIN_PROFILE
    stock = MAIN.read_bytes()
    report, spans = {}, {}
    out = patch_main.patch(stock, patch_main.with_loader(MODS + PREFILL + CACHE + SHOW, "cdj850"), "cdj850",
                           report, spans)
    assert all(report[n][0] == "applied" for n in CACHE), report
    p = payloadbuild.build(["hot_cues"], "cdj850")
    closure = bootcrit.closure(stock, BASE, byteorder=">")
    for pool, old, readers, symbol, gated in CACHE_POOLS:
        o = pool - BASE
        assert struct.unpack_from(">I", stock, o)[0] == old, hex(pool)
        assert tuple(_pool_readers(stock, pool)) == readers, hex(pool)
        assert struct.unpack_from(">I", out, o)[0] == _sym(p, payloadbuild.gate_name(symbol)), hex(pool)
        assert bool(closure.hit(o, o + 4)) == gated, hex(pool)
        assert (pool in GATED_BOOT_POOLS) == gated, hex(pool)
    rep = validate.Report()
    validate.check_main_image(stock, out, MAIN_PROFILE, spans, rep, gated_boot_pools=GATED_BOOT_POOLS)
    assert rep.exit_code == 0, rep.text()


@needs_toolchain
def test_cache_lies_above_every_cdj850_payload():
    """payload.ld emits .bss into the image, so the 1 MB cache sits at fixed SDRAM above all mods' payload."""
    p = payloadbuild.build(payloadbuild.payload_mods("cdj850"), "cdj850")
    assert _sym(p, "_payload_end") <= HC_CACHE_BASE
    assert HC_CACHE_BASE + 3 * HC_SLOT_STRIDE == HC_CACHE_END <= 0x0DF6FFFC
    assert HC_SLOT_STRIDE == 150 * (0x930 + 16)


def _cache_mem(p):
    mem = _mem(p, mode=1)
    mem.add(HC_CACHE_BASE, bytes(3 * HC_SLOT_STRIDE))
    mem.add(0x0DD00000, bytes(0x30000))                   # decoder output, messages
    mem.add(0x0CE5AC40, bytes(0x40))                      # decoder state (kind 0x0CE5AC70 ...)
    mem.add(0x0CE5A9E0, bytes(0x10))
    mem.add(0x0CE59B00, bytes(0x800))                     # track info (+0x3A1 VBR flag)
    mem.add(0x0C7F6400, bytes(0x80))
    mem.add(0x0CE49F50, bytes(8))
    _stub(mem, 0x0C1100B8, MEMCPY)
    return mem


def _slot(mem, p, k, cue, state, sectors=0, out=0, loop=0):
    mem.write(_sym(p, "hc_cache") + 20 * k,
              struct.pack(">IIIIHBB", 0x11111111, 0x22222222, cue, out, sectors, state, loop))


def _slot_get(mem, p, k):
    return struct.unpack(">IIIIHBB", mem.read(_sym(p, "hc_cache") + 20 * k, 20))


@needs_toolchain
def test_pcm_write_diverts_decoded_sectors_into_the_cache_slot():
    p = _payload()
    mem = _cache_mem(p)
    _stub(mem, 0x0C0EFB9A, _logger(5))                     # stock pcm_ring_write
    pcm = bytes((i * 7) & 0xFF for i in range(2 * SECTOR))
    mem.write(0x0DD00000, pcm)
    mem.write(_sym(p, "hc_bg_divert"), b"\x01")
    mem.write(_sym(p, "hc_bg_slot"), b"\x02")
    mem.write(_sym(p, "hc_bg_want"), struct.pack(">H", 150))
    mem.put32(_sym(p, "hc_bg_frame"), 4500)
    mem.write(0x0CE5A9E4, struct.pack(">H", 3))
    _call(p, mem, "hot_cues_pcm_write", 0x0DD00000, 2 * SECTOR // 4)
    slot = HC_CACHE_BASE + HC_SLOT_STRIDE
    assert mem.read(slot, 2 * SECTOR) == pcm
    assert mem.u16(_sym(p, "hc_bg_sectors")) == 2
    meta = [struct.unpack(">IIII", mem.read(slot + HC_SLOT_PCM + 16 * k, 16)) for k in range(3)]
    assert meta[:2] == [(4500, 0, 3, 0xFFFFFFFF), (4501, 0, 3, 0xFFFFFFFF)] and meta[2] == (0, 0, 0, 0)
    assert mem.u32(LOG + 0x40 * 5 + 28) == 0                # the ring was not written
    mem.write(_sym(p, "hc_bg_divert"), b"\x00")
    _call(p, mem, "hot_cues_pcm_write", 0x0DD00000, 588)
    assert mem.u32(LOG + 0x40 * 5 + 28) == 1 and mem.u32(LOG + 0x40 * 5 + 4) == 588


@needs_toolchain
@pytest.mark.parametrize("divert", [0, 1])
def test_pcm_init_switches_the_decode_off_the_ring_only_when_diverted(divert):
    p = _payload()
    mem = _cache_mem(p)
    _stub(mem, 0x0C0EFB8E, _logger(6))                     # stock pcm_ring_set_sector
    mem.write(0x0CE5AC70, b"\x04\x01")
    mem.write(0x0CE5AC44, struct.pack(">H", 0x546))
    mem.write(_sym(p, "hc_bg_divert"), bytes([divert]))
    _call(p, mem, "hot_cues_pcm_init", 0x546)
    assert mem.u32(LOG + 0x40 * 6) & 0xFFFF == (0 if divert else 0x546)
    assert mem.read(0x0CE5AC70, 2) == (b"\x0a\x02" if divert else b"\x04\x01")
    assert mem.u16(0x0CE5AC44) == (0 if divert else 0x546)


@needs_toolchain
def test_play_read_ahead_waits_for_the_restore_continuation():
    p = _payload()
    mem = _cache_mem(p)
    _stub(mem, 0x0C008C8A, _logger(7))
    mem.write(_sym(p, "hc_stream_inflight"), b"\x01")
    mem.put32(0x0CE49F54, 1000)
    mem.put32(_sym(p, "hc_stream_since"), 900)
    _call(p, mem, "hot_cues_play_ring_request", 5, 0, 1, 0)
    assert mem.u32(LOG + 0x40 * 7 + 28) == 0                # still streaming: no duplicate request
    mem.write(0x0C7F663D, b"\x01")                         # the continuation read is done
    _call(p, mem, "hot_cues_play_ring_request", 5, 0, 1, 0)
    assert mem.u32(LOG + 0x40 * 7 + 28) == 1 and _u8(mem, 0x0C7F663D) == 0
    assert _u8(mem, _sym(p, "hc_stream_inflight")) == 0


@needs_toolchain
@pytest.mark.parametrize("cancel", [0, 1])
def test_beba0_marks_the_slot_ready_after_a_diverted_decode(cancel):
    p = _payload()
    mem = _cache_mem(p)
    sectors = _sym(p, "hc_bg_sectors")
    divert = _sym(p, "hc_bg_divert")
    # FUN_0C0BEBA0 stand-in: the decode filled 150 sectors; log whether the divert flag was up
    _stub(mem, 0x0C0BEBA0, """
        mov.l   1f,r1
        mov     #150,r0
        extu.b  r0,r0
        mov.w   r0,@r1
        mov.l   2f,r1
        mov.b   @r1,r0
        mov.l   3f,r1
        mov.l   r0,@r1
        rts
        mov     #0,r0
        .align 2
1:      .long   %#x
2:      .long   %#x
3:      .long   %#x
""" % (sectors, divert, LOG + 0x40 * 9))
    _slot(mem, p, 2, 6187, 1, out=6328, loop=1)
    mem.write(_sym(p, "hc_bg_inflight"), b"\x01")
    mem.write(_sym(p, "hc_bg_slot"), b"\x03")
    mem.write(_sym(p, "hc_bg_cancel"), bytes([cancel]))
    mem.put32(_sym(p, "hc_bg_frame"), 6187)
    mem.put32(_sym(p, "hc_bg_h1"), 0x11111111)
    mem.put32(_sym(p, "hc_bg_h2"), 0x22222222)
    mem.write(0x0DD10011, b"\x04\xFE")                     # kind 4, background marker
    _call(p, mem, "hot_cues_beba0", 0x0DD10000)
    assert _u8(mem, _sym(p, "hc_bg_inflight")) == 0 and _u8(mem, divert) == 0
    if cancel:
        assert _slot_get(mem, p, 2)[5] == 1 and mem.u32(_sym(p, "hot_cues_precache_aborts")) == 1
        assert mem.u32(LOG + 0x40 * 9) == 0                 # a cancelled job is not decoded
    else:
        assert mem.u32(LOG + 0x40 * 9) == 1                 # decoded with the divert up
        assert _slot_get(mem, p, 2)[4:6] == (150, 3)
        assert mem.u32(_sym(p, "hot_cues_precaches")) == 1 and mem.u32(_sym(p, "hot_cues_cache_mask")) == 4
    # a stock message passes straight through, divert down
    mem.write(0x0DD10011, b"\x06\x00")
    _call(p, mem, "hot_cues_beba0", 0x0DD10000)
    assert mem.u32(LOG + 0x40 * 9) == 0


@needs_toolchain
def test_call_prefill_request_restores_the_cached_cue_into_the_ring():
    p = _payload()
    mem = _cache_mem(p)
    mem.add(RING, bytes(2400 * SECTOR))
    mem.add(RING_META, bytes(2400 * 16))
    _stub(mem, 0x0C008C8A, _logger(7))                     # FUN_0C008C8A, the ring read request
    _stub(mem, 0x0C003E56, _logger(8))                     # frames -> MSF
    n, cue, track = 30, 562, 7950
    pcm = bytes((i * 13 + 5) & 0xFF for i in range(n * SECTOR))
    mem.write(HC_CACHE_BASE, pcm)
    mem.write(HC_CACHE_BASE + HC_SLOT_PCM, b"".join(struct.pack(">IIII", cue + k, 0, 1, 0xFFFFFFFF)
                                                    for k in range(n)))
    _slot(mem, p, 0, cue, 3, sectors=n)
    mem.put32(0x0CE49F54, 5000)
    mem.write(_sym(p, "hc_restore_slot"), b"\x01")
    mem.put32(_sym(p, "hc_restore_frame"), cue)
    mem.put32(_sym(p, "hc_restore_since"), 4900)
    mem.put32(0x0C7F66F0, cue)
    mem.put32(0x0C7F650C, track)
    _call(p, mem, "hot_cues_ring_request", 4, 0, 1, 0x0DD20000, stack=(400, 120, 0))
    assert mem.read(RING + 120 * SECTOR, n * SECTOR) == pcm
    assert struct.unpack(">I", mem.read(RING_META + 120 * 16, 4))[0] == cue
    assert struct.unpack(">I", mem.read(RING_META + 119 * 16, 4))[0] == 0xFFFFFFFF      # sentinels around it
    assert struct.unpack(">I", mem.read(RING_META + (120 + n) * 16, 4))[0] == 0xFFFFFFFF
    assert mem.u16(0x0C7F645E) == 120 + n and mem.u16(0x0C7F6460) == 120
    assert mem.u32(0x0C7F6744) == cue + n - 1 and _u8(mem, 0x0C7F6625) == 0x72
    assert mem.u32(LOG + 0x40 * 8) == cue + n                                           # continuation from cue + n
    assert mem.u32(LOG + 0x40 * 7 + 28) == 1 and mem.u32(LOG + 0x40 * 7) == 4
    assert mem.u32(_sym(p, "hot_cues_cache_hits")) == 1 and mem.u32(_sym(p, "hot_cues_prefill_last")) == 400
    assert _u8(mem, _sym(p, "hc_stream_inflight")) == 1


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
      "last_result", "avail", "mask", "snapped", "last_pos", "instants", "instant_misses", "instant_from",
      "instant_sector", "prefills", "prefill_last", "cache_hits", "cache_captures", "cache_mask", "precaches"]
GRID_CTX = 0x0C9D24FC


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
    try:
        return _drive_steps(tr, stick, flash, log, res, snap, frames, leds, ensure_playing, wait_frame, toggle)
    finally:
        (work / "drive.json").write_text(json.dumps(res))       # what was seen, also when a step fails


def _drive_steps(tr, stick, flash, log, res, snap, frames, leds, ensure_playing, wait_frame, toggle):
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
        wait_frame(d, max(d.rw(FRAME) + 150, 281 + 160))
        d.key("beat_1")                                   # A while playing
        time.sleep(0.4)
        res["jump_a_frames"] = frames(d, 2.0)
        snap(d, "jump_a")
        wait_frame(d, 600)
        d.key("beat_4")                                   # C is empty: record while playing
        time.sleep(2.5)
        snap(d, "record_c")
        raw = d.dump(GRID_CTX + 0x7C, 8 * 80)               # the grid window: where C may snap to
        res["grid"] = {"w": d.rh(GRID_CTX + 0x78), "ms": [struct.unpack_from(">I", raw, 8 * i + 4)[0]
                                                         for i in range(80)]}
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
        d.key("beat_4")                                   # C while paused: auto-plays from C
        time.sleep(2.5)
        res["paused_frames"] = frames(d, 2.0)
        snap(d, "jump_c_paused")
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


def _moved_from(fr, start, late=10):
    """The frames after the jump: the first sample at or after `start` (at most `late` frames on),
    and the deck plays on from there."""
    i = next(i for i, f in enumerate(fr) if start <= f <= start + late)
    return fr[i:], fr[-1] > fr[i] + 5             # the emulator plays slower than wall time


@needs_toolchain
@pytest.mark.skipif(not FLASH.is_file(), reason="extract/cdj850/flash_cdj850.bin is not present")
def test_live_hot_cues_with_every_branch_mod(tmp_path):
    if os.name == "nt":
        pytest.skip("QMP socket descriptor inheritance requires POSIX")
    stick = build_stick(tmp_path)
    flash = tmp_path / "flash.bin"
    flash.write_bytes(patch_main.patch_flash(FLASH.read_bytes(), ALL_MODS + MODS + (PREFILL + CACHE if not os.environ.get("HC_LIVE_NO_PREFILL") else []) + SHOW, "cdj850"))
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
    assert on["mode"] == 0 and on["toggles"] == 1 and on["set"] == l0["set"]            # RAM-only arming, zero flash write
    assert on["d154"] == 0 and on["state"] == 3                                          # no fraction, no RELOOP
    assert r["leds_on"] == [3, 0xF]                                                      # C and BEAT 8 blink (A+B lit)
    # A while playing, A (frame 281) about 400 frames behind, still in the ring: the instant jump, no CALL
    # and no PLAY press; the deck plays on from A
    ja = r["jump_a"]
    assert ja["jumps"] == 1 and ja["last_in"] == A and ja["state"] == 3 and ja["plays"] == 0, ja
    assert ja["instants"] == 1 and ja["instant_misses"] == 0 and ja["instant_from"] > A + 150, ja
    # sampling starts 0.4 s after the press and an instant jump does not wait for a prefill, so the
    # first sample is already up to ~30 frames past A (emulator speed)
    fr, plays = _moved_from(r["jump_a_frames"], A, late=45)
    assert fr[0] == r["jump_a_frames"][0] and plays and all(b - a in range(0, 6) for a, b in zip(fr, fr[1:])), fr    # no gap, no CALL restart
    # record C while playing (about frame 600), the deck plays on; quantize is on (fresh deck) and GRID128's
    # grid gave the tempo, so C is the beat nearest the play position (quant_loops_in_point)
    rc = r["record_c"]
    assert rc["records"] == 1 and rc["last_result"] == 0 and rc["state"] == 3 and rc["mask"] == 7, rc
    c, pos = rc["last_in"], rc["last_pos"]
    beats = [(ms * 75 + 500) // 1000 for ms in r["grid"]["ms"]]
    assert r["grid"]["w"] == 0 and beats[8] == A                                          # A = bar 3 = beat 8
    assert 600 <= pos <= 640 and c == min(beats, key=lambda f: (abs(f - pos), f)), (pos, c, beats[15:20])
    assert rc["snapped"] == (c != pos) and abs(c - pos) <= 18, rc                         # half a beat at 128
    assert rc["slots"][2] == (3, 1, 150, 2 * c, 1), rc
    assert r["leds_c"] == [7, 0xF]                                                       # BEAT 8 blinks while active
    # B: the loop plays
    jb = r["jump_b"]
    assert jb["state"] == 4 and jb["active"] == 1 and (jb["in"], jb["out"]) == (B, 1265), jb
    # a loop slot takes the CALL path; B's 2 s were copied out of the ring after the load (snapshot cache),
    # so the CALL restores them from SDRAM instead of waiting for a prefill
    if not os.environ.get("HC_LIVE_NO_PREFILL"):
        assert jb["cache_hits"] == 1 and jb["prefills"] == 0, jb
    fr, plays = _moved_from(r["jump_b_frames"], B)
    assert plays and max(fr) <= 1265
    # delete A
    da = r["delete_a"]
    assert da["deletes"] == 1 and da["mask"] == 6 and da["slots"][0][0] == 0 and da["state"] == 4, da
    assert r["leds_del"] == [6, 0xF]                                                     # A and BEAT 8 blink
    # record A as the playing loop B
    ra = r["record_a_loop"]
    assert ra["records"] == 2 and ra["slots"][0] == (1, 2, 150, 2 * B, 2 * 1265 + 1), ra
    # jump to A (a loop) while playing: the loop plays
    jl = r["jump_a_loop"]
    assert jl["state"] == 4 and (jl["in"], jl["out"]) == (B, 1265), jl
    # C while paused: auto-plays from C immediately
    jc = r["jump_c_paused"]
    assert r["paused"]["state"] == 5 and jc["state"] == 3, jc
    fr, plays = _moved_from(r["paused_frames"], c, late=45)
    assert plays and all(b - a in range(0, 6) for a, b in zip(fr, fr[1:])), fr
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
    # reboot: cues come back from the stick, while hot-cue arming is RAM-only (off after reboot)
    rb = r["reboot"]
    assert rb["mode"] == 0 and rb["avail"] == 1 and rb["mask"] == 7, rb
    assert r["leds_reboot"] == [0xF]


# A 106 s track (56 bars at 128): hot cues A/B/C at bars 5/33/45 = frames 562 / 4500 / 6187; C is a one-bar
# loop (out 6328). B and C lie far outside the 32 s PCM ring, so only the background pre-cache can make them
# instant on the first hit.
_LONG_STICK_BUILDER = _STICK_BUILDER.split("spec = {")[0].replace("if num == 1:      # hot cue B",
                                                                    "if num == 2:      # hot cue C") \
    .replace('Num="1"', 'Num="2"') + r'''
spec = {"device_name": "PRECACHE", "playlist": "Precache", "tracks": [
    {"title": "LONG128", "bpm": 128, "first_downbeat": 0, "bars": 56, "memory_cues": [13], "hot_cues": [5, 33, 45]}]}
sp = pathlib.Path(out_dir) / 'spec.json'
sp.write_text(json.dumps(spec))
rc = stick.main(['--spec', str(sp), '--out', out_dir + '/wav-stick', '--image', image, '--size-mb', '64',
                 '--baken', baken])
if rc:
    raise SystemExit(rc)
'''


@needs_toolchain
@pytest.mark.skipif(not FLASH.is_file(), reason="extract/cdj850/flash_cdj850.bin is not present")
def test_live_out_of_ring_hot_cues_play_from_the_precache(tmp_path):
    if os.name == "nt":
        pytest.skip("QMP socket descriptor inheritance requires POSIX")
    import importlib.util
    if not BAKEN.is_file() or importlib.util.find_spec("pyfatfs") is None or importlib.util.find_spec("numpy") is None:
        pytest.skip("tools/baken/baken, pyfatfs and numpy are needed for the hot-cue stick")
    sys.path.insert(0, str(ROOT / "scripts" / "debug"))
    import cdj850_trace as tr
    stick = tmp_path / "precache.img"
    subprocess.run([sys.executable, "-c", _LONG_STICK_BUILDER, str(ROOT / "scripts" / "media"),
                    str(tmp_path / "stick"), str(stick), str(BAKEN)], check=True, capture_output=True, text=True,
                   timeout=300)
    flash = tmp_path / "flash.bin"
    flash.write_bytes(patch_main.patch_flash(FLASH.read_bytes(), ALL_MODS + MODS + PREFILL + CACHE + SHOW, "cdj850"))
    syms = payloadbuild.build(ALL_FOLDERS, "cdj850").symbols
    S = lambda n: payloadbuild.lookup(syms, "hot_cues_" + n)
    A, B, C, C_OUT = 562, 4500, 6187, 6328
    names = ["jumps", "plays", "prefills", "cache_hits", "cache_captures", "cache_mask", "precaches",
             "precache_aborts", "instants"]

    with tr.Deck(stick, flash=flash, log=open(tmp_path / "qemu.log", "w")) as d:
        def stats():
            d.q("stop")
            v = {"state": d.rb(STATE_USB), "frame": d.rw(FRAME), "in": d.rw(LOOP_IN), "out": d.rw(OUT_REC),
                 "active": d.rb(ACTIVE), "write_sec": d.rh(0x0C7F645E)}
            v.update({n: d.rw(S(n)) for n in names})
            d.q("cont")
            return v

        def follow(pred, timeout=90):
            """(wall s, frame, state) samples until pred(frame, state); the emulator is slower than wall time."""
            out, t0 = [], time.monotonic()
            while time.monotonic() - t0 < timeout:
                fr, st = d.rw(FRAME), d.rb(STATE_USB)
                out.append((round(time.monotonic() - t0, 2), fr, st))
                if pred(fr, st):
                    break
                time.sleep(0.05)
            return out

        d.wait_line("mode=0x0a", "[LONG128]", timeout=300)
        time.sleep(1.5)
        for _ in range(6):
            if d.rb(STATE_USB) == 3:
                break
            d.key("play")
            time.sleep(2.5)
        assert d.rb(STATE_USB) == 3
        # UTILITY HOT CUES is on by default: the pre-cache starts with the load; A is in the ring (copied
        # out of it), B and C are decoded into SDRAM by mp3ActTask between stock read-aheads
        t0 = time.monotonic()
        while d.rw(S("cache_mask")) != 7 and time.monotonic() - t0 < 120:
            time.sleep(0.5)
        pre = stats()
        print("precached:", pre)
        assert pre["cache_mask"] == 7 and pre["precaches"] >= 2 and pre["state"] == 3, pre
        d.hold("beat_select")
        time.sleep(0.6)
        d.key("reloop")
        time.sleep(0.8)
        d.release("beat_select")
        time.sleep(0.5)

        # B, ~60 s ahead: on B within one poll, playing throughout, on past the 150 cached sectors
        d.key("beat_2")
        fb = follow(lambda fr, st: fr > B + 200)
        jb = stats()
        print("B:", jb, fb[:6], fb[-3:])
        after = [x for x in fb if x[1] >= B]
        assert after and after[0][0] < 0.6, fb[:10]
        assert all(x[2] == 3 for x in after) and all(y[1] >= x[1] for x, y in zip(after, after[1:])), fb
        assert jb["cache_hits"] == 1 and jb["prefills"] == 0 and jb["frame"] > B + 200, jb

        # C, a loop: armed and playing from the cache
        d.key("beat_4")
        follow(lambda fr, st: st == 4 and C <= fr < C_OUT, timeout=30)
        time.sleep(1.0)
        jc = stats()
        print("C:", jc)
        assert jc["cache_hits"] == 2 and jc["prefills"] == 0, jc
        assert jc["state"] == 4 and jc["active"] == 1 and (jc["in"], jc["out"]) == (C, C_OUT), jc

        # leave the loop, pause, then A (~75 s back) from paused: auto-plays from the cache
        d.key("reloop")
        follow(lambda fr, st: st == 3, timeout=20)
        for _ in range(3):
            d.key("play")
            if follow(lambda fr, st: st not in (3, 4), timeout=10)[-1][2] not in (3, 4):
                break
        assert d.rb(STATE_USB) in (5, 6), stats()
        d.key("beat_1")
        fa = follow(lambda fr, st: st == 3 and fr > A + 160)
        ja = stats()
        print("A:", ja, fa[:6])
        assert ja["cache_hits"] == 3 and ja["prefills"] == 0, ja
        assert ja["state"] == 3 and ja["frame"] > A + 160, ja
