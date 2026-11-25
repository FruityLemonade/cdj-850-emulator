# quant_loops (CDJ-850 MAIN 1.13): loops snap to the rekordbox beat grid

Default off. Only on USB tracks whose beat grid gave the deck's tempo (rekordbox analysis);
without a grid (BPM counter, CD, unanalysed file) every loop is exactly stock.

## Quantize on/off on the deck
**UTILITY > `QUANTIZE`** (`ON`/`OFF`, saved to flash / USB) enables or disables quantize persistently.
**Hold BEAT SELECT, press LOOP IN.** While UTILITY `QUANTIZE` is `ON`, that temporarily arms or
disarms quantize in RAM only (zero flash/USB writes mid-playback; starts armed on a fresh boot),
showing `QUANTIZE ON` or `QUANTIZE OFF` for 1.5 s. When UTILITY `QUANTIZE` is `OFF`, quantize stays
off and the `BEAT SELECT` combo cannot turn it on.

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

**Every pass on the grid, to the block.** Stock loops are whole CD frames (1/75 s), so a grid loop
is up to ±0.5 frame (±6.7 ms) off per pass and the error adds up (4 beats at 128 BPM: 140 frames
for 140.625, -8.3 ms a pass, 0.27 s after 32 passes). With `quant_loops` the loop in and out come
straight from the grid in **feeder blocks** (84 samples, 1.9 ms; a frame is 7 blocks) and the IRQ7
feeder's loop-out test wraps the loop itself, to the block: every pass is within one block of the
exact grid length and a remainder carried from pass to pass keeps the average exact, however long
the loop plays (4 beats at 127.96 BPM = 984.68 blocks: passes of 984 and 985). See "Block-precise
loops" below.

Every auto loop has the playhead before its out (playhead < out); a case that would not (past
the end of a grid) stays stock. An in ahead of the playhead (the nearest beat is the next one)
is reached by playing on, exactly like a LOOP OUT ahead of it: emulator, BEAT 4 at frame 301 on
128 BPM gave in 308, the deck played on and wrapped 447 -> 308. The loop's points are blocks:
grid ms convert with `(ms x 21 + 20) / 40`. The stock loop records stay frames around them: the in
record is the frame that holds the in block, the out record the first frame at or after the out
block (so the stock test is never due before the block wrap). A loop is at least one frame long. When the 1000-beat grid window in RAM does not reach beat in + N, the
out uses the in beat's grid tempo (`N x 60000 / BPM` ms). Before the first beat of the grid
(lead-in) the first beat is the nearest. If the snapped point's ring sector is not buffered (right
after a jump) the in stays stock and the out keeps the grid length; LOOP IN/OUT stay stock.

Interaction with stock features (checked in the emulator, see the test):
- **RELOOP/EXIT**: exit leaves the loop (state 3), the records keep the snapped points, RELOOP
  re-enters the same loop.
- **Auto-loop LEDs** (page 2 byte 7): unchanged; they show the beat count stock stores in
  `0x0C1E1088`.
- **Beat buttons inside a loop** (resize, `FUN_0C0E5668`): stock code, from the loop in and
  `CA90` (the 8-beat out). The resize send (audio cmd 6, resize flag) goes through the mod, which
  makes the block loop of the new length (BEAT n: n beats; the ratio to `CA90` says which) and
  sends its out frame instead.
- **MEMORY / CALL / RELOOP / hot-cue loops**: MEMORY inside the loop stores the in/out records
  (memory cue list), CALL ◀ cues at the in and PLAY plays the memory loop. The stock entry
  (RELOOP, a memory or hot-cue loop) lands on the in sector's start, up to 6 blocks before the in
  block; on the first block after it the mod moves on to the in block. A loop the mod did not set
  (one stored by an older build or on another deck) is looked up on the grid from its records by
  the state-4 tick and gets a block loop too.
- LOOP OUT pressed past the snapped out (nearest beat behind the playhead): the loop closes at
  once and plays from in, as stock does for its own out.

