# SPDX-License-Identifier: GPL-2.0-or-later
"""The FLAC mod on the shared payload (mods/flac/cdj850/patch.py, payload/).

Static: the eight writes to MAIN (an EntryRedirect at the directory classifier and seven
PoolRedirects of the USB audio calls), their service-mode gates, the moved classifier entry,
the boot-critical closure, the decoder's private stack, and no FPU code in what lands in SDRAM.
The decoder itself is checked bit for bit on the host (make -C mods/flac/cdj850 test) and in
QEMU by mods/flac/probe.py + verify_probe.py (README).
"""
import os
from pathlib import Path
import re
import struct
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "mods"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import c850loader as L  # noqa: E402
import modelfw  # noqa: E402
import patch_main  # noqa: E402
import payloadbuild  # noqa: E402
import sigpatch  # noqa: E402

MAIN = ROOT / "extract" / "cdj850" / "main_unpacked.bin"
BASE = 0x0C000000
FLAC = modelfw.mod_patch("flac", "cdj850")
NAMES = list(FLAC.MODS)
PS = patch_main.PATCH_SETS["cdj850"]
PAYLOAD_DIR = ROOT / "mods" / "flac" / "cdj850" / "payload"

needs_toolchain = pytest.mark.skipif(not payloadbuild.have_toolchain(), reason="sh-elf toolchain not installed")
needs_main = pytest.mark.skipif(not MAIN.is_file(), reason="extract/cdj850/main_unpacked.bin is not present")

# pool word -> (stock function, payload symbol, the mov.l that read it)
POOLS = {
    0x0C08CBF0: (0x0C000CC4, "flac_wav_header_read", (0x0C08CAD4, 0x0C08CB3C)),
    0x0C0585F4: (0x0C0BE3E4, "flac_wav_pcm_read", (0x0C05845E,)),
    0x0C057F1C: (0x0C10E7C8, "flac_audio_source_size", (0x0C057E4A,)),
    0x0C05814C: (0x0C10E7C8, "flac_audio_source_size", (0x0C057F58,)),
    0x0C058150: (0x0C10E246, "flac_file_close", (0x0C057F82,)),
    0x0C0BE79C: (0x0C057CE4, "flac_audio_open", (0x0C0BE67C,)),
    0x0C0BED94: (0x0C057CE4, "flac_audio_open", (0x0C0BEBF4, 0x0C0BECF2)),
}
# readers inside the boot-critical closure: their calls reach the payload (through the gate)
BOOTCRIT_READERS = {0x0C057E4A, 0x0C0BE67C}


def u32(data, off):
    return struct.unpack_from(">I", data, off)[0]


def test_registry_declares_one_entry_and_seven_pool_redirects():
    assert NAMES[0] == "flac" and len(NAMES) == 8
    entry = FLAC.MODS["flac"]
    assert isinstance(entry, sigpatch.EntryRedirect) and PS.entries["flac"] is entry
    assert sigpatch.payload_symbol(entry) == "flac_file_classify"
    assert sigpatch.stock_symbol(entry) == "flac_stock_classify"
    assert entry.old == FLAC.CLASSIFIER_PROLOGUE and len(entry.old) == 12
    redirects = {n: m for n, m in FLAC.MODS.items() if n != "flac"}
    assert all(isinstance(m, sigpatch.PoolRedirect) for m in redirects.values())
    assert sorted((m.old, sigpatch.payload_symbol(m)) for m in redirects.values()) == \
        sorted((old, sym) for old, sym, _ in POOLS.values())
    assert patch_main.payload_folders(NAMES, "cdj850") == ["flac"]
    assert patch_main.with_loader(["flac"], "cdj850") == ["flac", "loader"]


def test_entry_jump_encoding():
    # 4-aligned entry: mov.l @(1,pc),r0 reads entry + 8
    assert sigpatch.entry_jump(0x0C040C90, 0x0DE01234) == bytes.fromhex("d001402b000900090de01234")
    # 2 mod 4: mov.l @(1,pc),r0 reads (entry & ~3) + 8 = entry + 6; ten bytes
    assert sigpatch.entry_jump(0x0C040C92, 0x0DE01234) == bytes.fromhex("d001402b00090de01234")


@needs_main
def test_signatures_find_the_traced_sites_in_stock():
    stock = MAIN.read_bytes()
    img = sigpatch.Image(PS.profile, stock)
    entry = FLAC.MODS["flac"]
    assert img.addr_of(sigpatch.anchor_offset(img, entry.anchor)) == FLAC.CLASSIFIER
    assert stock[FLAC.CLASSIFIER - BASE:FLAC.CLASSIFIER - BASE + 12] == entry.old
    reads = sigpatch.literal_pool_reads(img)
    for name, m in FLAC.MODS.items():
        if name == "flac":
            continue
        lit = sigpatch.literal_offset(img, sigpatch.anchor_offset(img, m.anchor))
        pool = BASE + lit
        old, _, readers = POOLS[pool]
        assert u32(stock, lit) == m.old == old, name
        assert tuple(sorted(img.addr_of(i) for i, o, w in reads if o == lit and w == 4)) == readers, name
    # the classifier is reached by two bsr and through no pool word
    assert not [o for o in range(0, len(stock) - 3, 4) if u32(stock, o) == FLAC.CLASSIFIER]
    for at in (0x0C040ACE, 0x0C0408A0):
        op = struct.unpack_from(">H", stock, at - BASE)[0]
        assert op >> 12 == 0xB and at + 4 + 2 * ((op & 0xFFF) - (0x1000 if op & 0x800 else 0)) == FLAC.CLASSIFIER


