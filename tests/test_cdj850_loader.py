# SPDX-License-Identifier: GPL-2.0-or-later
"""The CDJ-850 loader stub (mods/c850loader.py, mods/loader/cdj850): its
encoding, install/verify on a synthetic and the real MAIN, the validator's
treatment of the entry literal, and a boot of a loader build in QEMU."""
import json
import os
from pathlib import Path
import re
import socket
import struct
import subprocess
import sys
import time

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "mods"))
sys.path.insert(0, str(ROOT / "scripts" / "firmware"))

import c850_repack
import c850loader as L
import patch_main
import sigpatch
import validate



def _find_c850():
    env = os.environ.get("C850_UPD")
    if env:
        return Path(env)
    for p in ("C850MAIN.UPD", "extract/cdj850/C850MAIN.UPD", "fw/C850MAIN.UPD"):
        if (ROOT / p).is_file():
            return ROOT / p
    return ROOT / "C850MAIN.UPD"


C850 = _find_c850()
FLASH_BIN = ROOT / "extract" / "cdj850" / "flash_cdj850.bin"
STUB_SOURCE = ROOT / "mods" / "loader" / "cdj850" / "main_loader.s"


def synthetic_stock():
    """A zero image with just the bytes the loader checks: entry, clear, bounds."""
    img = bytearray(L.IMAGE_LEN)
    img[:L.ENTRY_LITERAL] = L.ENTRY
    img[L.ENTRY_LITERAL:L.ENTRY_LITERAL + 12] = struct.pack(">3I", L.CLEAR, 0x0C0B8FF4, 0x0C0B951C)
    img[L.CLEAR - L.BASE:L.CLEAR - L.BASE + len(L.CLEAR_CODE)] = L.CLEAR_CODE
    struct.pack_into(">2I", img, L.CLEAR_BOUNDS - L.BASE, L.STATICS, L.STATICS_END)
    return bytes(img)


def stock_image():
    if not C850.is_file():
        pytest.skip("C850MAIN.UPD is not present")
    return c850_repack.unpack_main(C850.read_bytes())


def test_stub_words_match_the_source_listing():
    listed = [int(h, 16) for h in re.findall(r"!= \+[0-9a-f]{2} ([0-9a-f]{4})", STUB_SOURCE.read_text())]
    assert tuple(listed) == L.STUB_CODE


def test_stub_pc_relative_loads_reach_their_pool_words():
    """Decode the hand-assembled mov.l @(disp,pc),Rn and bf/s independently of the table comments."""
    code = L.stub(0x40)
    pool = dict(zip(("src", "dst", "words", "guard_at", "guard", "clear"),
                    struct.unpack_from(">6I", code, L.POOL)))
    loads = {}
    for pc in range(0, L.POOL, 2):
        op = struct.unpack_from(">H", code, pc)[0]
        if op >> 12 == 0xD:
            target = (pc & ~3) + 4 + (op & 0xFF) * 4
            loads[(op >> 8) & 0xF] = struct.unpack_from(">I", code, target)[0]
        if op >> 8 == 0x8F:        # bf/s: pc + 4 + disp * 2
            assert pc + 4 + (((op & 0xFF) ^ 0x80) - 0x80) * 2 == 0x06
    assert loads == {1: pool["src"], 2: pool["dst"], 4: pool["words"], 5: pool["guard_at"],
                     6: pool["guard"], 0: pool["clear"]}
    assert pool == {"src": L.BLOB_ADDR + L.STUB_SIZE, "dst": 0x2DE00000, "words": 0x10,
                    "guard_at": 0x2DF6FFFC, "guard": L.GUARD_HI, "clear": 0x0C0B92AC}
    # r3 (the entry's next callee, loaded in the jsr delay slot) and PR are never written.
    for pc in range(0, L.POOL, 2):
        op = struct.unpack_from(">H", code, pc)[0]
        assert not (op >> 12 in (0x6, 0xD, 0x7, 0xE) and (op >> 8) & 0xF == 3), hex(op)
        assert op not in (0x400B, 0x410B, 0x420B, 0x430B, 0x440B), "no jsr in the stub"


def test_install_round_trip_and_idempotence_on_a_synthetic_image():
    stock = synthetic_stock()
    payload = bytes(range(256)) * 3 + b"xyz"
    out, spans = L.install(stock, payload)
    assert spans == [(0x10, 4, "loader"), (L.BLOB_ADDR - L.BASE, L.STUB_SIZE + 0x20 + 772, "loaderblob")]
    assert struct.unpack_from(">I", out, 0x10)[0] == L.BLOB_ADDR
    assert L.installed(out) == payload
    assert L.verify(stock, out) == []
    assert L.spans(stock, out) == spans
    assert L.install(out, payload)[0] == out
    assert L.uninstall(out) == stock
    smaller, _ = L.install(out, b"")
    assert smaller == L.install(stock, b"")[0]
    assert L.installed(smaller) == b""


