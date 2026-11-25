# bpm_lock (CDJ-850 MAIN 1.13): CDJ-350 BPM Lock with ±100% range

Default off. Ports the CDJ-350's **BPM LOCK** and **Master BPM** workflow onto the CDJ-850 using the firmware's dormant BPM-lock engine (`FUN_0C0D1F14` and state block `0x0CE649AC`), extended from ±16% to **±100%**.

## Controls
- **BEAT SELECT + MASTER TEMPO (hold >= 1 s):** Enter/update **Master BPM edit mode** (`MASTER <NNN.N>` flashes continuously on the dot matrix). Turn the **rotary selector** to adjust Master BPM in 0.1 BPM steps (push + turn for 1.0 BPM steps, 60.0–199.9 BPM; or up to 999.0 with `bpm999`). The edit dialogue remains displayed until you press **ENTER/push the rotary** or **BACK** to confirm/exit (no timeout).
- **BEAT SELECT + MASTER TEMPO (short press):** Lock/unlock playback tempo to the Master BPM (`BPM LOCK ON` / `BPM LOCK OFF`).
  - If the required pitch exceeds ±16%, the deck automatically switches to **WIDE** (±100%) so the DSP pitch math (`±100%`) and fader catch-up stay consistent.
  - Moving the tempo fader across the locked pitch unlocks back to fader control smoothly (stock catch-up in `FUN_0C0D1E84`).
  - `BEAT SELECT` alone and `MASTER TEMPO` alone keep their stock behaviour (the `BEAT SELECT` fraction-mode toggle is undone when taken as part of the combo).

## Use
```sh
.venv/bin/python mods/patch_update.py --model cdj850 C850MAIN.UPD C850MAIN_MOD.UPD bpm_lock
```
Combines cleanly with `phase_meter`, `hot_cues`, `quant_loops`, `loop_resize`, `bpm999`, `bpm_decimals`, and `tempo_wide`.

## How
- `bpm_lock_range1` / `bpm_lock_range2` (`DataPatch`): widen the lock pitch limit in `FUN_0C0D1CE6` and `FUN_0C0D1D56` from `±0x640` (±16.00%) to `±0x2710` (±100.00%).
- `tempowidemath1` / `tempowidemath2` (`DataPatch`, shared with `tempo_wide`): enable the overflow-safe WIDE multiplier on USB in `FUN_0C0D59B8`.
- `bpm_lock` (`0x0C0ED038` -> `c850_ui_keys`) and `bpm_lock_tick` (`0x0C0EC838` -> `c850_ui_tick`): shared key filter and player-cycle tick hook handling the `BEAT SELECT + MASTER TEMPO` short/long gestures, WIDE switching, and Master BPM rotary flash.
- `bpm_lock_notify1` / `bpm_lock_notify2` (`0x0C0D1DA4` / `0x0C0D1F34` -> `bpm_lock_notify`): replace the dormant `FUN_0C06E58A` calls inside the BPM-lock routines so status codes (`1, 2, 3, 4, 0x0D, 0x0E, 0x0F, 0x13`) flash on the dot matrix.
- `bpm_lock_display` & `bpm_lock_flash` (`BPM_SITE` hooks at `0x0C0642AA`): stage the Master BPM (while editing) or locked BPM and locked pitch/sign/range on the 7-segment display, and drive the 1.5 s dot-matrix flash.
