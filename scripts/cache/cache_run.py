#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Run the CDJ-850 headless under the SH7263 cache model and print its report.

    scripts/cache/cache_run.py [--flash FLASH] [--usb IMG] [--script PANEL]
                               [--seconds N] [--until TEXT] [--out PREFIX]

The deck runs under -icount (virtual time = instructions, so a panel script
lands at the same firmware state every run) with the cdj850_cache plugin
(built on first use by build_cache_plugin.sh). After --seconds of host time,
or once --until appears on stderr, QEMU is stopped; the plugin then writes
<out>-report.txt (coherency findings and totals) and <out>-profile.csv
(per-pc hit/miss counts for scripts/ghidra/apply_cache_profile.py).

--script is a CDJ850_PANEL_SCRIPT ("ms:key:hold,..."), for example
"6000:usb:200,9000:enter:200,11000:rot+1,13000:enter:200,19000:play:200"
to browse the stick, load the second entry and play it (DMA traffic).
"""
import argparse
import os
from pathlib import Path
import platform
import selectors
import subprocess
import sys
import time

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent


def plugin_path(build_dir):
    ext = {"Darwin": "dylib", "Windows": "dll"}.get(platform.system(), "so")
    return Path(build_dir) / f"libcdj850_cache.{ext}"


def ensure_plugin(path):
    src = HERE / "cdj850_cache.c"
    if not path.is_file() or path.stat().st_mtime < src.stat().st_mtime:
        subprocess.run(["bash", str(HERE / "build_cache_plugin.sh"), str(path)], check=True,
                       stdout=subprocess.DEVNULL)
    return path


def main(argv=None):
    build = os.environ.get("QEMU_EB_BUILD", os.path.expanduser("~/qemu-build-eb"))
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--qemu", default=os.environ.get("GUI_QEMU", os.path.join(build, "qemu-system-sh4eb")))
    ap.add_argument("--plugin", type=Path, default=plugin_path(build))
    ap.add_argument("--flash", type=Path, default=ROOT / "extract" / "cdj850" / "flash_cdj850.bin")
    ap.add_argument("--usb", type=Path, help="raw USB stick image (attached read-only)")
    ap.add_argument("--script", help="CDJ850_PANEL_SCRIPT for the run")
    ap.add_argument("--seconds", type=float, default=60.0, help="host seconds to run (default 60)")
    ap.add_argument("--until", help="stop early once this text appears on stderr")
    ap.add_argument("--out", default="cdj850-cache", help="output prefix (default ./cdj850-cache)")
    ap.add_argument("--no-icount", action="store_true", help="run on the host clock instead")
    ap.add_argument("--quiet", action="store_true", help="no live findings on stderr")
    a = ap.parse_args(argv)

    plugin = ensure_plugin(a.plugin)
    out = Path(a.out).resolve()
    cmd = [a.qemu, "-M", "cdj850", "-display", "none", "-monitor", "none", "-serial", "none",
           "-drive", f"if=pflash,format=raw,file={a.flash},snapshot=on",
           "-plugin", f"{plugin},out={out}" + (",quiet=on" if a.quiet else "")]
    if not a.no_icount:
        cmd += ["-icount", "shift=2,sleep=off"]
    if a.usb:
        cmd += ["-drive", f"if=none,id=usbstk,format=raw,file={a.usb},snapshot=on",
                "-device", "usb-storage,drive=usbstk,port=1"]
    env = os.environ.copy()
    # The last four write guest memory from helpers the model cannot see.
    for k in ("CDJ850_DSP_BOOT_HLE", "CDJ850_PF16_HIGH", "CDJ850_DSP_PLAY_PF", "CDJ850_PANEL_SCRIPT",
              "CDJ_NATIVE_COPY", "CDJ_PPOKE", "CDJ_PSTORE", "CDJ_RPOKE"):
        env.pop(k, None)
    if a.script:
        env["CDJ850_PANEL_SCRIPT"] = a.script

    p = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, env=env)
    t0 = time.monotonic()
    seen = ""
    with selectors.DefaultSelector() as sel:
        sel.register(p.stderr, selectors.EVENT_READ)
        try:
            while time.monotonic() - t0 < a.seconds and p.poll() is None:
                for key, _ in sel.select(timeout=0.5):
                    chunk = os.read(key.fileobj.fileno(), 65536).decode(errors="replace")
                    if not chunk:
                        break
                    for ln in chunk.splitlines(True):
                        if ln.startswith("cdj850-cache:"):
                            sys.stderr.write(ln)
                    seen = (seen + chunk)[-65536:]
                if a.until and a.until in seen:
                    break
        finally:
            if p.poll() is None:
                p.terminate()
            try:
                p.wait(timeout=30)
            except subprocess.TimeoutExpired:
                p.kill()
                p.wait()
    report = Path(f"{out}-report.txt")
    if not report.is_file():
        sys.stderr.write(seen[-4000:])
        sys.exit("cache_run: QEMU exited without a report (exit %s)" % p.returncode)
    text = report.read_text()
    print(text.split("\nkind,pc,")[0].rstrip())
    print(f"\nreport:  {report}\nprofile: {out}-profile.csv  ({time.monotonic() - t0:.0f} s host time)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
