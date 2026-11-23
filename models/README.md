# Model profiles

The firmware and launch scripts read the profile named by `CDJ_MODEL` (or
`--model`), so they carry no model specifics of their own. The default is
`cdj850`.

| profile | player | update | state |
|---|---|---|---|
| `cdj850.conf` | CDJ-850 | v1.13, `C850MAIN.UPD` | experimental |
| `cdj350.conf` | CDJ-350 | v1.14, `C350MAIN.UPD` | bring-up (see `docs/cdj350.md`) |

The CDJ-850 has an SH7263 (SH-2A) MAIN, a DSP56371 audio DSP, and text VFDs
driven by the panel CPU instead of a separate GUI processor. It boots the
`cdj850` machine (see "What works" in `README.md`).

## Variables

| variable | meaning |
|---|---|
| `MODEL_TITLE` | the player's name, for messages |
| `MODEL_RELEASED` | `<year>.<n>`: the release year and the order within it. Setup and the launcher list the default player first, then the rest by this, newest first |
| `MODEL_MAIN_MACHINE` | QEMU machine for the MAIN board (`-M`) |
| `MODEL_MAIN_ARCH` | the QEMU target MAIN runs on: `sh4eb` builds and runs `qemu-system-sh4eb` (default `sh4`) |
| `MODEL_MAIN_BOOT` | `flash`: MAIN resets into the bootloader in NOR flash (`-drive if=pflash`); `kernel` (default): the unpacked image is loaded with `-kernel` |
| `MODEL_MAIN_FLASH` | the flash image a `flash`-booting model boots, named in `MODEL_EXPECTED` |
| `MODEL_GUI_MACHINE` | QEMU machine for a separate GUI board; empty for the CDJ-850 |
| `MODEL_EXTRACT` | where the images are installed, relative to the repository |
| `MODEL_FW_VERSION` | the one update version every firmware address is for |
| `MODEL_UPD` | the update file name |
| `MODEL_UPD_SHA256` | its SHA-256 |
| `MODEL_MAIN_SECTION` | which section of the update holds MAIN (`main`: the whole flat file) |
| `MODEL_KERNEL` | the image `scripts/run/boot_main.sh` loads with `-kernel` (default `main_unpacked.bin`) |
| `MODEL_MAIN_LZSS` | offset of MAIN's LZSS stream in the update (after its 4-byte length) |
| `MODEL_MAIN_BASE` | where the unpacked MAIN image starts in the main CPU's address space (`0x0C000000`, SDRAM, on the CDJ-850) |
| `MODEL_UPDATE_WINDOW_END` | where the player's updater stops erasing and programming the MAIN region of a public update (see `mods/modelfw.py`) |
| `MODEL_FW_STEPS` | the `prepare_firmware.sh` steps to run, in order |
| `MODEL_EXPECTED` | each output image and its SHA-256 |
| `MODEL_LAUNCH` | `deck`: one MAIN emulator in one window, started by `launcher/deck.py`. The only launch this tree has |
| `MODEL_APP_FACE` | the face file in `app/faces/` (`<name>.json`) the virtual deck app draws. Without it `./start.sh --app` falls back to the plain window |
| `MODEL_IDLE_S`, `MODEL_LOAD_STEPS` | how the deck reaches its snapshot points (`SNAPSHOT=idle\|loaded`): seconds from power-on to the settled screen, then the `;`-separated steps that load a track (`sendkey <key> <ms>`, `key <panel payload>`, `wait <s>`; see `scripts/run/snapshot_deck.py`) |

## Adding a model

1. Write `models/<id>.conf`. Pin `MODEL_EXPECTED` from a first run of
   `scripts/firmware/prepare_firmware.sh --model <id> <update> <outdir>`.
2. Add its MAIN machine under `hw/cdj/boards/<board>/`, describing the board
   with a `CdjBoardDesc` (`hw/cdj/common/cdj_common.h`), and give it a
   Kconfig entry that selects `CDJ_COMMON`.
3. Boot it with `CDJ_MODEL=<id> scripts/run/boot_main.sh <tag>`; its log lists
   every region the firmware touches that the board does not model yet.
