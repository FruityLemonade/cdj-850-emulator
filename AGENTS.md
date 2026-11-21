# Project Instructions

## CDJ-850 Reference & Reverse Engineering

When working on CDJ-850 hardware, firmware, or emulator behavior, consult the service manual digest alongside PyGhidra (`pyghidra-mcp`) and SCIF1 captures:

- **Service manual agent reference:** `docs/cdj850-service-manual.md`, a structured digest of the Pioneer CDJ-850 service manual (`f70e4d.pdf`, RRV4128), and `docs/cdj850-modding.md` for reverse-engineered firmware internals and modding hooks.
- Check `docs/cdj850-service-manual.md` before guessing or re-deriving IC/board mappings, MAIN CPU (`IC103`, SH7263) and Panel CPU (`IC1007`) pin/net maps, user settings in flash, boot/power-on sequence, error codes, service modes, firmware update/recovery, USB switching, signal levels, waveforms/timing, power rails, or PDF page references.

## Keep the Ghidra annotations up to date (Claude, Codex, Antigravity)

`scripts/ghidra/cdj850-annotations.json` is the shared record of what we know about the firmware. Whenever you discover or confirm a function, RAM/flash address, pool word, task, hook point or behaviour (in Ghidra, the emulator, a trace, mod work or a doc), add or correct its entry there (`functions`, `labels` or `comments`, in the right program) in the same change. Keep it consistent with `docs/cdj850-modding.md` and `mods/payload/include/c850.h`, check that the JSON still parses, and never commit firmware bytes into it.
