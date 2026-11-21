# info_order (CDJ-850 MAIN 1.13): INFO screens in your order, BPM, Camelot key and rating up front

Default off. USB tracks only; CD and tracks without a BPM entry stay stock.

| Screen (jog) | Stock | With info_order |
|---|---|---|
| 1 (title view, the playing track's screen) | Track, Artist, waveform | Track, **`128.23 12A *****`**, waveform |
| 2 | Album, BPM, Comment | Artist, Rating, Key |
| 3 | Key, Rating | Album, Comment, then any other item the track has |

**`128.23 12A *****`** shows the actual BPM, Camelot key and rekordbox rating.

- BPM: with [`bpm_decimals`](../bpm_decimals/README.md) in the build and a beat grid that gave the
  tempo, the grid BPM at the current beat times the pitch (the same value `bpm_decimals` shows);
  otherwise the rekordbox BPM of the track times the pitch, rounded to 0.01.
- Key: the track's key as rekordbox exported it (`Am`, `F#m`, `Db`, Camelot `8A` or Open Key
  `1m`), shown as Camelot. **MASTER TEMPO off:** the key moves with the pitch, to the nearest
  semitone (+2.94 % is one semitone up, -2.85 % one down; +1 semitone = +7 on the Camelot wheel,
  so Am `8A` at +3.35 % reads `3A`). MASTER TEMPO on: the key as analysed.
- The rating uses one asterisk per star. A one-star track shows `128.23 12A *`, and an unrated track shows
  `128.23 12A`. The longest BPM and Camelot combination with five stars takes 16 characters.
- Each part is optional. A key outside the supported formats is shown as supplied, with its text
  shortened on a rated track if needed to fit the stars on the same row.

The second screen's Key item is the stock text (no Camelot, no pitch).

## Is the order a rekordbox setting?
No. When a track loads, the deck's track-metadata reader appends the code of each field it gets
to a list in the track's DB record, in the order the fields arrive; the INFO screens walk that
list. The USB INFO configuration controls which optional fields the reply includes. Swapping
rekordbox’s browse category and sort tables (17 and 18 in `export.pdb`) left the INFO order unchanged. The title view always shows title and artist
(the display code formats those two codes), then the list pages from its third entry.

## Use
    .venv/bin/python mods/patch_update.py --model cdj850 C850MAIN.UPD C850MAIN_MOD.UPD info_order
Combines with every mod on the branch; with `bpm_decimals` the first screen shows its grid value.
The payload word `info_order_on` (1) turns it off at run time.

## How
- A pool redirect at `0x0C052D80` enables the USB rating field for the stock `0x2002` request only.
  The stock database builder returns rating item `0x0A` and its text lands at DB record `+0x5A8`.
  The title row counts the filled-star glyphs (`0x04`).
- A hook on the tail of `display_stage_bpm` (`0x0C0642AA`, the `bpm999` / `bpm_decimals` span,
  before `bpm_decimals`' routine), C in `cdj850/payload/info_order.c`. DisplayTask runs it after the
  row filler and before the text serialiser, so a row written there is the one the panel gets.
- Each pass: every DB record in use (3 slots at `0x0C9D44D4`, `0x864` bytes) gets its INFO list
  (`+0x851` count, `+0x852..` codes) in the order title (04), BPM (0D), artist (07), rating (0A),
  key (0F), album (02), comment (23), others after in their stock order. Entries 0 and 1 are the
  two the title view stands for, so screen 2 starts with the artist. A list without both a title
  and a BPM entry is left alone. Only RAM changes; the count stays.
- In the title view (mode word `0x0C9D0AF8` = 10) of the shown track (`0x0C9D1D96`) whose list is
  in that order, row 1 gets attr0 `0x0D` (the BPM item) and the text above, in the row record and
  its text source. `bpm_decimals` leaves it alone (its source is not the stock `NNN.N bpm`).
- Record fields read: `+0x18` BPM valid, `+0x1C` BPM x 100, `+0x730` key text. Pitch `0x0CE6CA50`
  (1/100 %), MASTER TEMPO `0x0CE6D0E0`.
- The list readers (`info_screen_fill` `0x0C0669D6`, the row filler `FUN_0C06543E`) are
  boot-critical and are not patched. See `docs/cdj850-modding.md` section 4b.8.

## Tests
`tests/test_cdj850_info_order.py`: the payload on the SH-2A interpreter against a Python reference
(list order, Camelot from classic/Camelot/Open Key texts, the semitone thresholds over the whole
pitch range, the row text for 0–5 stars, the pass and its stock cases), the hook ahead of `bpm_decimals`, and live
in QEMU with `bpm_decimals` (a five-star 128 BPM track in Am). The title row reads `128.00 8A *****`
at zero pitch, shifts to `3A` with the fader at +3.35 % and MASTER TEMPO off, and returns to `8A`
with MASTER TEMPO on.

## Open questions for a real deck
The panel's font and icon for attr0 `0x0D` in the title view's second row (the emulator only
shows the text).
