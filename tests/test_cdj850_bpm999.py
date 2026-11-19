# SPDX-License-Identifier: GPL-2.0-or-later
"""bpm999 and bpm_probe on the CDJ-850 MAIN 1.13: static patch sites, the two
routines run on the SH-2A interpreter, and (when the emulator and the flash are
present) a live boot of the probe build whose TX frames show BPM 199 and
frame digits 75..99."""
import json
import os
import re
import selectors
import socket
import struct
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "mods"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import patch_main  # noqa: E402
from sh2a import Memory, Sh2a  # noqa: E402

MAIN = ROOT / "extract" / "cdj850" / "main_unpacked.bin"
FLASH = ROOT / "extract" / "cdj850" / "flash_cdj850.bin"
BASE = 0x0C000000
GRID_MOVI20 = 0x0C0737D6      # FUN_0c073788: movi20 #0x708 -> #9990
RANGE_MOVI20 = 0x0C0D1C80     # FUN_0c0d1c42: movi20 #0x7CF -> #9990
HOOK_SITE = 0x0C0642AA        # tail of display_stage_bpm (span 12..14 bytes)
STAGE_BPM = 0x0C9D0AE0
STAGE_FRAME = 0x0C9D0ADE
SCRATCH = 0x0DDF0C40          # bpm_probe tick and frame longwords
CODE = 0x0C200000             # where the routine is run in the interpreter
STOP = 0xFFFF0000             # Sh2a.RET: rts returns here


def _main_bytes():
    return MAIN.read_bytes()


def _patched(names):
    data = _main_bytes()
    return patch_main.patch(data, names, "cdj850")


def _blob(name):
    return Path(patch_main.blob_path(name, "cdj850")).read_bytes()


def _run(blob, bpm=None, frame=None, tick=None, frame_byte=None):
    """Run one routine call on the interpreter. Returns (staging BPM word,
    staging frame byte, the two SDRAM scratch longwords as bytes)."""
    mem = Memory()
    mem.add(CODE, blob)
    stage = bytearray(0x40)
    if bpm is not None:
        stage[0x8:0xA] = struct.pack(">H", bpm)
    if frame_byte is not None:
        stage[0x6] = frame_byte
    mem.add(0x0C9D0AD8, stage)
    scratch = bytearray(8)
    if tick is not None:
        scratch[0:4] = struct.pack(">I", tick)
    if frame is not None:
        scratch[4:8] = struct.pack(">I", frame)
    mem.add(SCRATCH, scratch)
    Sh2a(mem).run(CODE, STOP)
    # Memory.add copies, so read the results back from the interpreter's memory
    stage_out = mem.read(0x0C9D0AD8, 0x40)
    scratch_out = mem.read(SCRATCH, 8)
    return struct.unpack(">H", stage_out[0x8:0xA])[0], stage_out[0x6], scratch_out


def decode_movi20(words):
    """(mnemonic, imm, Rn) for a MOVI20 / MOVI20S at the start of `words` (two 16-bit words).
    Layout (SH-2A): 0000nnnn iiii0000 followed by imm[15:0]; MOVI20S has low nibble 1 and shifts
    imm16 left by 8."""
    w1, w2 = words
    if w1 & 0xF00F == 0x0000:
        return ("movi20", (((w1 >> 4) & 0xF) << 16) | w2, (w1 >> 8) & 0xF)
    if w1 & 0xF00F == 0x0001:
        return ("movi20s", w2 << 8, (w1 >> 8) & 0xF)
    raise AssertionError("not a MOVI20: %04x %04x" % (w1, w2))


def _words(data, at):
    return struct.unpack(">2H", data[at:at + 4])