def test_install_refuses_another_image():
    stock = bytearray(synthetic_stock())
    stock[L.CLEAR - L.BASE] ^= 1
    with pytest.raises(L.LoaderError, match="start-up clear"):
        L.install(bytes(stock), b"")
    stock = bytearray(synthetic_stock())
    stock[L.BLOB_ADDR - L.BASE + 0x50] = 1
    with pytest.raises(L.LoaderError, match="not zero"):
        L.install(bytes(stock), b"")
    with pytest.raises(L.LoaderError, match="does not fit"):
        L.install(synthetic_stock(), bytes(L.MAX_PAYLOAD + 1))


@pytest.mark.parametrize("where, why", [
    (0x10 + 3, "neither the clear nor the stub"),
    (L.BLOB_ADDR - L.BASE + 0x0E, "not the loader stub"),
    (L.BLOB_ADDR - L.BASE + L.STUB_SIZE + 0x20, "payload sum"),
    (L.BLOB_ADDR - L.BASE + L.STUB_SIZE, "low guard"),
])
def test_verify_rejects_a_tampered_loader(where, why):
    stock = synthetic_stock()
    out = bytearray(L.install(stock, b"\x11" * 64)[0])
    out[where] ^= 0x40
    problems = L.verify(stock, bytes(out))
    assert problems and why in problems[0], problems
    assert L.spans(stock, bytes(out)) == []


def test_check_ram():
    region = bytearray(L.REGION_END - L.REGION)
    data = L.blob(b"payload!")
    region[:len(data)] = data
    assert L.check_ram(bytes(region)) == ["high guard at 0xdf6fffc is 0x00000000, not 0x3e4c4452"]
    struct.pack_into(">I", region, L.GUARD_HI_ADDR - L.REGION, L.GUARD_HI)
    assert L.check_ram(bytes(region)) == []
    region[0x21] ^= 1
    assert "payload sum" in L.check_ram(bytes(region))[0]
    region[0] = 0
    assert "low guard" in L.check_ram(bytes(region))[0]


def test_real_image_install_verify_and_fits_the_flash_window():
    stock = stock_image()
    assert L.check_stock(stock) == []
    out, _ = L.install(stock, os.urandom(4096))
    assert L.verify(stock, out) == []
    upd = c850_repack.build_upd(C850.read_bytes(), out, check_boot=True)
    assert c850_repack.check_upd(upd) == []
    assert c850_repack.unpack_main(upd) == out


def test_loader_is_a_cdj850_main_mod():
    assert "loader" in patch_main.mods_for("cdj850")
    assert isinstance(patch_main.PATCH_SETS["cdj850"].images["loader"], sigpatch.ImagePatch)
    report, spans = {}, {}
    stock = synthetic_stock()
    out = patch_main.patch(stock, ["loader"], "cdj850", report, spans)
    assert report == {"loader": ("applied", "")}
    assert spans["loader"] == L.install(stock, b"")[1]
    report2 = {}
    assert patch_main.patch(out, ["loader"], "cdj850", report2) == out
    assert report2 == {"loader": ("present", "")}


def test_payload_comes_from_the_environment(tmp_path, monkeypatch):
    payload = tmp_path / "blob.bin"
    payload.write_bytes(b"\xde\xad\xbe\xef" * 8)
    monkeypatch.setenv("CDJ850_LOADER_PAYLOAD", str(payload))
    out = patch_main.patch(synthetic_stock(), ["loader"], "cdj850", {})
    assert L.installed(out) == payload.read_bytes()


def run(*args, env=None):
    return subprocess.run([sys.executable, *map(str, args)], capture_output=True, text=True, check=False,
                          env=env)


def test_patch_update_and_validate_accept_the_loader_and_reject_a_bad_one(tmp_path):
    if not C850.is_file():
        pytest.skip("C850MAIN.UPD is not present")
    out = tmp_path / "C850MAIN.UPD"
    r = run(ROOT / "mods" / "patch_update.py", "--model", "cdj850", C850, out, "loader", "tempo_wide")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "PASS main: loader: entry literal 0xc000010 -> stub 0xc1e2800" in r.stdout
    v = run(ROOT / "mods" / "validate.py", "--model", "cdj850", "--stock", C850, out)
    assert v.returncode == 0, v.stdout + v.stderr
    assert "as the loader stub's entry literal" in v.stdout

    # The same entry write without a valid stub behind it is boot-critical code changed.
    image = bytearray(c850_repack.unpack_main(out.read_bytes()))
    image[L.BLOB_ADDR - L.BASE + 0x16] = 0xD1          # mov.l ...,r0 -> r1: the stub now jumps to garbage
    bad = tmp_path / "bad" / "C850MAIN.UPD"
    bad.parent.mkdir()
    bad.write_bytes(c850_repack.build_upd(C850.read_bytes(), bytes(image), check_boot=True))
    v = run(ROOT / "mods" / "validate.py", "--model", "cdj850", "--stock", C850, bad)
    assert v.returncode == 1, v.stdout
    assert "overlaps boot-critical code" in v.stdout


