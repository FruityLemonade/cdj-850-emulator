#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Drive a live CDJ-850 emulator for firmware tracing: stick, keys, RAM, watchpoints.

Library + small CLI used for the player control-flow trace in
docs/cdj850-modding.md section 4b. It:

  * builds a rekordbox test stick with tools/baken/baken (GRID128 with memory
    cues and hot cues A-C, plus GRID96) through scripts/media/make_test_stick.py
    (`build_stick`), the same recipe as the GRID128 fixture in
    tests/test_cdj850_usb.py;
  * boots qemu-system-sh4eb -M cdj850 under -icount with QMP on a socketpair,
    a deterministic CDJ850_PANEL_SCRIPT for the boot/load part, and the live
    panel key socket (CDJ_PANEL_KEYSOCK) for presses sent later (`Deck.key`,
    `Deck.hold`, `Deck.release`; names as in hw/cdj/boards/cdj850/xc/panel_peer.c);
  * reads RAM over QMP (`Deck.rb/rh/rw`, `Deck.dump` via pmemsave) and diffs
    snapshots (`diff`);
  * optionally talks to QEMU's gdbstub (`-gdb`) with a minimal RSP client for
    write/read watchpoints and breakpoints (`Gdb.watch`, `Gdb.cont`, `Gdb.regs`):
    there is no sh gdb on this machine, this is enough to find who writes a byte.

CLI examples:
  .venv/bin/python scripts/debug/cdj850_trace.py stick /tmp/x/export.img
  .venv/bin/python scripts/debug/cdj850_trace.py demo /tmp/x/export.img
      (load GRID128, press beat_4, print loop state bytes)