def test_static_patch_sites_and_hook_jump():
    if not MAIN.is_file():
        pytest.skip("extract/cdj850/main_unpacked.bin is not present")
    stock = _main_bytes()
    out = _patched(["bpm999grid", "bpm999range"])
    g = GRID_MOVI20 - BASE
    r = RANGE_MOVI20 - BASE
    assert stock[g:g + 4].hex() == "04000708"
    assert out[g:g + 4].hex() == "04002706"
    assert decode_movi20(_words(out, g)) == ("movi20", 9990, 4)     # movi20 #9990,r4
    assert stock[r:r + 4].hex() == "050007cf"
    assert out[r:r + 4].hex() == "05002706"
    assert decode_movi20(_words(out, r)) == ("movi20", 9990, 5)     # movi20 #9990,r5
    assert decode_movi20(_words(stock, g)) == ("movi20", 1800, 4)   # stock: 180.0
    assert decode_movi20(_words(stock, r)) == ("movi20", 1999, 5)   # stock: 199.9
    changed = [i for i in range(len(stock)) if stock[i] != out[i]]
    assert all(g + 2 <= i < g + 4 or r + 2 <= i < r + 4 for i in changed)

    both = _patched(["bpm999", "bpm_probe"])
    h = HOOK_SITE - BASE
    # the site is `mov.l @(pool),r1; jmp @r1; nop; .long target` (sigpatch.call_site)
    assert both[h + 2:h + 6].hex() == "412b0009"
    assert stock[h:h + 14].hex() == "d62e626022288902d62a60618154"


# --- decode the routine blobs back to instructions and compare them with the .s source ---

def _s_source(name):
    """(label -> offset, [(offset, text)], pool {label: value}) read from the .s file, with the
    offsets the file's own layout gives (2 bytes per instruction, .align 2 pads to 4)."""
    path = Path(patch_main.source(name, "cdj850"))
    labels, ops, pool, pc = {}, [], {}, 0
    for raw in path.read_text().splitlines():
        line = raw.split("!", 1)[0].strip()
        if not line or line.startswith(".text"):
            continue
        if ":" in line.split()[0]:
            lab, _, rest = line.partition(":")
            labels[lab.strip()] = pc
            line = rest.strip()
            if not line:
                continue
        if line.startswith(".align"):
            pc = (pc + 3) & ~3
            continue
        if line.startswith(".long"):
            value = int(line.split()[1], 0)
            pool[lab] = value
            pc += 4
            continue
        ops.append((pc, " ".join(line.split())))
        pc += 2
    return labels, ops, pool


def _canon_s(text, labels, pool):
    m, _, args = text.partition(" ")
    args = args.replace(" ", "")
    if m in ("mov.l", "mov.w", "mov.b") and args.split(",")[0] in pool:
        return "mov.l =%#x,r%s" % (pool[args.split(",")[0]], args.split(",")[1][1:])
    if not args:
        return m
    return "%s %s" % (m, args)


def _decode(w, at, labels_by_off, pool_by_off, data):
    """Canonical text for one 16-bit word at offset `at` of the blob."""
    n = (w >> 8) & 0xF
    m = (w >> 4) & 0xF
    if w == 0x000B:
        return "rts"
    if w == 0x0009:
        return "nop"
    top = w >> 12
    if top == 0xD:                                    # mov.l @(disp,pc),rn
        disp = (w & 0xFF) * 4
        target = ((at & ~3) + 4) + disp
        value = struct.unpack(">I", data[target:target + 4])[0]
        return "mov.l =%#x,r%d" % (value, n)
    if top == 0x7:                                    # add #imm,rn
        imm = w & 0xFF
        imm = imm - 256 if imm & 0x80 else imm
        return "add #%d,r%d" % (imm, n)
    if top == 0xE:                                    # mov #imm,rn
        imm = w & 0xFF
        imm = imm - 256 if imm & 0x80 else imm
        return "mov #%d,r%d" % (imm, n)
    if top == 0x3:
        names = {0: "cmp/eq", 2: "cmp/hs", 3: "cmp/ge", 6: "cmp/hi", 7: "cmp/gt"}
        return "%s r%d,r%d" % (names[w & 0xF], m, n)
    if w & 0xF00F == 0x2008:
        return "tst r%d,r%d" % (m, n)
    if w & 0xF00F == 0x600D:
        return "extu.w r%d,r%d" % (m, n)
    if w & 0xF00F == 0x6003:
        return "mov r%d,r%d" % (m, n)
    if top == 0x6:
        sz = {0: "mov.b", 1: "mov.w", 2: "mov.l"}.get(w & 0xF)
        if sz:
            return "%s @r%d,r%d" % (sz, m, n)
    if top == 0x2:
        sz = {0: "mov.b", 1: "mov.w", 2: "mov.l"}.get(w & 0xF)
        if sz:
            return "%s r%d,@r%d" % (sz, m, n)
    if top == 0x8 and (w >> 8) & 0xF in (9, 0xB):   # bt / bf
        disp = w & 0xFF
        disp = disp - 256 if disp & 0x80 else disp
        target = at + 4 + 2 * disp
        return ("bt" if (w >> 8) & 0xF == 9 else "bf") + " @%#x" % target
    if top == 0xA:                                    # bra
        disp = w & 0xFFF
        disp = disp - 4096 if disp & 0x800 else disp
        return "bra @%#x" % (at + 4 + 2 * disp)
    raise AssertionError("cannot decode %#06x at %#x" % (w, at))


