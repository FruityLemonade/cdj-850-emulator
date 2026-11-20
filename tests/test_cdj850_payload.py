# SPDX-License-Identifier: GPL-2.0-or-later
"""The CDJ-850 shared payload (mods/payloadbuild.py): the build of a mod's C
into one image at 0x0DE00020, hooks and pool redirects that call its symbols,
the loader carrying it, the SH-2A assembly of the cave routines, and a live
boot of payload_demo in QEMU."""
import json
import os
import socket
import struct
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "mods"))
sys.path.insert(0, str(ROOT / "scripts" / "firmware"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import c850_repack  # noqa: E402
import c850loader as L  # noqa: E402
import patch_main  # noqa: E402
import payloadbuild  # noqa: E402
import sigpatch  # noqa: E402

MAIN = ROOT / "extract" / "cdj850" / "main_unpacked.bin"
FLASH = ROOT / "extract" / "cdj850" / "flash_cdj850.bin"
C850 = ROOT / "C850MAIN.UPD"
BASE = 0x0C000000
BPM_SITE = 0x0C0642AA            # display_stage_bpm tail (bpm999, bpm_probe, payload_demo)
REDIRECT_POOL = 0x0C0B9310       # pool word the 1 ms tick routine loads at 0x0C0B92EA
POWER_TICK = 0x0C0EF8C8          # power/standby part of the 1 ms tick
TICK_MS = 0x0CE49F54             # u32 +1 per 1 ms tick
STANDBY_SECONDS = 0x0C1E10AE
DEMO = ["payload_demo", "payload_demo_redirect"]

needs_toolchain = pytest.mark.skipif(not payloadbuild.have_toolchain(), reason="sh-elf toolchain not installed")
needs_main = pytest.mark.skipif(not MAIN.is_file(), reason="extract/cdj850/main_unpacked.bin is not present")


def u32(data, off):
    return struct.unpack_from(">I", data, off)[0]


@needs_toolchain
def test_demo_payload_builds_at_the_loader_address():
    p = payloadbuild.build(["payload_demo"], "cdj850")
    s = p.symbols
    assert s["__payload_start"] == L.PAYLOAD == 0x0DE00020
    assert s["__payload_end"] - L.PAYLOAD == len(p.blob) <= L.MAX_PAYLOAD
    assert p.blob[-4:] == b">PLD"
    # .bss travels as zeros inside the image, so the loader's copy clears it
    lo, hi = s["__bss_start"] - L.PAYLOAD, s["__bss_end"] - L.PAYLOAD
    assert hi - lo == 12 and not any(p.blob[lo:hi])
    for name in ("payload_demo_bpm_calls", "payload_demo_tick_calls", "payload_demo_last_r15"):
        assert s["__bss_start"] <= payloadbuild.lookup(s, name) < s["__bss_end"]
    assert payloadbuild.lookup(s, "payload_demo_count") == 0x0DE00020
    assert set(p.stack.values()) == {0}


@needs_toolchain
def test_demo_payload_code_is_sh2a_without_fpu_and_tail_jumps():
    p = payloadbuild.build(["payload_demo"], "cdj850")
    power = payloadbuild.lookup(p.symbols, "payload_demo_tick") - L.PAYLOAD
    count = payloadbuild.lookup(p.symbols, "payload_demo_count") - L.PAYLOAD
    text = p.blob[:p.symbols["__bss_start"] - L.PAYLOAD]
    # Each function is 0x10 bytes of code and then its pool (checked with sh-elf-objdump -d).
    code = [w for at in (count, power) for w in struct.unpack(">8H", text[at:at + 0x10])]
    assert not [w for w in code if w >> 12 == 0xF], "an FPU opcode in payload code"
    pw = code[8:]
    assert [w for w in pw if w & 0xF0FF == 0x402B] and not [w for w in pw if w & 0xF0FF == 0x400B], "payload_demo_tick must jmp, not jsr"
    assert POWER_TICK.to_bytes(4, "big") in text[power:power + 0x18]


@needs_toolchain
def test_payload_symbols_and_bounds_are_checked(tmp_path):
    good = payloadbuild.build(["payload_demo"], "cdj850")
    with pytest.raises(payloadbuild.PayloadError, match="marker"):
        payloadbuild.check(good.blob[:-4] + b"\0\0\0\0", good.symbols, {})
    with pytest.raises(payloadbuild.PayloadError, match="stack"):
        payloadbuild.check(good.blob, good.symbols, {"x:f": payloadbuild.STACK_LIMIT + 4})
    bad = dict(good.symbols, __payload_start=0x0DE00000)
    with pytest.raises(payloadbuild.PayloadError, match="starts at"):
        payloadbuild.check(good.blob, bad, {})
    assert payloadbuild.lookup(good.symbols, "nope") is None
    with pytest.raises(sigpatch.NotApplied, match="no symbol"):
        sigpatch.payload_address(good.symbols, "nope")


def test_registry_declares_payload_hook_and_redirect():
    mods = patch_main.mods_for("cdj850")
    assert sigpatch.payload_symbol(mods["payload_demo"]) == "payload_demo_count"
    assert isinstance(mods["payload_demo_redirect"], sigpatch.PoolRedirect)
    assert patch_main.payload_folders(["payload_demo_redirect", "bpm999"], "cdj850") == ["payload_demo"]
    assert patch_main.with_loader(["bpm999"], "cdj850") == ["bpm999"]
    assert patch_main.with_loader(["payload_demo"], "cdj850") == ["payload_demo", "loader"]


def test_cave_reservation_and_room_left():
    """The registry-fixed cave layout: payload hooks take no routine bytes, only trampoline slots."""
    ps = patch_main.PATCH_SETS["cdj850"]
    offsets, total = sigpatch.layout(lambda n: patch_main.blob_path(n, "cdj850"), ps.mods)
    assert "payload_demo" not in offsets and "bpm_decimals" not in offsets
    assert total == 384          # 368 + one slot for bpm_decimals on the shared display_stage_bpm site (16)
    if MAIN.is_file():
        img = sigpatch.Image(ps.profile, MAIN.read_bytes())
        start, end = sigpatch.find_cave(img)
        assert end - ((start + 3) & ~3) - total == 732


@needs_toolchain
@needs_main
def test_demo_hook_and_redirect_literals_are_the_symbol_addresses():
    stock = MAIN.read_bytes()
    report, spans = {}, {}
    out = patch_main.patch(stock, DEMO + ["bpm999"], "cdj850", report, spans)
    assert {n: s for n, (s, _) in report.items()} == {
        "payload_demo": "applied", "payload_demo_redirect": "applied", "bpm999": "applied", "loader": "applied"}
    p = payloadbuild.build(["payload_demo"], "cdj850")
    count = payloadbuild.lookup(p.symbols, "payload_demo_count")
    power = payloadbuild.lookup(p.symbols, "payload_demo_tick")
    # the redirect: one 4-byte site, the pool word now holds the symbol address
    assert spans["payload_demo_redirect"] == [(REDIRECT_POOL - BASE, 4, "site")]
    assert u32(stock, REDIRECT_POOL - BASE) == POWER_TICK
    assert u32(out, REDIRECT_POOL - BASE) == power
    # the hook: the site jumps to the shared trampoline, whose pool calls bpm999's cave routine and
    # then the payload symbol, in that (alphabetical) order
    lit = (BPM_SITE + 6 + 3) & ~3          # call_site(jump=True): mov.l lit,r1; jmp @r1; nop; .long
    tramp = u32(out, lit - BASE)
    (t_off, t_len, kind), = [s for s in spans["payload_demo"] if s[2] == "free"]
    assert kind == "free" and t_off == tramp - BASE
    pool = struct.unpack(">%dI" % (t_len // 4), out[t_off + t_len - (t_len // 4) * 4:t_off + t_len])
    hooks = [w for w in pool if w in (count, 0x0C0172B4)]
    assert hooks == [0x0C0172B4, count]
    # the loader carries exactly the built payload
    assert L.installed(out) == p.blob
    assert L.verify(stock, out) == []
    # nothing else of the mod is in the cave: no routine bytes, only the trampoline
    assert [s for s in spans["payload_demo"] if s[2] != "free"] == [(BPM_SITE - BASE, 14, "site")]
    # patching again changes nothing and says so
    again = {}
    assert patch_main.patch(out, DEMO + ["bpm999"], "cdj850", again) == out
    assert set(s for s, _ in again.values()) == {"present"}


@needs_toolchain
@needs_main
def test_redirect_refuses_a_changed_pool_word():
    stock = bytearray(MAIN.read_bytes())
    struct.pack_into(">I", stock, REDIRECT_POOL - BASE, 0x0C000004)
    report = {}
    patch_main.patch(bytes(stock), ["payload_demo_redirect"], "cdj850", report)
    assert report["payload_demo_redirect"][0] == "skipped" and "holds 0xc000004" in report["payload_demo_redirect"][1]


@needs_toolchain
@needs_main
def test_env_payload_must_match_the_built_one(tmp_path, monkeypatch):
    other = tmp_path / "other.bin"
    other.write_bytes(b"\x00\x09" * 8)
    monkeypatch.setenv("CDJ850_LOADER_PAYLOAD", str(other))
    with pytest.raises(SystemExit, match="loader did not go in"):
        patch_main.patch(MAIN.read_bytes(), ["payload_demo"], "cdj850", {})
    # the same file as the built payload is accepted; without payload mods the file is the payload
    built = payloadbuild.build(["payload_demo"], "cdj850").blob
    other.write_bytes(built)
    assert L.installed(patch_main.patch(MAIN.read_bytes(), ["payload_demo"], "cdj850", {})) == built
    other.write_bytes(b"\x11" * 16)
    assert L.installed(patch_main.patch(MAIN.read_bytes(), ["loader"], "cdj850", {})) == b"\x11" * 16


@needs_toolchain
def test_assembled_cave_routines_match_the_committed_bins(tmp_path):
    """patch_main --assemble (sh-elf-as -isa=sh2a -big) rebuilds every shipped .bin byte for byte."""
    ps = patch_main.PATCH_SETS["cdj850"]
    names = [n for n in ps.mods if not sigpatch.payload_symbol(ps.mods[n])]
    assert {"tempowide", "bpm999", "bpm_probe"} <= set(names)
    for name in names:
        out = tmp_path / (name + ".bin")
        patch_main.assemble_routine(name, "cdj850", str(out), str(tmp_path))
        assert out.read_bytes() == Path(patch_main.blob_path(name, "cdj850")).read_bytes(), name


@needs_toolchain
def test_loader_stub_source_assembles_to_the_stub_words(tmp_path):
    src = ROOT / "mods" / "loader" / "cdj850" / "main_loader.s"
    pre = payloadbuild.tool_prefix()
    subprocess.run([pre + "as", "-isa=sh2a", "-big", "-o", tmp_path / "l.o", src], check=True)
    subprocess.run([pre + "objcopy", "-O", "binary", tmp_path / "l.o", tmp_path / "l.bin"], check=True)
    code = (tmp_path / "l.bin").read_bytes()
    assert struct.unpack(">%dH" % len(L.STUB_CODE), code[:2 * len(L.STUB_CODE)]) == L.STUB_CODE


def _run(*args):
    return subprocess.run([sys.executable, *map(str, args)], capture_output=True, text=True, check=False)


@needs_toolchain
@pytest.mark.skipif(not C850.is_file(), reason="C850MAIN.UPD is not present")
def test_patch_update_adds_the_loader_and_validate_accepts_all_mods_combined(tmp_path):
    out = tmp_path / "C850MAIN.UPD"
    names = [ln.split()[0] for ln in _run(ROOT / "mods" / "patch_update.py", "--model", "cdj850", "--list")
             .stdout.splitlines() if ln.strip()]
    assert "payload_demo" in names and "loader" in names
    names.remove("loader")             # added by itself for the payload
    r = _run(ROOT / "mods" / "patch_update.py", "--model", "cdj850", C850, out, *names)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "patch_main: payload of bpm_decimals, loop_resize, payload_demo, quant_loops" in r.stdout
    assert "PASS main: loader: entry literal" in r.stdout
    # payload_demo's hook literal and redirect, bpm_decimals' hook literal and redirect,
    # quant_loops' seven redirects, loop_resize's three redirects
    assert "PASS main: 14 written words call into the payload" in r.stdout
    v = _run(ROOT / "mods" / "validate.py", "--model", "cdj850", "--stock", C850, out)
    assert v.returncode == 0, v.stdout + v.stderr
    assert "all checks passed" in v.stdout


@needs_main
def test_validate_rejects_a_payload_call_without_the_loader():
    import validate
    stock = MAIN.read_bytes()
    out = bytearray(stock)
    struct.pack_into(">I", out, REDIRECT_POOL - BASE, 0x0DE00038)
    rep = validate.Report()
    validate.check_payload_calls(stock, bytes(out), [("x", "site", REDIRECT_POOL - BASE, REDIRECT_POOL - BASE + 4)],
                                 rep)
    assert rep.failed and "no loader is installed" in rep.text()


def _qemu():
    import test_cdj850_usb as tu
    return tu._cdj850_binary()


@needs_toolchain
@pytest.mark.skipif(not FLASH.is_file(), reason="extract/cdj850/flash_cdj850.bin is not present")
def test_live_payload_demo_hook_and_redirect_run(tmp_path):
    """Boot payload_demo: the hook counter (display_stage_bpm tail) and the redirect counter (power
    state machine) rise, the power state machine still does its job (the auto-standby seconds
    counter keeps counting), and the loader's guards and payload are intact in SDRAM."""
    if os.name == "nt":
        pytest.skip("QMP socket descriptor inheritance requires POSIX")
    import test_cdj850_usb as tu
    binary = _qemu()
    p_built = payloadbuild.build(["payload_demo"], "cdj850")
    bpm_calls = payloadbuild.lookup(p_built.symbols, "payload_demo_bpm_calls")
    tick_calls = payloadbuild.lookup(p_built.symbols, "payload_demo_tick_calls")
    last_r15 = payloadbuild.lookup(p_built.symbols, "payload_demo_last_r15")
    report = {}
    flash = tmp_path / "flash.bin"
    flash.write_bytes(patch_main.patch_flash(FLASH.read_bytes(), DEMO, "cdj850", report))
    assert {n: s for n, (s, _) in report.items()} == {
        "payload_demo": "applied", "payload_demo_redirect": "applied", "loader": "applied"}
    env = os.environ.copy()
    for k in ("CDJ850_DSP_BOOT_HLE", "CDJ850_PF16_HIGH", "CDJ850_DSP_PLAY_PF", "CDJ850_PANEL_SCRIPT"):
        env.pop(k, None)
    qmp_s, qmp_c = socket.socketpair()
    errlog = tmp_path / "stderr.log"
    with open(errlog, "wb") as err:
        p = subprocess.Popen(
            [binary, "-M", "cdj850", "-display", "none", "-monitor", "none", "-serial", "none",
             "-icount", "shift=2,sleep=off",
             "-drive", f"if=pflash,format=raw,file={flash},snapshot=on",
             "-chardev", f"socket,id=qmp,fd={qmp_c.fileno()}", "-mon", "chardev=qmp,mode=control"],
            stdout=subprocess.DEVNULL, stderr=err, env=env, pass_fds=(qmp_c.fileno(),))
    qmp_c.close()
    qmp_s.settimeout(60)
    qmp = qmp_s.makefile("rwb", buffering=0)
    seen = {}
    try:
        assert "QMP" in json.loads(qmp.readline())
        tu._qmp_execute(qmp, "qmp_capabilities")
        deadline = time.time() + 120
        while b"NO DISC" not in errlog.read_bytes() and time.time() < deadline:
            time.sleep(0.5)
        assert b"NO DISC" in errlog.read_bytes(), "the payload_demo build did not reach the idle display"

        def snap():
            tu._qmp_execute(qmp, "stop")
            vals = {"bpm": tu._qmp_read(qmp, bpm_calls, "w"), "tick": tu._qmp_read(qmp, tick_calls, "w"), "ms": tu._qmp_read(qmp, TICK_MS, "w"),
                    "r15": tu._qmp_read(qmp, last_r15, "w"), "secs": tu._qmp_read(qmp, STANDBY_SECONDS, "h"),
                    "pool": tu._qmp_read(qmp, REDIRECT_POOL, "w")}
            tu._qmp_execute(qmp, "cont")
            return vals
        first = snap()
        time.sleep(15)
        second = snap()
        seen = {"first": first, "second": second}
        dump = tmp_path / "region.bin"
        tu._qmp_execute(qmp, "human-monitor-command",
                        **{"command-line": f'pmemsave {L.REGION:#x} {L.REGION_END - L.REGION:#x} "{dump}"'})
        region = dump.read_bytes()
    finally:
        qmp.close()
        qmp_s.close()
        p.terminate()
        p.wait(timeout=10)
    print("payload_demo live:", seen)
    assert first["pool"] == second["pool"] == payloadbuild.lookup(p_built.symbols, "payload_demo_tick")
    assert 0 < first["bpm"] < second["bpm"], seen
    assert 0 < first["tick"] < second["tick"], seen
    # one redirected call per 1 ms tick (the routine is the tick's own pool call)
    assert abs((second["tick"] - first["tick"]) - (second["ms"] - first["ms"])) <= 2, seen
    # the stock job of the redirected function: the idle seconds counter it drives keeps counting,
    # one second per 1000 ticks
    secs = second["secs"] - first["secs"]
    assert secs > 0 and abs(secs - (second["ms"] - first["ms"]) / 1000) <= 1.5, seen
    # the hook got r5 = r15 as the span left it: the task's stack (on-chip RAM or SDRAM)
    assert 0xFFF80000 <= first["r15"] < 0xFFFC0000 or 0x0C000000 <= first["r15"] < 0x0E000000, seen
    # guards and the payload's code are intact; .bss is live data now, so compare up to it
    text_end = p_built.symbols["__bss_start"] - L.PAYLOAD
    assert region[L.HEADER:L.HEADER + text_end] == p_built.blob[:text_end]
    assert u32(region, L.GUARD_HI_ADDR - L.REGION) == L.GUARD_HI
    assert u32(region, 0) == L.GUARD_LO
