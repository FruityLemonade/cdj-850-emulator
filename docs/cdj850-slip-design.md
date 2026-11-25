# CDJ-850 slip mode for scratch, backspin, reverse and pause

Status: **implemented in `mods/loop_roll` (prototype, emulator-tested; never run on hardware).**
Builds on `mods/loop_roll` (the shadow playhead and the in-ring landing, `docs/cdj850-modding.md`
section 12b). Facts tagged **[known]** are in the modding doc or the payload headers; **[emu]** /
**[static]** are the resolved trace results.

### Resolved findings
- **Audio direction & pointer alignment [static + emu]:** `u8 0x0C7F666C` (pool `0x0C076790`) is `0`
  forward and `1` reverse. While reversing, `(pcm_read_ptr - base) % 0x150 == 332` (`0x14C`),
  returning to `0` and `0x0C7F666C == 0` when forward play resumes.
- **Ring fill during reverse / scratch / pause [emu]:** During reverse, `pcm_write_sector`
  (`0x0C7F645E`) steps back by 100 sectors whenever ahead > 1000, keeping ~900–1000 sectors (~12 s)
  ahead of the backward-moving read pointer (~6 s of forward slip range at 1×). During VINYL
  scratch and pause, `pcm_write_sector` holds ~900–1066 sectors (~12–14 s) ahead.
- **Release transitions [emu]:** On VINYL jog release, `speed_substate` (`0x0CE6CA9D`) stays `4`
  across the release ramp (~100–200 ms) and returns to `0` once normal playback resumes. On CDJ
  pause (`state 5`), `audio_task_state` (`0x0C7F6627`) is `4` (stutter) and returns to `3` on PLAY.
- **Landing from playertask [static + emu]:** Pool leads `0x0C076764` (`0x0C015258`) and
  `0x0C076768` (`0x0C005BB6`) are only inside the `0x0C7F66C8 == 2` branch of `irq7_dspdreq_isr`,
  not USB playback. Instead, playertask lands using MAIN's `c850_irq_save` (`0x0C0C688A`,
  `SR.IMASK = 15`) and `c850_irq_restore` (`0x0C0C6894`) around `c850_ring_jump()`.

## What slip should do

While slip is on, a **shadow playhead** keeps running at the tempo-fader speed. Any of these
pauses normal playback:

| Gesture | Starts | Ends (lands on the shadow) |
|---|---|---|
| Scratch (VINYL mode) | jog top touched (`u8 0x0CE6D0F3` = 1) **[known]** | jog released |
| Backspin | the same touch, platter thrown backwards | jog released (the deck restarts at the shadow, not where the spin stopped) |
| Reverse | REV pressed (K0 `0x8000`) **[known]** | REV pressed again / released |
| Pause (optional) | PLAY/PAUSE to pause | PLAY: resumes at the shadow |
| Loop roll | already built | already built |

With quantize on, the landing can wait for the next quantize point of the shadow, as the
`loop_roll` release now does (same `quant_loops_roll_target`). Pioneer's own slip lands at once
for scratch and reverse; making that a choice is cheap once the engine exists.

## Controls

- **UTILITY > `SLIP MODE`** (`ON`/`OFF`, saved): master enable/disable for slip mode (and `BEAT SELECT + LOOP OUT`). When set to `OFF`, slip mode and `BEAT SELECT + LOOP OUT` are disabled.
- **`BEAT SELECT + LOOP OUT`**: arms or disarms slip mode in RAM (`SLIP ON` / `SLIP OFF`, zero flash/USB writes mid-playback; starts disarmed at boot). When active, the jog display's **`VINYL` indicator** (`S[1]`/`S[0xF]` bit `0x18`, via gated pool `0x0C063124`) blinks at 300 ms intervals.
- **`REV` hold >= 0.75 s**: pressing and holding `REV` for 0.75 s starts momentary slip-reverse until released even when slip mode isn't armed (short `< 0.75 s` `REV` presses stay normal non-slip reverse unless slip mode is armed, and toggle latched slip-reverse when slip mode is armed). With slip mode armed, all beat rolls, `REV`, `VINYL` scratch, and pause slip back to the shadow playhead. With quantize on, releasing `REV` waits in reverse until the next 1/4, 1/2, or 1 beat quantize point before exiting reverse and landing on the shadow.

## The shadow clock

Loop roll counts the feeder's 84-sample blocks inside the loop-out test. That cannot work for
slip: during a scratch the feeder runs at jog speed (or backwards), so the blocks it eats are not
the time that passed.

**Recommended: integrate the effective pitch over a clock.**
`shadow += dt × speed`, with `speed` from the tempo fader alone: `c850_effective_pitch()`
(`c850.h`, already used by `phase_meter`, includes the tempo range and BPM lock) and **not**
`dsp_target_speed` (`0x0CE6C940`), which carries the jog bend and the vinyl ramps **[known]**.
Keep the shadow in 1/65536 blocks (u32 whole blocks + u16 fraction) to stay integer-only.