def _check_blob_against_source(name):
    data = Path(patch_main.blob_path(name, "cdj850")).read_bytes()
    labels, ops, pool = _s_source(name)
    off_of_label = labels
    # canonical expectations, branch targets as the label's offset
    expected = []
    for at, text in ops:
        m, _, args = text.partition(" ")
        args = args.replace(" ", "")
        if m in ("bt", "bf", "bra"):
            expected.append((at, "%s @%#x" % (m, off_of_label[args])))
        elif m in ("mov.l", "mov.w", "mov.b") and args.split(",")[0] in pool:
            expected.append((at, "mov.l =%#x,r%s" % (pool[args.split(",")[0]], args.split(",")[1][1:])))
        else:
            expected.append((at, _canon_s(text, labels, pool)))
    code_end = min(off_of_label[lab] for lab in pool)
    got = []
    for at in range(0, code_end, 2):
        w = struct.unpack_from(">H", data, at)[0]
        got.append((at, _decode(w, at, None, None, data)))
    expected_ops = [(at, t) for at, t in expected if at < code_end]
    got_ops = [(at, t) for at, t in got]
    # the assembler leaves an `.align` filler word (nop) before the pool; the .s has none
    got_ops = [(at, t) for at, t in got_ops if not (t == "nop" and all(a != at for a, _ in expected_ops))]
    assert [t for _, t in got_ops] == [t for _, t in expected_ops], (name, got_ops, expected_ops)
    # pool entries are the values the .s declares
    for lab, value in pool.items():
        at = off_of_label[lab]
        assert struct.unpack_from(">I", data, at)[0] == value, (name, lab)
    return len(expected_ops)


def test_bpm999_blob_decodes_to_its_source():
    assert _check_blob_against_source("bpm999") >= 10


def test_bpm_probe_blob_decodes_to_its_source():
    assert _check_blob_against_source("bpm_probe") >= 30


def test_clamp_routine_on_the_interpreter():
    blob = _blob("bpm999")
    assert len(blob) == 36
    cases = {1800: 999, 1500: 999, 1000: 999, 999: 999, 998: 998, 180: 180, 0: 0, 0xFFFF: 0xFFFF}
    for before, after in cases.items():
        got, _, _ = _run(blob, bpm=before)
        assert got == after, (before, got)


def test_probe_routine_on_the_interpreter():
    blob = _blob("bpm_probe")
    assert len(blob) == 96
    # Start from zeroed counters: the first frame step is on call 128.
    bpm, frame, _ = _run(blob, bpm=0x1234, tick=0, frame=0, frame_byte=0x55)
    assert bpm == 199 and frame == 75          # out-of-range frame is pulled into 75..99 at once
    seen = []
    tick, frame_val = 0, 0
    for call in range(1, 3 * 128 + 1):
        bpm, frame, scratch = _run(blob, bpm=0xFFFF, tick=tick, frame=frame_val)
        tick = struct.unpack(">I", scratch[0:4])[0]
        frame_val = struct.unpack(">I", scratch[4:8])[0]
        assert bpm == 199
        if call % 128 == 0:
            seen.append(frame)
    assert seen == [76, 77, 78]                # the first step (call 128) moves 75 -> 76
    # garbage left in SDRAM is pulled back into 75..99 on the first step
    _, frame, _ = _run(blob, bpm=0, tick=127, frame=0xDEADBEEF)
    assert frame == 75


