#!/usr/bin/env python3
"""Roll a cache-model profile up per function and put it into a Ghidra program.

The cdj850_cache plugin (scripts/cache/cdj850_cache.c, run with
scripts/cache/cache_run.py) writes <out>-profile.csv: per pc, the fetches and
instruction-cache misses, cached reads/writes with misses, dirty write-backs,
uncached accesses and prefetches of one emulator run. This script sums those
rows per function of an imported program (boot.bin or main_unpacked.bin; rows
outside the program are skipped), prints the functions that miss most, and
records the totals in the program as bookmarks of category "CacheProfile":
one at each function entry with its totals, one at each of the hottest single
pcs. Comments and names are left alone, and the previous CacheProfile
bookmarks are removed first, so a rerun replaces the last profile.

    python scripts/ghidra/apply_cache_profile.py PROJECT.gpr /main_unpacked.bin \\
        cdj850-cache-profile.csv [--top 30] [--pcs 50] [--csv per-function.csv] [--dry-run]

Close the project in Ghidra (and stop pyghidra-mcp, which holds it open)
first, as for apply_cdj850_annotations.py. GHIDRA_INSTALL_DIR must point at the
Ghidra installation.
"""

import argparse
import csv
from pathlib import Path

FIELDS = ("fetch", "ic_miss", "rd", "rd_miss", "wr", "wr_miss", "writeback",
          "unc_rd", "unc_wr", "pref", "pref_miss")
CATEGORY = "CacheProfile"


def read_profile(path):
    rows = []
    with open(path, newline="") as f:
        for r in csv.DictReader(f):
            rows.append((int(r["pc"], 16), {k: int(r[k]) for k in FIELDS}))
    return rows


def misses(t):
    """Line fills from memory: what costs bus time on the chip."""
    return t["ic_miss"] + t["rd_miss"] + t["wr_miss"] + t["pref_miss"]


def uncached(t):
    """Accesses through the cache-disabled mirror: each is a bus cycle."""
    return t["unc_rd"] + t["unc_wr"]


def summary(t):
    def pct(a, b):
        return f"{100.0 * a / b:.2f}%" if b else "-"
    return (f"I-miss {t['ic_miss']}/{t['fetch']} ({pct(t['ic_miss'], t['fetch'])}), "
            f"O-read miss {t['rd_miss']}/{t['rd']} ({pct(t['rd_miss'], t['rd'])}), "
            f"O-write miss {t['wr_miss']}/{t['wr']}, write-backs {t['writeback']}, "
            f"uncached r/w {t['unc_rd']}/{t['unc_wr']}, pref miss {t['pref_miss']}/{t['pref']}")


def roll_up(program, rows):
    """{function entry offset: (name, totals)}, plus the per-pc rows that fall
    in the program (pc, function name or None, counts)."""
    space = program.getAddressFactory().getDefaultAddressSpace()
    memory = program.getMemory()
    functions = program.getFunctionManager()
    per_fn, per_pc = {}, []
    for pc, counts in rows:
        addr = space.getAddress(pc)
        if not memory.contains(addr):
            continue
        fn = functions.getFunctionContaining(addr)
        key = fn.getEntryPoint().getOffset() if fn else None
        name = fn.getName() if fn else None
        per_pc.append((pc, name, counts))
        if key is None:
            continue
        entry = per_fn.setdefault(key, [name, dict.fromkeys(FIELDS, 0)])
        for k in FIELDS:
            entry[1][k] += counts[k]
    return per_fn, per_pc


def apply_bookmarks(program, per_fn, per_pc, n_pcs):
    import pyghidra
    from ghidra.util.task import TaskMonitor

    space = program.getAddressFactory().getDefaultAddressSpace()
    bookmarks = program.getBookmarkManager()
    with pyghidra.transaction(program, "Apply cache profile"):
        for category in (CATEGORY, CATEGORY + "-pc"):
            bookmarks.removeBookmarks("Note", category, TaskMonitor.DUMMY)
        for entry, (name, t) in per_fn.items():
            if misses(t) or t["writeback"] or t["unc_rd"] or t["unc_wr"]:
                bookmarks.setBookmark(space.getAddress(entry), "Note", CATEGORY,
                                      f"{name}: {summary(t)}")
        hot = sorted(per_pc, key=lambda r: misses(r[2]), reverse=True)[:n_pcs]
        for pc, name, t in hot:
            if misses(t) and pc not in per_fn:     # entries carry the function totals
                bookmarks.setBookmark(space.getAddress(pc), "Note", CATEGORY + "-pc",
                                      f"hot pc in {name or '?'}: {summary(t)}")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("project", type=Path, help="path to the local .gpr project file")
    ap.add_argument("program", help="program path inside the project, e.g. /main_unpacked.bin")
    ap.add_argument("profile", type=Path, help="<out>-profile.csv from the cache plugin")
    ap.add_argument("--top", type=int, default=30, help="functions to print (default 30)")
    ap.add_argument("--pcs", type=int, default=50, help="hottest single pcs to bookmark (default 50)")
    ap.add_argument("--rank", choices=("fills", "uncached"), default="fills",
                    help="order by line fills (default) or by uncached accesses, which cost a "
                         "full bus cycle each on the chip and never show as misses")
    ap.add_argument("--csv", type=Path, help="also write the per-function totals here")
    ap.add_argument("--dry-run", action="store_true", help="print only, change nothing")
    args = ap.parse_args()
    if args.project.suffix != ".gpr" or not args.project.is_file():
        ap.error("project must be an existing .gpr file")
    rows = read_profile(args.profile)

    import pyghidra
    pyghidra.start()
    project_file = args.project.resolve()
    with pyghidra.open_project(project_file.parent, project_file.stem) as project:
        with pyghidra.program_context(project, "/" + args.program.lstrip("/")) as program:
            per_fn, per_pc = roll_up(program, rows)
            if not args.dry_run:
                apply_bookmarks(program, per_fn, per_pc, args.pcs)
                if program.isChanged():
                    from ghidra.util.task import TaskMonitor
                    program.getDomainFile().save(TaskMonitor.DUMMY)

    key = misses if args.rank == "fills" else uncached
    ranked = sorted(per_fn.items(), key=lambda kv: key(kv[1][1]), reverse=True)
    total = dict.fromkeys(FIELDS, 0)
    for _, (_, t) in per_fn.items():
        for k in FIELDS:
            total[k] += t[k]
    outside = sum(1 for _, name, _ in per_pc if name is None)
    print(f"{args.program}: {len(per_pc)} profiled pcs in {len(per_fn)} functions "
          f"({outside} pcs outside any function)")
    print(f"all functions: {summary(total)}")
    print(f"\n{'entry':>10}  {'line fills':>10}  {'share':>6}  {'uncached':>10}  {'share':>6}  function")
    all_miss, all_unc = misses(total) or 1, uncached(total) or 1
    for entry, (name, t) in ranked[:args.top]:
        print(f"{entry:#010x}  {misses(t):>10}  {100.0 * misses(t) / all_miss:5.1f}%  "
              f"{uncached(t):>10}  {100.0 * uncached(t) / all_unc:5.1f}%  {name}")
    if args.csv:
        with open(args.csv, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(("entry", "function", "line_fills", "uncached") + FIELDS)
            for entry, (name, t) in ranked:
                w.writerow((f"{entry:#010x}", name, misses(t), uncached(t)) + tuple(t[k] for k in FIELDS))
    if not args.dry_run:
        print(f"\nbookmarks ({CATEGORY}) written to {args.program}")


if __name__ == "__main__":
    main()
