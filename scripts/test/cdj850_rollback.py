#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Install a modded C850MAIN.UPD on an emulated stock CDJ-850 and roll it back.

The closing check of the CDJ-850 firmware-mod series (feasibility study section 6:
a modded deck must always be able to go back to stock). Everything runs the way a
user would do it on the deck, through the firmware's own service-mode USB updater
(power on holding USB SELECT + RELOOP, `C850MAIN.UPD` in the stick's root):

  1. build    patch_update.py the mods into C850MAIN.UPD (or take --upd), run
              validate.py; the file carries patch_update's default mod label
              "0.13" (first digit 0: the updater always writes it)
  2. same     negative control: the same file relabelled to the running 1.13
              (what patch_update --keep-version gives) is skipped by the updater
  3. install  service update with the file as built on the stock deck: flash
              0x060000-0x3EFFFF must equal the file padded with 0xFF
  4. modded   boot it with a rekordbox stick: loader payload in 0x0DE00000, every
              mod-written range of MAIN in SDRAM as built, load GRID128 and play,
              BEAT 4 loop + CALL < (halve), hot-cue mode on (saved to flash), the
              service gate open (0x0C1E109A and 0x0C1E10AA both 0), and a gdb
              breakpoint on a mod routine (hot_cues_keys) hit as a positive control
  5. rollback service update with the stock C850MAIN.UPD on the modded deck, with
              gdb breakpoints on every function of the payload, every halfword of the
              mods' cave routines (not the trampolines: they hold the moved stock
              code) and every service gate (__gate_*); each hit is logged and the
              set is re-armed when flashing starts. ZERO mod-code hits are allowed
              before and during the update (the gates run and take the stock path).
              Flash 0x060000-0x3EFFFF must then equal the stock file padded
              with 0xFF (= a stock-installed deck), boot region unchanged
  6. stock    boot the rolled-back deck: no payload in 0x0DE00000, every
              mod-written range back to stock in SDRAM, GRID128 plays, and the
              settings record's +0x22 byte left by hot_cues is reported

    .venv/bin/python scripts/test/cdj850_rollback.py [--work DIR] [--upd MOD.UPD]
            [--mods m1 m2 ...] [--no-gdb] [--skip-same-version]

