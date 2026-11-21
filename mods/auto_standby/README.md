# auto_standby (CDJ-850 MAIN 1.13)

Changes the AUTO STANDBY timeout from the stock 4 hours to a set number of minutes.
UTILITY AUTO STANDBY stays OFF/ON as before; OFF is still gated out before the limit.

## Knob
- `CDJ850_STANDBY_MINUTES` (integer 1..1092, default 20). Set it in the environment when building:
  `CDJ850_STANDBY_MINUTES=30 .venv/bin/python mods/patch_update.py --model cdj850 C850MAIN.UPD out.UPD autostandby`
- Values outside 1..1092 or not an integer are rejected at build time (1092 minutes = 65520 s, the largest value that fits the u16).

## What it changes
- One u16 in MAIN at `0x0C18CCDE` (file `0x18CCDE`), the AUTO STANDBY ON entry of the standby table at `0x0C18CCDC`: `0x3840` (14400 s) becomes `minutes*60`. DataPatch, no code hook, no cave.
- Verified by the emulator: the 1-minute build requests standby about 60 s after idle starts; a TEMPO press restarts the count.

## Use on the deck
Flash via the service-mode USB update (`C850MAIN.UPD`, see docs/cdj850-modding.md section 2). `patch_update.py` labels the file `0.13`, which the updater always writes.

In the service modes the idle counter does not run (the player state is `0x0D`, which the idle gate `FUN_0C0EF7D6` rejects; the seconds counter stayed 0 through a whole emulated USB update), so even a 1-minute timeout cannot cut an update short (docs/cdj850-modding.md section 8.5).

## Open hardware questions
- Not run on a real CDJ-850; the 20-minute timeout itself was not timed (only 1-minute and stock 150 s runs in the emulator).
- Whether a real deck counts the same activities (jog, fader, USB traffic) as the emulator's TEMPO key press.