@needs_toolchain
@needs_main
def test_patch_writes_eight_gated_sites_and_is_idempotent():
    stock = MAIN.read_bytes()
    report, spans = {}, {}
    out = patch_main.patch(stock, NAMES, "cdj850", report, spans)
    assert {n: s for n, (s, _) in report.items()} == dict({n: "applied" for n in NAMES}, loader="applied")
    p = payloadbuild.build(["flac"], "cdj850")
    assert L.installed(out) == p.blob
    gate = lambda sym: payloadbuild.lookup(p.symbols, payloadbuild.gate_name(sym))  # noqa: E731
    off = FLAC.CLASSIFIER - BASE
    assert out[off:off + 12] == sigpatch.entry_jump(FLAC.CLASSIFIER, gate("flac_file_classify"))
    assert spans["flac"] == [(off, 12, "site")]
    for pool, (old, sym, _) in POOLS.items():
        assert u32(stock, pool - BASE) == old and u32(out, pool - BASE) == gate(sym), hex(pool)
    sites = [(FLAC.CLASSIFIER, 12)] + [(pool, 4) for pool in POOLS]
    diff = [a for a in range(0x0C000100, L.BLOB_ADDR) if stock[a - BASE] != out[a - BASE]]
    assert all(any(s <= a < s + n for s, n in sites) for a in diff), [hex(a) for a in diff]
    report2 = {}
    assert patch_main.patch(out, NAMES, "cdj850", report2) == out
    assert all(report2[n][0] == "present" for n in NAMES), report2


@needs_toolchain
@needs_main
def test_entry_redirect_refuses_unexpected_entry_bytes():
    stock = bytearray(MAIN.read_bytes())
    stock[FLAC.CLASSIFIER - BASE + 1] ^= 1          # 4cf0 -> 4cf1
    report = {}
    out = patch_main.patch(bytes(stock), ["flac"], "cdj850", report)
    assert report["flac"][0] == "skipped" and "holds" in report["flac"][1]
    assert out[FLAC.CLASSIFIER - BASE:FLAC.CLASSIFIER - BASE + 12] == stock[FLAC.CLASSIFIER - BASE:
                                                                          FLAC.CLASSIFIER - BASE + 12]


def test_entry_redirect_refuses_entry_code_that_cannot_move():
    e = FLAC.MODS["flac"]
    for bad in ("d0014cf07fdc6c5362526d43", "4cf07fdc6c53a0016d43842b", "4cf07fdc6c5300096d43000b"):
        img = bytearray(0x400)
        img[0x100:0x10C] = bytes.fromhex(bad)
        img[0x10C:0x118] = struct.pack(">6H", *e.anchor.sig)
        profile = PS.profile._replace(loadtab_n=0, flash_base=BASE, gate=())
        report = {}
        mods = {"x": e._replace(old=bytes.fromhex(bad))}
        sigpatch.patch_entries(profile, bytes(img), mods, ["x"],
                               {"_flac_file_classify": 0x0DE00100, "_flac_stock_classify": 0x0DE00200},
                               "t", report, {})
        assert report["x"][0] == "skipped" and "cannot run elsewhere" in report["x"][1], bad


@needs_toolchain
@needs_main
def test_boot_critical_closure_holds_no_written_byte():
    import bootcrit
    stock = MAIN.read_bytes()
    closure = bootcrit.closure(stock, BASE, byteorder=">")
    off = FLAC.CLASSIFIER - BASE
    assert not closure.hit(off, off + 12)
    for pool, (_, _, readers) in POOLS.items():
        assert not closure.hit(pool - BASE, pool - BASE + 4), hex(pool)
        for r in readers:
            assert bool(closure.hit(r - BASE, r - BASE + 2)) == (r in BOOTCRIT_READERS), hex(r)
    # every stock function behind the gates is the one each reader called before
    for old in {o for o, _, _ in POOLS.values()}:
        assert stock[old - BASE:old - BASE + 2] != b"\xff\xff"


