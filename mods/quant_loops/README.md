# quant_loops (CDJ-850 MAIN 1.13): loops snap to the rekordbox beat grid

Default off. Only on USB tracks whose beat grid gave the deck's tempo (rekordbox analysis);
without a grid (BPM counter, CD, unanalysed file) every loop is exactly stock.

## Quantize on/off on the deck
**Hold BEAT SELECT, press LOOP IN.** That switches quantize; the display shows `QUANTIZE ON` or
`QUANTIZE OFF` for 1.5 s (in the playing view on the title line, in the browser/INFO views as a
single middle line), then the rows come back. The setting is saved in flash and survives a
restart; a fresh deck (or a factory reset / HISTORY name change) starts with quantize **on**.

- Quantize off: every loop path below is stock, and `loop_resize` resizes in frames instead of
  on the grid.
- LOOP IN alone and BEAT SELECT alone stay stock. The BEAT SELECT press of the combo does not
  leave fraction mode toggled (the toggle is undone), and neither key reaches the stock handlers
  until it is released.
- The switch is the mod flags byte of `payload/include/c850_ui.h` (settings record `+0x22`,
  shared with `hot_cues`); a 31-character HISTORY name leaves no room for it, and the combo then
  only shows the unchanged state.

| Press | Stock | With quant_loops |
|---|---|---|
| BEAT 1/2/4/8 (auto beat loop) | in = the playhead, out = in + beats x 4500 / whole BPM (frames; 4 beats at 128 BPM = 140) | in = the grid beat nearest the playhead, behind or ahead of it (ahead: the deck plays on to it, then loops; phase kept either way), out = the grid time of beat in + N, so variable-tempo grids are exact |
| BEAT SELECT (fraction 1/16..1/2) + BEAT n | out = in + n x 4500 / (BPM x 16) | L = n/16 of the current beat's interval (grid times); in = the multiple of L after that beat (up to the next beat) nearest the playhead, out = in + L. 1/2 pressed 2/5 into a beat: in = beat + 1/2, out = the next beat; 4/5 into it: in = the next beat |
| LOOP IN | in = the playhead | in = the nearest grid beat |
| LOOP OUT | out = the playhead | out = the nearest grid beat after the loop in (the next one if the nearest is not after in) |
| RELOOP/EXIT, CALL, MEMORY, beat resize in a loop | | stock (they use the points set above) |

**No drift over many passes.** Loop points are whole frames, so a loop of N grid beats is
`L = N x 4500 x 100 / BPMx100` frames exactly (140.625 at 128 BPM for 4 beats) but plays a
whole number of frames per pass. Stock and a fixed grid out both jump back that whole number
at every wrap, so the track slides against the grid by the rounding every pass (4 beats at
128: -8.3 ms stock, -8.3 or +5.0 ms with a fixed grid out; 32 passes = up to 0.27 s) and keeps
the sum when you EXIT. With `quant_loops` the deck counts the passes and sets the out of each
next pass so that the total follows the exact length: passes alternate between 140 and 141
frames and the loop stays within half a frame (6.7 ms) of the grid however long it plays, also
after EXIT. See "Drift correction" below.

Every auto loop has the playhead before its out (playhead < out); a case that would not (past
the end of a grid) stays stock. An in ahead of the playhead (the nearest beat is the next one)
is reached by playing on, exactly like a LOOP OUT ahead of it: emulator, BEAT 4 at frame 301 on
128 BPM gave in 308, the deck played on and wrapped 447 -> 308. All points are whole frames (1/75 s): grid ms convert with `(ms x 75 + 500) / 1000`. A loop is
at least one frame long. When the 1000-beat grid window in RAM does not reach beat in + N, the
out uses the in beat's grid tempo (`N x 60000 / BPM` ms). Before the first beat of the grid
(lead-in) the first beat is the nearest. If the snapped point's ring sector is not buffered (right
after a jump) the in stays stock and the out keeps the grid length; LOOP IN/OUT stay stock.

Interaction with stock features (checked in the emulator, see the test):
- **RELOOP/EXIT**: exit leaves the loop (state 3), the records keep the snapped points, RELOOP
  re-enters the same loop.
- **Auto-loop LEDs** (page 2 byte 7): unchanged; they show the beat count stock stores in
  `0x0C1E1088`.
- **Beat buttons inside a loop** (resize, `FUN_0C0E5668`): stock code, from the loop in and
  `CA90`. The mod sets `CA90` to the grid time of in + 8 beats, so a resize divides that grid
  length (one-frame floor): BEAT 1 in the 4-beat loop 272..413 asked for out 307 (beat: 308).
