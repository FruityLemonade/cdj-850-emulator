# Project Instructions

## CDJ-850 Reference & Reverse Engineering

When working on CDJ-850 hardware, firmware, or emulator behavior, consult the service manual digest alongside PyGhidra (`pyghidra-mcp`) and SCIF1 captures:

- **Service manual agent reference:** `docs/cdj850-service-manual.md`, a structured digest of the Pioneer CDJ-850 service manual (`f70e4d.pdf`, RRV4128), and `docs/cdj850-modding.md` for reverse-engineered firmware internals and modding hooks.
- Check `docs/cdj850-service-manual.md` before guessing or re-deriving IC/board mappings, MAIN CPU (`IC103`, SH7263) and Panel CPU (`IC1007`) pin/net maps, user settings in flash, boot/power-on sequence, error codes, service modes, firmware update/recovery, USB switching, signal levels, waveforms/timing, power rails, or PDF page references.

## CDJ-350

The CDJ-350 shares the CDJ-850's MAIN board; `docs/cdj350.md` covers the firmware and the `cdj350` machine, and `docs/cdj350-service-manual.md` digests its service manual (RRV4069). Check both before re-deriving CDJ-350 hardware or panel-link facts.

## CDJ-850 Memory and Cache Analysis

Use the SH7263 cache hit/miss profiler and coherency checker whenever investigating CDJ-850 MAIN CPU memory. Follow `docs/cdj850-cache-model.md` to run the relevant workload with `scripts/cache/cache_run.py` and review the coherency report and per-PC profile. Use `scripts/ghidra/apply_cache_profile.py` to correlate the profile with Ghidra functions when needed. Check the report for model warnings before drawing conclusions.

## Keep the Ghidra annotations up to date (Claude, Codex, Antigravity)

`scripts/ghidra/cdj850-annotations.json` (CDJ-850) and `scripts/ghidra/cdj350-annotations.json` (CDJ-350) are the shared record of what we know about the firmware. Whenever you discover or confirm a function, RAM/flash address, pool word, task, hook point or behaviour (in Ghidra, the emulator, a trace, mod work or a doc), add or correct its entry in the bundle for that player (`functions`, `labels` or `comments`, in the right program) in the same change. Keep the CDJ-850 bundle consistent with `docs/cdj850-modding.md` and `mods/payload/include/c850.h`, and the CDJ-350 bundle with `docs/cdj350.md`. Comments must sit on addresses inside the imported image (labels may name RAM). Check that the JSON still parses, and never commit firmware bytes into it.