@needs_toolchain
def test_payload_moved_entry_private_stack_and_bss():
    p = payloadbuild.build(["flac"], "cdj850")
    s = p.symbols
    moved = payloadbuild.lookup(s, "flac_stock_classify") - L.PAYLOAD
    # the 12 classifier bytes, mov.l @(1,pc),r3; jmp @r3; nop; nop; .long 0x0C040C9C
    assert p.blob[moved:moved + 24] == FLAC.CLASSIFIER_PROLOGUE + bytes.fromhex("d301432b00090009") + \
        struct.pack(">I", FLAC.CLASSIFIER + 12)
    on = payloadbuild.lookup(s, "flac_on_stack") - L.PAYLOAD
    ops = struct.unpack(">17H", p.blob[on:on + 34])
    assert ops == (0xD108, 0x62F3, 0x2126, 0x4122, 0x4102, 0x4112, 0x6F13, 0x6143, 0x6453, 0x6563,
                   0x410B, 0x6673, 0x4F16, 0x4F06, 0x4F26, 0x000B, 0x6FF2)
    stack = payloadbuild.lookup(s, "flac_stack")
    top = u32(p.blob, on + 0x24)
    assert top == stack + 16384 and top % 8 == 0
    lo, hi = s["__bss_start"], s["__bss_end"]
    for name in ("flac_stack", "arena", "pcm", "files", "flac_diagnostics"):
        assert lo <= payloadbuild.lookup(s, name) < hi, name
    assert lo <= top <= hi
    assert len(p.blob) <= L.MAX_PAYLOAD
    # frames: only decoder.c (dr_flac, on the private stack) goes over the 512-byte rule
    big = {f: n for f, n in p.stack.items() if n > payloadbuild.STACK_LIMIT}
    assert big and max(big.values()) <= FLAC.PAYLOAD_STACK_LIMITS["decoder.c"] <= 16384 // 2
    firmware = [f for f in p.stack if f.split(":")[1].startswith("flac_") and "on_stack" not in f]
    assert all(p.stack[f] <= payloadbuild.STACK_LIMIT for f in firmware)


@needs_toolchain
def test_no_fpu_code_in_the_flac_payload(tmp_path):
    """C sources compiled to assembly carry no FPU mnemonic; the libgcc members the payload
    links (soft-float for dr_flac's seek, shifts, movmem) are built for SH1, which has no FPU."""
    pre = payloadbuild.tool_prefix()
    for src in sorted(PAYLOAD_DIR.glob("*.c")):
        asm = tmp_path / (src.stem + ".s")
        subprocess.run([pre + "gcc", *payloadbuild.CFLAGS, "-I", payloadbuild.INCLUDE,
                        "-I", str(PAYLOAD_DIR / "include"), "-S", "-o", str(asm), str(src)], check=True)
        insns = [ln.split()[0] for ln in asm.read_text().splitlines() if ln.startswith("\t") and ln.split()
                 and not ln.split()[0].startswith(".")]
        fpu = [i for i in insns if i.startswith("f") or "fpul" in i or "fpscr" in i]
        assert insns and not fpu, (src.name, fpu[:5])
        text = asm.read_text()
        assert not re.search(r"\b(fpul|fpscr|fr\d+|dr\d+)\b", text), src.name
    keep = tmp_path / "build"
    payloadbuild.build(["flac"], "cdj850", keep=str(keep))
    members = sorted(set(re.findall(r"libgcc\.a\(([^)]+)\)", (keep / "payload.ld.map").read_text())))
    assert members
    subprocess.run([pre + "ar", "x", payloadbuild.libgcc(), *members], cwd=tmp_path, check=True)
    for m in members:
        flags = struct.unpack_from(">I", (tmp_path / m).read_bytes(), 0x24)[0]
        assert flags & 0x1F == 1, (m, hex(flags))       # EF_SH1


@pytest.mark.skipif(not (ROOT / "C850MAIN.UPD").is_file(), reason="C850MAIN.UPD is not present")
@needs_toolchain
def test_patch_update_and_validate_flac(tmp_path):
    out = tmp_path / "C850MAIN.UPD"
    r = subprocess.run([sys.executable, str(ROOT / "mods" / "patch_update.py"), "--model", "cdj850",
                        str(ROOT / "C850MAIN.UPD"), str(out), "flac"], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "label 0.13" in r.stdout and "8 written words call into the payload" in r.stdout
    v = subprocess.run([sys.executable, str(ROOT / "mods" / "validate.py"), "--model", "cdj850", "--stock",
                        str(ROOT / "C850MAIN.UPD"), str(out)], capture_output=True)
    assert v.returncode == 0 and b"all checks passed" in v.stdout, v.stdout[-2000:]


@pytest.mark.skipif(os.system("command -v flac >/dev/null 2>&1") != 0 or os.system("command -v cc >/dev/null 2>&1") != 0,
                    reason="the flac encoder and a host C compiler are needed")
def test_host_decoder_is_bit_exact(tmp_path):
    exe = tmp_path / "host-test"
    d = ROOT / "mods" / "flac" / "cdj850"
    subprocess.run(["cc", "-std=c99", "-O2", "-Wall", "-Wextra", "-Werror", "-I", str(d / "payload"),
                    str(d / "test.c"), str(d / "payload" / "decoder.c"), "-o", str(exe)], check=True)
    r = subprocess.run([sys.executable, str(d / "test_decoder.py"), str(exe)], capture_output=True, text=True)
    assert r.returncode == 0 and "PASS" in r.stdout, r.stdout + r.stderr
