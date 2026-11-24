# waveform (CDJ-850 MAIN 1.13): Zoomed Scrolling Half-Waveform on INFO Page 1

Streams a live 50 Hz zoomed/scrolling half-waveform into the existing 100×5 VFD strip on INFO Page 1 while keeping Rows 1–2 untouched (fully compatible with `info_order` and `phase_meter`).

## Controls

On INFO Page 1 (top cursor position, Row 1 highlighted):

- **Scroll counterclockwise (CCW)**: zoom out through three levels:
  1. **Close** (default) shows a 4.0 s window (3 CD frames per column) with a slight contrast boost.
  2. **Medium** shows a 16.0 s window (12 CD frames per column) with a stronger contrast boost.
  3. **Overview**: whole-track 100-column stock overview
- **Scroll clockwise (CW)**: zoom back in (`overview → medium → close`), then advance normally through INFO Page 1 Row 2 and Page 2+ (`info_order`, `phase_meter`, etc.).

Hold **MENU/UTILITY**, turn the selector past **VERSION No.** to **WAVE PLAYHEAD**, then press the selector to cycle positions:

- **LEFT 1/3** (default) places the playhead at 1/3 of the width (column 33, zero-based), marked by a red dot on the memory-cue strip.
- **CENTER** places the playhead at the center (column 50), marked by a red dot on the memory-cue strip.
- **LEFT** starts the waveform at the playhead (column 0) and uses the whole window for upcoming audio.

In zoomed view (close or medium), any active loop's visible `[loop-in..loop-out]` span is also lit on the 100-bit red memory-cue strip (`0x60`), while the green progress bar stays stock. In overview mode both strips behave like stock.

The deck saves the choice with its UTILITY settings. If the HISTORY name is longer than 29 characters, the choice lasts until power-off because the name occupies the shared mod settings bytes (`+0x21..+0x22`).

## How It Works

- **Data source**: Samples the 2400-sector (32 s) PCM ring buffer (`0x0C1E8E5C`) and caches each sector's level using Pioneer's 2 dB/step logarithmic table from `FUN_0C0D5590`. The analysis fallback uses the 400-column ANLZ `PWAV` buffer (`0x0CE65862`) or the 100-column overview (`0x0CE65B82`). Works on rekordbox USB, non-rekordbox USB, and Audio CD tracks.
- Each column uses PCM whenever any of its sectors are buffered. Close zoom shows the peak, while medium zoom averages the available sector levels and reserves the top row for sustained loud audio. During a brief PCM dropout the display can hold the last detailed waveform for up to 250 ms while playback stays near the same position.
- **Display hook** (`waveform_hook`): Hooks the shared `display_stage_bpm` tail (`0x0C0642AA`, outside the boot-critical closure) after `info_order` and `phase_meter`, writing 100 non-zero bytes (`1..15`) into `0x0C9D1199` and setting `0x0C9D1188 = 1` so `panel_tx_waveform` (`0x0C0662AE`) transmits `0x70..0x77` at up to 50 Hz.
- **Rotary hook** (`waveform_rotary_hook`): Hooks `0x0C0E782E` inside `FUN_0C0E655C` (INFO screen rotary branch, outside the boot-critical closure and after the BPM Lock edit check).

## Building

```sh
.venv/bin/python mods/patch_update.py --model cdj850 C850MAIN.UPD C850MAIN_MOD.UPD waveform
```

Combines cleanly with `info_order`, `phase_meter`, `bpm_lock`, `bpm_decimals`, `bpm999`, `hot_cues`, `quant_loops`, `loop_resize`, `tempo_wide`, and `flac`.
