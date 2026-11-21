# bpm_probe (CDJ-850 MAIN 1.13): hardware glass test

A probe, not a feature. Default off. Use it on one real deck to see what the front panel
does with values the stock firmware never sends:

- BPM box: forced to `199` (stock never sends 181-999 in the BPM box).
- Frame digits (the `F` in `M:S:F`, TX page 0x01 byte 7): cycle `75 -> 99`, one step every
  128 `display_stage_bpm` calls (about 2.5 s in the emulator's capture timestamps). Stock
  frames are `0..74`.

## What to look for on the glass
1. The BPM box shows `199` (three digits, not blank, not `1` or `19` with a stray segment).
   If it shows `199`, the panel MCU draws a number above 180. This is the first question.
2. The frame digits, while the probe runs, show a value between `75` and `99`, stepping
   about every 2.5 s, through 99 and back to 75. If the panel shows `75..99` as normal
   digits, a two-digit frame is drawn. If it shows blanks, dashes, `00`, or a wrapped value,
   the panel does not draw frames above 74.
3. Anything else (a flicker, a lit segment in the wrong place, a reset) is also worth a note.

The probe does not touch playback or the audio path. It runs only in the display path,
so the deck keeps playing, and the time readout will be wrong while it is on.

## Build and flash (single deck, service-mode USB update)
    .venv/bin/python mods/patch_update.py --model cdj850 C850MAIN.UPD C850MAIN_PROBE.UPD bpm_probe

Flash it with the same service-mode USB update that installs stock (docs/cdj850-modding.md
section 2). Use a bumped `--version` if the updater refuses an equal version.

## Flash back to stock
Flash the stock `C850MAIN.UPD` with the same service-mode USB update. Its SHA-256 is
`12ed3453...` (see section 2). The probe changes only the MAIN image (no bootloader, no
updater, no panel boot code), so a stock update restores the deck completely.

## Where it lives
- The routine: `mods/bpm_probe/cdj850/main_bpm_probe.s` (96 bytes, `main_bpm_probe.bin`),
  hooking the same span as bpm999 (`0x0C0642AA..0x0C0642B8`).
- Counters: two longwords at `0x0DDF0C40` (tick) and `0x0DDF0C44` (frame), unused SDRAM
  (docs section 8). The hook blob cannot hold them: MAIN's image is read-only at run time.

## Verified
- Emulator, probe flash, WAV stick, track loaded: SCIF1 TX page 0x01 BPM word is `199` on
  every frame, frame byte cycles 75..99 (`tests/test_cdj850_bpm999.py::test_live_probe_build...`).
  About 2% of frames (37 of 1931 in one run) read `0`: the time code rewrites the staging frame
  byte between `display_stage_bpm` calls, and the TX builder can copy that value. A probe that
  shows `0` now and then is expected, and it is not a sign of a fault.
- Stock flash, same run: BPM word `0xFFFF` (blank), frame 0..74.
- Interpreter: first call pulls an out-of-range frame into 75..99; the first step is on
  call 128 (`test_probe_routine_on_the_interpreter`).

Not verified: the real panel's drawing of 199 or of frames 75..99 (the emulator's VFD model is
our own, not the panel MCU ROM).
