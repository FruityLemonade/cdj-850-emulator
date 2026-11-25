# hot_cues (CDJ-850 MAIN 1.13): BEAT 1/2/4 as hot cues A/B/C

Default off. Adds a hot-cue mode to the beat-loop buttons, backed by the rekordbox hot cues
(PCOB/PCPT A, B, C) of the loaded USB track's ANLZ file.

| On the deck | Hot-cue mode on | Hot-cue mode off |
|---|---|---|
| UTILITY > `HOT CUES` | `ON` (default, saved to USB): enables hot cues | `OFF`: disables hot cues and `BEAT SELECT + RELOOP` |
| hold BEAT SELECT, press RELOOP | arms/disarms hot-cue mode in RAM (not saved, zero flash/USB writes); displays `HOT CUE ON` / `HOT CUE OFF` for 1.5 s | same (when UTILITY `HOT CUES` is `ON`) |
| BEAT SELECT alone / RELOOP alone | stock (fraction mode / RELOOP-EXIT) | stock |
| BEAT 1 / 2 / 4, slot set | jump to hot cue A / B / C and auto-play (even from paused or cued state) | stock beat loops |
| BEAT 1 / 2 / 4, slot empty | record the position into the slot (the nearest beat with quantize on, below) and write the ANLZ | stock |
| hold BEAT 8, press 1 / 2 / 4 | delete the slot and write the ANLZ | stock |
| beat LEDs 1 / 2 / 4 | lit = set, blinking = empty; BEAT 8 blinks while active | stock |

