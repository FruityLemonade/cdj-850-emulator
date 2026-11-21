# CDJ-850 FLAC mod

Opt-in native USB FLAC playback for MAIN v1.13. The initial build supports stereo 16-bit / 44.1 kHz native FLAC with a known sample count and blocks up to 4608 frames. It uses the stock transport and DSP.

## Build and run

Run from the repository root. The code is built into the shared CDJ-850 payload (`mods/payloadbuild.py`, toolchain in `tools/sh-elf` or `$CDJ850_SH_ELF`), so FLAC combines with the other payload mods; nothing to build by hand.

```sh
# an update file (labelled 0.13, like every mod build) with FLAC alone or with other mods
python3 mods/patch_update.py --model cdj850 C850MAIN.UPD build/C850MAIN.UPD flac
python3 mods/patch_update.py --model cdj850 C850MAIN.UPD build/C850MAIN.UPD \
  auto_standby bpm999 bpm_decimals quant_loops loop_resize hot_cues tempo_wide flac
# a QEMU flash image (patch_main takes the registry names: flac flac_header flac_pcm
# flac_size flac_size2 flac_file_close flac_audio_open flac_audio_open2)
python3 mods/patch_main.py --model cdj850 --flash extract/cdj850/flash_cdj850.bin \
  mods/flac/build/flac.flash flac flac_header flac_pcm flac_size flac_size2 flac_file_close \
  flac_audio_open flac_audio_open2
```

In the launcher, the FLAC toggle (`mod.conf`) turns on all eight knobs. Patch from stock: the payload is linked anew for each set of mods. The patch has been tested in QEMU only; installation and decode throughput on a physical CDJ-850 are unverified.

## Verification

`make -C mods/flac/cdj850 test` checks bit-exact PCM, sample-accurate reads with and without seek tables, final-sector padding, and sample-rate rejection (host build of `payload/decoder.c`). `pytest tests/test_cdj850_flac_patch.py` checks the signatures, the eight gated writes, the moved classifier entry, the private stack, the boot-critical closure and that no FPU code reaches the payload; `tests/test_cdj850_service_gate.py` runs the gates.

Use the repository Python environment with `pyfatfs` and the bundled `baken` tool for the end-to-end QEMU probes.

```sh
.venv/bin/python mods/flac/make_fixture.py --repo . --out mods/flac/build/fixture
# the payload map of the flash's mods (here FLAC alone), for the diagnostics address
python3 mods/payloadbuild.py --model cdj850 mods/flac/build/payload.bin flac
for recipe in folder export; do
  python3 mods/flac/probe.py --qemu qemu-build-eb/qemu-system-sh4eb \
    --flash mods/flac/build/flac.flash --symbols mods/flac/build/payload.map \
    --usb "mods/flac/build/fixture/$recipe.img" \
    --out "mods/flac/build/$recipe" --recipe "$recipe" --with-info
done
python3 mods/flac/verify_probe.py mods/flac/build/fixture/music/Baseline.wav \
  mods/flac/build/folder mods/flac/build/export
```

The FLAC fixture has no seek table. The folder run compares the entire 18-second track against the PCM ring. The legacy export run checks WAV and FLAC loading, waveform and memory markers, the 8-second cue, a two-beat loop wrap, reverse motion, jog inputs, and the Master Tempo indicator.

The plain folder browser lists the FLAC filename. The loaded title is blank with the current fixture. Legacy library entries supply title and artist. Cue persistence, audible scratch parity, and sustained decode throughput still need testing.

## Firmware hooks

Eight writes to MAIN, none in the boot-critical closure, each behind the service-mode gate (in a power-on update mode the stock code runs and FLAC files are not listed):

- `flac`: the directory classifier `0x0C040C90` is reached only by `bsr`, so its 12 entry bytes become a jump to `__gate_flac_file_classify` (`sigpatch.EntryRedirect`); `payload/stock.S` holds the moved bytes and goes on at `0x0C040C9C`. `.flac` becomes type 11 (WAV).
- `flac_header`, `flac_pcm`, `flac_size`, `flac_size2`, `flac_file_close`, `flac_audio_open`, `flac_audio_open2`: pool-word redirects of the USB audio calls (WAV header read, PCM read, file size twice, close, audio open twice). The open path detects native FLAC and reports the decoded WAV length; the WAV header and PCM readers serve a virtual PCM WAV while the decoder reads the original file through the stock file routines.

The decoder (`payload/decoder.c`, dr_flac) runs on a 16 KB stack of its own (`payload/flac_stack.s`, which also keeps `MACH`/`MACL` for MAIN): dr_flac's open takes a 4.4 KB frame, and the audio task (mp3ActTask, 7.5 KB of on-chip RAM) reached the file read 5.8 KB deep with it in the original build, against 2.5 KB for stock WAV playback. RAM in the payload's `.bss`: a 256 KB decoder arena, the 16 KB stack, a 2352-byte PCM sector and the 8 file slots, about 280 KB in all, between the other mods' variables (the stack's low end borders FLAC's own file slots).

dr_flac is vendored at revision `7f032b984ae5dd3ab8a58721375aca9200f91965`. Its license is in the header. [Upstream source](https://github.com/mackron/dr_libs/blob/7f032b984ae5dd3ab8a58721375aca9200f91965/dr_flac.h)
