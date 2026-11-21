# bpm_decimals (CDJ-850 MAIN 1.13): actual BPM with two decimals in the INFO view

Default off. Load a track, press **INFO** and turn the jog down to the BPM item. Stock shows the
rekordbox tempo of the track, e.g. `128.0 bpm` for a 127.96 BPM grid. With this mod, while a
beat grid gave the BPM, the same item reads

    128.0>132.25 bpm

The left part is the stock text (the DB tempo). The right part is the BPM the deck is playing
right now, with two decimals:

    actual = round(grid BPMx100 at the current beat x (10000 + pitch) / 10000) / 100

- The beat is the last grid beat at or before the play position. On a variable-tempo grid the
  value changes on the beat where the tempo changes.
- Pitch is the firmware's pitch word (1/100 %, up to +-100 % on WIDE).
- The line updates when the value changes (fader, tempo change) and is re-sent to the panel only
  then. An unchanged value sends nothing.

The item is exactly stock when no grid gave the BPM (BPM counter, CD, unanalysed file), and
outside the loaded-track views. The BPM box, the time readout and its frame digits are not
touched. The format fits the 100-dot matrix: 16 characters, 85 dots in the emulator's
glyph widths (5-dot digits, 1-dot `.`, 2-dot space), from x = 7.

## Use
    .venv/bin/python mods/patch_update.py --model cdj850 C850MAIN.UPD C850MAIN_MOD.UPD bpm_decimals
Combines with `bpm999`, `tempo_wide`, `bpm_probe` and `payload_demo`. No knobs.

## How
- `bpm_decimals`: payload hook (`bpm_decimals_hook`) on the tail of `display_stage_bpm`
  (`0x0C0642AA`, the span `bpm999` uses; it adds a 16-byte slot to that trampoline). In each
  DisplayTask pass, the INFO row filler (`FUN_0C06543E`, which runs only on scroll and track
  events) comes first, then `display_stage_bpm`, then the text serialiser (`FUN_0C064CA4`).
  So the routine rewrites the BPM row record (attr0 `0x0D`, stock text `NNN.N bpm`) between
  the two, and only when its text changes. The serialiser sends a changed row and bumps the
  panel's text sequence on its own.
- `bpm_decimals_grid`: pool redirect of `0x0C073830`, the word `FUN_0C073788` uses to call
  `grid_find_beat`. The payload notes the BPMx100 of the beat stock looked up. The actual
  value is shown only while the base BPM word still equals what stock made of it, i.e. the
  BPM came from the grid.
- Both sites are outside the boot-critical closure (`mods/bootcrit.py`). The filler,
  serialiser and item formatter are inside it and are not patched.
- Code: C in `cdj850/payload/bpm_decimals.c` (shared SDRAM payload, about 1.3 KB).

## Open questions for a real deck
1. Does the panel's font have `>`? (Plain ASCII; the stock firmware sends `[`, `]`, `:`.)
2. Width on the real glass: 16 characters. The emulator's glyph widths come from photos;
   the real font may be wider.
3. Attr0 `0x0D` as "BPM item" and mode 11 as "INFO view" were seen in the emulator only.
