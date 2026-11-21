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

`make -C mods/flac/cdj850 test` checks bit-exact PCM in the WAV's byte order (LPC orders up to 32 from `--lax -l 32`, wasted bits, all four stereo decorrelations), sample-accurate reads with and without seek tables, final-sector padding, and sample-rate rejection (host build of `payload/decoder.c`, which runs the C predictor; the SH-2A `mac.l` path is checked by the QEMU folder probe below). `pytest tests/test_cdj850_flac_patch.py` checks the signatures, the eight gated writes, the moved classifier entry, the private stack, the boot-critical closure and that no FPU code reaches the payload; `tests/test_cdj850_service_gate.py` runs the gates.

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

### Decode speed

The decoder runs on a 200 MHz SH-2A without FPU, and the firmware asks for a lot of it at once: holding SEARCH reads a 0.41 s snippet every 7.57 s of the track, and every landing re-reads 16 s ahead and about 11 s behind (`docs/cdj850-modding.md`, section 4b.13). With stock dr_flac most of the time went to its 64-bit LPC predictor (taken for nearly all 16-bit subframes from `flac -5` to `-8`, since 16 + 15 + log2(order) > 32), its 4-way unrolled Rice loop, which spills on the SH-2A's 16 registers, and byte-wise PCM conversion and copies. `payload/decoder.c` now:

- decodes each Rice partition in `rice_residual()`, reached through `DRFLAC_RICE_RESIDUAL_HOOK`, the one local change in `vendor/dr_flac.h`. A code inside the current cache word is read in place, a code that runs into the next buffered word is joined inline with dr_flac's CRC-16 update, and anything else (refills, long runs) goes through dr_flac's own reader, so the bitstream and CRC state move exactly as in dr_flac;
- predicts with `mac.l` (32 x 32 + 64 into `MACH:MACL`): `braf` enters a 32-step ladder for the order, so every order and precision is exact with one instruction per coefficient. If `SR.S` is set (`mac.l` would saturate; the SH-2A has no `clrs`), the partition goes to dr_flac's scalar code;
- builds the WAV's little-endian sample words straight from the decoded subframes (`swap.b`/`xtrct`), and `memcpy` copies whole words.

Emulator instruction counts (`-icount`, the payload alone, a 150 s 16-bit track in the SEARCH/scratch scenario): 395 instructions per stereo sample before, 207 after. With the emulated CPU slowed to about 62 MIPS (`-icount shift=4`) a SEARCH snippet takes 155 ms instead of 244 ms, the first 2 s after releasing SEARCH arrive in about 0.4 s instead of 0.6 s, and the 16 s re-read takes 3.6 s instead of 5.7 s (stock WAV at the same speed: 21 ms per snippet, 0.9 s for the 16 s; the emulator's USB costs no time, so real USB adds to both). The decoded PCM matches the source WAV bit for bit in the QEMU folder probe, for the synthetic fixture and for real music encoded with `-8` and with `--lax -l 32`.

dr_flac is vendored at revision `7f032b984ae5dd3ab8a58721375aca9200f91965`, with the `DRFLAC_RICE_RESIDUAL_HOOK` hook as its only change. Its license is in the header. [Upstream source](https://github.com/mackron/dr_libs/blob/7f032b984ae5dd3ab8a58721375aca9200f91965/dr_flac.h)
