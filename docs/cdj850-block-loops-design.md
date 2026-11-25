# CDJ-850 block-precise loops: design brainstorm

Status: **plan steps 1-3 built** (branch `cdj850-block-loops`): the shared dispatcher,
block loops in `quant_loops` with every out writer on `quant_loops_set_block_loop`, the
RELOOP/memory re-alignment, and the frame drift correction retired. Steps 4 (QEMU phase test;
a deck still open), 5 and 6 (CUE in pause) are not. "As built" below records where the build
differs from the brainstorm. Tags as in `docs/cdj850-slip-design.md`: **[known]** is in the
modding doc, the payload or a test; **[open]** needs the emulator or a deck.

## As built

- **Dispatcher:** `mods/payload/common/c850_loop_out.c`, `c850_loop_out_isr` on pool
  `0x0C07730C` (mod names `loop_roll`, `quant_loops_blocks`), weak `loop_roll_out_hook` then
  `quant_loops_out_hook` (-1 = not mine), then stock. `lr_read_pos` / `lr_jump` moved there as
  `c850_read_pos` / `c850_ring_jump`; loop_roll is unchanged otherwise.
- **No cached position.** The hook reads the position from the read pointer every block (one
  `ring_sector_to_frame` call, a table load in USB mode). Any jump by anyone (a roll landing, a
  stock entry) is seen at once, so there is nothing to invalidate; a position that does not
  follow the last one is how an entry is detected.
- **Length as a rational.** `num / den` in 1/40 blocks: `q * 7875000 / BPMx100` when the tempo is
  constant over the loop (exact long-run average, not the integer-ms grid entries), else
  `(grid_ms(i, b+q) - grid_ms(i, b)) * 21 / 1`. The accumulator starts at the in's rounding
  remainder, so the wraps track the exact grid times from the in within half a block.
- **The hook owns the wrap entirely** while armed: it returns 0 for every block before `out_blk`
  (not -1), so stock records that are not floor/ceil (a memory loop stored by an older build)
  cannot wrap early. Writers still set in = floor(in_blk/7), out = ceil(out_blk/7).
- **Ownership:** writers arm directly; the state-4 tick (`quant_loops_loop_tick`, replacing the
  drift tick on the same shared word) finds loops nobody armed from their records (in: the frame
  holding a 1/16 step's block, else its rounded frame, else the frame's first block; q: the
  length whose out block lands in the out frame), and waits up to 50 cycles for the audio task
  to apply a writer's records. The ISR checks state 4, loop active and the in record each block.
- **Resizes:** the cmd 6 resize send of `auto_beat_loop_set` goes through the mod already
  (`quant_loops_auto_send`); q is `loop_resize`'s (`quant_loops_resize_hint`), else the stock
  ratio to `CA90`, else the search. `loop_resize` takes its grid frames from `quant_loops_q_out`.
  A halving past the new out needs no deferral any more (the overshoot keeps the phase), but
  loop_resize's deferral is left in.
- **Drift item:** gone entirely on the CDJ-850 (see the quant_loops README); the flag bit stays
  for the CDJ-350.
- **Stock test semantics** (traced): forward wrap when the play sector's frame is past the out
  frame; the ISR passes the applied out record `0x0C7F64B0`. **[known]**

## The problem

Stock loops are whole CD frames (1/75 s = 13.3 ms). The in sits at a sector start and the
ISR wraps when the play sector reaches the out frame. A grid loop is almost never a whole
number of frames, so each pass is up to ±0.5 frame (±6.7 ms) off the grid length. The drift
correction stops the error from adding up over many passes by moving the out a frame now and
then, but within one pass the error is still there. That is the remaining "slightly off" feel.

The feeder already works in **blocks**: one DSPDREQ plays 84 stereo samples (0x150 bytes,
1.9 ms), and a sector is exactly 7 blocks **[known]**. `loop_roll` already lands the read
pointer on any block inside a sector, with no audio glitch in the emulator **[known]**. A loop
whose in and out are blocks would be at most ±0.95 ms off per pass, and with an error
accumulator it would never drift over many passes.

## Idea

Keep the stock loop (state 4, the in and out records, the LEDs, MEMORY, RELOOP) as the
**frame-level frame** around the loop, and let the ISR take over the wrap itself, to the block:

- `in_blk` = grid ms → blocks of the in point (`(ms*21+20)/40`), not rounded to a frame.
- `out_blk` = `in_blk` + the loop length in blocks (the exact grid length, see "No drift").
- Stock records: in frame = `in_blk / 7` (floor), out frame = `ceil(out_blk / 7)`, so the stock
  test is never due before ours. The display, MEMORY and loop_resize keep working on frames.