## Quantize items in UTILITY
**Hold MENU/UTILITY, turn past VERSION No.:**
- **`QUANTIZE`** (`ON` default / `OFF`, saved): enables or disables quantize persistently.
- **`QUANTIZE SIZE`** (`1 BEAT` default → `1/2 BEAT` → `1/4 BEAT`, saved; hidden when `QUANTIZE` is `OFF`): sets the grid step of every quantized action:
LOOP IN, LOOP OUT, the in of an auto beat loop (whole-beat loops start on the nearest point and
are N beats of grid time long from there), a new `hot_cues` point cue, and the `loop_roll`
release. Fraction loops (BEAT SELECT) keep snapping to multiples of their own length. `BEAT SELECT + LOOP IN`
arms/disarms quantize in RAM during a performance without wearing out USB drives or flash. The UTILITY
values persist in `/PIONEER/MODSET.DAT` and flash/RAM.

## UTILITY QUANTIZE DRIFT is gone
Earlier builds corrected loop drift a frame at a time (`quant_loops_drift`, UTILITY
`QUANTIZE DRIFT ON/OFF`). Block loops have no drift to correct, and where the block path cannot
run the correction could not help either (no grid or quantize off: no grid length; the in out of
the ring: the stock wrap re-reads the ring with a gap anyway; reverse: rare), so the item, its
tick and its flag are gone on the CDJ-850. The flag bit (`+0x21` bit `0x02`,
`C850_UI_FLAG_DRIFT_OFF`) is left alone; the CDJ-350 port still uses it.

**Settings on the stick (prototype).** `quant_loops_usb_settings` reads `/PIONEER/MODSET.DAT`
when a rekordbox stick mounts; while it is in, every on-deck switch (QUANTIZE and the other mods'
flags) is also written there, so the settings travel with the stick to any
deck with this build. See `docs/cdj850-modding.md` section 4b.6.

## Use
    .venv/bin/python mods/patch_update.py --model cdj850 C850MAIN.UPD C850MAIN_MOD.UPD quant_loops
Switch: `CDJ_MAIN_QUANT_LOOPS=1` (and its fourteen sibling `CDJ_MAIN_QUANT_LOOPS_*` knobs in
`mod.conf`, all one row: `quant_loops` on the command line brings all fifteen patches). The payload
word `quant_loops_on` (1 = the mod may snap) and the on-deck switch above are both checked first by
every routine, the block loops included.

## How
Fourteen pool-word redirects and one display hook into the shared payload
(`cdj850/payload/quant_loops.c`, `loop_tick.c`, and the shared `payload/common/c850_ui.c` and
`c850_loop_out.c`), no cave bytes but one 8-byte trampoline slot. Each word and every
instruction that reads it is outside the boot-critical closure (`mods/bootcrit.py`); the IRQ7
word is read by the ISR, whose vector MAIN installs at run time, so the static walk does not reach
it and the generated service gate returns to stock. See `docs/cdj850-modding.md` section 4b.9 and
`docs/cdj850-block-loops-design.md`.

