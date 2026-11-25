# SPDX-License-Identifier: GPL-2.0-or-later
"""Regression coverage for the CDJ-850's opt-in USB browse patches."""

import importlib
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "mods"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import patch_main
import payloadbuild
from sh2a import Memory, Sh2a


pytestmark = pytest.mark.skipif(not payloadbuild.have_toolchain(),
                                reason="sh-elf toolchain not installed")
MAIN = ROOT / "extract" / "cdj850" / "main_unpacked.bin"
RECORD = 0x0C9D44D4
ROW = 0x0DDF0100
REGS = 0x0DDF0000
MATRIX = 0x0C9D0B18


@pytest.mark.skipif(not MAIN.is_file(), reason="CDJ-850 MAIN image missing")
def test_browse_hooks_compose_with_the_shared_display_and_control_hooks():
    browse = list(importlib.import_module("browse.cdj850.patch").MODS)
    names = browse + ["info_order", "bpm_lock_tick", "bpm_lock_flash", "bpm_decimals"]
    report = {}
    patch_main.patch(MAIN.read_bytes(), names, "cdj850", report)
    assert {name: state for name, (state, _) in report.items()} == {
        name: "applied" for name in names + ["loader"]
    }


def test_played_threshold_pause_track_identity_and_remount():
    payload = payloadbuild.build(["browse"], "cdj850")
    mem = Memory()
    mem.add(payloadbuild.BASE, payload.blob)
    mem.add(0x0DDF0000, bytes(0x1000))
    mem.add(0x0CE49F54, bytes(4))             # millisecond clock
    mem.add(0x0CE6C900, bytes(0xA00))         # transport and browse state
    mem.add(0x0C9D0AF8, b"\0\0\0\1")          # browse display mode
    mem.add(0x0C1E108C, b"\0")                # menu level
    mem.add(MATRIX, bytes(3 * 0x105))
    mem.add(0x0C9D1D96, b"\0\1")             # displayed track number
    mem.add(RECORD, bytes(0x864))
    mem.add(0x0C0750D8, bytes.fromhex("006b"))  # stock mount callback returns
    mem.add(0x0C073F7C, bytes.fromhex("d000006b") + RECORD.to_bytes(4, "big"))
    mem.write(0x0CE6D252, b"\x02")            # USB source
    mem.write(0x0CE6D25A, b"\x01")            # USB mode
    mem.write(0x0CE6D0FD, b"\x01")            # BROWSE
    mem.put32(0x0CE6C940, 0x80000)            # 1x DSP speed
    mem.put32(RECORD + 0x10, 1)
    mem.write(RECORD + 0x16, b"\0\2")        # loaded from a two-track list
    mem.write(RECORD + 0x38, b"SAME\0")
    mem.write(MATRIX, b"\x25\x5a\0\0\x04SAME")
    mem.put32(ROW, 1)
    mem.write(ROW + 0x14, "SAME\0".encode("utf-16-be"))

    def call(name, *args):
        cpu = Sh2a(mem)
        cpu.r[15] = 0x0DDF0F00
        for register, value in enumerate(args, 4):
            cpu.r[register] = value
        cpu.run(payloadbuild.lookup(payload.symbols, name), Sh2a.RET)

    def sample(ms, state=3):
        mem.put32(0x0CE49F54, ms)
        mem.write(0x0CE6D25F, bytes([state]))
        call("browse_playback_tick", REGS, 0)

    def rendered_title():
        mem.put32(REGS, 0x04)
        mem.put32(REGS + 7 * 4, ROW + 0x14)
        call("browse_mark_title", REGS, 0)
        start = mem.u32(REGS + 7 * 4)
        return mem.read(start, 12).decode("utf-16-be").split("\0", 1)[0]

    call("browse_mount_export", 3)
    sample(0)
    sample(10000)
    sample(12000, state=5)
    sample(25000)
    sample(35000)
    assert rendered_title() == "SAME"
    assert mem.read(MATRIX + 4, 5) == b"\x04SAME"
    sample(35001)
    assert rendered_title() == "*SAME"
    assert mem.read(MATRIX + 4, 6) == b"\x05*SAME"
    sample(36000)
    assert mem.read(MATRIX + 4, 6) == b"\x05*SAME"
    assert mem.read(ROW + 0x14, 10).decode("utf-16-be") == "SAME\0"
    mem.put32(ROW, 2)
    assert rendered_title() == "SAME"
    mem.put32(ROW, 1)
    call("browse_mount_export", 3)
    assert rendered_title() == "SAME"