BAKEN = ROOT / "tools" / "baken" / "baken"
GRID_TRACKS = [("GRID128", 128), ("GRID200", 200), ("GRID400", 400), ("GRID600", 600)]

_GRID_BUILDER = """
import json, sys, wave, pathlib
import numpy as np
root, out_dir, image, baken = sys.argv[1:5]
sys.path.insert(0, root)
import make_test_stick as stick
_orig_number = stick._number
stick._number = lambda track, key, low, high=None: _orig_number(track, key, low, 999 if key == 'bpm' else high)
def render(track, path):
    rate = 44100
    t = np.arange(round(stick.track_length(track) * rate)) / rate
    phase = t % (60 / track['bpm'])
    sig = 0.65 * np.sin(2 * np.pi * 55 * phase) * np.exp(-25 * phase)
    sig += 0.2 * np.sin(2 * np.pi * 1500 * phase) * np.exp(-65 * phase)
    samples = np.rint(sig * 32767).astype('<i2')
    with wave.open(path, 'wb') as wav:
        wav.setparams((2, 2, rate, 0, 'NONE', 'not compressed'))
        wav.writeframes(np.repeat(samples, 2).tobytes())
    return True
stick.ARTIST = 'GRID ARTIST'
stick.have_ffmpeg = lambda: True
stick.render_audio = render
stick.file_name = lambda number, title: f'{title}.wav'
pathlib.Path(out_dir).mkdir(parents=True, exist_ok=True)
spec = {"device_name": "GRIDTEST", "playlist": "Grid Test", "tracks": [
    {"title": t, "bpm": b, "first_downbeat": 0, "bars": 16} for t, b in @TRACKS@]}
spec_path = pathlib.Path(out_dir) / 'spec.json'
spec_path.write_text(json.dumps(spec), encoding='utf-8')
raise SystemExit(stick.main(['--spec', str(spec_path), '--out', out_dir + '/wav-stick',
                             '--image', image, '--size-mb', '32', '--baken', baken]))
""".replace("@TRACKS@", repr(GRID_TRACKS))


def _grid_stick(tmp_path):
    """A stick with four grid tracks (128/200/400/600 BPM) built by the repo's stick builder."""
    import importlib.util
    import shutil
    if not BAKEN.is_file() or importlib.util.find_spec("pyfatfs") is None or importlib.util.find_spec("numpy") is None:
        pytest.skip("tools/baken/baken, pyfatfs and numpy are needed for the grid stick")
    image = tmp_path / "grid.img"
    subprocess.run([sys.executable, "-c", _GRID_BUILDER, str(ROOT / "scripts" / "media"),
                    str(tmp_path / "stick"), str(image), str(BAKEN)],
                   check=True, capture_output=True, text=True, timeout=300)
    return image


