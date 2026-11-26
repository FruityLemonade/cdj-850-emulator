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


class StubCpu(Sh2a):
    """Sh2a that runs Python in place of the firmware functions in `stubs`
    ({address: fn(cpu) -> r0 or None}) and returns to pr."""

    def __init__(self, mem, stubs):
        super().__init__(mem)
        self.stubs = stubs

    def step(self, pc):
        if pc in self.stubs:
            r0 = self.stubs[pc](self)
            if r0 is not None:
                self.r[0] = r0 & 0xFFFFFFFF
            return self.pr
        return super().step(pc)


RESULTS = 0x0DDE0000
INDEX = 0x0DDE0100
ROWS = 0x0DDE0200
HEAP = 0x0DDE1000
VALUE = 0x0DDE0F00
# content ID -> (BPM x 100, key ID); the stock list order is by title
CONTENT = {1: (17400, 5), 2: (10000, 9), 3: (12800, 1)}


def sort_rig(row_ids, row_type=0x04):
    payload = payloadbuild.build(["browse"], "cdj850")
    sym = lambda name: payloadbuild.lookup(payload.symbols, name)
    mem = Memory()
    mem.add(payloadbuild.BASE, payload.blob)
    mem.add(0x0DDE0000, bytes(0x10000))
    mem.add(0x0DDF0000, bytes(0x1000))
    state = {"heap": HEAP, "rows": [], "calls": []}

    def malloc(cpu):
        p = state["heap"]
        state["heap"] += (cpu.r[4] + 7) & ~3
        return p

    def query(cpu):
        state["calls"].append(("query", cpu.r[4], cpu.r[5]))
        state["rows"] = sorted(CONTENT)
        return 0x5000

    def next_row(cpu):
        return state["rows"].pop(0) if state["rows"] else 0

    def column(cpu):
        row, col = cpu.r[4], cpu.r[5]
        value = {0: row, 8: CONTENT[row][0], 0x14: CONTENT[row][1]}[col]
        mem.put32(VALUE + col * 4, value)
        mem.write(cpu.r[6], b"\0")
        return VALUE + col * 4

    stubs = {
        0x0C0750D8: lambda cpu: None,             # stock sort-menu loader
        0x0C0BBABE: lambda cpu: 0xFFFFFFFF,       # menu cache: miss
        0x0C0BABAC: lambda cpu: 0,                # stock finalizer
        0x0C07C330: query,
        0x0C11D572: next_row,
        0x0C11D5E6: column,
        0x0C11D39C: lambda cpu: 0,
        0x0C088BC4: malloc,
        0x0C088C5C: lambda cpu: None,
    }

    def call(name, *args):
        cpu = StubCpu(mem, stubs)
        cpu.r[15] = 0x0DDF0F00
        for register, value in enumerate(args, 4):
            cpu.r[register] = value
        cpu.run(sym(name), Sh2a.RET)
        return cpu.r[0]

    mem.put32(RESULTS + 0x0C, 0x00010301)
    mem.write(RESULTS + 2, len(row_ids).to_bytes(2, "big"))
    mem.put32(RESULTS + 0x2C, INDEX)
    mem.put32(RESULTS + 0x44, ROWS)
    for i, content_id in enumerate(row_ids):
        mem.put32(ROWS + i * 0x14, content_id)
        mem.write(ROWS + i * 0x14 + 0x0C, bytes([row_type]))
        mem.write(INDEX + i * 2, i.to_bytes(2, "big"))
    call("browse_mount_export", 3)
    return mem, sym, call, state


def index_of(mem, n):
    return [int.from_bytes(mem.read(INDEX + i * 2, 2), "big") for i in range(n)]


@pytest.mark.parametrize("order, expected", [(0, [0, 1, 2]), (1, [1, 2, 0]), (2, [2, 0, 1])])
def test_any_usb_track_list_is_reordered_by_bpm_or_key(order, expected):
    mem, sym, call, state = sort_rig([1, 2, 3])
    mem.write(sym("selected_sort"), bytes([order]))
    args = 0x0DDF0800
    call("browse_track_cache", 0x0CE4A3B4, 0x00010301, 0x1105, args)
    assert mem.u32(args + 7 * 4) == order      # the order is part of the cache key
    assert call("browse_sort_results", RESULTS) == 0
    assert index_of(mem, 3) == expected
    assert bool(state["calls"]) == bool(order)


def test_sort_keeps_list_order_among_equal_values_and_repeats():
    # a playlist holding track 2 twice, and a track the content table lacks
    mem, sym, call, _ = sort_rig([2, 9, 1, 2])
    mem.write(sym("selected_sort"), b"\x01")
    call("browse_track_cache", 0x0CE4A3B4, 0x00010301, 0x1105, 0x0DDF0800)
    call("browse_sort_results", RESULTS)
    assert index_of(mem, 4) == [0, 3, 2, 1]


def test_lists_of_other_items_and_other_media_keep_their_order():
    mem, sym, call, state = sort_rig([1, 2, 3], row_type=0x07)  # artists
    mem.write(sym("selected_sort"), b"\x01")
    call("browse_track_cache", 0x0CE4A3B4, 0x00010301, 0x1002, 0x0DDF0800)
    call("browse_sort_results", RESULTS)
    assert index_of(mem, 3) == [0, 1, 2] and not state["calls"]
    mem, sym, call, state = sort_rig([1, 2, 3])
    mem.write(sym("selected_sort"), b"\x01")
    mem.put32(RESULTS + 0x0C, 0x00010201)
    call("browse_track_cache", 0x0CE4A3B4, 0x00010201, 0x1004, 0x0DDF0800)
    call("browse_sort_results", RESULTS)
    assert index_of(mem, 3) == [0, 1, 2] and not state["calls"]
