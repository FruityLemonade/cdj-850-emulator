# Mods

Optional changes to the CDJ-850 firmware, each with a folder of its own under
`mods/` (see [adding a mod](#adding-a-mod)). Every mod is off until you switch
it on. All of them target CDJ-850 MAIN 1.13.

| mod | what it does |
|---|---|
| [`tempo_wide`](../mods/tempo_wide/cdj850/patch.py) | WIDE tempo on USB: TEMPO cycles ±6 % → ±10 % → ±16 % → WIDE (±100 %) → ±6 %, and enables overflow-safe WIDE pitch scaling on USB. |
| [`bpm999`](../mods/bpm999/README.md) | BPM up to 999: the beat-grid clamp goes from 180.0 to 999.0, and the pitched BPM shows up to 999. |
| [`bpm_decimals`](../mods/bpm_decimals/README.md) | The actual BPM with two decimals in place of the rekordbox BPM in the INFO view (tracks with a grid). |
| [`quant_loops`](../mods/quant_loops/README.md) | Loops snap to the nearest beat of the rekordbox grid (behind or ahead): auto beat loops, LOOP IN and LOOP OUT, with no drift over many passes (USB tracks with a grid). BEAT SELECT + LOOP IN switches quantize (saved, QUANTIZE ON/OFF on the display). |
| [`info_order`](../mods/info_order/README.md) | INFO screens in the order Track, `128.23 12A *****` (actual BPM, Camelot key, track rating as asterisks), waveform / Artist, Rating, Key / Album, Comment (USB tracks). |
| [`loop_resize`](../mods/loop_resize/README.md) | While a loop plays, CUE/LOOP CALL < halves it and CALL > doubles it (grid aware); outside a loop CALL works as stock. |
| [`loop_roll`](../mods/loop_roll/README.md) | Prototype loop roll: BEAT SELECT + LOOP OUT toggles roll mode (ROLL ON/OFF, not saved); a held BEAT 1/2/4/8 loops, and on release playback lands where the track would have been (USB, within the buffered audio). |
| [`hot_cues`](../mods/hot_cues/README.md) | Hot-cue mode (BEAT SELECT + RELOOP, saved, HOT CUE ON/OFF on the display): BEAT 1/2/4 are hot cues A–C from the rekordbox ANLZ (jump, record, hold 8 + pad to delete, saved to the stick); the beat LEDs show the set slots. |
| [`bpm_lock`](../mods/bpm_lock/README.md) | BPM Lock from CDJ-350 (±100% range): BEAT SELECT + MASTER TEMPO short toggles lock/unlock, hold 1 s edits Master BPM via the rotary selector. |
| [`phase_meter`](../mods/phase_meter/README.md) | Beat & phase meter from CDJ-350 on INFO Page 2 (shifts stock INFO pages down; works with BPM Lock on or off). |
| [`waveform`](../mods/waveform/README.md) | Zoomed scrolling half-waveform in the 100×5 INFO Page 1 strip (CCW zooms out: close 4 s → medium 16 s → whole-track overview; compatible with `info_order`). |
| [`auto_standby`](../mods/auto_standby/README.md) | AUTO STANDBY after `CDJ850_STANDBY_MINUTES` (default 20) without use, instead of 4 hours. |
| [`flac`](../mods/flac/README.md) | FLAC playback from USB (stereo, 16- or 24-bit, 44.1 or 48 kHz). |
| [`loader`](../mods/loader/README.md) | Infrastructure: at boot, copies a blob of custom code and data to unused SDRAM (`0x0DE00000`) for payload mods to run from. Added automatically when a mod needs it. |
| [`payload_demo`](../mods/payload_demo/README.md) | Reference payload mod: a C hook and a pool-literal redirect into the SDRAM payload, both only counting calls. |
| [`bpm_probe`](../mods/bpm_probe/README.md) | Hardware probe: the BPM box shows 199 and the frame digits cycle 75–99 (a test of the display segments on one deck). |
| [`dsp_probe`](../mods/dsp_probe/cdj850/patch.py) | DSP56371 segment probe: routes a main-loop NOP slot in the audio DSP program through an unused vector subroutine (`P:$0080`). |

See [CDJ-850 Reverse Engineering & Modding Reference](cdj850-modding.md) for the CDJ-850's memory map, code cave, DSP56371 SHI segments, and modding guide.

Each mod with a page lists its knobs and what it patches there.

`./setup.sh` shows the defaults (step 7) and either takes them or asks about
each one, and saves your answers to `cdj.conf`; `./setup.sh --reconfigure`
asks again. You can also edit `cdj.conf` directly,
or override any of them for one run with the environment, e.g.
`CDJ_MAIN_AUTOSTANDBY=1 ./start.sh` (a caller's own environment always wins over
`cdj.conf`).

[SERVICE MODE](using-the-deck.md#service-mode) is a separate boot option, not a mod: it changes
what the deck boots into, not how it behaves once it is up.

## Patching a real firmware update

The mods are firmware code patches, not emulator knobs: the launcher applies
them to a copy of the MAIN image at boot (`mods/patch_main.py`), the same code
a real update would carry. `mods/patch_update.py` makes the same patches to the
real Pioneer update file, `C850MAIN.UPD`, so they end up on an actual player
instead of a copy this emulator throws away on exit; each mod's page gives its
command, for example:

```sh
python mods/patch_update.py C850MAIN.UPD out/C850MAIN.UPD bpm999
```

The CDJ-850 is the default player of `patch_update.py`, `validate.py`,
`mod_check.py` and `./setup.sh`, so none of them needs `--model`.

MAIN has no scatter-load table of its own: it runs in place, so a mod's routine
goes straight into a run of erased flash inside its own address space. A
payload mod (one with sources in `mods/<mod>/cdj850/payload/`) is built into a
single payload that the [`loader`](../mods/loader/README.md) copies to SDRAM at
boot; `patch_main.py` and `patch_update.py` add `loader` themselves when a mod
named needs it.

`python mods/patch_update.py --list` shows what is available and
which firmware version it was verified against; a mismatched version is refused
unless you pass `--force-version`.

**Version label.** The service-mode USB update (USB SELECT + RELOOP at
power-on) skips a file whose label matches the running firmware, then shows
COMPLETE! having written nothing. So `patch_update.py` labels a modded file
`0.13` instead of `1.13` by default: it installs over stock and over any
earlier modded build, the stock `1.13` file always installs over it
(rollback), and the version screen reads "Ver 0.13". `--version D.DD` sets
another label; `--keep-version` keeps the input's.

**Combining mods.** Name as many as you like in one command, e.g.
`python mods/patch_update.py C850MAIN.UPD out/C850MAIN.UPD bpm999 quant_loops hot_cues`.
Each name brings every patch its `mod.conf` switches on. All routines
are placed in a single pass into one free area, so they cannot
overlap, and mods that hook the same spot in the firmware share it: the spot
is replaced once by a stub that runs the original code and then each mod in
turn. The launcher combines the mods you switched on the same way.

**Flashing a modified firmware update is entirely at your own risk.** Keep
the original `C850MAIN.UPD`, and expect that a mistake here could mean a
trip through service mode's recovery path, or worse.

**Checking an update someone else modded.** One command checks any
`C850MAIN.UPD` before you flash it, ours or not:

```sh
python mods/validate.py downloaded.UPD
python mods/validate.py --stock official/C850MAIN.UPD downloaded.UPD   # name the official file yourself
```

The version is read from the file's own section label. If the file is
byte for byte the official update, it says so and passes. Otherwise it looks for
the official file of that version in the folder of the file, in `fw/`
and in the player's firmware folder, and compares. A modded file never has the
official checksums, so none are compared with the official ones: every
checksum in the file (S-record lines, section CRC-16, packed MAIN sum, emergency
image sum) is recomputed from the file's own bytes and has to agree with what
the file stores. Then it prints what changed (sections, size growth, the
firmware address ranges, whether our patcher writes the same bytes) and fails a
change that lands in code the boot, the updater, the panel, the display link or
the USB stick can run, a rewrite of existing code bigger than a call site, a
write into "erased" bytes that something points at or a task can reach, and a
packed MAIN that no longer fits the updater's window.

| exit | meaning |
|---|---|
| 0 | safe as far as this checks |
| 1 | a check failed; do not flash |
| 2 | not shown safe: nothing failed, but there is no official copy of that version to compare with (only the checksums and the updater's window were checked), or a write could not be proven |

NOT CHECKED, and printed after every run: indirect calls through RAM tables
(the code a task reaches is a lower bound), what the firmware itself writes
into a span at run time, that the boot loader and emergency updater in your
unit are the ones in the file, and whether the updater takes an update of equal
version. Nothing here boots the file. A pass does not replace testing on a real
deck, with a way back (the official file and service mode) at hand.

**Then run it.** `mod_check.py` boots a modified MAIN image beside the
installed stock one, takes its pictures at the same points in both (idle, the
browser, a cued track, the menus), and passes when the image matches stock
outside the `--allow` regions the change is meant to touch:

```sh
python scripts/test/mod_check.py my_main.bin /tmp/check \
    --allow X0,Y0,X1,Y1
```

`<out>/summary.txt` gives a verdict per picture and `<out>/diff/` marks every
difference; `mod_check.py --help` has the steps and the verdicts.

<a id="adding-a-mod"></a>

## Adding a mod

Everything about one mod sits in its own folder:

```
mods/bpm999/
  mod.conf                  its row: knobs, on/off values, default, players, description
  README.md                 its page
  cdj850/
    patch.py                where each routine hooks the firmware and what it imports
    main_bpm999.s           a MAIN routine
    main_bpm999.bin         the same, assembled (patch_main.py --assemble)
    payload/                C or assembly for the SDRAM payload, if the mod has any
```

The tools find mods by these folders: setup reads every `mod.conf`, and
`patch_main.py`, `patch_update.py` and the validator read every
`cdj850/patch.py`. A `patch.py` holds a `MODS` dict, one `sigpatch.Mod` per
routine, keyed by the routine's name, whose knob is `CDJ_MAIN_<NAME>` (list
them all in `MOD_ENV`, joined by `+`). What a mod finds in the firmware by
signature and needs only for itself stays in its own `patch.py`. What several
mods share lives in `mods/players/cdj850.py`, where each mod imports it from:
the image profile, a hook site two mods sit on (both are then called from one
stub at that site), and an address more than one mod imports. Words a mod's
routines hand each other at the end of the free space (`FreeEnd`) belong to
that mod; no two mods may take the same bytes, which the tests check. For
payload mods, see
[writing a payload mod](cdj850-modding.md#writing-a-payload-mod) in the
modding reference.

A few patches are neither a routine nor a byte rewrite found by a table. These
are declared as a `sigpatch.ImagePatch`: a function that transforms the whole
image and reports what it wrote. The only one so far is the
[`loader`](../mods/loader/README.md). It changes the first literal of MAIN's
entry, and the validator checks it with its own rule, `validate.check_loader`.
