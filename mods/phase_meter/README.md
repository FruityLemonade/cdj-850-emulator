# phase_meter (CDJ-850 MAIN 1.13): Beat & phase meter on INFO Page 2

Default off. Ports the CDJ-350's beat and phase indicator to **Page 2 of the INFO screen** on the CDJ-850, shifting the stock INFO items down by one page (`Page 2 -> Page 3`, etc.). Works independently whether `bpm_lock` is active, inactive, or not installed.

## Display
Press **INFO** and turn the rotary selector to **Page 2** (items `2..4`):

```
B1/4 128.00 GRID
(1) _2_ _3_ _4_
[+------------]
```

- **Row 0:** `B` (unlocked) or `L` (BPM Lock active), beat-in-bar (`1/4`–`4/4`), actual played BPM with two decimals (`128.00`), and source tag (`GRID`, `BPM`, or `LOCK`; `BEAT ---.-- ----` when no BPM is known).
- **Row 1:** 4-beat bar indicator (`(1) _2_ _3_ _4_` highlights the active beat in parentheses).
- **Row 2:** 13-step sub-beat phase sweep (`[+------------]`, stepping left-to-right across each beat).

Original INFO items (Album, BPM, Comment, Key, etc.) move cleanly to **Page 3** (`items 5+`).

## Use
```sh
.venv/bin/python mods/patch_update.py --model cdj850 C850MAIN.UPD C850MAIN_MOD.UPD phase_meter
```
Combines cleanly with `bpm_lock`, `bpm999`, `bpm_decimals`, `hot_cues`, `quant_loops`, `loop_resize`, and `tempo_wide`.

## How
- Single payload hook (`phase_meter_hook`) on the shared `display_stage_bpm` tail (`0x0C0642AA`, outside the boot-critical closure), running after `bpm999`, `bpm_decimals`, `bpm_lock_display`, and `info_order` and before `c850_ui_flash_hook`.
- Injects three safe unused DB metadata codes (`0x13, 0x14, 0x15`, all pointing to valid zeroed strings in `FUN_0C074824`) at indices `2, 3, 4` of each active track DB record (`0x0C9D44D4`), shifting existing items right by 3 (capped at 18).
- Snaps `C850_INFO_CURSOR` across `3..4` so Page 2 advances cleanly between Page 1, Page 2, and Page 3 in a single rotary click (`2 -> 3 => 5`, `5 -> 4 => 2`).
- When `C850_DISPLAY_MODE == 11` and `C850_INFO_CURSOR == 2`, computes beat and phase in 32-bit integer arithmetic from the rekordbox beat grid (`0x0C9D24FC`, primary) or base BPM (`0x0C9D23EC`, fallback) and writes centered, unhighlighted, icon-free rows (`tag = 0x11..0x13`, `attr0 = 0x00`) to `C850_MATRIX_ROW(0..2)` and `C850_ROW_SOURCE(0..2)` before `FUN_0C064CA4` serialises them.
