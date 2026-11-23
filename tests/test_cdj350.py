# SPDX-License-Identifier: GPL-2.0-or-later
"""End-to-end checks of the cdj350 machine with the CDJ-350 v1.14 firmware
(docs/cdj350.md). They run under -icount so the firmware's timing (the 1 s
source-key hold, the 10 s RELOOP hold) holds on a busy host."""
import array
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

from test_cdj850_usb import _cdj850_binary

ROOT = Path(__file__).resolve().parent.parent
ICOUNT = "shift=2,align=off,sleep=off"


def _binary():
    binary = _cdj850_binary()
    machines = subprocess.run([binary, "-M", "help"], capture_output=True,
                              text=True, timeout=30).stdout
    if "cdj350" not in machines:
        pytest.skip("qemu-system-sh4eb has no cdj350 machine")
    return binary


def _flash():
    flash = ROOT / "extract" / "cdj350" / "flash_cdj350.bin"
    if not flash.is_file():
        pytest.skip("flash_cdj350.bin not prepared "
                    "(scripts/firmware/prepare_firmware.sh --model cdj350 --install)")
    return flash


def _start(tmp_path, script, extra_env=None, extra_args=()):
    env = dict(os.environ)
    env["CDJ850_PANEL_SCRIPT"] = script
    env.update(extra_env or {})
    log_path = tmp_path / "qemu.log"
    cmd = [_binary(), "-M", "cdj350", "-display", "none", "-monitor", "none",
           "-serial", "none", "-icount", ICOUNT,
           "-drive", f"if=pflash,file={_flash()},format=raw,snapshot=on",
           *extra_args]
    log = open(log_path, "wb")
    proc = subprocess.Popen(cmd, env=env, stdout=subprocess.DEVNULL, stderr=log)
    return proc, log, log_path


def _stop(proc, log):
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=5)
    log.close()


def _wait_for_text(log_path, proc, wanted, timeout):
    """Wait until every string in `wanted` has appeared in the log's
    cdj350-panel text lines; return those lines."""
    deadline = time.monotonic() + timeout
    lines = []
    while time.monotonic() < deadline:
        assert proc.poll() is None, log_path.read_text(errors="replace")
        lines = [l for l in log_path.read_text(errors="replace").splitlines()
                 if "cdj350-panel: text" in l]
        if all(any(w in l for l in lines) for w in wanted):
            return lines
        time.sleep(0.5)
    pytest.fail("missing %r in:\n%s" % (wanted, "\n".join(lines)))


def test_cdj350_boots_to_no_disc_and_shows_versions(tmp_path):
    """RELOOP held at power-on shows the MAIN version after about 10 s, and
    BPM LOCK steps to the panel and DSP versions (service manual 6.1)."""
    proc, log, log_path = _start(
        tmp_path, "0:+reloop,12000:-reloop,14000:bpm_lock:300,16000:bpm_lock:300")
    try:
        lines = _wait_for_text(log_path, proc,
                               ["NO DISC", "Main 1.14", "Disp ", "DSP "], 240)
    finally:
        _stop(proc, log)
    assert any("EJECT" in l for l in lines)


def test_cdj350_pc_mode_enumerates_and_sends_midi(tmp_path):
    """Holding PC enters CONTROL MODE; USB-B enumerates as the CDJ-350
    (pid 0x0156) with MIDI and HID, and PLAY sends MIDI note 0 on and off."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("127.0.0.1", 0))
    sock.settimeout(0.2)
    proc, log, log_path = _start(
        tmp_path, "4000:pc:1500,9000:play:300",
        {"CDJ_USBF_HOST": str(sock.getsockname()[1])})
    statuses, midi = [], []
    try:
        deadline = time.monotonic() + 240
        while time.monotonic() < deadline:
            assert proc.poll() is None, log_path.read_text(errors="replace")
            try:
                data, _ = sock.recvfrom(4096)
            except socket.timeout:
                continue
            if data[0] == 0:
                statuses.append(data[1:].decode("ascii", "replace"))
            elif data[0] == 0x83:
                midi.append(data[9:])
            if (bytes.fromhex("0990007f") in midi
                    and bytes.fromhex("09900000") in midi):
                break
        text = log_path.read_text(errors="replace")
    finally:
        sock.close()
        _stop(proc, log)
    assert "CONTROL MODE" in text
    assert any("configured vid=08e4 pid=0156" in s and "midi_in=83" in s
               and "hid_in=87" in s for s in statuses), statuses
    assert bytes.fromhex("0990007f") in midi and bytes.fromhex("09900000") in midi, midi


def _repeat_period(samples, t0, t1, rate=44100, hop=220):
    """Lag in seconds (0.15..0.6) at which the 5 ms level envelope of
    samples[t0:t1] repeats best."""
    seg = samples[int(t0 * rate):int(t1 * rate)]
    env = [sum(abs(x) for x in seg[i:i + hop]) / hop
           for i in range(0, len(seg) - hop, hop)]
    mean = sum(env) / len(env)
    env = [x - mean for x in env]
    best = max(range(int(0.15 * rate / hop), int(0.6 * rate / hop)),
               key=lambda k: sum(env[i] * env[i + k] for i in range(len(env) - k)))
    return best * hop / rate


def test_cdj350_loop_divide_replays_half_of_each_beat(tmp_path):
    """BEAT LOOP starts a 4-beat loop on the 128 BPM stick track; one press of
    LOOP DIVIDE - makes the audio feeder replay the first half of every beat
    (docs/cdj350.md), so the kick repeats every half beat instead of every
    beat."""
    stick = ROOT / "extract" / "wavstick.img"
    if not stick.is_file():
        pytest.skip("extract/wavstick.img (128 BPM WAV stick) not prepared")
    wav = tmp_path / "out.wav"
    proc, log, log_path = _start(
        tmp_path,
        "8000:usb:1500,11000:rot+2,13000:push,16000:push,"
        "32000:beat_loop:300,40000:loop_divide_minus:300",
        {"CDJ_DSP_AUDIO": "1", "CDJ_DSP_AUDIO_ASRC": "0"},
        ["-audio", f"wav,path={wav}",
         "-drive", f"if=none,id=usbstk,file={stick},format=raw,snapshot=on",
         "-device", "usb-storage,drive=usbstk,port=1"])
    want = 44 + 50 * 44100 * 4
    try:
        deadline = time.monotonic() + 400
        while not (wav.is_file() and wav.stat().st_size >= want):
            assert proc.poll() is None, log_path.read_text(errors="replace")
            assert time.monotonic() < deadline, "audio stopped at %d bytes" % (
                wav.stat().st_size if wav.is_file() else 0)
            time.sleep(1)
    finally:
        _stop(proc, log)
    pcm = array.array("h")
    raw = wav.read_bytes()[44:want]
    pcm.frombytes(raw[:len(raw) // 4 * 4])
    if sys.byteorder == "big":
        pcm.byteswap()
    left = pcm[0::2]
    beat = 60 / 128
    assert abs(_repeat_period(left, 34, 39.5) - beat) < 0.03
    assert abs(_repeat_period(left, 41, 49) - beat / 2) < 0.02
