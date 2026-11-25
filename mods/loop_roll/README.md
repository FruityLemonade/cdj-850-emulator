# loop_roll (CDJ-850 MAIN 1.13): slip mode — loop roll, reverse, scratch/backspin, and pause

Default off. **Prototype**: emulator-tested only, never run on a deck.

| On the deck | Slip mode armed | Slip mode disarmed (default after power-on) |
|---|---|---|
| UTILITY > `SLIP MODE` | `ON` (default, saved to USB): enables slip mode | `OFF`: disables slip mode, `BEAT SELECT + LOOP OUT`, and momentary `REV` hold |
| hold BEAT SELECT, press LOOP OUT | arms/disarms slip mode in RAM (not saved, zero flash/USB writes); `VINYL` jog indicator flashes while active; displays `SLIP ON` / `SLIP OFF` for 1.5 s | same (when UTILITY `SLIP MODE` is `ON`) |
| hold BEAT 1 / 2 / 4 / 8 while playing | the stock auto beat loop plays while the button is held (with `quant_loops`: on the grid, wrapping to the block); another beat button while held resizes it | stock beat loop |
| release every beat button | playback lands where it would be had the loop never played, and the loop is left | stock: the loop keeps playing |
| hold REV >= 1.5 s while playing | slips during reverse and jumps to the shadow on release | momentary slip-reverse while held (>= 1.5 s), then exits reverse and jumps to the shadow on release (VINYL indicator flashes) |
| short press REV (< 1.5 s) while playing | track reverses while held/latched; on returning to forward playback, jumps to the shadow playhead | normal non-slip reverse |
| touch jog top in VINYL mode (scratch / backspin), then release | platter scratches or spins back; once the release ramp ends, playback jumps to the shadow playhead | stock VINYL scratch |
| pause with PLAY/PAUSE, then press PLAY | deck pauses; on PLAY (and after any VINYL ramp), playback jumps to the shadow playhead | stock pause |
| release / return with quantize on (and `quant_loops`, a rekordbox grid) | playback waits until the next quantize point of the shadow (UTILITY `QUANTIZE SIZE`: 1, 1/2 or 1/4 beat) and lands there; a point at most 10 blocks (19 ms) behind counts as reached and lands at once; resuming a roll/gesture before it lands keeps slipping | same |

BEAT SELECT alone still switches the 1/16–1/2 beat fractions, so rolls of 1/16 to 8 beats are
possible. With `hot_cues` in hot-cue mode the beat buttons stay hot cues and nothing rolls (reverse,
VINYL scratch, and pause still slip when slip mode is active).
UTILITY > `SLIP MODE` enables/disables slip mode persistently (default `ON`, saved to `MODSET.DAT`); `BEAT SELECT + LOOP OUT` arms/disarms slip mode in RAM without flash or USB writes, flashing the jog `VINYL` indicator while active.

## How it works

- **Loop roll shadow playhead (sample clock).** The IRQ7 audio feeder plays one block of
  84 stereo samples (1/7 of a CD frame) per DSP request, and while a loop plays it asks
  `isr_loop_out_reached` (`0x0C0158D4`) once per block whether to wrap. That call (pool
  `0x0C07730C`) goes to the shared dispatcher `c850_loop_out_isr` (`mods/payload/common/
  c850_loop_out.c`, also `quant_loops`'), which asks `loop_roll_out_hook` first, then
  `quant_loops`' block loop, then the stock test. From the first block of a roll the hook counts
  blocks from the read position, so the count is the position the track would have reached,
  at whatever pitch the deck plays.
- **Reverse, scratch, and pause shadow playhead (1 ms tick clock).** Because non-loop gestures
  do not run through the forward loop-out path (and scratch moves at jog speed), `loop_roll_tick`
  seeds the shadow at gesture start with `c850_read_pos()` and integrates the tempo-fader pitch
  `c850_effective_pitch()` (including BPM Lock, excluding jog bend/ramps) over the 1 ms tick
  `0x0CE49F54` in exact integer units (`dt * 21 * (10000 + pitch) / 400000` blocks).
- **The landing is an in-ring seek.**
  - For loop rolls, `loop_roll_out_hook` moves `pcm_read_ptr` inside `irq7_dspdreq_isr`, clears
    the loop segments, and suppresses stock wraps until playertask gives one RELOOP/EXIT press.
  - For reverse, scratch, and pause, playertask waits until the deck is back in normal forward
    playback (`audio_state == 3`, `audio_reverse == 0`, `speed_substate == 0`, pointer on a
    336-byte boundary), masks interrupts with `c850_irq_save` (`0x0C0C688A`), calls
    `c850_ring_jump()`, and restores interrupts with `c850_irq_restore` (`0x0C0C6894`).
    There is no reread from the stick and no stop of the DSP stream.
- **Quantized landing.** On release or gesture end, `loop_roll` asks `quant_loops_roll_target` for
  the first quantize point at or after the shadow position (blocks = grid ms × 525 / 1000) and
  waits until the shadow reaches that block. Without `quant_loops` a weak default says "no grid"
  and the landing is at once.
- **Limit.** The landing needs the audio to still be in the ring: about 15 s ahead with WAV during
  scratch/pause, and ~6 s during 1x reverse (while reversing, `pcm_write_sector` steps back by 100
  sectors whenever ahead > 1000, keeping ~900–1000 sectors ahead of the reversed read pointer).
  Past the buffered audio, loop roll ends with a stock EXIT (`loop_roll_misses`), while slip
  gestures stay where playback resumed and flash `SLIP MISS` (`loop_roll_slip_misses`).
- **Roll lengths.** With `quant_loops` (quantize on, a grid) the roll's loop wraps to the
  84-sample block, so 1/8 and 1/16 beat rolls are even (within 1.9 ms). Without it the stock loop
  wraps on whole CD frames and short rolls alternate between neighbouring lengths.

USB only (CD sources keep stock behavior). Section 12b of [docs/cdj850-modding.md](../../docs/cdj850-modding.md)
and [docs/cdj850-slip-design.md](../../docs/cdj850-slip-design.md) have the trace and design;
[cdj850/patch.py](cdj850/patch.py) lists the patched words.
