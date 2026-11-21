# SPDX-License-Identifier: GPL-2.0-or-later
"""The SH7263 cache-model plugin (scripts/cache/cdj850_cache.c).

A hand-assembled SH-2A program in an otherwise erased flash image turns the
caches on and then makes each kind of coherency mistake once, at its own pc,
next to the correct way of doing the same thing. The plugin has to report
exactly the mistakes, at the right pcs, and stay quiet about the rest."""
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
import time

import pytest

ROOT = Path(__file__).resolve().parent.parent
BUILD = ROOT / "scripts" / "cache" / "build_cache_plugin.sh"
CODE = 0x100                      # the board's fixed reset entry (cached space)


class Asm:
    """Just enough SH-2A to write the scenarios: literal loads go to one pool
    after the code, and labels mark the pcs the plugin should name."""

    def __init__(self, base):
        self.base = base
        self.words = []
        self.lits = []            # (word index, value)
        self.labels = {}

    @property
    def pc(self):
        return self.base + 2 * len(self.words)

    def w(self, *ws):
        self.words.extend(ws)

    def label(self, name):
        self.labels[name] = self.pc

    def li(self, n, value):       # mov.l @(disp,pc),Rn from the pool
        self.lits.append((len(self.words), value & 0xFFFFFFFF))
        self.w(0xD000 | n << 8)

    def mov(self, n, imm):        # mov #imm,Rn
        self.w(0xE000 | n << 8 | (imm & 0xFF))

    def st(self, m, n):           # mov.l Rm,@Rn
        self.w(0x2002 | n << 8 | m << 4)

    def ld(self, m, n):           # mov.l @Rm,Rn
        self.w(0x6002 | n << 8 | m << 4)

    def jsr(self, n):             # jsr @Rn ; nop
        self.w(0x400B | n << 8, 0x0009)

    def image(self):
        code_end = self.base + 2 * len(self.words)
        pool = (code_end + 3) & ~3
        words = list(self.words) + [0x0009] * ((pool - code_end) // 2)
        values = []
        for idx, value in self.lits:
            pc = self.base + 2 * idx
            if value in values:
                slot = values.index(value)
            else:
                slot = len(values)
                values.append(value)
            disp = (pool + 4 * slot - ((pc & ~3) + 4)) // 4
            assert 0 <= disp <= 0xFF, "literal pool out of reach"
            words[idx] |= disp
        return struct.pack(">%dH" % len(words), *words) + b"".join(struct.pack(">I", v) for v in values)


def program():
    a = Asm(CODE)
    a.li(1, 0xFFFC1000)                       # CCR1
    a.li(0, 0x808); a.st(0, 1)                # flush both caches
    a.li(0, 0x101); a.st(0, 1)                # ICE | OCE, write-back

    # stale-read: a cached line, then the same word written around the cache.
    a.li(1, 0x0C100000); a.li(2, 0x2C100000)
    a.ld(1, 0)                                # fill
    a.mov(3, 0x55); a.st(3, 2)                # uncached store
    a.label("stale_read"); a.ld(1, 0)         # hits the old line

    # uncached-stale: a dirty line, then an uncached read of the same word.
    a.li(1, 0x0C100100); a.li(2, 0x2C100100)
    a.mov(3, 0x66); a.st(3, 1)
    a.label("uncached_stale"); a.ld(2, 0)

    # The right way: write back with an associative invalidate first.
    a.li(1, 0x0C100200); a.li(2, 0x2C100200)
    a.mov(3, 0x67); a.st(3, 1)
    a.li(4, 0xF0800008 | (0x0C100200 & 0x7F0))
    a.li(5, 0x0C100200 & 0x1FFFF800)          # tag, U = V = 0
    a.st(5, 4)
    a.label("clean_uncached"); a.ld(2, 0)

    # dirty-discard: OCF throws a dirty line away.
    a.li(1, 0x0C100300); a.mov(3, 0x77); a.st(3, 1)
    a.li(1, 0xFFFC1000); a.li(0, 0x109)
    a.label("dirty_discard"); a.st(0, 1)

    # dma-read-stale: DMAC channel 0 armed to read a dirty line.
    a.li(1, 0x0C100400); a.mov(3, 0x78); a.st(3, 1)
    a.li(6, 0xFFFE1000)
    a.st(1, 6)                                # SAR
    a.li(0, 0xFFFC1C14); a.li(7, 0xFFFE1004); a.st(0, 7)        # DAR: a FIFO
    a.mov(0, 4); a.li(7, 0xFFFE1008); a.st(0, 7)                # DMATCR
    a.li(0, 0x1011); a.li(7, 0xFFFE100C)      # SM increment, TS longword, DE
    a.label("dma_arm"); a.st(0, 7)

    # stale-code: run a routine, rewrite it around the cache, run it again.
    a.li(8, 0x0C100800); a.li(9, 0x2C100800)
    a.li(0, 0x000B0009); a.st(0, 9)           # rts ; nop
    a.jsr(8)
    a.li(0, 0xE501000B); a.st(0, 9)           # mov #1,r5 ; rts
    a.li(0, 0x00090009); a.li(10, 0x2C100804); a.st(0, 10)
    a.jsr(8)                                  # still the old line on the chip

    # code-not-in-mem: code written through the cache, never written back.
    a.li(8, 0x0C100900)
    a.li(0, 0x000B0009); a.st(0, 8)
    a.jsr(8)

    # The right way for code: write it uncached, invalidate the I-line.
    a.li(8, 0x0C100A00); a.li(9, 0x2C100A00)
    a.li(0, 0x000B0009); a.st(0, 9)
    a.li(4, 0xF0000008 | (0x0C100A00 & 0x7F0)); a.li(5, 0x0C100A00 & 0x1FFFF800)
    a.st(5, 4)
    a.jsr(8)

    a.label("idle"); a.w(0xAFFE, 0x0009)      # bra . ; nop
    return a


def flash_image(a):
    img = bytearray(b"\xFF" * (4 << 20))
    struct.pack_into(">II", img, 0, CODE, 0xFFF90000)    # vectors, for the record
    code = a.image()
    img[CODE:CODE + len(code)] = code
    return bytes(img)


def _qemu():
    binary = os.environ.get("GUI_QEMU", os.path.expanduser("~/qemu-build-eb/qemu-system-sh4eb"))
    if not os.path.isfile(binary):
        pytest.skip("qemu-system-sh4eb is not built")
    return binary


def _plugin(tmp_path):
    if not shutil.which(os.environ.get("CC", "cc")) or not shutil.which("pkg-config"):
        pytest.skip("no C compiler or pkg-config")
    root = os.environ.get("CDJ_ROOT", str(ROOT))
    if not (Path(root) / "qemu-src" / "include" / "plugins" / "qemu-plugin.h").is_file():
        pytest.skip("no QEMU source tree (set CDJ_ROOT)")
    ext = "dylib" if os.uname().sysname == "Darwin" else "so"
    out = tmp_path / ("libcdj850_cache." + ext)
    subprocess.run(["bash", str(BUILD), str(out)], check=True, env=dict(os.environ, CDJ_ROOT=root),
                   stdout=subprocess.DEVNULL)
    return out


def run(tmp_path, image, wait_for, timeout=30):
    binary = _qemu()
    plugin = _plugin(tmp_path)
    flash = tmp_path / "flash.bin"
    flash.write_bytes(image)
    out = tmp_path / "cache"
    p = subprocess.Popen(
        [binary, "-M", "cdj850", "-display", "none", "-monitor", "none", "-serial", "none",
         "-drive", f"if=pflash,format=raw,file={flash},snapshot=on",
         "-plugin", f"{plugin},out={out}"],
        stderr=subprocess.PIPE)
    os.set_blocking(p.stderr.fileno(), False)
    log = b""
    deadline = time.time() + timeout
    try:
        while time.time() < deadline and not wait_for(log):
            time.sleep(0.2)
            log += p.stderr.read() or b""
    finally:
        p.terminate()
        p.wait(timeout=10)
        log += p.stderr.read() or b""
    text = Path(str(out) + "-report.txt").read_text()
    return text, log.decode("latin1")


def findings(report):
    return {(m.group(1), int(m.group(2), 16))
            for m in re.finditer(r"^([a-z-]+),0x([0-9a-f]{8}),", report, re.M)}


def test_each_mistake_is_reported_once_at_its_pc(tmp_path):
    a = program()
    report, log = run(tmp_path, flash_image(a), lambda log: len(re.findall(rb"cdj850-cache: [a-z-]+ pc=", log)) >= 6)
    got = findings(report)
    want = {
        ("stale-read", a.labels["stale_read"]),
        ("uncached-stale", a.labels["uncached_stale"]),
        ("dirty-discard", a.labels["dirty_discard"]),
        ("dma-read-stale", a.labels["dma_arm"]),
        ("stale-code", 0x0C100800),
        ("code-not-in-mem", 0x0C100900),
    }
    assert got == want, report + log
    assert "CCR1 writes 3 (ICF 1, OCF 2)" in report
    assert "DMA transfers armed 1" in report


def test_correct_firmware_patterns_stay_quiet(tmp_path):
    """Only the correct halves: caches on, write back before an uncached read,
    code written uncached with its I-line invalidated."""
    a = Asm(CODE)
    a.li(1, 0xFFFC1000); a.li(0, 0x808); a.st(0, 1); a.li(0, 0x101); a.st(0, 1)
    a.li(1, 0x0C100200); a.li(2, 0x2C100200)
    a.mov(3, 0x67); a.st(3, 1)
    a.li(4, 0xF0800008 | 0x200); a.li(5, 0x0C100200 & 0x1FFFF800); a.st(5, 4)
    a.ld(2, 0)
    a.li(8, 0x0C100A00); a.li(9, 0x2C100A00)
    a.li(0, 0x000B0009); a.st(0, 9)
    a.jsr(8)
    a.li(0, 0xE501000B); a.st(0, 9)
    a.li(4, 0xF0000008 | 0x200); a.li(5, 0x0C100A00 & 0x1FFFF800); a.st(5, 4)
    a.jsr(8)
    a.w(0xAFFE, 0x0009)
    report, log = run(tmp_path, flash_image(a), lambda log: False, timeout=3)
    assert findings(report) == set(), report + log
    assert "coherency findings: 0 distinct" in report
    rows = (Path(str(tmp_path / "cache") + "-profile.csv")).read_text().splitlines()
    assert rows[0].startswith("pc,fetch,ic_miss,")
    assert any(r.startswith("0x0c100a00,2,") for r in rows), rows


def test_exception_frames_on_a_cached_stack_stay_quiet(tmp_path):
    """TRAPA pushes SR and PC from inside QEMU, where no plugin memory
    callback sees it; patches/target_sh4_helper.c.patch reports the entry to
    plugins so the model can replay the push. With the stack line already in
    the operand cache, a missed push would make RTE's pops read stale."""
    a = Asm(CODE)
    a.li(1, 0xFFFC1000); a.li(0, 0x808); a.st(0, 1); a.li(0, 0x101); a.st(0, 1)
    a.li(15, 0x0C1FFF00)
    a.li(1, 0x0C1FFF00 - 8); a.ld(1, 0)        # the frame's line is cached
    a.w(0xC300, 0x0009)                       # trapa #0
    a.label("back"); a.ld(1, 0)               # read the (popped) frame again
    a.li(2, 0x0C1F0000); a.w(0x0283)          # pref @r2: one line, needs r2
    a.w(0xAFFE, 0x0009)
    a.label("handler"); a.w(0x002B, 0x0009)   # rte ; nop
    image = bytearray(flash_image(a))
    struct.pack_into(">I", image, 32 * 4, a.labels["handler"])   # VBR = 0: vector 32
    report, log = run(tmp_path, bytes(image), lambda log: False, timeout=3)
    if "lacks the SH-2A plugin hook" in report or "lacks the cdj850 plugin register feature" in report:
        pytest.skip("this qemu-system-sh4eb predates the cache-model hooks: ./build.sh display")
    assert "exception/interrupt entries 1 (push not modelled 0), RTE executed 1" in report, report
    assert "prefetches 1 (misses 1, not modelled 0)" in report, report
    assert findings(report) == set(), report + log
