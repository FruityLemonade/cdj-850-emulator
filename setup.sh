#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-2.0-or-later
#
# From a fresh clone to a running, customised CDJ-850, in one command.
#
#   ./setup.sh                 walk through everything, asking as it goes
#   ./setup.sh --dry-run       show every step and command, change nothing
#
# Seven steps, each one safe to re-run and each one skipped when its result is
# already there:
#
#   1 prerequisites   compilers, libraries and Python packages, with the exact
#                     pacman / apt / brew command for whatever is missing
#   2 build           QEMU 11.1.2 fetched and patched, and the CDJ-850's MAIN
#                     emulator (SH-2A, sh4eb) built from it (logs in logs/)
#   3 firmware        your own C850MAIN.UPD (v1.13) turned into the images the
#                     emulator boots, each checked against a known SHA-256
#   4 USB stick       a disk image made from a folder of your own music: either
#                     a rekordbox USB export, or a plain folder of music files
#                     that baken (github.com/M-Igashi/baken, MIT) analyses --
#                     no rekordbox needed
#   5 DSP module      nothing to build: the CDJ-850's DSP56371 runs in-process
#   6 your setup      nothing to ask: the CDJ-850 starts as one window with your
#                     stick; the player is saved to cdj.conf, which ./start.sh uses
#   7 mods            the CDJ-850 firmware mods (see docs/mods.md and
#                     mods/<mod>/mod.conf), all defaults in one answer or one
#                     at a time, saved alongside your setup
#
# options:
#   --dry-run              print what would happen; run and write nothing
#   -y, --yes              never ask: take the defaults and the options below
#   --skip-build           leave step 2 out (a build tree you made yourself)
#   --rebuild              run step 2 even when the emulators are already built
#   --skip-dsp             leave step 5 out (a player with a DSP module only)
#   --reconfigure          ask the step 6 and 7 questions again
#   --model <id>           the player: a profile in models/, cdj850 by default;
#                          asked when not given (Enter keeps the one in cdj.conf)
#   --firmware <file>      your update file for it (C850MAIN.UPD for the CDJ-850)
#   --music <folder>       the rekordbox USB export to image (re-makes the stick)
#   --tracks <folder>      a plain folder of music to image instead, analysed by baken
#   --decks 1|2            --name <deck name>    --djlink on|off   --audio on|off
#   --controller none|<profile>|learn            --relay-port <port>
#                          (these six only for a two-board rig player, not the CDJ-850)
#   --build-dir <dir>      where the QEMU build trees go
#   -h, --help
#
# Nothing from Pioneer DJ / AlphaTheta is in this repository: the firmware file
# and the music are yours, and they stay in extract/ on your machine.
#
# The work is done by launcher/setup.py (python -m launcher setup), the same
# code the packaged program runs.
. "$(dirname "${BASH_SOURCE[0]}")/scripts/cdj_python.sh"
cdj_launcher setup "$@"
