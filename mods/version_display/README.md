# version_display (CDJ-850)

Shows the custom firmware version label followed by the base firmware version
in **MENU/UTILITY → VERSION No.**, for example:

```text
VERSION No.
Ver. 1.23 (1.13)
```

In stock CDJ-850 MAIN 1.13, `FUN_0c067b8c` (UTILITY item 6) prefixes `"Ver. "`
and calls `FUN_0c067972`, which hardcodes `"1.13"` rather than reading the
running version label copied from flash `0x060010` to `0x0CE6D242` at boot.

This cave-assembly hook runs on the shared `display_stage_bpm` tail
(`0x0C0642AA`, outside the boot-critical closure, 50 Hz in `DisplayTask` before
`FUN_0C064CA4` serializes the dot-matrix rows). When display mode is `12`
(UTILITY), menu level `0x0C1E108C == 2`, and cursor `0x0C1E108D == 6`, it
rewrites dot-matrix row 2 (`0x0C9D0D22`) to `Ver. <running> (1.13)`.
It is included automatically by `mods/patch_update.py` whenever a CDJ-850
update is patched with any mod.
