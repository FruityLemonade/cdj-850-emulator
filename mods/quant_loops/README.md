# quant_loops (CDJ-850 MAIN 1.13): loops snap to the rekordbox beat grid

Default off. Only on USB tracks whose beat grid gave the deck's tempo (rekordbox analysis);
without a grid (BPM counter, CD, unanalysed file) every loop is exactly stock.

| Press | Stock | With quant_loops |
|---|---|---|
| BEAT 1/2/4/8 (auto beat loop) | in = the playhead, out = in + beats x 4500 / whole BPM (frames; 4 beats at 128 BPM = 140) | in = the grid beat at or before the playhead (the playhead stays inside the loop, phase kept), out = the grid time of beat in + N, so variable-tempo grids are exact |
| BEAT SELECT (fraction 1/16..1/2) + BEAT n | out = in + n x 4500 / (BPM x 16) | L = n/16 of the current beat's interval (grid times); in = the latest multiple of L after the beat that is at or before the playhead, out = in + L. 1/2 pressed 3/4 into a beat: in = beat + 1/2, out = the next beat |
| LOOP IN | in = the playhead | in = the nearest grid beat |
| LOOP OUT | out = the playhead | out = the nearest grid beat after the loop in (the next one if the nearest is not after in) |
| RELOOP/EXIT, CALL, MEMORY, beat resize in a loop | | stock (they use the points set above) |

Every auto loop keeps the playhead inside it (in <= playhead < out); a case that would not
(past the end of a grid) stays stock. All points are whole frames (1/75 s): grid ms convert with `(ms x 75 + 500) / 1000`. A loop is
at least one frame long. When the 1000-beat grid window in RAM does not reach beat in + N, the
out uses the in beat's grid tempo (`N x 60000 / BPM` ms). Before the first beat of the grid
(lead-in) the auto loop is stock. If the snapped point's ring sector is not buffered (right
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

## Use
    .venv/bin/python mods/patch_update.py --model cdj850 C850MAIN.UPD C850MAIN_MOD.UPD quant_loops
Switch: `CDJ_MAIN_QUANT_LOOPS=1` (and its six sibling `CDJ_MAIN_QUANT_LOOPS_*` knobs in
`mod.conf`, all one row: `quant_loops` on the command line brings all seven patches).
There is no on-deck toggle. A future QUANTIZE toggle would set or clear the payload word
`quant_loops_on` (1 = snap); every routine checks it first.

## How
Seven pool-word redirects into the shared payload (`cdj850/payload/quant_loops.c`, about
3.0 KB), no hook, no cave bytes. Each word and every instruction that reads it is outside
the boot-critical closure (`mods/bootcrit.py`). See `docs/cdj850-modding.md` section 4b.9.

| Patch | Pool word (stock value) | Read at | Payload |
|---|---|---|---|
| `quant_loops` | `0x0C0E5D4C` (`FUN_0C073954`) | `0x0C0E5C0E` | `quant_loops_auto_in`: snap the in (D1C0 and the in sector `0x0C7F6468`), compute the grid out |
| `quant_loops_auto_send` | `0x0C0E5FC8` (`audio_send_cmd`) | auto_beat_loop_set only | `quant_loops_auto_send`: grid out into the new loop's cmd 6 |
| `quant_loops_auto_send2` | `0x0C0E5D08` (`audio_send_cmd`) | auto_beat_loop_set only | same, cued path |
| `quant_loops_auto_keep` | `0x0C0E5FD0` (`FUN_0C0059D2`) | `0x0C0E5DAA` | keep the snapped in sector (stock refreshes it right after the send) |
| `quant_loops_loop_in` | `0x0C0DFA80` (`FUN_0C0059D2`) | `0x0C0DF95E` | `quant_loops_loop_in`: in sector to the nearest beat |
| `quant_loops_loop_out` | `0x0C0E0090` (`audio_send_cmd`) | LOOP OUT code only | `quant_loops_loop_out`: out sector of cmd 1/mode 3 |
| `quant_loops_loop_out2` | `0x0C0E02C0` (`audio_send_cmd`) | LOOP OUT code only | same, other branch |

The grid is read from the window in RAM (`0x0C9D24FC`, read-only, never paged; `bpm_decimals`'
redirect of `grid_find_beat` is not involved), and only when the base BPM word equals what
`FUN_0C073788` made of a beat within 8 of the position (the proof `bpm_decimals` uses).

## Reuse
- `quant_loops_beat_at_or_before`, `quant_loops_beat_nearest`, `quant_loops_beat_frame`,
  `quant_loops_beat_ms_after` and `quant_loops_ms_to_frames`: grid lookups in frames for
  `hot_cues` (snap a hot cue / quantised jump) and `loop_resize`.
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