def _run_grid(tmp_path, flash_bytes, stick, script, tag):
    """Boot `flash_bytes` with `stick`. For each grid track the panel shows, pause and read the
    base tempo, the whole BPM and the TX staging word; after a `fader=` press take one more
    snapshot. Returns {title or 'after_fader': {addr name: value}}."""
    import test_cdj850_usb as tu
    flash = tmp_path / ("flash_%s.bin" % tag)
    flash.write_bytes(flash_bytes)
    capture = tmp_path / ("%s.scif1" % tag)
    errlog = tmp_path / ("%s.stderr" % tag)
    env = os.environ.copy()
    for k in ("CDJ850_DSP_BOOT_HLE", "CDJ850_PF16_HIGH", "CDJ850_DSP_PLAY_PF"):
        env.pop(k, None)
    env["CDJ850_PANEL_SCRIPT"] = script
    env["CDJ850_SCIF1_CAPTURE"] = str(capture)
    qmp_s, qmp_c = socket.socketpair()
    results = {}
    with open(errlog, "w") as err:
        p = subprocess.Popen(
            [tu._cdj850_binary(), "-M", "cdj850", "-display", "none", "-monitor", "none", "-serial", "none",
             "-icount", "shift=2,sleep=off",
             "-drive", f"if=pflash,format=raw,file={flash},snapshot=on",
             "-drive", f"if=none,id=usbstk,format=raw,file={stick},snapshot=on",
             "-device", "usb-storage,drive=usbstk,port=1",
             "-chardev", f"socket,id=qmp,fd={qmp_c.fileno()}", "-mon", "chardev=qmp,mode=control"],
            stdout=subprocess.DEVNULL, stderr=err, pass_fds=(qmp_c.fileno(),), env=env)
        qmp_c.close()
        qmp_s.settimeout(60)
        with qmp_s.makefile("rwb", buffering=0) as qmp:
            try:
                assert "QMP" in json.loads(qmp.readline())
                tu._qmp_execute(qmp, "qmp_capabilities")

                def snap():
                    tu._qmp_execute(qmp, "stop")
                    vals = {"base": tu._qmp_read(qmp, 0x0C9D23EC, "h"),
                            "whole": tu._qmp_read(qmp, 0x0C9D23F0, "h"),
                            "tx": tu._qmp_read(qmp, 0x0C9D0AE0, "h")}
                    tu._qmp_execute(qmp, "cont")
                    return vals

                seen, deadline, fader_done = set(), time.monotonic() + 300, False
                while time.monotonic() < deadline and not fader_done:
                    time.sleep(0.5)
                    text = errlog.read_text(errors="replace")
                    for m in re.finditer(r"mode=0x0a[^\n]*?\[(GRID\d+)\]", text):
                        if m.group(1) not in seen:
                            seen.add(m.group(1))
                            time.sleep(0.2)
                            results[m.group(1)] = snap()
                    if "input fader=" in text:
                        time.sleep(1.0)
                        results["after_fader"] = snap()
                        fader_done = True
                    if p.poll() is not None:
                        break
                tu._qmp_execute(qmp, "quit")
            except Exception:
                p.kill()
                raise
            finally:
                try:
                    p.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    p.kill()
    pages = [f for f in tu._captured_status_pages(capture) if f is not None and f[0] == 1]
    results["tx_words"] = sorted({(f[8] << 8) | f[9] for f in pages})
    return results


GRID_SCRIPT = ("6000:usb:200,7600:rot+2,9000:enter:200,11500:enter:200,17000:next:200,"
               "22500:next:200,28000:next:200,33500:tempo:200,36000:fader=0")


def _with_flash(names):
    if not FLASH.is_file():
        pytest.skip("extract/cdj850/flash_cdj850.bin is not present")
    if os.name == "nt":
        pytest.skip("QMP socket descriptor inheritance requires POSIX")
    return patch_main.patch_flash(FLASH.read_bytes(), names, "cdj850")


def test_live_grid_stock_vs_bpm999_base_and_tx(tmp_path):
    """Grids of 128/200/400/600 BPM: stock clamps the base tempo at 180.0 and the TX word at 180;
    bpm999 keeps the grid values. The TX word is the staging word the panel frame is copied from."""
    stick = _grid_stick(tmp_path)
    stock = _run_grid(tmp_path, FLASH.read_bytes(), stick, GRID_SCRIPT, "stock")
    fixed = _run_grid(tmp_path, _with_flash(["bpm999", "bpm999grid", "bpm999range"]), stick, GRID_SCRIPT, "bpm999")
    for title, bpm in GRID_TRACKS:
        assert title in stock and title in fixed, (title, sorted(stock), sorted(fixed))
        assert stock[title]["base"] == min(bpm * 10, 1800), (title, stock[title])
        assert stock[title]["tx"] == min(bpm, 180), (title, stock[title])
        assert fixed[title]["base"] == bpm * 10, (title, fixed[title])
        assert fixed[title]["whole"] == bpm, (title, fixed[title])
        assert fixed[title]["tx"] == bpm, (title, fixed[title])
    assert max(w for w in fixed["tx_words"] if w != 0xFFFF) <= 999


