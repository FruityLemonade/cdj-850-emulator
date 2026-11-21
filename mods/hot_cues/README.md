# hot_cues (CDJ-850 MAIN 1.13): BEAT 1/2/4 as hot cues A/B/C

Default off. Adds a hot-cue mode to the beat-loop buttons, backed by the rekordbox hot cues
(PCOB/PCPT A, B, C) of the loaded USB track's ANLZ file.

| On the deck | Hot-cue mode on | Hot-cue mode off |
|---|---|---|
| hold BEAT SELECT, press RELOOP | toggles the mode (saved to flash, survives a restart); the display shows `HOT CUE ON` / `HOT CUE OFF` for 1.5 s | same |
| BEAT SELECT alone / RELOOP alone | stock (fraction mode / RELOOP-EXIT) | stock |
| BEAT 1 / 2 / 4, slot set | jump to hot cue A / B / C | stock beat loops |
| BEAT 1 / 2 / 4, slot empty | record the position into the slot and write the ANLZ | stock |
| hold BEAT 8, press 1 / 2 / 4 | delete the slot and write the ANLZ | stock |
| beat LEDs 1 / 2 / 4 | lit = set, blinking = empty; 8 lit | stock |

- **Jump.** The jump goes through the stock CALL path (`cue_call_prev_next`) with the hot cue as
  the selection, exactly as a memory cue would be called. Playing (or a playing loop) stays
  playing: after the jump the payload gives the state-6 handler PLAY presses until the deck plays
  (in the emulator two presses, the first comes while the cue is still being prepared; the deck
  played from the cue about 0.9 s (point) to 1.3-2 s (loop) wall time after the pad press there,
  under -icount; not measured on a deck).
  Paused or cued stays cued at the hot cue. Jumping leaves a playing loop, as CDJ hot cues do.
- **Loops.** A loop slot (rekordbox type 2) is armed like a memory loop; when the deck was playing
  it plays the loop. Recording into an empty slot while a loop plays stores that loop (the out in
  effect, also after a BEAT or `loop_resize` resize); otherwise a point at the play position.
- **Writes.** Record and delete use the firmware's own ANLZ hot-cue setter (`FUN_0C0C1442(ctx,
  path, record, 1)` → `FUN_0C0C166C` → section write `FUN_0C0C5960` → `FUN_0C0C5EEC`), the same
  machinery stock MEMORY uses for memory cues. Times are written in the deck's units (divisor 150,
  time = frames × 2, loop end = out × 2 + 1), as stock MEMORY writes them. A delete empties the
  slot before the section write; every hot_cues write also counts the extra PCPT entries the
  writer appends (hot cues past C) into the PCOB header, which the stock setter does not.
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
Switches: `CDJ_MAIN_HOT_CUES=1` (and `_TICK`, `_SELECT`, `_SELECT2`, `_LEDS`, `_ANLZ`; the row
`hot_cues` brings all six). Payload word `hot_cues_on` (1) turns every key stock at run time.
Composes with every mod on the branch (`tests/test_cdj850_hot_cues.py` builds and runs them all).

## Patch
Six pool-word redirects, no hook, no cave bytes; payload `cdj850/payload/hot_cues.c` (about 3.7 KB).

| Patch | Pool word (stock value) | Read at | Payload |
|---|---|---|---|
| `hot_cues` | `0x0C0ED038` (`FUN_0C0DD492`) | `0x0C0ECEFC`, in `panel_keys_to_internal_words` after K0..K2 are built | `hot_cues_keys`: combo, pads hidden from stock and queued |
| `hot_cues_tick` | `0x0C0EC838` (`FUN_0C0EB388`) | `0x0C0EC73E`, first call of the state tick `FUN_0C0EC73C` | `hot_cues_tick`: jump/record/delete, PLAY after a jump, ANLZ check per track |
| `hot_cues_select` | `0x0C0D41BC` (`cue_select_from_list`) | `0x0C0D3F5C` | `hot_cues_select`: the hot cue as the CALL selection |
| `hot_cues_select2` | `0x0C0D3CC8` (`cue_select_from_list`) | `0x0C0D3B94` (the USB branch the emulator takes) | same |
| `hot_cues_leds` | `0x0C0ED244` (`twai_flg`) | `0x0C0ED1B0`, the playertask loop's wait | `hot_cues_wait`: LED inputs faked while playertask waits |
| `hot_cues_anlz` | `0x0C0C195C` (`FUN_0C0C5960`) | `0x0C0C17CE`, in the hot-cue setter | `hot_cues_anlz_write`: delete, PCOB count |

Every word and its reader are outside the boot-critical closure (`mods/bootcrit.py`). See
`docs/cdj850-modding.md` section 4b.11.

## Open hardware questions
- Only the emulator was used. The jump-to-play latency, gaplessness and the LED look on a real
  panel are not verified.
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