- **Jump.** Two ways, the fast one first:
  - **Instant (in the ring).** Playing (not in a loop), a point hot cue whose audio is still in
    the PCM ring (up to ~13 s already played, or the ~15 s read ahead with WAV) is reached by
    moving the IRQ7 feeder's read pointer and play sector there from playertask, with interrupts
    masked for the two stores: no CALL, no ring restart, no wait. The ring run between the play
    position and the cue is checked in the sector table first (every 16th sector and the cue's).
    The main CUE point stays where it was. `hot_cues_instants` counts these,
    `hot_cues_instant_misses` the point jumps that were too far and took the CALL path;
    payload word `hot_cues_instant` (1) = 0 turns it off.
  - **CALL (everything else).** Loop slots, a deck that is looping, and cues
    outside the ring go through the stock CALL path (`cue_call_prev_next`) with the hot cue as
    the selection, exactly as a memory cue would be called. Playing, paused, and cued decks all
    auto-play: after the jump the payload gives the state-6 handler PLAY presses until the deck
    plays. Jumping leaves a playing loop, as CDJ hot
    cues do. A hot-cue CALL **prefills 180 sectors (2.4 s of audio) instead of the stock 400
    (5.33 s)** before PLAY is taken (`hot_cues_prefill`, payload word, 0 = stock, smaller
    values are raised to 180), see *Prefill* below.
- **Loops.** A loop slot (rekordbox type 2) is armed like a memory loop; when the deck was playing
  it plays the loop. Recording into an empty slot while a loop plays stores that loop (the out in
  effect, also after a BEAT or `loop_resize` resize); otherwise a point at the play position.
- **Quantize.** With `quant_loops` in the build and quantize on (BEAT SELECT + LOOP IN, the default), a
  new point cue is recorded on the beat nearest the play position, the same snap LOOP IN uses
  (`quant_loops_in_point`: a USB track whose rekordbox grid gave the deck's tempo, the beat in the
  grid window in RAM; ties go to the earlier beat, before the first beat to the first beat). Where
  the grid cannot say, and with quantize off, the play position is kept. A recorded loop is the
  playing loop as it plays (one quant_loops made is on the grid already). Built without
  `quant_loops` the payload's weak `quant_loops_in_point` returns the position: no snapping.
  `hot_cues_snapped` counts the records that moved, `hot_cues_last_pos` holds the play position of
  the last one. The jump itself is not quantized (it does not wait for the beat).
- **Writes.** Record and delete use the firmware's own ANLZ hot-cue setter (`FUN_0C0C1442(ctx,
  path, record, 1)` → `FUN_0C0C166C` → section write `FUN_0C0C5960` → `FUN_0C0C5EEC`), the same
  machinery stock MEMORY uses for memory cues. Times are written in the deck's units (divisor 150,
  time = frames × 2, loop end = out × 2 + 1), as stock MEMORY writes them. A delete empties the
  slot before the section write; every hot_cues write also counts the extra PCPT entries the
  writer appends (hot cues past C) into the PCOB header, which the stock setter does not.
- **Prefill.** After a CALL the audio task asks the reader for 400 sectors from ring slot 120
  (`FUN_0C008C8A(4, .., 400, 120, ..)` at `0x0C0F534E`, through pool `0x0C0F54D8`, read only
  there) and the state-6 handler takes PLAY once they are in. `hot_cues_prefill` redirects that
  pool word to `hot_cues_ring_request`, which gives a smaller count when a hot-cue CALL armed it
  (2 s window); the reader's end slot, done flag and timeout follow from the count, the stock
  read of the 120 sectors before the cue and the ready report follow as after 400, and stock
  streaming refills ahead after the cue is ready. **The count must stay above about 150:** the
  audio task sets the play sector and starts that second read only once ~150 sectors are in
  (emulator: 160, 180, 200, 250, 300 became ready; 75 and 140 left the deck cued for good, PLAY
  ignored), so the payload never asks for fewer than 180. The pool word lies inside the boot-critical
  closure (the audio task): it is in `players/cdj850.GATED_BOOT_POOLS`, so it goes through the
  service-mode gate and `validate.py` checks that the gate jumps to stock `FUN_0C008C8A` while
  the deck is in a power-on update mode (or has not read the power-on keys yet). No instruction
  is changed, and USB recovery runs the stock request.
- **Fallback.** Without a loaded USB track whose ANLZ is in the ANLZ cache (CD, no ANLZ file,
  not a DB track) the BEAT buttons are stock in hot-cue mode too; the mode only changes the LEDs
  and buttons where it can act.
- **The mode** is bit `0x08` of the mod flags byte (`payload/include/c850_ui.h`), stored in the
  settings record at `+0x22` (`0x0C9DA25E`), the last byte of the 32-byte HISTORY name field,
  behind the name's NUL: `0x5A` = hot cues on (what earlier builds wrote, still read the same),
  `0x5B` = hot cues on and quantize off (`quant_loops`), `0x53` = quantize off only, 0 = neither.
- **Keys and display** go through the shared `payload/common/c850_ui.c`: the key filter chain on
  pool `0x0C0ED038` (`c850_ui_keys`, which `quant_loops` also redirects to) and the display flash
  hook on the tail of `display_stage_bpm` (`hot_cues_flash`). Docs section 4b.6: the flag bits
  that look spare (byte `+1` bits 4..6) are the settings log's end marker; a record with them set
  is ignored at boot. A HISTORY name change (factory reset) clears the mode.

## Use
    .venv/bin/python mods/patch_update.py --model cdj850 C850MAIN.UPD C850MAIN_MOD.UPD hot_cues
Switches: `CDJ_MAIN_HOT_CUES=1` (and `_TICK`, `_SELECT`, `_SELECT2`, `_LEDS`, `_ANLZ`, `_FLASH`,
`_PREFILL`; the row `hot_cues` brings them all). Payload word `hot_cues_on` (1) turns every key stock at run time.
Composes with every mod on the branch (`tests/test_cdj850_hot_cues.py` builds and runs them all).

## Patch
Seven pool-word redirects and the shared display hook, no cave bytes; payload `cdj850/payload/hot_cues.c` (about 3.7 KB).

| Patch | Pool word (stock value) | Read at | Payload |
|---|---|---|---|
| `hot_cues` | `0x0C0ED038` (`FUN_0C0DD492`) | `0x0C0ECEFC`, in `panel_keys_to_internal_words` after K0..K2 are built | `hot_cues_keys`: combo, pads hidden from stock and queued |
| `hot_cues_tick` | `0x0C0EC838` (`FUN_0C0EB388`) | `0x0C0EC73E`, first call of the state tick `FUN_0C0EC73C` | `hot_cues_tick`: jump/record/delete, PLAY after a jump, ANLZ check per track |
| `hot_cues_select` | `0x0C0D41BC` (`cue_select_from_list`) | `0x0C0D3F5C` | `hot_cues_select`: the hot cue as the CALL selection |
| `hot_cues_select2` | `0x0C0D3CC8` (`cue_select_from_list`) | `0x0C0D3B94` (the USB branch the emulator takes) | same |
| `hot_cues_leds` | `0x0C0ED244` (`twai_flg`) | `0x0C0ED1B0`, the playertask loop's wait | `hot_cues_wait`: LED inputs faked while playertask waits |
| `hot_cues_anlz` | `0x0C0C195C` (`FUN_0C0C5960`) | `0x0C0C17CE`, in the hot-cue setter | `hot_cues_anlz_write`: delete, PCOB count |
| `hot_cues_prefill` | `0x0C0F54D8` (`FUN_0C008C8A`) | `0x0C0F534C`, the audio task's cue prefill (boot-critical closure, gated) | `hot_cues_ring_request`: a short prefill after a hot-cue CALL |

Every word and its reader except `hot_cues_prefill`'s are outside the boot-critical closure
(`mods/bootcrit.py`); that one is a gated boot pool (above). See
`docs/cdj850-modding.md` section 4b.11.

## Open hardware questions
- Only the emulator was used. The jump-to-play latency, gaplessness and the LED look on a real
  panel are not verified.
- **Jump latency on a deck.** An instant jump is as fast as a loop wrap. A CALL jump waits for
  the 180-sector prefill (docs section 4b.11, "cue prefill"): 45% of the stock wait, not
  measured on a deck. The ~150-sector threshold was found in the emulator; that a deck has the
  same threshold (it is a count in the firmware, not a timing, as far as the probes show) is
  not checked.
- **LEDs** (no free hook: `display_stage_navigation_leds` is boot-critical). The LED stage's inputs
  (`0x0CE6D154` fraction mode, `0x0C1E1088` beats) hold the pattern only while playertask waits in
  its loop and their real values while it runs (playertask, priority 2, is the only other reader;
  DisplayTask is priority 3). A pass of DisplayTask while playertask blocks elsewhere (an ANLZ
  write) shows the stock pattern for that pass. Empty slots blink at the stock loop-LED rate, and
  while a slot is empty the RELOOP LED is lit (the stage lights it whenever `0x0C1E1088` ≠ 0).
- ANLZ writes block playertask for the file write, as stock MEMORY does.
- rekordbox reading deck-written hot cues (divisor 150, as for deck-written memory cues) is not
  checked here.
- While BEAT SELECT is held for the combo, fraction mode shows for that moment (stock toggles on
  the press); the combo undoes it.
- Hot cues D-H are not reachable (three pads); they are kept on writes.