def test_live_grid_600_wide_plus100_shows_999(tmp_path):
    """A 600 BPM grid on WIDE (tempo_wide) at +100%: the pitched whole BPM in memory is 1200, and
    the TX word is clamped to 999."""
    stick = _grid_stick(tmp_path)
    flash = _with_flash(["bpm999", "bpm999grid", "bpm999range",
                         "tempowide", "tempowidemath1", "tempowidemath2"])
    res = _run_grid(tmp_path, flash, stick, GRID_SCRIPT, "wide")
    after = res["after_fader"]
    assert after["base"] == 6000, after
    assert after["whole"] == 1200, after            # 600 x 2.0, unclamped in memory
    assert after["tx"] == 999, after                # what the panel frame carries
    assert max(w for w in res["tx_words"] if w != 0xFFFF) == 999


@pytest.mark.skipif(not FLASH.is_file(), reason="extract/cdj850/flash_cdj850.bin is not present")
def test_live_probe_build_shows_199_and_frames_75_to_99(tmp_path):
    if os.name == "nt":
        pytest.skip("QMP socket descriptor inheritance requires POSIX")
    import test_cdj850_usb as tu
    flash = tmp_path / "flash_probe.bin"
    flash.write_bytes(patch_main.patch_flash(FLASH.read_bytes(), ["bpm999", "bpm_probe"], "cdj850"))
    usb = tmp_path / "usb.img"
    tu._build_fat16_usb_image(usb, ["ALPHA.wav", "BETA.wav"], 10)
    capture = tmp_path / "frames.scif1"
    env = os.environ.copy()
    for k in ("CDJ850_DSP_BOOT_HLE", "CDJ850_PF16_HIGH", "CDJ850_DSP_PLAY_PF"):
        env.pop(k, None)
    env["CDJ850_PANEL_SCRIPT"] = "6000:usb:200,9000:enter:200,11000:rot+1,13000:enter:200"
    env["CDJ850_SCIF1_CAPTURE"] = str(capture)
    qmp_s, qmp_c = socket.socketpair()
    with qmp_s, qmp_c, open(tmp_path / "stderr.log", "w") as err:
        p = subprocess.Popen(
            [tu._cdj850_binary(), "-M", "cdj850", "-display", "none", "-monitor", "none", "-serial", "none",
             "-icount", "shift=2,sleep=off",
             "-drive", f"if=pflash,format=raw,file={flash},snapshot=on",
             "-drive", f"if=none,id=usbstk,format=raw,file={usb},snapshot=on",
             "-device", "usb-storage,drive=usbstk,port=1",
             "-chardev", f"socket,id=qmp,fd={qmp_c.fileno()}", "-mon", "chardev=qmp,mode=control"],
            stdout=subprocess.DEVNULL, stderr=err, pass_fds=(qmp_c.fileno(),), env=env)
        qmp_c.close()
        qmp_s.settimeout(30)
        with qmp_s.makefile("rwb", buffering=0) as qmp:
            try:
                assert "QMP" in json.loads(qmp.readline())
                tu._qmp_execute(qmp, "qmp_capabilities")
                time.sleep(60)
                tu._qmp_execute(qmp, "quit")
                p.wait(timeout=10)
            except Exception:
                p.kill()
                raise
    pages = [f for f in tu._captured_status_pages(capture) if f is not None and f[0] == 1]
    assert len(pages) > 100
    assert {(f[8] << 8) | f[9] for f in pages} == {199}
    # The time code rewrites the staging frame byte between display updates, so the TX builder
    # sometimes copies a 0 (about 2% of frames in the emulator). Everything else is 75..99.
    zeros = sum(1 for f in pages if f[7] == 0)
    assert zeros * 10 < len(pages), (zeros, len(pages))
    frames = {f[7] for f in pages if f[7] != 0}
    assert frames <= set(range(75, 100)) and len(frames) >= 3
