# SPDX-License-Identifier: GPL-2.0-or-later
"""CDJ-850 mods are inert in the service modes (docs/cdj850-modding.md, "Service mode gate").

panel_keys_to_internal_words sets u8 0x0C1E109A to 1/2 when RELOOP is held at power-on
(firmware update from USB/CD) and clears u8 0x0C1E10AA (1 in .data) once it has looked at
the power-on keys. While either is nonzero no mod may act: every hook trampoline runs only
its moved stock span, and every PoolRedirect and EntryRedirect goes through a generated gate in
the payload (__gate_<symbol>) that jumps to the stock function (for an EntryRedirect: to the
payload's copy of the moved entry, which goes on in the stock function).

These tests enumerate every hook Mod, PoolRedirect and EntryRedirect the cdj850 registry declares,
build one image with all of them, and check that each payload entry and each cave routine
is reached only through a gate, running the gates on the SH-2A interpreter.
"""
import struct
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "mods"))
sys.path.insert(0, str(ROOT / "scripts" / "firmware"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import c850loader as L  # noqa: E402
import modelfw  # noqa: E402
import patch_main  # noqa: E402
import payloadbuild  # noqa: E402
import sigpatch  # noqa: E402
from sh2a import Memory, Sh2a  # noqa: E402

MAIN = ROOT / "extract" / "cdj850" / "main_unpacked.bin"
BASE = 0x0C000000
UPDATE_MODE, KEYS_PENDING = 0x0C1E109A, 0x0C1E10AA
PS = patch_main.PATCH_SETS["cdj850"]

needs_toolchain = pytest.mark.skipif(not payloadbuild.have_toolchain(), reason="sh-elf toolchain not installed")
needs_main = pytest.mark.skipif(not MAIN.is_file(), reason="extract/cdj850/main_unpacked.bin is not present")


def u32(data, off):
    return struct.unpack_from(">I", data, off)[0]


def test_profile_gate_is_the_traced_service_bytes():
    assert PS.profile.gate == (UPDATE_MODE, KEYS_PENDING)
    if MAIN.is_file():
        img = MAIN.read_bytes()
        assert img[UPDATE_MODE - BASE] == 0 and img[KEYS_PENDING - BASE] == 1     # .data initial values


def test_every_registry_patch_is_gateable():
    """Every routine patch is a hook (gated by its trampoline) and every redirect has one
    stock function per symbol (one gate); nothing else calls mod code."""
    assert PS.mods and PS.redirects and PS.entries
    assert all(m.hook for m in PS.mods.values()), [n for n, m in PS.mods.items() if not m.hook]
    olds = {}
    for name, r in PS.redirects.items():
        sym = sigpatch.payload_symbol(r)
        assert olds.setdefault(sym, r.old) == r.old, name
    for name, e in PS.entries.items():
        sym = sigpatch.payload_symbol(e)
        assert olds.setdefault(sym, sigpatch.stock_symbol(e)) == sigpatch.stock_symbol(e), name
    # DataPatches and the loader are the only other kinds; they hold no code a gate could stop
    assert set(PS.images) == {"loader"}


@pytest.fixture(scope="module")
def all_mods():
    """MAIN with every cdj850 patch the registry has (all mod folders), and the payload."""
    stock = MAIN.read_bytes()
    names = sorted(set(PS.mods) | set(PS.tables) | set(PS.redirects) | set(PS.entries))
    report, spans = {}, {}
    out = patch_main.patch(stock, names, "cdj850", report, spans)
    # a redirect of a word another applied redirect already points at the same symbol (the
    # shared state-4 tick c850_loop_tick, c850.h) finds it 'present': it shares that span
    for n, r in report.items():
        if r[0] == "present" and n in PS.redirects:
            sym, old = sigpatch.payload_symbol(PS.redirects[n]), PS.redirects[n].old
            twins = [m for m, x in PS.redirects.items() if m != n and report.get(m, ("",))[0] == "applied"
                     and sigpatch.payload_symbol(x) == sym and x.old == old]
            assert twins and spans[twins[0]][0][0] == spans.get(n, spans[twins[0]])[0][0], (n, twins)
            spans[n] = spans[twins[0]]
            report[n] = ("applied", "shared with %s" % twins[0])
    bad = {n: r for n, r in report.items() if r[0] != "applied"}
    assert not bad, bad
    folders = patch_main.payload_folders(names, "cdj850")
    p = payloadbuild.build(folders, "cdj850")
    assert L.installed(out) == p.blob
    return stock, out, p, spans


def _cpu(image, payload, flags):
    mem = Memory()
    image = bytearray(image)
    image[UPDATE_MODE - BASE], image[KEYS_PENDING - BASE] = flags
    mem.add(BASE, bytes(image) + bytes(0x0D000000 - BASE - len(image)))   # MAIN + its RAM up to 0x0D000000
    mem.add(L.PAYLOAD, payload.blob)
    mem.add(0x0DF00000, bytes(0x1000))                                   # a stack
    cpu = Sh2a(mem)
    cpu.r[15] = 0x0DF00F00
    return cpu


def _run_until(cpu, pc, stops, limit=2000):
    seen = []
    while pc not in stops:
        seen.append(pc)
        assert len(seen) < limit, "no stop after %d instructions" % limit
        pc = cpu.step(pc)
    return pc, seen


@needs_toolchain
@needs_main
def test_every_redirect_points_at_its_gate_and_the_gate_picks_stock_in_service_mode(all_mods):
    stock, out, p, spans = all_mods
    for name, r in sorted(PS.redirects.items()):
        sym = sigpatch.payload_symbol(r)
        gate = payloadbuild.lookup(p.symbols, payloadbuild.gate_name(sym))
        real = payloadbuild.lookup(p.symbols, sym)
        # a word another redirect already pointed at the same routine (c850_ui_keys: hot_cues and
        # quant_loops_keys) is 'applied' shared and writes no span of its own
        owner = name if name in spans else next(n for n, o in PS.redirects.items()
                                                if n in spans and o.anchor == r.anchor and o.old == r.old
                                                and sigpatch.payload_symbol(o) == sym)
        (off, size, kind), = spans[owner]
        assert (size, kind) == (4, "site") and u32(stock, off) == r.old
        assert u32(out, off) == gate, "%s: pool word %#x is not the gate" % (name, BASE + off)
        for flags, want in (((0, 0), real), ((1, 0), r.old), ((2, 0), r.old), ((0, 1), r.old), ((1, 1), r.old)):
            cpu = _cpu(out, p, flags)
            cpu.r[4:8] = [0x11, 0x22, 0x33, 0x44]
            cpu.pr, cpu.r[15] = 0x0C0FFFF0, 0x0DF00F00
            end, _ = _run_until(cpu, gate, {real, r.old})
            assert end == want, (name, flags, hex(end))
            # the callee sees the caller's arguments, stack and return address
            assert cpu.r[4:8] == [0x11, 0x22, 0x33, 0x44] and cpu.pr == 0x0C0FFFF0 and cpu.r[15] == 0x0DF00F00


@needs_toolchain
@needs_main
def test_every_entry_redirect_jumps_to_its_gate_and_the_gate_picks_the_moved_entry_in_service_mode(all_mods):
    stock, out, p, spans = all_mods
    for name, e in sorted(PS.entries.items()):
        sym = sigpatch.payload_symbol(e)
        gate = payloadbuild.lookup(p.symbols, payloadbuild.gate_name(sym))
        real = payloadbuild.lookup(p.symbols, sym)
        moved = payloadbuild.lookup(p.symbols, sigpatch.stock_symbol(e))
        (off, size, kind), = spans[name]
        entry = BASE + off
        assert kind == "site" and stock[off:off + size] == e.old
        assert out[off:off + size] == sigpatch.entry_jump(entry, gate), name
        # the moved entry runs the replaced bytes and goes on behind them in MAIN
        assert p.blob[moved - L.PAYLOAD:moved - L.PAYLOAD + size] == e.old
        for flags, want in (((0, 0), real), ((1, 0), moved), ((2, 0), moved), ((0, 1), moved), ((1, 1), moved)):
            cpu = _cpu(out, p, flags)
            cpu.r[4:8] = [0x11, 0x22, 0x33, 0x44]
            cpu.pr, cpu.r[15] = 0x0C0FFFF0, 0x0DF00F00
            end, _ = _run_until(cpu, entry, {real, moved})
            assert end == want, (name, flags, hex(end))
            # the callee sees the caller's arguments, stack and return address
            assert cpu.r[4:8] == [0x11, 0x22, 0x33, 0x44] and cpu.pr == 0x0C0FFFF0 and cpu.r[15] == 0x0DF00F00


@needs_toolchain
@needs_main
def test_every_hook_trampoline_skips_its_hooks_in_service_mode(all_mods):
    stock, out, p, spans = all_mods
    sites = sigpatch.hook_sites(PS.mods)
    assert sites
    for key, hooks in sites.items():
        sig, start, end = key
        site = sigpatch.sig_hits(sigpatch.Image(PS.profile, stock), list(sig))
        assert len(site) == 1
        site_addr = BASE + site[0] + 2 * start
        resume = BASE + site[0] + 2 * end
        lit = (site_addr + 6 + 3) & ~3                  # call_site(jump=True): mov.l lit,r1; jmp @r1; nop
        tramp = u32(out, lit - BASE)
        size = sigpatch.trampoline_size(key, len(hooks), PS.profile.gate)
        code = out[tramp - BASE:tramp - BASE + size]
        pool = struct.unpack(">%dI" % (size // 4), code[size - 4 * (size // 4):])
        # the hooks: cave routines or payload symbols, all in the trampoline's pool
        targets = []
        for n in hooks:
            sym = sigpatch.payload_symbol(PS.mods[n])
            targets.append(payloadbuild.lookup(p.symbols, sym) if sym else
                           sigpatch.cave_base(sigpatch.Image(PS.profile, stock)) +
                           sigpatch.layout(lambda m: patch_main.blob_path(m, "cdj850"), PS.mods,
                                           PS.profile.gate)[0][n])
        assert all(t in pool for t in targets), (hooks, [hex(t) for t in targets])
        assert UPDATE_MODE in pool and KEYS_PENDING in pool
        for flags in ((1, 0), (2, 0), (0, 1)):
            cpu = _cpu(out, p, flags)
            for k in range(16):
                cpu.r[k] = 0x0C9D0000 + 0x100 * k if k < 15 else 0x0DF00F00
            end, seen = _run_until(cpu, site_addr, {resume} | set(targets))
            assert end == resume, (hooks, flags, hex(end))
            assert not set(seen) & set(targets)
            assert cpu.r[15] == 0x0DF00F00
        # gate open: the first hook is called
        cpu = _cpu(out, p, (0, 0))
        for k in range(16):
            cpu.r[k] = 0x0C9D0000 + 0x100 * k if k < 15 else 0x0DF00F00
        end, _ = _run_until(cpu, site_addr, {resume} | set(targets))
        assert end == targets[0], (hooks, hex(end))


@needs_toolchain
@needs_main
def test_every_word_into_mod_code_is_a_gate_or_a_gated_trampoline(all_mods):
    """Scan everything the mods wrote outside the loader blob: a word that points into the
    payload is a __gate_ symbol or a hook literal inside a trampoline's pool; a word that
    points at a cave routine is in a trampoline's pool too. So no path into mod code skips a gate."""
    stock, out, p, spans = all_mods
    gates = {a for n, a in p.symbols.items() if n.startswith(payloadbuild.GATE_PREFIX)}
    offsets, total = sigpatch.layout(lambda m: patch_main.blob_path(m, "cdj850"), PS.mods, PS.profile.gate)
    cave0 = sigpatch.cave_base(sigpatch.Image(PS.profile, stock))
    tramp_ranges = [(cave0 + o, cave0 + o + sigpatch.trampoline_size(k, len(h), PS.profile.gate))
                    for k, h in sigpatch.hook_sites(PS.mods).items() for o in [offsets[k]]]
    routines = {cave0 + offsets[n]: n for n in offsets if isinstance(n, str)}
    pay_hi = L.PAYLOAD + len(p.blob)
    checked = 0
    for name, written in spans.items():
        for off, size, kind in written:
            if kind not in ("site", "free"):
                continue
            for o in range(off + (-off % 4), off + size - 3, 4):
                w = u32(out, o)
                if L.PAYLOAD <= w < pay_hi or w in routines:
                    checked += 1
                    in_tramp = any(lo <= BASE + o < hi for lo, hi in tramp_ranges)
                    assert w in gates or in_tramp, (name, hex(BASE + o), hex(w))
    assert checked >= len(PS.redirects) + len(PS.entries) + len(PS.mods)


@needs_toolchain
def test_gate_thunk_bytes():
    """The generated gate for a symbol: reads both service bytes, jmp to the stock
    function when either is set, else to the routine; r0, r1 and T only."""
    p = payloadbuild.build(["hot_cues"], "cdj850")
    gate = payloadbuild.lookup(p.symbols, "__gate_c850_ui_keys")
    off = gate - L.PAYLOAD
    ops = struct.unpack(">14H", p.blob[off:off + 28])
    assert ops == (0xD106, 0x6110, 0x6013, 0xD106, 0x6110, 0x201B, 0x2008, 0x8B02,
                   0xD104, 0x412B, 0x0009, 0xD104, 0x412B, 0x0009)
    lits = struct.unpack(">4I", p.blob[off + 28:off + 44])
    assert lits == (UPDATE_MODE, KEYS_PENDING, payloadbuild.lookup(p.symbols, "c850_ui_keys"), 0x0C0DD492)


def test_trampoline_gate_layout_is_unchanged_without_a_gate():
    """Other models (no gate) get the trampolines they always had."""
    span = [0xD62E, 0x6260, 0x2228]
    a = sigpatch.trampoline(span, [0x1234], [0x0C0172B4], 0x0C0642B8, 0x0C017364)
    b = sigpatch.trampoline(span, [0x1234], [0x0C0172B4], 0x0C0642B8, 0x0C017364, gate=())
    assert a == b
    g = sigpatch.trampoline(span, [0x1234], [0x0C0172B4], 0x0C0642B8, 0x0C017364, gate=(UPDATE_MODE, KEYS_PENDING))
    assert len(g) > len(a)