Only for emulator work; nothing here touches the firmware image.
"""
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "run"))

# RX offsets for the live key socket: RX1 byte n -> n, RX2 byte n -> 0x10+n.
# value = (word index, mask) as in panel_peer.c; word 0 = RX1 bytes 13..14,
# word k (1..4) = RX2 bytes 2k-1..2k.
KEYS = {
    "enter": (0, 0x0100), "jog_touch": (0, 0x2000),
    "usb_stop": (1, 0x0100), "play": (1, 0x0200), "cue": (1, 0x0400),
    "loop_in": (1, 0x0800), "loop_out": (1, 0x1000), "reloop": (1, 0x2000),
    "beat_1": (1, 0x0001), "beat_2": (1, 0x0002), "beat_4": (1, 0x0004),
    "beat_8": (1, 0x0008), "beat_select": (1, 0x0010), "rev": (1, 0x0020),
    "eject": (2, 0x0100), "vinyl": (2, 0x0200), "tempo": (2, 0x0800),
    "master_tempo": (2, 0x1000), "call_prev": (2, 0x0001),
    "call_next": (2, 0x0002), "delete": (2, 0x0004), "memory": (2, 0x0008),
    "time": (3, 0x0100), "prev": (3, 0x0200), "next": (3, 0x0400),
    "search_prev": (3, 0x0800), "search_next": (3, 0x1000),
    "link": (3, 0x0001), "usb": (3, 0x0002), "disc": (3, 0x0004),
    "browse": (4, 0x0100), "tag": (4, 0x0200), "info": (4, 0x0400),
    "menu": (4, 0x0800), "back": (4, 0x1000), "tag_track": (4, 0x2000),
}

DEFAULT_SPEC = {
    "device_name": "TRACE",
    "playlist": "Trace",
    "tracks": [
        {"title": "GRID128", "bpm": 128, "first_downbeat": 0, "bars": 64,
         "memory_cues": [3, 7, 12, 40], "hot_cues": [2, 5, 9]},
        {"title": "GRID96", "bpm": 96, "first_downbeat": 0, "bars": 16},
    ],
}

# Boot, USB, select GRID128 (first track of the playlist) and load it.
LOAD_SCRIPT = "6000:usb:200,7600:rot+2,9000:enter:200,11500:enter:200"


def qemu_binary():
    for b in (os.environ.get("GUI_QEMU", ""),
              str(ROOT / "qemu-build-eb" / "qemu-system-sh4eb")):
        if b and os.path.isfile(b):
            return b
    raise SystemExit("qemu-system-sh4eb not found")


def build_stick(img_path, spec=None):
    """Build a FAT rekordbox export image with baken (WAV clicks on the grid)."""
    img_path = Path(img_path)
    work = Path(tempfile.mkdtemp(prefix="cdj850stick-"))
    spec_path = work / "spec.json"
    spec_path.write_text(json.dumps(spec or DEFAULT_SPEC))
    baken = ROOT / "tools" / "baken" / "baken"
    builder = (
        "import sys, wave, numpy as np\n"
        "sys.path.insert(0, sys.argv[1])\n"
        "import make_test_stick as stick\n"
        "def render(track, path):\n"
        "    rate = 44100\n"
        "    t = np.arange(round(stick.track_length(track) * rate)) / rate\n"
        "    phase = t % (60 / track['bpm'])\n"
        "    sig = 0.65 * np.sin(2 * np.pi * 55 * phase) * np.exp(-25 * phase)\n"
        "    sig += 0.2 * np.sin(2 * np.pi * 1500 * phase) * np.exp(-65 * phase)\n"
        "    s = np.rint(sig * 32767).astype('<i2')\n"
        "    with wave.open(path, 'wb') as w:\n"
        "        w.setparams((2, 2, rate, 0, 'NONE', 'not compressed'))\n"
        "        w.writeframes(np.repeat(s, 2).tobytes())\n"
        "    return True\n"
        "stick.have_ffmpeg = lambda: True\n"
        "stick.render_audio = render\n"
        "stick.file_name = lambda n, title: f'{title}.wav'\n"
        "raise SystemExit(stick.main(['--spec', sys.argv[2], '--out', sys.argv[3],\n"
        "    '--image', sys.argv[4], '--size-mb', '32', '--baken', sys.argv[5]]))\n")
    subprocess.run([sys.executable, "-c", builder, str(ROOT / "scripts" / "media"),
                    str(spec_path), str(work / "stick"), str(img_path), str(baken)],
                   check=True, timeout=300)
    return img_path


class Gdb:
    """Minimal GDB remote-serial client for QEMU's gdbstub (SH register file)."""

    def __init__(self, port):
        self.s = socket.create_connection(("127.0.0.1", port), timeout=30)
        self.buf = b""
        self.s.sendall(b"+")

    def _send(self, pkt):
        cs = sum(pkt.encode()) & 0xFF
        self.s.sendall(b"$%s#%02x" % (pkt.encode(), cs))

    def _recv(self, timeout=30):
        self.s.settimeout(timeout)
        while True:
            i = self.buf.find(b"$")
            j = self.buf.find(b"#", i + 1) if i >= 0 else -1
            if i >= 0 and j >= 0 and len(self.buf) >= j + 3:
                pkt = self.buf[i + 1:j].decode(errors="replace")
                self.buf = self.buf[j + 3:]
                self.s.sendall(b"+")
                return pkt
            self.buf += self.s.recv(65536)

    def cmd(self, pkt, timeout=30):
        self._send(pkt)
        return self._recv(timeout)

    def regs(self):
        """r0..r15, pc, pr, gbr, vbr, mach, macl, sr as ints."""
        g = self.cmd("g")
        vals = [int(g[i:i + 8], 16) for i in range(0, len(g) - 7, 8)]
        names = ["r%d" % i for i in range(16)] + ["pc", "pr", "gbr", "vbr", "mach", "macl", "sr"]
        return dict(zip(names, vals))

    def mem(self, addr, n):
        return bytes.fromhex(self.cmd("m%x,%x" % (addr, n)))

    def watch(self, addr, n=1, kind=2):
        """kind 2 = write, 3 = read, 4 = access; 0 = sw break, 1 = hw break."""
        return self.cmd("Z%d,%x,%x" % (kind, addr, n))

    def unwatch(self, addr, n=1, kind=2):
        return self.cmd("z%d,%x,%x" % (kind, addr, n))

    def cont(self, timeout=60):
        return self.cmd("c", timeout)

    def stop(self):
        self.s.sendall(b"\x03")
        return self._recv()

    def detach(self):
        try:
            self._send("D")
            self._recv(5)
        except OSError:
            pass
        self.s.close()