def test_check_loader_rejects_spans_it_did_not_write():
    stock = synthetic_stock()
    out, spans = L.install(stock, b"")
    rep = validate.Report()
    flat = [("loader", kind, off, off + size) for off, size, kind in spans]
    validate.check_loader("main", stock, out, flat + [("loader", "loaderblob", 0x100, 0x104)], rep)
    assert rep.failed and "not one c850loader makes" in rep.text()
    rep = validate.Report()
    validate.check_loader("main", stock, stock, flat, rep)
    assert rep.failed and "still calls the clear" in rep.text()


def _qemu():
    binary = os.environ.get("GUI_QEMU", os.path.expanduser("~/qemu-build-eb/qemu-system-sh4eb"))
    if not os.path.isfile(binary):
        pytest.skip("qemu-system-sh4eb is not built")
    return binary


def test_loader_build_boots_and_leaves_the_blob_in_sdram(tmp_path, monkeypatch):
    binary = _qemu()
    if not FLASH_BIN.is_file():
        pytest.skip("extract/cdj850/flash_cdj850.bin is not present")
    if os.name == "nt":
        pytest.skip("QMP socket descriptor inheritance requires POSIX")
    payload = os.urandom(4098)
    (tmp_path / "payload.bin").write_bytes(payload)
    monkeypatch.setenv("CDJ850_LOADER_PAYLOAD", str(tmp_path / "payload.bin"))
    report = {}
    flash = tmp_path / "flash.bin"
    flash.write_bytes(patch_main.patch_flash(FLASH_BIN.read_bytes(), ["loader", "tempowide"], "cdj850", report))
    assert report["loader"][0] == "applied" and report["tempowide"][0] == "applied"

    qmp_s, qmp_c = socket.socketpair()
    env = os.environ.copy()
    for k in ("CDJ850_DSP_BOOT_HLE", "CDJ850_PF16_HIGH", "CDJ850_DSP_PLAY_PF", "CDJ850_PANEL_SCRIPT"):
        env.pop(k, None)
    p = subprocess.Popen(
        [binary, "-M", "cdj850", "-display", "none", "-monitor", "none", "-serial", "none",
         "-icount", "shift=2,sleep=off",
         "-drive", f"if=pflash,format=raw,file={flash},snapshot=on",
         "-chardev", f"socket,id=qmp,fd={qmp_c.fileno()}", "-mon", "chardev=qmp,mode=control"],
        stderr=subprocess.PIPE, env=env, pass_fds=(qmp_c.fileno(),))
    qmp_c.close()
    qmp = qmp_s.makefile("rwb", buffering=0)

    def hmp(line):
        qmp.write((json.dumps({"execute": "human-monitor-command",
                               "arguments": {"command-line": line}}) + "\n").encode())
        while True:
            reply = json.loads(qmp.readline())
            if "return" in reply or "error" in reply:
                assert not reply.get("return"), reply    # HMP reports its errors as text
                return reply

    log = b""
    try:
        assert "QMP" in json.loads(qmp.readline())
        qmp.write(b'{"execute":"qmp_capabilities"}\n')
        assert "return" in json.loads(qmp.readline())
        os.set_blocking(p.stderr.fileno(), False)
        deadline = time.time() + 120
        while b"NO DISC" not in log and time.time() < deadline:
            time.sleep(0.5)
            log += p.stderr.read() or b""
        assert b"NO DISC" in log, "loader build did not reach the idle display:\n" + log.decode("latin1")[-3000:]
        dump = tmp_path / "region.bin"
        hmp(f'pmemsave {L.REGION:#x} {L.REGION_END - L.REGION:#x} "{dump}"')   # quoted: HMP reads a bare /path as division
        stub = tmp_path / "stub.bin"
        hmp(f'pmemsave {L.BLOB_ADDR:#x} {L.STUB_SIZE + 0x20 + len(payload) + 2:#x} "{stub}"')
        region = dump.read_bytes()
        assert L.check_ram(region) == []
        assert region[0x20:0x20 + len(payload)] == payload
        assert not any(stub.read_bytes()), "the start-up clear did not wipe the stub and its source copy"
    finally:
        qmp.close()
        qmp_s.close()
        p.terminate()
        p.wait(timeout=10)