- In the forward loop-out test (pool `0x0C07730C`, once per block while a loop plays
  **[known]**): `pos` = position of the block the read pointer names. When `pos >= out_blk`,
  move the read pointer to `in_blk + (pos - out_blk)` (the overshoot is normally 0), clear the
  loop segments as a stock wrap does, and return 0 (no stock wrap). Before that, return 0 as well
  if the stock test would fire early, which the ceil above should rule out.

This is `lr_jump` again, aimed backwards to `in_blk`. It stays on 336-byte boundaries from the
ring base, so the feeder's alignment rule holds **[known]**.

## Cost

The test runs ~525 times a second, only while a loop plays. Per call: a cached position
(`pos++`, re-read from the pointer after each jump, as `loop_roll_isr` does), one compare, and
the stock call. `c850_ring_sector_to_frame` (`0x0C00851E`) is called only at a wrap. That is
a few dozen instructions per block, about what `loop_roll_isr` already adds; no RTOS calls.

## No drift: an error accumulator

A grid length of `L` ms is `L*21/40` blocks, almost never a whole number. Keep the remainder:

    len_blk = (L*21 + acc) / 40;   acc = (L*21 + acc) % 40;   out_blk = in_blk + len_blk

recomputed at every wrap (Bresenham). Each pass is then off by less than one block, and the
average is exact for as long as the loop plays. Integer-only and tiny. With a variable-tempo
grid, `L` is `grid_ms(i, b+q) - grid_ms(i, b)` taken once when the loop is set.

This replaces the frame-level drift correction for loops the block path owns:
`quant_loops_drift_tick` must leave such a loop alone (it would move the stock out frame, which
the block path ignores anyway, but its counters and MEMORY save would be wrong). Loops the block
path does not own (no grid, quantize off, out of range) keep the frame drift correction.

## Who owns the pool word

`loop_roll` already redirects `0x0C07730C`. Two ISR mods on one word need a fixed order.
Recommendation: **move the redirect to the shared payload** (`mods/payload/common`):
`c850_loop_out_isr(sector, out, z)` calls, in order, a weak `loop_roll_out_hook` (a roll owns
the wrap while it counts or lands), a weak `quant_loops_out_hook` (the block loop), and then
the stock test. Each hook returns -1 for "not mine", or the value to return. That keeps each mod
building alone, like the existing weak-symbol pattern, and needs one `PoolRedirect`.

A roll inside a block loop: the roll's shadow counts from the read pointer, so it is block
precise already; on release the block loop must re-read its position (`pos` cache invalid after
any jump that is not its own).

## Edge cases