Prints one PASS/FAIL line per check and a JSON summary (DIR/summary.json); exit 1 on
any FAIL. About 10 minutes under -icount on an M-series Mac. Emulator only.
"""
import argparse
import json
import os
import shutil
import socket
import struct
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for p in ("mods", "scripts/firmware", "scripts/debug"):
    sys.path.insert(0, str(ROOT / p))
import c850_repack  # noqa: E402
import c850loader as L  # noqa: E402
import cdj850_trace as tr  # noqa: E402
import patch_main  # noqa: E402
import payloadbuild  # noqa: E402
import sigpatch  # noqa: E402

PY = str(ROOT / ".venv" / "bin" / "python") if (ROOT / ".venv" / "bin" / "python").is_file() else sys.executable
STOCK_UPD = ROOT / "C850MAIN.UPD"
STOCK_FLASH = ROOT / "extract" / "cdj850" / "flash_cdj850.bin"
DEFAULT_MODS = ["auto_standby", "bpm999", "bpm_decimals", "quant_loops", "loop_resize", "hot_cues",
                "tempo_wide", "flac"]
SERVICE_USB = "0:+usb,0:+reloop,8000:-usb,8000:-reloop"    # service manual mode 4
MAIN_BASE = 0x0C000000
RUN_VERSION = 0x0CE6D242       # the running version the updater compares (0x0C08EE7C)
SETTINGS_SECTOR = 0x3F8000
GATE = patch_main.PATCH_SETS["cdj850"].profile.gate   # 0x0C1E109A update mode, 0x0C1E10AA keys pending
TICK_MS = 0x0CE49F54           # u32 +1 per MAIN 1 ms tick
PAYLOAD_SRC = L.BLOB_ADDR - 0x0C000000 + L.STUB_SIZE + L.HEADER + 0x100   # 256 payload bytes to look for     # settings/log sectors 0x3F8000-0x3FFFFF, never written by an update
STATE_USB, FRAME, LOOP_IN, OUT_REC, OUT_REQ = 0x0CE6D25F, 0x0C7F647C, 0x0C7F6498, 0x0C7F64B0, 0x0C7F64DC
LOOP_ACTIVE, MODE_BYTE = 0x0CE6D0ED, 0x0C9DA23C + 0x22

results = []


def check(name, ok, detail=""):
    results.append({"check": name, "ok": bool(ok), "detail": detail})
    print("%s %s%s" % ("PASS" if ok else "FAIL", name, (": " + str(detail)) if detail else ""), flush=True)
    return ok


def padded(upd):
    """What mode 6 leaves in flash 0x060000-0x3EFFFF: the 0xFF-filled buffer with the file in it."""
    body = upd[0x60000:]
    return body + b"\xff" * (c850_repack.FLASH_WRITE_END - 0x60000 - len(body))


def changed_ranges(a, b, gap=8):
    out, i, n = [], 0, min(len(a), len(b))
    while i < n:
        if a[i] != b[i]:
            j = i
            while j < n and (a[j] != b[j] or any(a[k] != b[k] for k in range(j, min(n, j + gap)))):
                j += 1
            out.append((i, j))
            i = j
        else:
            i += 1
    return out


def update_stick(work, upd_path, name):
    d = work / (name + ".dir")
    shutil.rmtree(d, ignore_errors=True)
    d.mkdir(parents=True)
    shutil.copyfile(upd_path, d / "C850MAIN.UPD")
    img = work / (name + ".img")
    subprocess.run([PY, str(ROOT / "scripts" / "media" / "make_usb_image.py"), str(d), str(img), "16"],
                   check=True, capture_output=True)
    return img


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def lines_from(d, i):
    with d._lock:
        return d.lines[i:]


def service_update(work, flash, stick, tag, breakpoints=(), timeout=900):
    """Boot `flash` (written in place) into the service USB update with `stick`; returns
    (outcome, interesting log lines, breakpoint stop or None, running version, seconds,
    hit log, gate bytes at the end)."""
    log = open(work / ("%s.log" % tag), "w")
    port = free_port() if breakpoints else None
    t0 = time.monotonic()
    armed_tick = None
    with tr.Deck(stick, script=SERVICE_USB, flash=flash, writable=True, log=log, gdb_port=port) as d:
        g = None
        if breakpoints:
            g = tr.Gdb(port)
            bad = [a for a in breakpoints if g.cmd("Z0,%x,2" % a) != "OK"]
            assert not bad, "breakpoints refused at %s" % [hex(a) for a in bad[:4]]
            armed_tick = d.rw(TICK_MS)       # MAIN's 1 ms tick when the set was armed (0 = not started)
            g._send("c")
        hits, hit_log, outcome, rearmed = [], [], "timeout", False
        end = time.monotonic() + timeout
        while time.monotonic() < end and d.p.poll() is None:
            text = "\n".join(lines_from(d, 0))
            if "COMPLETE" in text:
                outcome = "complete"
                break
            if "failed" in text.lower() and "update" in text.lower():
                outcome = "failed"
                break
            if g is not None and not rearmed and "NOW UPDATING" in text:
                # flashing starts: arm every breakpoint again, so a routine that already
                # stopped once before the update is caught if it also runs during it
                g.stop()
                for x in breakpoints:
                    if x in hits:
                        g.cmd("Z0,%x,2" % x)
                hits = []
                rearmed = True
                g._send("c")
            if g is not None:
                g.s.settimeout(0.01)
                try:
                    g.buf += g.s.recv(65536)
                except (socket.timeout, BlockingIOError):
                    pass
                if b"$T" in g.buf or b"$S" in g.buf:   # a hit: note it, drop that one, go on
                    g._recv(5)
                    pc = g.regs()["pc"]
                    shown = [ln for ln in lines_from(d, 0) if "display mode" in ln]
                    phase = ("update" if any("NOW UPDATING" in ln for ln in shown) else "before") \
                        if not any("COMPLETE" in ln for ln in shown) else "after"
                    hits.append(pc)
                    hit_log.append({"pc": hex(pc), "phase": phase,
                                    "last_display": shown[-1].split("] ")[-1][-60:] if shown else ""})
                    g.cmd("z0,%x,2" % pc)
                    g._send("c")
            time.sleep(0.5)
        time.sleep(1.0)            # let the last sector's status settle before quitting
        ver, gate = None, None
        if g is not None:
            try:
                g.stop()
            except OSError:
                pass
        try:
            ver = d.dump(RUN_VERSION, 4)
            gate = [d.rb(a) for a in GATE]
        except Exception:
            pass
        if g is not None:
            for a in breakpoints:
                if a not in hits:
                    g.cmd("z0,%x,2" % a)
            g.detach()
        keep = [ln for ln in lines_from(d, 0) if any(k in ln for k in ("UPDAT", "COMPLETE", "Connect", "failed",
                                                                       "version", "Ver"))]
    log.close()
    (work / ("%s.hits.json" % tag)).write_text(json.dumps({"armed_at_tick_ms": armed_tick, "hits": hit_log},
                                                           indent=1))
    if breakpoints:
        print("INFO %s: %d breakpoints armed at MAIN tick %s ms" % (tag, len(breakpoints), armed_tick), flush=True)
    return outcome, keep[-12:], hits, ver, round(time.monotonic() - t0, 1), hit_log, gate


def ram_ranges(d, ranges):
    return {a: d.dump(MAIN_BASE + a, b - a) for a, b in ranges}


def ensure_playing(d):
    for _ in range(4):
        st = d.rb(STATE_USB)
        if st == 3:
            return True
        d.key("reloop" if st == 4 else "play")
        time.sleep(2.5)
    return d.rb(STATE_USB) == 3


def moving(d, frames=40, timeout=20.0):
    """Frames played until `frames` have passed or `timeout` s of host time (the emulator's
    speed follows the host's load, so wait on the frame counter, not the clock)."""
    a, end = d.rw(FRAME), time.monotonic() + timeout
    while d.rw(FRAME) - a < frames and time.monotonic() < end:
        time.sleep(0.1)
    return d.rw(FRAME) - a


def until(pred, timeout=10.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.1)
    return pred()


def loop_regs(d):
    return {"state": d.rb(STATE_USB), "in": d.rw(LOOP_IN), "out": d.rw(OUT_REC), "req": d.rw(OUT_REQ)}


def boot_and_play(work, flash, stick, tag, mod_ranges, want, modded, payload_entry=None):
    """Boot, compare the mod-written MAIN ranges in SDRAM with `want`, load GRID128 and play."""
    log = open(work / ("%s.log" % tag), "w")
    port = free_port() if payload_entry else None
    out = {}
    with tr.Deck(stick, flash=flash, writable=True, log=log, gdb_port=port) as d:
        g = None
        if payload_entry:
            g = tr.Gdb(port)
            g._send("c")
        d.wait_line("mode=0x0a", "[GRID128]", timeout=300)
        out["version"] = d.dump(RUN_VERSION, 4).decode("latin1")
        out["gate"] = [d.rb(a) for a in GATE]
        check(tag + ": service gate open in a normal boot (%s all 0)" % ", ".join("%#x" % a for a in GATE),
              out["gate"] == [0] * len(GATE), out["gate"])
        region = d.dump(L.REGION, L.REGION_END - L.REGION)
        problems = L.check_ram(region)
        guard = struct.unpack_from(">I", region, 0)[0]
        if modded:
            # the running payload changes its own .bss, so the header sum no longer
            # matches; everything below .bss must still be the built image
            blob, bss = want["payload"], want["bss"]
            got = region[L.HEADER:L.HEADER + len(blob)]
            diff = [i for i in range(len(blob)) if got[i] != blob[i]]
            outside = [hex(L.REGION + L.HEADER + i) for i in diff if not bss[0] <= i < bss[1]]
            rest = [x for x in problems if not x.startswith("payload sum")]
            check(tag + ": loader payload in 0x0DE00000: guards and header verify, text/rodata/data "
                  "byte-identical to the build (%d bytes differ, all in .bss)" % len(diff),
                  not rest and not outside, rest + outside[:6])
        else:
            check(tag + ": no loader payload in 0x0DE00000", problems and guard != L.GUARD_LO
                  and want["payload"] not in region, "first word %#010x" % guard)
        got = ram_ranges(d, mod_ranges)
        # the stub and blob sit in statics MAIN's start-up clears and then uses (the
        # loader has copied them to 0x0DE00000 by then): not compared
        blob_lo = L.BLOB_ADDR - MAIN_BASE
        bad = [hex(MAIN_BASE + a) for a, b in mod_ranges if got[a] != want["image"][a:b]
               and not (blob_lo <= a < blob_lo + want["blob_len"])]
        check(tag + ": %d mod-written ranges of MAIN in SDRAM are %s" % (len(mod_ranges), "modded" if modded else "stock"),
              not bad, bad[:6])
        time.sleep(1.5)
        playing = ensure_playing(d)
        adv = moving(d)
        check(tag + ": GRID128 loaded and playing", playing and adv >= 40,
              "state %d, +%d frames" % (d.rb(STATE_USB), adv))
        for _ in range(2):                    # a press can be missed right after a state change
            d.key("beat_4")
            if until(lambda: d.rb(STATE_USB) == 4 and d.rw(OUT_REC) > d.rw(LOOP_IN)):
                break
        time.sleep(0.5)
        a = loop_regs(d)
        half = lambda: abs(2 * (d.rw(OUT_REQ) - d.rw(LOOP_IN)) - (a["out"] - a["in"])) <= 2
        for _ in range(2):
            d.key("call_prev")
            if until(half, 8.0 if modded else 4.0):
                break
        b = loop_regs(d)
        out["loop"] = {"beat4": a, "call_prev": b}
        if modded:
            len4, len2 = a["out"] - a["in"], b["req"] - b["in"]
            check(tag + ": BEAT 4 loop, CALL < halves it", a["state"] == 4 and b["state"] == 4 and len4 > 0
                  and abs(2 * len2 - len4) <= 2, "4-beat %d frames -> %d after CALL <" % (len4, len2))
        else:
            check(tag + ": BEAT 4 loop, CALL < does not resize it (stock)", a["state"] == 4
                  and b["req"] - b["in"] != (a["out"] - a["in"]) // 2, out["loop"])
        if d.rb(STATE_USB) == 4:
            d.key("reloop")
            time.sleep(1.5)
        out["history_name"] = d.dump(MODE_BYTE - 0x1F, 32).hex()
        before = d.rb(MODE_BYTE)
        d.hold("beat_select")                 # hot_cues: BEAT SELECT + RELOOP toggles the mode
        time.sleep(0.6)
        d.key("reloop")
        time.sleep(0.8)
        d.release("beat_select")
        time.sleep(3.0)                       # the settings log write
        out["mode_byte"] = (before, d.rb(MODE_BYTE))
        if modded:
            check(tag + ": hot-cue mode toggled on (settings +0x22 = 0x5A)", out["mode_byte"][1] == 0x5A,
                  "0x%02x -> 0x%02x" % out["mode_byte"])
        if g is not None:                     # positive control: a mod routine runs on a key press
            g.stop()
            g.cmd("Z0,%x,2" % payload_entry)
            g._send("c")
            d.key("beat_1")
            g.s.settimeout(30)
            stop = g._recv(30)
            pc = g.regs()["pc"]
            g.cmd("z0,%x,2" % payload_entry)
            check(tag + ": gdb breakpoint on a mod routine (%#x) hits on a key press (positive control)"
                  % payload_entry,
                  stop.startswith(("T", "S")) and pc == payload_entry, "%s pc=%#x" % (stop[:3], pc))
            g.detach()
    log.close()
    return out


def patch_update_names(mod):
    """The registry names a mod folder name stands for (as patch_update.py expands it)."""
    folders = __import__("modelfw").routine_folders("cdj850")
    return [n for n, f in folders.items() if f == mod]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--work", default=os.path.join(os.environ.get("TMPDIR", "/tmp"), "cdj850-rollback"))
    ap.add_argument("--upd", help="a modded C850MAIN.UPD (default: build --mods)")
    ap.add_argument("--mods", nargs="+", default=DEFAULT_MODS)
    ap.add_argument("--no-gdb", action="store_true")
    ap.add_argument("--skip-same-version", action="store_true")
    a = ap.parse_args(argv)
    results.clear()
    work = Path(a.work)
    work.mkdir(parents=True, exist_ok=True)
    t0 = time.monotonic()

    # 1. build: patch_update's own output, label and all
    mod_upd = Path(a.upd) if a.upd else work / "ALL.UPD"
    if not a.upd:
        subprocess.run([PY, str(ROOT / "mods" / "patch_update.py"), "--model", "cdj850", str(STOCK_UPD),
                        str(mod_upd)] + a.mods, check=True, capture_output=True)
    v = subprocess.run([PY, str(ROOT / "mods" / "validate.py"), "--model", "cdj850", "--stock", str(STOCK_UPD),
                        str(mod_upd)], capture_output=True, text=True)
    check("validate.py on the modded update", v.returncode == 0 and "all checks passed" in v.stdout + v.stderr)
    stock, mod = STOCK_UPD.read_bytes(), mod_upd.read_bytes()
    label = c850_repack.get_version(mod)
    check("patch_update labels the file %s (default mod label): installs over 1.13 and over itself, stock "
          "installs over it" % label, label == c850_repack.mod_label("1.13")
          and c850_repack.installs_over(label, "1.13") and c850_repack.installs_over(label, label)
          and c850_repack.installs_over("1.13", label), label)
    same_label = work / "ALL-1.13.UPD"            # the negative control: what --keep-version gives
    same_label.write_bytes(c850_repack.set_version(mod, "1.13"))
    stock_img, mod_img = c850_repack.unpack_main(stock), c850_repack.unpack_main(mod)
    ranges = changed_ranges(stock_img, mod_img)
    hdr = L.BLOB_ADDR - MAIN_BASE + L.STUB_SIZE
    pay_len = struct.unpack_from(">I", mod_img, hdr + 8)[0]
    pay_blob = mod_img[hdr + L.HEADER:hdr + L.HEADER + pay_len]
    blob_len = max(0x4000, L.STUB_SIZE + L.HEADER + pay_len + 8)    # stub, header, payload (padded)
    keep = work / "payload.build"
    built = payloadbuild.build([m for m in a.mods if payloadbuild.sources(m, "cdj850")], "cdj850", keep=str(keep))
    assert built.blob == pay_blob, "the payload in the update is not what payloadbuild makes for --mods"
    pay_bss = (built.symbols["__bss_start"] - L.REGION - L.HEADER, built.symbols["__bss_end"] - L.REGION - L.HEADER)
    # every function of the payload (mod code), and the service gates in front of the redirects
    nm = subprocess.run([payloadbuild.tool_prefix() + "nm", "-n", str(keep / "payload.elf")],
                        capture_output=True, text=True, check=True).stdout.split("\n")
    funcs = {int(x[0], 16): x[2] for x in (ln.split() for ln in nm) if len(x) == 3 and x[1] in "Tt"
             and x[2] != "__payload_start"}
    gates = sorted(addr for addr, name in funcs.items() if name.startswith(payloadbuild.GATE_PREFIX))
    entries = sorted(addr for addr, name in funcs.items() if not name.startswith(payloadbuild.GATE_PREFIX))
    # every halfword of the mods' own cave routines (the trampolines run the moved stock span)
    ps = patch_main.PATCH_SETS["cdj850"]
    offsets, _ = sigpatch.layout(lambda n: patch_main.blob_path(n, "cdj850"), ps.mods, ps.profile.gate)
    cave0 = sigpatch.cave_base(sigpatch.Image(ps.profile, stock_img))
    selected = [n for n in patch_main.with_loader(
        [k for m in a.mods for k in patch_update_names(m)], "cdj850") if n in offsets]
    cave_bps = sorted({cave0 + offsets[n] + k for n in selected
                       for k in range(0, len(open(patch_main.blob_path(n, "cdj850"), "rb").read()), 2)})
    bps = entries + cave_bps + gates
    key_filter = payloadbuild.lookup(built.symbols, "hot_cues_keys")
    print("mod-written ranges: %d; payload functions: %d; cave routine halfwords: %d (%s); gates: %d"
          % (len(ranges), len(entries), len(cave_bps), ", ".join(selected), len(gates)), flush=True)
    summary = {"mod_upd": str(mod_upd), "mod_sha256": __import__("hashlib").sha256(mod).hexdigest(),
               "mod_len": len(mod), "label": label, "ranges": len(ranges),
               "entries": {hex(x): funcs[x] for x in entries}, "gates": {hex(x): funcs[x] for x in gates},
               "cave_routines": selected}

    stock_stick = update_stick(work, STOCK_UPD, "stick-stock")
    mod_stick = update_stick(work, mod_upd, "stick-mod")
    same_stick = update_stick(work, same_label, "stick-mod-1.13")
    grid_stick = work / "grid.img"
    if not grid_stick.is_file():
        tr.build_stick(grid_stick)
    flash = work / "flash.bin"
    shutil.copyfile(STOCK_FLASH, flash)
    boot0 = flash.read_bytes()[:0x60000]

    # 2. negative control: the same file labelled like the running stock firmware
    if not a.skip_same_version:
        r = service_update(work, flash, same_stick, "2-same")
        summary["same"] = r[:2] + (r[4],)
        f = flash.read_bytes()
        check("same-label file (1.13 over 1.13, --keep-version): COMPLETE, flash 0x0-0x3EFFFF unchanged "
              "(version skip)", r[0] == "complete" and f[:0x3F0000] == STOCK_FLASH.read_bytes()[:0x3F0000],
              "%s, %s s, running %r, log %s" % (r[0], r[4], r[3], r[1][-2:]))

    # 3. install the file as patch_update wrote it (no relabel)
    r = service_update(work, flash, mod_stick, "3-install")
    f = flash.read_bytes()
    summary["install"] = r[:2] + (r[4],)
    check("install: service USB update of the %s file over stock 1.13 reports COMPLETE" % label,
          r[0] == "complete", "%s s, log %s" % (r[4], r[1][-3:]))
    check("install: flash 0x060000-0x3EFFFF == modded file padded with 0xFF",
          f[0x60000:0x3F0000] == padded(mod))
    check("install: boot region 0x000000-0x05FFFF unchanged", f[:0x60000] == boot0)

    # 4. boot the modded deck
    summary["modded"] = boot_and_play(work, flash, grid_stick, "4-modded", ranges,
                                      {"image": mod_img, "payload": pay_blob, "bss": pay_bss,
                                       "blob_len": blob_len}, True,
                                      payload_entry=None if a.no_gdb else key_filter)
    check("modded: running version is the mod label %s" % label, summary["modded"]["version"].startswith(label),
          summary["modded"]["version"])
    settings_mod = flash.read_bytes()[SETTINGS_SECTOR:0x400000]

    # 5. roll back with the stock file
    r = service_update(work, flash, stock_stick, "5-rollback", breakpoints=() if a.no_gdb else bps)
    f = flash.read_bytes()
    summary["rollback"] = r[:2] + (r[4],)
    summary["rollback_hits"] = r[5]
    summary["rollback_running_version"] = r[3].decode("latin1") if r[3] else None
    summary["rollback_gate"] = r[6]
    check("rollback: service gate set in service mode (0x0C1E109A = 1, 0x0C1E10AA = 0 at the end)",
          r[6] == [1, 0], r[6])
    if not a.no_gdb:
        names = {**{x: funcs[x] for x in funcs}, **{x: "cave" for x in cave_bps}}
        for h in r[5]:
            h["name"] = names.get(int(h["pc"], 16), "?")
        mod_hits = [h for h in r[5] if int(h["pc"], 16) not in gates]
        gate_hits = sorted({h["name"] for h in r[5] if int(h["pc"], 16) in gates})
        print("INFO rollback: service gates that ran (and took the stock path): %s" % gate_hits)
        check("rollback: the armed breakpoints work: service gates were hit (%d of %d)" % (len(gate_hits), len(gates)),
              gate_hits, gate_hits)
        check("rollback: ZERO hits on mod code in service mode, before and during the update (%d payload "
              "functions, %d cave routine halfwords armed)" % (len(entries), len(cave_bps)),
              not mod_hits and r[0] == "complete", mod_hits[:8])
    check("rollback: service USB update with stock C850MAIN.UPD reports COMPLETE", r[0] == "complete",
          "%s s, log %s" % (r[4], r[1][-3:]))
    check("rollback: flash 0x060000-0x3EFFFF == stock file padded with 0xFF (stock-installed deck)",
          f[0x60000:0x3F0000] == padded(stock))
    check("rollback: packed MAIN 0x060000-0x1FE70C == extract/cdj850/flash_cdj850.bin",
          f[0x60000:0x1FE70C] == STOCK_FLASH.read_bytes()[0x60000:0x1FE70C])
    check("rollback: boot region 0x000000-0x05FFFF unchanged", f[:0x60000] == boot0)
    settings_after = f[SETTINGS_SECTOR:0x400000]
    check("rollback: settings/log sectors 0x3F8000-0x3FFFFF untouched by the update", settings_after == settings_mod)
    summary["settings_has_0x5A_record"] = b"\x5a" in settings_after

    # 6. boot the rolled-back deck
    summary["stock"] = boot_and_play(work, flash, grid_stick, "6-stock", ranges,
                                     {"image": stock_img, "payload": mod_img[PAYLOAD_SRC:PAYLOAD_SRC + 256],
                                      "blob_len": blob_len}, False)
    hist = bytes.fromhex(summary["stock"]["history_name"])
    check("stock: settings +0x22 left at 0x5A by hot_cues survives the rollback and is harmless "
          "(HISTORY name reads %r, deck boots and plays)" % hist.split(b"\0")[0].decode("latin1"),
          summary["stock"]["mode_byte"][0] == 0x5A and hist[:7] == b"HISTORY", "0x%02x" % summary["stock"]["mode_byte"][0])
    check("stock: running version is the stock label 1.13", summary["stock"]["version"].startswith("1.13"),
          summary["stock"]["version"])
    summary["minutes"] = round((time.monotonic() - t0) / 60, 1)
    summary["results"] = results
    (work / "summary.json").write_text(json.dumps(summary, indent=1, default=str))
    failed = [x for x in results if not x["ok"]]
    print("%d checks, %d failed, %.1f min; %s" % (len(results), len(failed), summary["minutes"], work / "summary.json"))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