class Deck:
    def __init__(self, usb_image, script=LOAD_SCRIPT, flash=None, gdb_port=None,
                 log=None, extra_env=None, writable=False):
        """writable=True: the flash file (a copy the caller owns) and the copy of
        the stick (self.usb) take the guest's writes (snapshot=off), so settings
        and ANLZ writes can be read back after close()."""
        flash = flash or ROOT / "extract" / "cdj850" / "flash_cdj850.bin"
        self.tmp = Path(tempfile.mkdtemp(prefix="cdj850trace-"))
        usb = self.tmp / "usb.img"
        shutil.copyfile(usb_image, usb)
        self.usb = usb
        snap = "off" if writable else "on"
        self.keysock = "cdj850trace-%d" % os.getpid()
        env = os.environ.copy()
        for k in ("CDJ850_DSP_BOOT_HLE", "CDJ850_PF16_HIGH", "CDJ850_DSP_PLAY_PF"):
            env.pop(k, None)
        env["CDJ850_PANEL_SCRIPT"] = script
        env["CDJ_PANEL_KEYSOCK"] = self.keysock
        env.update(extra_env or {})
        qs, qc = socket.socketpair()
        args = [qemu_binary(), "-M", "cdj850", "-display", "none", "-monitor", "none",
                "-serial", "none", "-icount", "shift=2,sleep=off",
                "-drive", f"if=pflash,format=raw,file={flash},snapshot={snap}",
                "-drive", f"if=none,id=usbstk,format=raw,file={usb},snapshot={snap}",
                "-device", "usb-storage,drive=usbstk,port=1",
                "-chardev", f"socket,id=qmp,fd={qc.fileno()}",
                "-mon", "chardev=qmp,mode=control"]
        if gdb_port:
            args += ["-gdb", f"tcp:127.0.0.1:{gdb_port}"]
        self.p = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                                  pass_fds=(qc.fileno(),), env=env)
        qc.close()
        qs.settimeout(60)
        self.qs = qs
        self.qmp = qs.makefile("rwb", buffering=0)
        self.lines = []
        self.log = log
        self._lock = threading.Lock()
        threading.Thread(target=self._pump, daemon=True).start()
        assert b"QMP" in self.qmp.readline()
        self.q("qmp_capabilities")

    def _pump(self):
        for raw in self.p.stderr:
            line = raw.decode(errors="replace").rstrip("\n")
            with self._lock:
                self.lines.append(line)
            if self.log:
                self.log.write(line + "\n")
                self.log.flush()

    def q(self, command, **arguments):
        self.qmp.write(json.dumps({"execute": command, "arguments": arguments}).encode() + b"\n")
        while True:
            r = json.loads(self.qmp.readline())
            if "error" in r:
                raise RuntimeError((command, r))
            if "return" in r:
                return r["return"]

    def hmp(self, line):
        return self.q("human-monitor-command", **{"command-line": line})

    def _x(self, addr, unit):
        return int(self.hmp(f"xp /1{unit}x {addr:#x}").split(":")[-1].strip(), 16)

    def rb(self, a): return self._x(a, "b")
    def rh(self, a): return self._x(a, "h")
    def rw(self, a): return self._x(a, "w")

    def dump(self, addr, size):
        path = self.tmp / ("d%x.bin" % addr)
        self.q("pmemsave", val=addr, size=size, filename=str(path))
        return path.read_bytes()

    def wait_line(self, *needles, timeout=120, start=0):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            with self._lock:
                for i in range(start, len(self.lines)):
                    if all(n in self.lines[i] for n in needles):
                        return i, self.lines[i]
            if self.p.poll() is not None:
                break
            time.sleep(0.1)
        raise TimeoutError(needles)

    def _send(self, name, dur, op):
        import cdj_panelsock
        word, mask = KEYS[name]
        if word == 0:
            off, val = (13, mask >> 8) if mask >> 8 else (14, mask)
        else:
            off, val = ((0x10 + 2 * word - 1, mask >> 8) if mask >> 8
                        else (0x10 + 2 * word, mask))
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.sendto(f"0x{off:02x}:{val}:{dur}:{op}".encode(),
                 (cdj_panelsock.HOST, cdj_panelsock.port_for(self.keysock)))
        s.close()

    def key(self, name, dur=200):
        """Press for dur virtual ms."""
        self._send(name, dur, "or")

    def hold(self, name):
        self._send(name, 0, "hold")

    def release(self, name):
        self._send(name, 0, "rel")

    def close(self):
        try:
            self.qmp.write(b'{"execute":"quit"}\n')
            self.p.wait(timeout=5)
        except Exception:
            self.p.kill()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


def diff(a, b, base, limit=200):
    """List (addr, old, new) for differing bytes."""
    out = []
    for i, (x, y) in enumerate(zip(a, b)):
        if x != y:
            out.append((base + i, x, y))
            if len(out) >= limit:
                break
    return out


def main(argv):
    if len(argv) >= 2 and argv[0] == "stick":
        build_stick(argv[1])
        return
    if len(argv) >= 2 and argv[0] == "demo":
        with Deck(argv[1]) as d:
            d.wait_line("mode=0x0a", "[GRID128]", timeout=300)
            time.sleep(3)
            d.key("beat_4")
            time.sleep(2)
            for a in (0x0CE6D25F, 0x0C7F645C):
                print(hex(a), hex(d.rh(a) if a == 0x0C7F645C else d.rb(a)))
        return
    print(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
