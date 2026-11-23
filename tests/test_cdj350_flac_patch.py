# SPDX-License-Identifier: GPL-2.0-or-later
"""Verify the CDJ-350's FLAC hooks and the boot-critical service gate."""
import os
import struct
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "mods"), str(ROOT / "scripts" / "firmware")]

import c850loader  # noqa: E402
import modelfw  # noqa: E402
import patch_main  # noqa: E402
import payloadbuild  # noqa: E402
import validate  # noqa: E402
from launcher import mods  # noqa: E402
from players.cdj350 import GATED_BOOT_POOLS, LOADER_STARTUP, MAIN_PROFILE  # noqa: E402

MODEL = "cdj350"
MAIN = ROOT / "extract" / MODEL / "main_unpacked.bin"
PORT = modelfw.mod_patch("flac", MODEL)


def test_launcher_flac_knobs_match_the_model():
    layout = type("Layout", (), {"emu": os.fspath(ROOT)})()
    flac = next(row for row in mods.load(layout, MODEL) if row.key == "flac")
    assert {knob.removeprefix("CDJ_MAIN_").lower() for knob in mods.env_names(flac.env)} == PORT.MODS.keys()


@pytest.mark.skipif(not MAIN.is_file() or not payloadbuild.have_toolchain(),
                    reason="stock CDJ-350 MAIN and sh-elf toolchain are required")
def test_stock_hooks_and_boot_critical_pool_gates():
    stock = MAIN.read_bytes()[:LOADER_STARTUP.image_len]
    base = MAIN_PROFILE.flash_base
    report, spans = {}, {}
    names = patch_main.with_loader(PORT.MODS, MODEL)
    patched = patch_main.patch(stock, names, MODEL, report, spans)
    assert all(report[name][0] == "applied" for name in names), report
    assert spans["flac"] == [(PORT.CLASSIFIER - base, 12, "site")]
    assert c850loader.verify(stock, patched, startup=LOADER_STARTUP) == []

    payload = c850loader.installed(patched, startup=LOADER_STARTUP)
    for name, addr, original, _, _ in PORT.AUDIO_CALLS:
        off = addr - base
        assert spans[name] == [(off, 4, "site")]
        assert struct.unpack_from(">I", stock, off)[0] == original
        if addr in GATED_BOOT_POOLS:
            edit = (name, "site", off, off + 4)
            assert validate.is_gated_pool(stock, patched, MAIN_PROFILE, edit, GATED_BOOT_POOLS, payload)
            gate = struct.unpack_from(">I", patched, off)[0] - c850loader.PAYLOAD
            corrupted = bytearray(payload)
            corrupted[gate + 43] ^= 1
            assert not validate.is_gated_pool(stock, patched, MAIN_PROFILE, edit, GATED_BOOT_POOLS, corrupted)