- **Entering the loop.** Auto loop and LOOP IN start where the playhead is, so the first pass
  starts on the real in (with quant_loops' snapping, the nearest point). Only the wraps need the
  block path.
- **RELOOP and memory loops.** The stock re-entry seeks to the in sector start, up to 6 blocks
  (11 ms) before `in_blk`. Fix in the hook: on the first call after a re-entry, if
  `in_frame*7 <= pos < in_blk`, jump to `in_blk`. Memory cues store frames, so `in_blk` must be
  recomputed from the grid on re-entry (the in frame's nearest grid point), or the sub-block
  offset stored in a spare cue word (`[5..8]` extra positions **[known]**, unused by stock
  for loops? **[open]**).
- **The in is not in the ring.** A stock wrap re-reads the in when `loop_in_reread_needed`
  (`0x0C0093A8`) says so **[known]**; the ring keeps about 1000 sectors behind (13 s). For a
  loop longer than that, or whenever `lr_jump`'s ring checks fail, fall back to the stock wrap
  (frame precise) for that pass.
- **loop_resize and CALL.** They change the out frame request; the hook must take the new length
  from quant_loops (it already computes the resized out from the grid), not from the frame. One
  shared `quant_loops_set_block_loop(in_blk, len)` that every out writer calls.
- **Reverse.** The reverse test (pool `0x0C076C74`) stays stock (frame precise). Reverse loops are
  rare; the block path only owns forward play.
- **No grid / quantize off.** Stock, unchanged.

## The loop in on the block

The in matters in three places, and each one can use the block:

1. **Every wrap** jumps to `in_blk`, the grid point rounded to a block (±0.95 ms), not the sector
   start (up to 6 blocks, 11 ms, early today). That is the main gain and is covered above.
2. **The first pass** needs nothing. With the in ahead of the playhead, the deck plays on into
   the loop without a jump, so the audio is continuous and the beat arrives when it is due. With
   the in just behind (nearest beat already passed), the first pass starts where the playhead is
   and ends at `out_blk`, which is measured from `in_blk`, so it still ends on the grid.
3. **Entering a stored loop** (RELOOP, a memory loop, a hot-cue loop): the stock seek lands on
   the in sector start. Re-align on the first block (see Edge cases), or, from a cued state,
   start on the block directly (next section).

Sample precision (4-byte steps) is not worth it: the ring wrap and `pcm_align_read_position`
assume 336-byte steps from the ring base **[known]**, and a non-multiple leaves a residue that
plays 42 samples of foreign data at the next ring wrap. One block (1.9 ms, ±0.95 ms rounding) is
below what a flam sounds like.

## CUE in pause with quantize on: the nearest beat

Wanted: with quantize on and a grid, pressing CUE while paused sets the cue on the nearest beat,
and the deck sits there (pause preview and PLAY start from the beat), not where the jog left it.
No grid or quantize off: stock.

**Frame level (no boot-critical edit):**

- In the shared key filter, on the CUE press edge (K0 `0x4000` **[known]**) in the pause states,
  note the press; let stock run, which sets state 6 with the cue record `0x0C7F6498` = the paused
  frame **[known]**.
- In the shared state tick, once state 6 is reached: `f = quant_loops_in_point(frame)` with a
  whole-beat step (`ql_point_nearest(frame, 16, …)`), or the UTILITY quantize value if CUE should
  follow it like LOOP IN does (a choice: Pioneer's quantize uses the set value).
- When `f != frame`, move the cue there **inside the ring**: the nearest beat is at most half a
  beat away (≤ 0.25 s at 120 BPM), always buffered, so no CALL-style reload and no 5 s prefill.
  Write the cue record (frame, MSF, ring sector `0x0C7F64A8`) and `D158`, and park
  `pcm_read_ptr` at the new sector as stock parks it. The record builder is `0x0C0F01DC(sector)`
  (audio task, the LOOP IN path) **[known]**; whether playertask may call it, or should ask the
  audio task with the same cmd 7/2 LOOP IN uses, is **[open]**: trace one CUE in pause and one
  LOOP IN in the emulator first.
- Result: the cue is on the beat's frame, ±0.5 frame (±6.7 ms). Memory cues saved after that
  keep the snapped frame.

**Block level (needs the user's OK for one boot-critical pool edit):**

The play start reads the cue record's ring sector and sets the read pointer to its start
(`FUN_0C009B7E` → `pcm_set_read_sector`, pool `0x0C009D4C`), then starts the DSP stream
**[known]**. Section 12 of the modding doc already sketches redirecting that pool word to a
wrapper that adds an offset before `dsp_stream_start` (no race with the first DSP request). Use
it for blocks: a one-shot `cue_block` (0..6) armed by playertask for that sector, added as
`cue_block * 0x150`. Then PLAY from the cue starts within ±0.95 ms of the beat, and the same
wrapper serves RELOOP or a hot-cue loop entered from a cued state. `FUN_0C009B7E` is in the
boot-critical closure, so this step needs that permission and a boot test.

**Pause preview** (holding CUE at the cue) plays from the parked pointer; it should start from
the same block. Whether stock re-parks the pointer from the cue record each time, or plays from
the last park, is **[open]**.

## Tests

- **Interpreter** (like `_roll_deck` in `tests/test_cdj850_loop_roll.py`): feed the hook
  blocks and check the pass lengths in blocks, the accumulator's average over 1000 passes for
  128, 127.96 and 133.33 BPM, the fall-back when the ring check fails, and the RELOOP
  re-alignment.
- **QEMU:** a 4-beat loop on Q12796 for 60 s; per-pass block counts from an evidence counter
  (all within ±1 block of `L*21/40`) and the phase against the 1 ms tick at the end (< 2 ms,
  where the frame loops hit up to 6.7 ms per pass).
- **Hardware [open]:** whether a mid-sector jump is click-free on a real DSP56371 (the
  emulator does not model the DSP's own buffering), and whether ±1 ms per pass is audibly better
  than ±6.7 ms (it should be: 6.7 ms is a flam, 1 ms is not).

## Plan

1. Shared dispatcher for pool `0x0C07730C`; move `loop_roll` onto it (no behaviour change,
   existing tests).
2. `quant_loops`: `in_blk`, the accumulator, the hook, and `set_block_loop` from every out
   writer; the drift tick skips owned loops. Interpreter tests.
3. RELOOP / memory-loop re-alignment.
4. QEMU phase test, then a deck.
5. CUE in pause on the nearest beat, frame level (independent of 1-4; can ship first).
6. Block-level play start (boot-critical pool `0x0C009D4C`, only with the user's OK): the cue
   and loop entries from a cued state.
