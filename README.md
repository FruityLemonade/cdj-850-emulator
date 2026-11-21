<div align="center">

# Pioneer CDJ-850 Emulator

**Original firmware. Emulated hardware.**

The Pioneer CDJ-850 running its real firmware on your computer.

[![License: GPL-2.0-or-later](https://img.shields.io/badge/license-GPL--2.0--or--later-blue.svg)](LICENSE)
[![Platform: Windows | Linux | macOS](https://img.shields.io/badge/platform-Windows%20%7C%20Linux%20%7C%20macOS-lightgrey.svg)](#quick-start)
[![QEMU 11.1](https://img.shields.io/badge/QEMU-11.1-orange.svg)](https://www.qemu.org/)
[![Firmware not included](https://img.shields.io/badge/firmware-not%20included-red.svg)](#not-included)

[Features](#features) · [Quick start](#quick-start) · [What works](#what-works) · [Modding & testing](#modding--testing) · [Documentation](#documentation)

<table>
  <tr>
    <td align="center"><a href="docs/img/decks/cdj850-browse.png"><img src="docs/img/decks/cdj850-browse.png" alt="CDJ-850 USB browser showing three tracks with the second selected" width="420"></a><br><sub>Browsing a USB stick</sub></td>
    <td align="center"><a href="docs/img/decks/cdj850.png"><img src="docs/img/decks/cdj850.png" alt="CDJ-850 playing a 128 BPM track with a track overview and memory cue markers" width="420"></a><br><sub>Playing a track with memory cues</sub></td>
  </tr>
</table>

<sub>Screens captured from the emulator. Select one for full size.</sub>

</div>

The **CDJ-850** boots its original firmware, mounts a virtual USB stick, and loads and plays tracks from it. Its own 24-bit audio DSP program runs on an emulated Freescale DSP56371 and computes the sound you hear. The display window and virtual deck recreate the blue character and dot-matrix display, the jog wheel's centre readout, and the illuminated panel controls.

Based on the [Pioneer CDJ & XDJ Emulator](https://github.com/Stijn-Jacobs/cdj-nxs2-qemu) project.

For an easy way to mod your firmware see my **[CDJ-850 Firmware Patcher](https://github.com/FruityLemonade/cdj-850-mods).**
<br>
  The emulator is aimed at people developing firmware mods.
</br>

<a id="features"></a>

## Features

| | |
|---|---|
| 🎛️ **The real firmware** | Both processors run the player's own code: the Renesas SH7263 (SH-2A big-endian) MAIN CPU and the Freescale DSP56371 24-bit audio DSP that MAIN boots over SHI. |
| 🔊 **The DSP's own sound** | The emulated DSP56371 executes the four-stage program MAIN uploads at boot and streams audio through the ESAI transmitter to your host sound card. |
| 🖥️ **Blue panel & jog display** | High-level emulation of the Panel CPU (`IC1007`) over SCIF1 renders the 3-row browse/text display, track overview waveform, memory cue markers, BPM/tempo readout, and 135° jog ring. |
| 🌀 **Play and mix** | PLAY/PAUSE, CUE, IN/OUT/4-BEAT loops, reloop, memory cues, tempo fader (±6 / ±10 / ±16 / WIDE), VINYL touch/scratch, pitch bend, and VINYL SPEED ADJUST work. |
| 🔌 **USB-B PC mode** | Emulates the R8A66597 USB function controller for PC mode, sending USB-MIDI and USB-HID reports to a host or controller bridge. |
| 🧪 **Built for modding** | Patch and validate `C850MAIN.UPD` firmware updates, inject SH-2A code hooks or DSP56371 patches, load up to 1.44 MB of custom code into free SDRAM at boot, and script panel inputs in virtual time. |

<a id="quick-start"></a>

## Quick start

**You will need** a recent multi-core CPU (see [Limits](#limits)), about 2 GB of disk for the QEMU build tree, Python 3.11 or newer, your own copy of `C850MAIN.UPD` **version 1.13**, and a rekordbox USB export or a folder of music.

**1. Get a shell for your system** (only your own line applies):

| system | what to do first |
|---|---|
| **Windows** | Install [MSYS2](https://www.msys2.org) and open the **MSYS2 MINGW64** shell. `setup.sh` tells you which packages to add. |
| **Linux** (native or WSL) | Nothing. Any shell will do; `setup.sh` prints the `apt` command for anything missing. |
| **macOS** (Apple silicon or Intel) | Install the Xcode Command Line Tools and [Homebrew](https://brew.sh), then the build tools and a newer bash (macOS's own is 3.2; the scripts find Homebrew's by themselves): `xcode-select --install` then `brew install bash ninja meson pkgconf glib pixman python` |

**2. Clone and run setup** (the same on every system):

```sh
git clone https://github.com/FruityLemonade/cdj-850-emulator.git
cd cdj-850-emulator
./setup.sh --firmware path/to/C850MAIN.UPD
```

`setup.sh` checks your build tools, fetches and patches QEMU 11.1.2, builds `qemu-system-sh4eb` with the `cdj850` machine, unpacks `C850MAIN.UPD` into a 4 MB NOR flash image (`extract/cdj850/flash_cdj850.bin`), and prepares your virtual USB stick (from a rekordbox export or a plain folder of music). Every step is safe to re-run and is skipped when already done. See [Setup in detail](docs/setup.md) for all options.

**3. Play:**

```sh
./start.sh --app    # launches the emulator with the full virtual deck face
```

Or run `./start.sh` for the compact display window on its own (`Ctrl-C` stops everything).

In the deck, select **USB**, turn the browse knob to move through the list, and press it to open a folder or load a track. Click **PLAY/PAUSE** to start or pause. On the keyboard, press `U` for USB, `↑` or `↓` to browse, `Enter` to select, and `Space` to play or pause (every key: [Using the deck](docs/using-the-deck.md)).

To keep user UTILITY settings (such as auto cue, tempo range, and jog mode) in flash across launches, start with:

```sh
PERSIST=1 ./start.sh --app
```

**Updating:** `git pull`, then `./start.sh` as usual. When a pull changes the C sources or patches, `start.sh` notices and offers to rebuild (`./build.sh display` does the same by hand).

<a id="what-works"></a>

## What works

| | |
|---|---|
| **Play and mix** | PLAY/PAUSE, CUE, loops and memory cues work. Move the tempo fader to change speed. Drag the edge of the jog wheel to bend the pitch or its top to scratch in VINYL mode. |
| **Read the deck** | The display shows track and artist, elapsed or remaining time, BPM, tempo range and the track overview with cue markers. The jog display shows playback position and VINYL status. |
| **Use the panel** | The source, transport and loop lamps follow the firmware, including flashes. Drag or scroll VINYL SPEED ADJUST to change how fast the track stops and starts. A button press wakes the deck from standby. |
| **Connect over USB-B** | The emulated PC mode sends MIDI and HID reports to a host or test client. |

<a id="modding--testing"></a>

## Modding & testing

The repository includes a full firmware patching, repacking, and static validation pipeline for `C850MAIN.UPD` (v1.13):

- **Build and validate a modded firmware update** with the included `tempo_wide` (enables `WIDE` ±100% tempo on USB/Rekordbox tracks) and `dsp_probe` (DSP56371 stage-4 P-RAM subroutine hook) mods:

  ```sh
  python3 mods/patch_update.py --model cdj850 path/to/C850MAIN.UPD C850MAIN_MOD.UPD tempo_wide dsp_probe
  python3 mods/validate.py --model cdj850 --stock path/to/C850MAIN.UPD C850MAIN_MOD.UPD
  ```

- **Load custom code/data into free SDRAM at boot** (`0x0DE00000–0x0DF70000`, up to 1.44 MB) using the [`loader`](mods/loader/README.md) mod (`mods/c850loader.py`):

  ```sh
  CDJ850_LOADER_PAYLOAD=blob.bin python3 mods/patch_update.py --model cdj850 path/to/C850MAIN.UPD C850MAIN_MOD.UPD loader
  ```

- **Script panel inputs in virtual time** with `CDJ850_PANEL_SCRIPT` (timestamps in virtual milliseconds). This example selects USB and presses the browse rotary twice:

  ```sh
  CDJ850_PANEL_SCRIPT=6000:usb:200,9000:enter:200,13000:enter:200 ./start.sh
  ```

- **Deterministic runs & snapshots:** The emulator supports QEMU VM snapshots (`savevm`/`loadvm`) with USB storage attached. For repeatable runs, `-icount shift=2,sleep=off` advances virtual time with guest instructions, and `CDJ850_DSP_IPS=2048` provides sample-accurate reference execution.
- **Run the test suite:**

  ```sh
  python3 -m pytest tests -ra
  ```

  Offline unit tests run without firmware or a QEMU build; live integration tests (`tests/test_cdj850_*.py`, `tests/test_sh2a_smoke.py`) run automatically when `qemu-system-sh4eb` and `extract/cdj850/` are present.
- **Ghidra annotations:** To import the reverse-engineered function names, globals, and comments into a local Ghidra project, follow the [Ghidra annotation import guide](scripts/ghidra/README.md).

<a id="limits"></a>

## Limits

- **Speed depends on your CPU.** The SH7263 and DSP56371 run together inside `qemu-system-sh4eb`; audio can break up if the host CPU falls behind real time.
- **MASTER TEMPO** adds pitch-scale synthesis work on the DSP and still needs playback-speed calibration.
- **Only firmware v1.13** (`C850MAIN.UPD`) is targeted by the mod addresses and profile constants.
- **CD drive playback** (ATAPI / CD-DA) is still under development; USB storage is the primary medium.

<a id="not-included"></a>

## Nothing from Pioneer DJ is included

> **This repository distributes no firmware, no ROM or flash images, no fonts,
> no artwork files and no manuals or schematics.** Everything here was written
> for this project. To run it you supply your own copy of the public
> firmware update file (`C850MAIN.UPD`), which the setup unpacks on your machine into a folder
> that is never committed.
>
> The screenshots in this README and in `docs/` show the emulated firmware's own interface, running in
> this emulator, for illustration.
>
> This project is not affiliated with, endorsed by or supported by Pioneer DJ or
> AlphaTheta. Pioneer DJ, CDJ and rekordbox are trademarks of their
> respective owners.

<a id="documentation"></a>

## Documentation

- [CDJ-850 reverse engineering & modding](docs/cdj850-modding.md): memory map, flash container (`C850MAIN.UPD`), bootloader checksums, SCIF1 panel protocol, DSP56371 SHI/ESAI pipeline, SDRAM layout, and mod hooks
- [CDJ-850 service manual reference](docs/cdj850-service-manual.md): hardware IC/board mappings, SH7263 & Panel CPU pin maps, user settings in flash, error codes, service modes, and signal levels
- [Ghidra annotations](scripts/ghidra/README.md): portable annotation bundle and headless import script for `C850MAIN.UPD` v1.13
- [Loader mod](mods/loader/README.md): boot-time stub copying custom code/data into free SDRAM (`0x0DE00000–0x0DF70000`)
- [Mods](docs/mods.md): patching and validating firmware updates, writing code hooks, data patches, DSP patches, and whole-image transforms
- [Setup in detail](docs/setup.md): setup steps, options, and platform notes
- [Using the deck](docs/using-the-deck.md): keyboard shortcuts, virtual deck controls, and service mode
- [MIDI controllers](midi/README.md): mapping a MIDI controller and using the deck's USB-MIDI port
- [HID mode](docs/hid-mode.md): using the deck as a controller over its emulated USB-B port
- [How it works](docs/how-it-works.md): architecture overview, flash booting, snapshots, and repository layout
- [Model profiles](models/README.md): configuration fields in `models/cdj850.conf`

## Licence

This project's own files are licensed under the **GNU General Public License,
version 2 or (at your option) any later version** (`GPL-2.0-or-later`); the
full text is in [`LICENSE`](LICENSE), and each file carries an
`SPDX-License-Identifier` line.

## Credits

- [Pioneer CDJ & XDJ Emulator (`cdj-nxs2-qemu`)](https://github.com/Stijn-Jacobs/cdj-nxs2-qemu), the upstream emulator and modding framework this project builds on
- [QEMU](https://www.qemu.org/), which the SH-2A and CDJ-850 machine model plug into
- [baken](https://github.com/M-Igashi/baken) (MIT), which writes the rekordbox USB export for the folder-of-music setup step
