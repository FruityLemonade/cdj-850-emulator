# auto_standby (CDJ-850 MAIN 1.13)

Adds a `STANDBY TIME` setting to the UTILITY menu (after `VERSION No.`) to choose the AUTO STANDBY timeout between `20 MIN` (default), `40 MIN`, `60 MIN`, and `120 MIN`, persisted to `/PIONEER/MODSET.DAT` on the USB stick.
UTILITY `AUTO STANDBY` stays `OFF`/`ON` as before; `OFF` is still gated out before the limit.

## What it changes
- Pool redirect on `0x0C0EF998` (`FUN_0C08F186` inside `auto_standby_seconds` at `0x0C0EF844`, outside the boot-critical closure) to `auto_standby_get_mode` in the shared SDRAM payload.
- Shared `c850_ui` UTILITY and USB settings hooks (`STANDBY TIME` item at order 10).

## Use on the deck
Hold `MENU/UTILITY`, turn past `VERSION No.` to `STANDBY TIME`, and press the rotary selector to cycle `20 MIN -> 40 MIN -> 60 MIN -> 120 MIN`.
