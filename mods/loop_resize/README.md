# loop_resize (CDJ-850 MAIN 1.13): CALL ◀ halves, CALL ▶ doubles the active loop

Default off. While a loop plays on a USB track (player state 4, `0x0CE6D0ED == 1`), CUE/LOOP
CALL ◀ halves the loop and CALL ▶ doubles it (CDJ-2000 style). The loop in stays put. There is
no loop move. Outside a loop (playing, cued, paused, a memory loop that is armed but not yet
playing, CD) CALL ◀/▶ are the stock memory-cue buttons, unchanged.

| Loop | CALL ◀ / CALL ▶ |
|---|---|
| quantize on (`quant_loops`' BEAT SELECT + LOOP IN switch; on unless switched off), a rekordbox grid gave the deck's tempo (same gate as `quant_loops`), and the loop is q/16 grid beats long (within one frame) | q/2 or 2q sixteenths of a beat on the grid, counted from the in: out = the grid time of in + q'/16 beats (piecewise over the grid, so variable-tempo grids are exact; an in on a beat or a 1/16 step of one gives the exact grid frame, an in off the grid gets in + the grid duration) |
| quantize off, no grid, or a length that is not a whole number of 1/16 beats | out = in + len/2 or in + 2 len (frames) |

Limits: 1/16 beat (1 frame without grid) and 32 beats; a press at a limit is ignored. A
doubled out past the track end is clamped to tracklen - 1 (the next ▶ is then ignored).

## How it resizes
The press goes through the stock state-4 BEAT handler (`FUN_0C0E5390` → `auto_beat_loop_set`'s
resize branch → `FUN_0C0E5668`, buffer check `FUN_0C00A4CC`, audio cmd 6 with the resize
flag), i.e. exactly what BEAT 1/2/4/8 do inside a loop, with every stock flag. The stock
resize computes `out = in + (CA90 - in) / ((8 / beats) * (frac*15 + 1))`; the payload sets
`CA90` for one call so that this is the wanted out, and puts it back afterwards (BEAT buttons
keep resizing from their own 8-beat reference). The beat-loop LED (`0x0C1E1088`) shows 1/2/4/8
when the new length is that BEAT loop (1/16..1/2 in fraction mode), else none.

As with a stock BEAT resize, the new out (`0x0C7F64DC`) takes effect in the current pass
(doubling at once; a halving at once when the playhead is in the first half of the new loop).
A new out behind the playhead would make the feeder jump back to in at once (phase lost), so a
halving pressed later in the loop waits for the next pass: the loop plays on to the old out,
wraps to in, and loops the new length from there. The old length is a multiple of the new one (to the frame: grid outs round to whole frames),
so the loop stays in phase, with no runaway past the out and no jump. Presses while a halving
waits stack (◀◀ = a quarter at the next pass). EXIT drops a waiting halving.

Stock keeps the old out in the out record (`0x0C7F64B0`) until EXIT/RELOOP, so stock MEMORY
after a BEAT resize stores the old loop. With this mod MEMORY stores the loop that plays
(resized by CALL or by a BEAT button). RELOOP after EXIT re-enters the resized loop.

## Use
    .venv/bin/python mods/patch_update.py --model cdj850 C850MAIN.UPD C850MAIN_MOD.UPD loop_resize
Switch: `CDJ_MAIN_LOOP_RESIZE=1` (and `_TICK`, `_MEMORY`; one row, `loop_resize` brings all three). Payload word `loop_resize_on` (1) turns it off at run time.
Composes with every mod on the branch (with `quant_loops` loops start on the grid, so the
resized lengths are exact grid beats, and its drift correction keeps a resized loop on the grid
pass after pass: a resize starts its count over).

The state-4 tick is shared with `quant_loops`: both redirect pool word `0x0C0D7B98` to
`c850_loop_tick` (`mods/payload/include/c850.h`), which runs this mod's `loop_resize_state4`, then
`quant_loops_drift_tick`, then the stock handler; each mod ships the same weak definition in
`cdj850/payload/loop_tick.c`.

## Patch
Three pool-word redirects, no hook, no cave bytes; payload `cdj850/payload/loop_resize.c`
(about 3 KB; it repeats the few grid helpers of `quant_loops.c` instead of sharing them, so
`quant_loops` is untouched and neither mod needs the other).

| Patch | Pool word (stock value) | Read at | Payload |
|---|---|---|---|
| `loop_resize` | `0x0C0D9994` (`cue_call_prev_next` `0x0C0D3B1A`) | `0x0C0D98AC` (▶, r4 = 1), `0x0C0D98EA` (◀, r4 = 0) | `loop_resize_call`: resize in state 4, else tail-call stock |
| `loop_resize_tick` | `0x0C0D7B98` (`keys_state4_looping` `0x0C0E0F34`) | `0x0C0D79A6` | the shared state-4 tick `c850_loop_tick` (`c850.h`, also `quant_loops`' drift correction) → `loop_resize_state4`: follow which out is in effect, send a waiting halving; then stock |
| `loop_resize_memory` | `0x0C0D9714` (`memory_button` `0x0C0D4172`) | `0x0C0D9664` | `loop_resize_memory_button`: the out record holds the effective out during MEMORY |

Every word, its readers and the routines called are outside the boot-critical closure
(`mods/bootcrit.py`). See `docs/cdj850-modding.md` section 4b.10.

## Open hardware questions
- Only the emulator was used. Whether the real audio path is gapless at a resized wrap (the
  same stock path as the BEAT buttons) is not verified on a deck.
- CALL ◀/▶ inside a playing loop no longer jump to memory cues; leave the loop (RELOOP/EXIT) first.
- MEMORY in a resized loop briefly rewrites the out record (frame and M/S/F) around the stock
  call and puts it back; if the ANLZ write blocks, the audio task could read the record in
  between (it then sees the out that is playing anyway).
- A paused loop (CDJ jog mode, state 5) is not resized (state 4 only).