- Clock: the 1 ms tick `0x0CE49F54` **[known]**. The loop_roll trace already shows the tick and
  the sample clock agree to about 0.1 frame over 6 s. Read it from the shared state tick
  (playertask, ~80 Hz); at each call add `(tick - last) × speed`. Sub-ms resolution is not needed
  (one block = 1.9 ms).
- At the start of a slip, seed the shadow from the block the read pointer names
  (`lr_read_pos()`, as roll does), so the shadow and the audio start from the same sample.
- During a roll the existing block count stays the more exact clock; keep it for rolls.

## Detecting the gestures (playertask, the shared key filter and tick)

- **Scratch / backspin:** `jog_touched` `0x0CE6D0F3` and `speed_substate` `0x0CE6CA9D` = 1
  (VINYL scratch) **[known]**. Only in VINYL mode (`0x0CE6D0DE` = 1). In CDJ mode the jog only
  bends pitch, so no slip there.
- **Reverse:** the K0 REV bit in the key filter (press and release). The audio layer's
  direction should be confirmed with the feeder's reverse path (pool `0x0C076C74`) **[open]**.
- **Pause:** player state 3 → 5 (VINYL brake) / 4 (CDJ pause) **[known, note the USB state 4 is a
  loop]**.

## Landing

The landing primitive is `lr_jump()` in `loop_roll.c`: write `pcm_read_ptr` and `pcm_play_sector`
to the shadow's block, clear the loop segments, after checking the sector table and the ring range.

Two ways to run it outside a loop:

1. **From playertask with IRQ7 masked** (recommended first). Raise `SR.IMASK` to 7 for the few
   stores (`READ_PTR`, `PLAY_SECTOR`, `SEG_SOURCE`), then restore it. That is the same write the
   ISR makes, so the feeder never sees a half-written pointer. The landing is then up to one
   player cycle (~12 ms) late against the moment the jog is released, but it is always on the
   shadow's block, so the phase is right. No new hook, nothing boot-critical.
2. **A per-request ISR hook** (exact to the block, needed only if (1) sounds late). The forward
   loop-out word `0x0C07730C` is called only while a loop plays. Leads for a word the ISR reads on
   every request: pool `0x0C076764` → `0x0C015258`, read once at `0x0C0766B8` near the ISR's
   start, and `0x0C076768` → `0x0C005BB6` at `0x0C0766C8` **[lead]**. Each needs its readers and
   its call path traced before it is used.

**When to land:** after the firmware is back to forward playback at normal speed, not during the
transition. For reverse, wait until the direction is forward (the stock cmd-1 path adds 4 to the
pointer when leaving reverse, section 12) and the pointer is back on a 336-byte boundary from the
ring base (`(ptr - base) % 0x150 == 0`), then jump. For scratch, wait for `speed_substate` to
leave 1. Otherwise the stock ramp could re-seek after the landing.

## Range: the hard limit

The shadow must still be in the PCM ring: about 15 s ahead with WAV and 1000 sectors behind
**[known]**. Scratches and reverse bursts are short, so most slips fit. Two things need the
emulator before anything is built **[open]**:

- **Does the audio task keep filling ahead while the deck plays backwards or is held still?**
  If it re-plans the ring around the read pointer during reverse (the write index follows the
  direction), the shadow, which keeps going forward, falls out of the buffered region within
  seconds. Watch `pcm_write_sector` (`0x0C7F645E`) during a 5 s reverse and a 5 s held scratch.
- **Past the ring:** land with the stock CALL-style reload (`hot_cues` uses it; a deck needs ~5 s
  of prefill, so there is audible silence), or give up and count a miss as roll does.
  Recommendation: miss and stay put, flash `SLIP MISS`.

## Plan

1. **Emulator trace (no code):** ring behaviour in reverse, scratch and pause; `speed_substate` and
   pointer alignment around the transitions; the two ISR pool-word leads. Results go in section
   12b and `scripts/ghidra/cdj850-annotations.json`.
2. **Reverse slip** (simplest: one key, no jog): tick-integrated shadow, landing from playertask
   with IRQ7 masked. Test as `loop_roll` is tested: the interpreter for the arithmetic, QEMU for a
   5 s reverse with the landing compared to the tick.
3. **Scratch / backspin slip** on the jog touch (VINYL mode).
4. **Pause slip** and the quantized landing for all of them.
5. Rename roll mode to slip mode (`SLIP ON/OFF`), and the README and `docs/mods.md` rows.

## Risks

- The landing from playertask races the ISR only if the IRQ mask is wrong; keep the masked
  section to the stores.
- The jog's own slow ramps (VINYL release start) could move the pointer after the landing. Land
  only after the ramp.
- Hardware: none of this has run on a deck. The emulator's DSP request timing is not a real
  DSP56371's, so the scratch-release feel must be checked on hardware.