- **MEMORY / CALL**: MEMORY inside the loop stores the snapped in/out (memory cue list),
  CALL ◀ cues at the snapped in and PLAY plays the memory loop between the snapped points.
- LOOP OUT pressed past the snapped out (nearest beat behind the playhead): the loop closes at
  once and plays from in, as stock does for its own out.

## Drift correction on/off in UTILITY
**Hold MENU/UTILITY, turn the selector past VERSION No. to `QUANTIZE DRIFT`** (after
`WAVE PLAYHEAD` when `waveform` is built in), **press the selector** to switch `ON` / `OFF`.
- `ON` (default; a fresh deck, factory reset or HISTORY name change): the drift correction below.
- `OFF`: every loop plays the out it was set with (whole frames, the stock drift); snapping
  to the grid is unchanged.

Saved with the UTILITY settings, in the mod flags (settings `+0x21` bit `0x02`,
`C850_UI_FLAG_DRIFT_OFF`, `payload/include/c850_ui.h`). A HISTORY name longer than 29
characters occupies that byte: the choice then lasts until power-off. The list is the shared
`c850_ui` UTILITY list (`c850_ui_utility_scroll` / `_select`, the same pool words and routines
as `waveform`'s); `c850_ui_flash_hook` draws the rows.

**Settings on the stick (prototype).** `quant_loops_usb_settings` reads `/PIONEER/MODSET.DAT`
when a rekordbox stick mounts; while it is in, every on-deck switch (QUANTIZE, QUANTIZE DRIFT,
and the other mods' flags) is also written there, so the settings travel with the stick to any
deck with this build. See `docs/cdj850-modding.md` section 4b.6.

## Use
    .venv/bin/python mods/patch_update.py --model cdj850 C850MAIN.UPD C850MAIN_MOD.UPD quant_loops
Switch: `CDJ_MAIN_QUANT_LOOPS=1` (and its eleven sibling `CDJ_MAIN_QUANT_LOOPS_*` knobs in
`mod.conf`, all one row: `quant_loops` on the command line brings all twelve patches). The payload
word `quant_loops_on` (1 = the mod may snap) and the on-deck switch above are both checked first by
every routine, the drift correction included. `quant_loops_drift_on` (1) and the UTILITY setting (`QUANTIZE DRIFT ON`) are
both needed for the drift correction.

## How
Eleven pool-word redirects and one display hook into the shared payload
(`cdj850/payload/quant_loops.c`, about 4.4 KB, `loop_tick.c`, and the shared
`payload/common/c850_ui.c`), no cave bytes but one 8-byte trampoline slot. Each word and every
instruction that reads it is outside the boot-critical closure (`mods/bootcrit.py`). See
`docs/cdj850-modding.md` section 4b.9.

| Patch | Pool word (stock value) | Read at | Payload |
|---|---|---|---|
| `quant_loops` | `0x0C0E5D4C` (`FUN_0C073954`) | `0x0C0E5C0E` | `quant_loops_auto_in`: snap the in (D1C0 and the in sector `0x0C7F6468`), compute the grid out |
| `quant_loops_auto_send` | `0x0C0E5FC8` (`audio_send_cmd`) | auto_beat_loop_set only | `quant_loops_auto_send`: grid out into the new loop's cmd 6 |
| `quant_loops_auto_send2` | `0x0C0E5D08` (`audio_send_cmd`) | auto_beat_loop_set only | same, cued path |
| `quant_loops_auto_keep` | `0x0C0E5FD0` (`FUN_0C0059D2`) | `0x0C0E5DAA` | keep the snapped in sector (stock refreshes it right after the send) |
| `quant_loops_loop_in` | `0x0C0DFA80` (`FUN_0C0059D2`) | `0x0C0DF95E` | `quant_loops_loop_in`: in sector to the nearest beat |
| `quant_loops_loop_out` | `0x0C0E0090` (`audio_send_cmd`) | LOOP OUT code only | `quant_loops_loop_out`: out sector of cmd 1/mode 3 |
| `quant_loops_loop_out2` | `0x0C0E02C0` (`audio_send_cmd`) | LOOP OUT code only | same, other branch |
| `quant_loops_keys` | `0x0C0ED038` (`FUN_0C0DD492`) | `0x0C0ECEFC` | `c850_ui_keys`, the shared key filter chain (also `hot_cues`'): BEAT SELECT + LOOP IN |
| `quant_loops_flash` | hook, tail of `display_stage_bpm` (`0x0C0642AA`, the shared `bpm999` span) | DisplayTask | `c850_ui_flash_hook`: `QUANTIZE ON/OFF` on the matrix, rows put back after 1.5 s |
| `quant_loops_drift` | `0x0C0D7B98` (`keys_state4_looping`) | `0x0C0D79A6` | the shared state-4 tick `c850_loop_tick` → `quant_loops_drift_tick`, then stock |
| `quant_loops_utility_scroll` | `0x0C0E797C` (`utility_scroll_item`) | `0x0C0E77C4` | `c850_ui_utility_scroll`: UTILITY list continues after VERSION No. (shared with `waveform`) |
| `quant_loops_utility_select` | `0x0C0E7F10` (`utility_set_menu_level`) | `0x0C0E7E64` | `c850_ui_utility_select`: QUANTIZE DRIFT ON/OFF (shared with `waveform`) |

The grid is read from the window in RAM (`0x0C9D24FC`, read-only, never paged; `bpm_decimals`'
redirect of `grid_find_beat` is not involved), and only when the base BPM word equals what
`FUN_0C073788` made of a beat within 8 of the position (the proof `bpm_decimals` uses).

## Drift correction
`quant_loops_drift_tick` runs once per player cycle while a loop plays (state 4), through the
state-4 key handler's pool word `0x0C0D7B98`. That word is also `loop_resize`'s tick, and one
word can name one routine, so both mods point it at the same symbol `c850_loop_tick`
(`mods/payload/include/c850.h`, `C850_LOOP_TICK_DEFINE`): weak, identical definitions in each
mod's `loop_tick.c`, which call `loop_resize_state4` and `quant_loops_drift_tick` (each a weak
no-op unless its mod is built) and then the stock handler. With both mods the second redirect
finds the word already set (`present`). `mods/payload/payload.ld` discards the empty
dynamic-relocation section `sh-elf-ld` reserves for the weak references, and `payloadbuild`
checks the linked payload has no relocations left.

- **Whose loops:** only loops quant_loops made: an auto beat loop or LOOP IN/OUT
  (`quant_loops_drift_owner` = their in frame), also after BEAT or `loop_resize` changed the
  out (the in stays). A hot-cue or memory loop loaded from the stick has another in and is never
  moved, so it plays as stored and `hot_cues` records it back unchanged (before this rule the
  drift-moved out was saved: the stored 2531 half-frames became 2533). Playing any other
  loop ends the ownership.
- **Which loops:** a loop the grid can name, as `loop_resize` finds it: the in on a beat or a
  1/16 step of one (or off the grid: lengths from the in), the length within one frame of q/16
  beats, 1 <= q <= 512, and at least 12 frames. Exact length: `L = q x 28125 / BPMx100` frames
  when the tempo is constant over the loop, else the grid duration (`ms x 75 / 1000`). Anything
  else (no grid, a manual loop off the grid lengths, CD) is not touched.
- **Per pass:** a wrap is the frame going back to within 6 frames of the in from within 6
  frames of the out. At each wrap `e += (out - in) - L` (kept in 1/den frames: integers only),
  and the out of the next pass becomes `in + round(L - e)`, so `|e| <= 1/2` frame from the
  second wrap on. The first pass keeps the out the loop was made with. Any other jump (jog in
  the loop, a seek) or a change of the loop (new loop, a resize by BEAT, CALL with loop_resize,
  RELOOP) starts the count over.
- **How the out moves:** `auto_beat_loop_set(8)` (`0x0C0E570A`) with `CA90` set for one call
  so that its resize branch `FUN_0C0E5668` gives the wanted out; that is the stock BEAT-button
  resize inside a loop (`FUN_0C00A4CC` writes the requested out `0x0C7F64DC` at once and
  re-plans the ring, audio cmd 6 with the resize flag), not via `FUN_0C0E5390`, which also
  resets display state (`FUN_0C06A244`). Put back afterwards: `CA90`, the beat LED byte
  `0x0C1E1088`, `0x0CE6D155`, `0x0CE6D156`. The move is made only while the playhead is in the
  first half of the next pass, so the new out is never behind it. Skipped while
  `0x0CE6D26A == 0xFF` (the stock BEAT handler does nothing then either).
- **Evidence words:** `quant_loops_drift_ticks`, `_loops`, `_passes`, `_sum`, `_err` (1/den
  frames), `_num`/`_den`, `_q`, `_moves`, `_refused`, `_lost`.
- **Tests:** `tests/test_cdj850_loop_drift.py`: the payload on the SH-2A interpreter with a
  simulated feeder against a Python reference (128.00, 127.96, 174.00 BPM; 1, 4, 8 beats and a
  1/2-beat fraction loop; a tempo change inside the loop; an in off the grid; what is left
  alone; resets), and live in QEMU with `loop_resize`: a 4-beat auto loop for 16 passes at 128.00 BPM
  played 2250 frames = 16 × 140.625 exactly, 127.96 stayed 0.30 frame off the grid, 120.00 (150
  frames, exact) was never moved; after CALL ◀ the 2-beat loop stayed within half a frame. The tick
  runs about 80 times per second (1.07 per frame), so no wrap is missed.

## Reuse
- `quant_loops_beat_at_or_before`, `quant_loops_beat_nearest`, `quant_loops_beat_frame`,
  `quant_loops_beat_ms_after` and `quant_loops_ms_to_frames`: grid lookups in frames for
  `hot_cues` (a quantised jump, not done) and `loop_resize`. `quant_loops_in_point` also places a
  new `hot_cues` point cue on the nearest beat (hot_cues' weak default keeps the position without this mod).
- `quant_loops_sector_of(sector, pos, frame)`: the ring sector of another frame, checked
  against the sector table (`frame` must be buffered, same track).
- `loop_resize` (CALL halves/doubles via `audio_send_cmd(6, 0, new_out, 1, ...)`): new out =
  `quant_loops_ms_to_frames(quant_loops_beat_ms_after(i_in, k))` for whole beats, or the
  fraction formula of `quant_loops_auto_points`; it can send through the stock function, the
  redirects above only touch flag-0 sends of a new loop.
- half_frame: everything goes through `quant_loops_ms_to_frames` and the ring sector of a frame;
  a half-frame position would change those two and add the byte offset in the feeder.

## Ordering with the audio task
The audio task (descriptor named `DjControlTask`, entry `0x0C014FC8`, priority 3) builds the loop
in from `0x0C7F6468` when it drains mailbox 2. The key handlers run in `playertask` (entry
`0x0C0ED178`, priority 2; lower number = higher priority in this µITRON kernel).
`audio_send_cmd` queues the packet (`snd_mbx`) and sets event flag 2, which wakes the
lower-priority audio task without preempting the sender, so `auto_beat_loop_set` reaches its
refresh of the word at `0x0C0E5DAA` first. The mod writes the snapped sector before the send
(`quant_loops_auto_in`, again in `quant_loops_auto_send`) and keeps it at the refresh
(`quant_loops_auto_keep`), so the sector is right in either order. LOOP IN writes it before its
send and nothing in that path touches it afterwards. Emulator breakpoint trace: BEAT 4 ran
`auto_beat_loop_set` → `audio_send_cmd(6)` (word = 261, the snapped sector) → `0x0C0E5DAA`
(261) → audio task `0x0C0F01DC(261)` on its own stack; LOOP IN ran `audio_send_cmd(7)`
(682) → `0x0C0F01DC(682)`, with no `FUN_0C0059D2` in between.

The other order, forced in the emulator: a gdbstub breakpoint at `0x0C0E5DAA` (playertask, after
the cmd 6 send, before the refresh) turned that point into a call of `tslp_tsk(30)`
(`0x0C0C8AFA`, returning to `0x0C0E5DAA`), so playertask slept and the audio task ran first.
BEAT 4 at frame 311: the audio task built the in record from sector 261 (frame 308, the beat)
before the refresh ran; loop 308..448 as without the delay. 1/2 beat at frame 1119: sector
1069 (frame 1116), loop 1116..1134. `audio_send_cmd` itself can block only in its first call
(`0x0C0CE17C`, a wait for a free message block), before the command is queued.

## Open questions for a real deck
1. The snapped in lies up to one beat behind the playhead; the ring must still hold it. In
   the emulator it always did while playing; after a jump the mod falls back to the stock in.
2. Task order: covered both ways (see above); a real deck runs the same MAIN, so the order is
   the same, but only the emulator was checked.
3. Track end: when the grid out lies past the track, it is clamped to the last frame; if
   that is not after the playhead the loop stays stock.
4. Drift correction: it moves the out by one frame on about every other pass through the
   stock BEAT resize path. Whether a real deck plays that wrap gaplessly (the same question as
   for a BEAT resize) is not checked on hardware.

## CDJ-350 (MAIN 1.14)

`cdj350/` ports the snapping and the drift correction (see `docs/cdj350.md`,
"Quantized loops"). The CDJ-350 has no BEAT SELECT, so the switches are in UTILITY: hold
DISPLAY/UTILITY, turn past `Ver` to `QUANTIZE   ON` and `DRIFT FIX  ON`, and push to switch.
Both are saved with the deck's settings and are on on a fresh deck.
