# loop_roll (CDJ-850 MAIN 1.13): loop roll — hold a beat button, release to where the track would be

Default off. **Prototype**: emulator-tested only, never run on a deck.

| On the deck | Roll mode on | Roll mode off (default after power-on) |
|---|---|---|
| hold BEAT SELECT, press LOOP OUT | toggles roll mode (not saved); the display shows `ROLL ON` / `ROLL OFF` for 1.5 s | same |
| hold BEAT 1 / 2 / 4 / 8 while playing | the stock auto beat loop plays while the button is held (with `quant_loops`: on the grid, wrapping to the block); another beat button while held resizes it | stock beat loop |
| release every beat button | playback lands where it would be had the loop never played, and the loop is left | stock: the loop keeps playing |
| release with quantize on (and `quant_loops`, a rekordbox grid) | the roll plays on to the next quantize point of that position (UTILITY `QUANTIZE`: 1, 1/2 or 1/4 beat) and lands there; a point at most 10 blocks (19 ms) behind counts as reached and lands at once; a beat button pressed while it waits takes the release back (the roll goes on, resized by stock) | same |

BEAT SELECT alone still switches the 1/16–1/2 beat fractions, so rolls of 1/16 to 8 beats are
possible. With `hot_cues` in hot-cue mode the beat buttons stay hot cues and nothing rolls.
Roll mode has no UTILITY item (the `LOOP ROLL` item of earlier builds is gone, and its saved OFF
bit is ignored): BEAT SELECT + LOOP OUT is the only switch.

## How it works

- **The shadow playhead is counted on the sample clock.** The IRQ7 audio feeder plays one block of
  84 stereo samples (1/7 of a CD frame) per DSP request, and while a loop plays it asks
  `isr_loop_out_reached` (`0x0C0158D4`) once per block whether to wrap. That call (pool
  `0x0C07730C`) goes to the shared dispatcher `c850_loop_out_isr` (`mods/payload/common/
  c850_loop_out.c`, also `quant_loops`'), which asks `loop_roll_out_hook` first, then
  `quant_loops`' block loop, then the stock test. From the first block of a roll the hook counts
  blocks from the read position, so the count is the position the track would have reached,
  at whatever pitch the deck plays.
- **The landing is an in-ring seek.** On release the next block reads from that counted position:
  the wrapper moves the feeder's read pointer to the right ring sector and block (checked against
  the PCM ring's sector table), clears the loop segments as a stock wrap does, and suppresses the
  stock wraps until playertask has given one RELOOP/EXIT press to leave the loop. There is no
  reread from the stick and no stop of the DSP stream. Landing precision is one block (1.9 ms).
- **Quantized release.** At the release the key filter asks `quant_loops_roll_target` for the
  first quantize point at or after the shadow position (blocks = grid ms × 525 / 1000) and the
  ISR lands when its count reaches that block, so the landing is exactly on the point (block
  precision). Without `quant_loops` a weak default says "no grid" and the landing is at once. The
  stall guard (0.6 s without a counted block) only runs while the feeder stops counting, so a wait
  of up to a beat at any tempo is fine. Evidence: `loop_roll_release_pos`, `loop_roll_quantized`,
  `loop_roll_cancels`.
- **Limit.** The landing needs the audio to still be in the ring: about 15 s ahead with WAV, less
  with slower decoders. Past it the roll ends with a stock EXIT (playback goes on from the loop)
  and `loop_roll_misses` counts it.
- **Roll lengths.** With `quant_loops` (quantize on, a grid) the roll's loop wraps to the
  84-sample block, so 1/8 and 1/16 beat rolls are even (within 1.9 ms). Without it the stock loop
  wraps on whole CD frames and short rolls alternate between neighbouring lengths.

USB only (CD sources keep stock loops). Section 12b of [docs/cdj850-modding.md](../../docs/cdj850-modding.md)
has the trace; [cdj850/patch.py](cdj850/patch.py) lists the patched words.