| Patch | Pool word (stock value) | Read at | Payload |
|---|---|---|---|
| `quant_loops` | `0x0C0E5D4C` (`FUN_0C073954`) | `0x0C0E5C0E` | `quant_loops_auto_in`: snap the in (D1C0 and the in sector `0x0C7F6468`), compute the grid out |
| `quant_loops_auto_send` | `0x0C0E5FC8` (`audio_send_cmd`) | auto_beat_loop_set only | `quant_loops_auto_send`: grid out into the new loop's cmd 6 |
| `quant_loops_auto_send2` | `0x0C0E5D08` (`audio_send_cmd`) | auto_beat_loop_set only | same, cued path |
| `quant_loops_resize` | `0x0C0E62CC` (`audio_send_cmd`) | the state-4 resize sender only | `quant_loops_auto_send`: a resize (cmd 6, resize flag) gets the block loop of the new length |
| `quant_loops_resize_req` | `0x0C0E62BC` (`FUN_0C00A4CC`) | `0x0C0E602E`, `0x0C0E61B4` | `quant_loops_resize_req`: the out request a resize writes (the send follows only when it returns 1) gets the block loop's frame |
| `quant_loops_auto_keep` | `0x0C0E5FD0` (`FUN_0C0059D2`) | `0x0C0E5DAA` | keep the snapped in sector (stock refreshes it right after the send) |
| `quant_loops_loop_in` | `0x0C0DFA80` (`FUN_0C0059D2`) | `0x0C0DF95E` | `quant_loops_loop_in`: in sector to the nearest beat |
| `quant_loops_loop_out` | `0x0C0E0090` (`audio_send_cmd`) | LOOP OUT code only | `quant_loops_loop_out`: out sector of cmd 1/mode 3 |
| `quant_loops_loop_out2` | `0x0C0E02C0` (`audio_send_cmd`) | LOOP OUT code only | same, other branch |
| `quant_loops_keys` | `0x0C0ED038` (`FUN_0C0DD492`) | `0x0C0ECEFC` | `c850_ui_keys`, the shared key filter chain (also `hot_cues`'): BEAT SELECT + LOOP IN |
| `quant_loops_flash` | hook, tail of `display_stage_bpm` (`0x0C0642AA`, the shared `bpm999` span) | DisplayTask | `c850_ui_flash_hook`: `QUANTIZE ON/OFF` on the matrix, rows put back after 1.5 s |
| `quant_loops_tick` | `0x0C0D7B98` (`keys_state4_looping`) | `0x0C0D79A6` | the shared state-4 tick `c850_loop_tick` → `quant_loops_loop_tick` (which loop the block path owns), then stock |
| `quant_loops_blocks` | `0x0C07730C` (`isr_loop_out_reached` `0x0C0158D4`) | `0x0C07715E` (IRQ7) | the shared loop-out dispatcher `c850_loop_out_isr` (also `loop_roll`'s) → `quant_loops_out_hook`: the block wrap |
| `quant_loops_utility_scroll` | `0x0C0E797C` (`utility_scroll_item`) | `0x0C0E77C4` | `c850_ui_utility_scroll`: UTILITY list continues after VERSION No. (shared with `waveform`) |
| `quant_loops_utility_select` | `0x0C0E7F10` (`utility_set_menu_level`) | `0x0C0E7E64` | `c850_ui_utility_select`: the QUANTIZE value (shared with `waveform`) |

The grid is read from the window in RAM (`0x0C9D24FC`, read-only, never paged; `bpm_decimals`'
redirect of `grid_find_beat` is not involved), and only when the base BPM word equals what
`FUN_0C073788` made of a beat within 8 of the position (the proof `bpm_decimals` uses).

## Block-precise loops
The IRQ7 feeder plays one block of 84 stereo samples per DSPDREQ and, while a loop plays forward,
asks `isr_loop_out_reached(play_sector, out_frame, 0)` (`0x0C0158D4`) once per block through pool
`0x0C07730C`; the read pointer then already names the next block. That word belongs to the shared
dispatcher `c850_loop_out_isr` (`mods/payload/common/c850_loop_out.c`): `loop_roll_out_hook`
first (a roll counts and lands), then `quant_loops_out_hook`, then the stock test. A hook says -1
for "not mine".

- **The loop.** In: the grid point in ms to blocks, `in_blk = (ms x 21 + 20) / 40`, with its
  rounding remainder `a0`. Length: kept exactly as `num / den` in 1/40 blocks: `q x 7875000 /
  BPMx100` for q/16 beats at a constant tempo over the loop, else the grid duration
  `(grid_ms(i, b + q) - grid_ms(i, b)) x 21 / 1`. Each pass: `len = (num + acc) / (40 den)`,
  `acc = (num + acc) % (40 den)` (Bresenham; `acc` starts at `a0 x den`), `out_blk = in_blk + len`.
  So every pass is floor or ceil of the exact length and the wraps stay within half a block of the
  exact grid times from the in, however long the loop plays.
- **The wrap** (`quant_loops_out_hook`, interrupt context, frame <= 64 bytes, no RTOS calls): the
  position of the next block is read from the read pointer every block (`c850_read_pos`: frame of
  the play sector x 7 + the block in the sector), so a jump by anyone else is seen at once and
  there is no cached position to invalidate. While the position is before `out_blk` it returns 0
  (no stock wrap). At `out_blk` it moves the read pointer to `in_blk` plus the overshoot (normally
  0; modulo the length) with `c850_ring_jump` (the in-ring jump `loop_roll` lands with: 336-byte
  steps from the ring base, the sector table checked, the loop segments cleared as a stock wrap
  does), takes the next pass length, and returns 0.
- **Who arms it.** Every out writer goes through `quant_loops_set_block_loop(in_blk, a0, num,
  den)`: the auto beat loop (at its cmd 6 send), LOOP OUT, and resizes (the cmd 6 resize send of
  `auto_beat_loop_set`: BEAT n inside a loop, `loop_resize`'s CALL ◀/▶ with its q passed by
  `quant_loops_resize_hint`; that send reads pool `0x0C0E62CC`, `quant_loops_resize`). A loop nobody here set (a memory or hot-cue loop, RELOOP of an older
  loop, an out changed elsewhere) is found by the state-4 tick `quant_loops_loop_tick` from its
  stock records: the in's grid point (the frame holding a 1/16 step's block, or its rounded frame
  for loops stored before block loops; else the frame's first block) and the q whose out block
  lands in the out frame (`quant_loops_find_q`).
- **Entries.** A stock entry (RELOOP, CALL + PLAY, a hot-cue loop) lands on the in sector's start,
  up to 6 blocks before `in_blk`. The hook sees a position that does not follow the last one; when
  it lies in the in sector it moves on to `in_blk` plus the blocks played since and restarts the
  passes from the in (`quant_loops_blk_realigns`).
- **Where it stays stock (frame precise).** No grid or a grid that did not give the tempo,
  quantize off (the key filter disarms at once), a loop whose length is no q/16 beats of the grid,
  CD, reverse play (pool `0x0C076C74` is not redirected), and a pass whose in is no longer in the
  ring (more than about 1000 sectors behind, or slot reused): the hook returns -1 and the stock
  test wraps at the out frame, re-reading if needed; the entry after that is moved on to the in
  block as above (`quant_loops_blk_misses`).
- **The stock test** (traced, `0x0C015A74`, forward path): wrap when the play sector's frame is
  past the out frame (same track); at the out frame it notes sector + 1 in `0x0C7F6784`, which only
  the loop-segment remap (`0x0C015D8E`) reads. The block wrap never lets the stock test fire while
  it owns the loop, so no loop segments are built.
- **Evidence words:** `quant_loops_blk_arms`, `_derived`, `_calls`, `_wraps`, `_realigns`,
  `_misses`, `_passes`, `_len_last`/`_min`/`_max`/`_sum` (whole passes, blocks), `_blocks` and
  `_tick0`/`_tick` (blocks played and the 1 ms tick since the first wrap), `_pos`, `_in`, `_a0`,
  `_out0`, `_num`, `_den`, `_next`.
- **Tests:** `tests/test_cdj850_block_loops.py`: the payload on the SH-2A interpreter with a
  simulated feeder (128.00, 127.96 and 133.33 BPM, 1/4 to 4 beats, a tempo change inside the loop,
  1000 passes, the ring miss, RELOOP, a memory loop found by the tick, a roll inside a block loop),
  and a 60 s live run in QEMU.

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
- `quant_loops_q_out(in, q)`: the stock out frame of the block loop q/16 beats long from a loop
  in; `loop_resize` takes its grid frames from it when `quant_loops` is built.

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
4. Block wraps: a jump inside a sector is click-free in the emulator (as `loop_roll`'s landing);
   whether the DSP56371 on a real deck plays it without a click (its own buffering is not
   modelled) is not checked on hardware.

## CDJ-350 (MAIN 1.14)

`cdj350/` ports the snapping and the drift correction (see `docs/cdj350.md`,
"Quantized loops"). The CDJ-350 has no BEAT SELECT, so the switches are in UTILITY: hold
DISPLAY/UTILITY, turn past `Ver` to `QUANTIZE   ON` and `DRIFT FIX  ON`, and push to switch.
Both are saved with the deck's settings and are on on a fresh deck.
