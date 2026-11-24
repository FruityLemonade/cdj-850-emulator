# bpm999 (CDJ-850 MAIN 1.13)

Raises the BPM ceiling from 180.0 to 999.0. Stock clamps the rekordbox beat-grid tempo
at 180.0 and the pitched display can only show what the base tempo allows. This mod
lifts the clamps and adds the one clamp the display still needs.

## What it changes

| Part | Where | How |
|---|---|---|
| Beat-grid tempo clamp | `FUN_0c073788` at `0x0C0737D6` (`movi20 #0x708` = 1800 = 180.0) | DataPatch `bpm999grid`: immediate becomes `#9990` (999.0). No code. |
| Base tempo range check | `FUN_0c0d1c42` at `0x0C0D1C80` (`movi20 #0x7CF`, 1999 = 199.9) | DataPatch `bpm999range`: upper bound becomes 9990. The lower bound 600 (60.0) is unchanged. Outside the range the function stores 130.0 in its sync/link field. Without this a 200+ track would be replaced by 130.0 there. |
| Pitched display clamp | `display_stage_bpm` tail, `0x0C0642AA..0x0C0642B8` | Hook `bpm999` (`main_bpm999.s`, 36 bytes, in the cave). After the stock stores, the staging BPM word at `0x0C9D0AE0` (TX page 0x01 bytes 8..9) is clamped to 999. Blank (`0xFFFF`) is kept. |

The pitched value can reach 2x the base (WIDE +100%), or about 1.16x at +-16%, so a 900 BPM
track at +16% would show 1044. The clamp is needed for that case too, not only for WIDE.

Not changed, on purpose:
- The auto BPM counter (`FUN_0c0726ec`, `FUN_0c072824`): it rejects intervals above 0x710 (180.8)
  and clamps at 0x708. That is the 70-180 detection window, so CD and unanalysed tracks still
  stop at 180.
- The link/MIDI tempo conversion `FUN_0c0e54ae` (whole BPM from base, unchanged).

## Limits you should know
- A rekordbox PQTZ grid entry is a u16 of BPM x 100, so a grid can hold at most 655.35 BPM.
  Values from 180.0 up to 655.3 work. Above that the file itself cannot carry the tempo.
- The panel's BPM box is three digits, so anything above 999 is clamped to 999. The clamp is
  in the staging word, so the BPM digits are 999 at most.
- Whole BPM values 1000..1998 can exist in memory at `0x0C9D23F0` (no clamp there). Readers of
  that word are listed in `docs/cdj850-modding.md` section 4 (BPM readers).

## Knobs
None. Enable with the `bpm999` folder in `patch_update.py` (see below).

## Build
    .venv/bin/python mods/patch_update.py --model cdj850 C850MAIN.UPD out.UPD bpm999
    .venv/bin/python mods/validate.py --model cdj850 --stock C850MAIN.UPD out.UPD

Combined with `bpm_probe` (the hardware probe, off by default) the two share one trampoline.

## What the emulator showed
Stick: four grid tracks (128, 200, 400, 600 BPM), built with `tools/baken/baken` by the repo's stick
builder (`tests/test_cdj850_bpm999.py::_grid_stick`). Panel: browse, load, NEXT between tracks,
TEMPO, then a `fader=0` push (top of the pitch fader). Values read through QMP while paused:

| track | stock base 0x0C9D23EC | stock TX word | bpm999 base | bpm999 TX word |
|---|---|---|---|---|
| GRID128 | 1280 | 128 | 1280 | 128 |
| GRID200 | 1800 | 180 | 2000 | 200 |
| GRID400 | 1800 | 180 | 4000 | 400 |
| GRID600 | 1800 | 180 | 6000 | 600 |

WIDE with `tempo_wide` (bpm999 + tempo_wide), GRID600 at +100%: base 6000, whole BPM at
0x0C9D23F0 = 1200 (unclamped in memory), TX word 999. Stock with the fader at its top (+16% after
TEMPO): 180 -> 209, so the top of the fader is +16% in stock, not WIDE.

- The routine on the SH-2A interpreter (`tests/test_cdj850_bpm999.py`): staging 1800, 1500, 1000
  become 999; 999, 998, 180, 0 and `0xFFFF` are unchanged.
- The probe build and the bpm999 build boot in the emulator with the WAV stick; stock and bpm999
  show the same blank (`0xFFFF`) TX word for unanalysed WAV tracks.

## Open hardware questions
- Real rekordbox grids above 180 BPM on a deck: not run (the emulator grids are baken-generated).
- Whether the panel shows 3 digits up to 999 (the glass is three seven-segment digits, per the
  feasibility study).
- The sync/link consumer of the field that receives 130.0 is not checked (see
  `docs/cdj850-modding.md` section 4).
- Pitched values in memory above 999 are read by a few non-display paths (player state
  machines). They were compared as signed 16-bit and are fine up to 1998, but not run.

## CDJ-350 (MAIN 1.14)

`cdj350/patch.py` makes the same grid change (`bpm999grid`, 0x0C06B866) and raises the two
BPM LOCK bounds the CDJ-350 has: `bpm999range` (0x0C0C6064, the base tempo it accepts as
master BPM instead of `Default 130`) and `bpm999lock` (0x0C0C61D0, the master BPM rotary
edit). No display clamp: the CDJ-350's BPM box has three digits and a tenth, up to 999.9.

    .venv/bin/python mods/patch_update.py --model cdj350 C350MAIN.UPD out.UPD bpm999

Emulator: a 400 BPM grid shows `400.0` (stock `180.0`) and BPM LOCK edits from `400.0`.
See `docs/cdj350.md`, "BPM above 180".
