# CDJ-850 cache model

The emulator runs the MAIN firmware with coherent memory: every load sees the
last store, whoever made it. The SH7263 does not. It has an 8 KB instruction
cache and an 8 KB write-back operand cache, and software keeps them coherent
itself. A mod that gets that wrong runs fine in QEMU and breaks on the deck.

`scripts/cache/cdj850_cache.c` is a QEMU TCG plugin that runs the SH7263 cache
rules next to the emulator. It does two jobs from one model:

- **Coherency checker.** It keeps a second copy of SDRAM holding what the real
  chips would hold, and reports every place where the real CPU would get
  different bytes than QEMU just gave it.
- **Hit/miss profiler.** It counts fetches, reads and writes per pc with their
  cache misses, dirty write-backs and uncached accesses.
  `scripts/ghidra/apply_cache_profile.py` rolls those counts up per function
  and writes them into Ghidra.

It is a functional model. It counts misses, not cycles: QEMU has no bus timing.

## Running it

```sh
scripts/cache/cache_run.py --until "NO DISC" --out runs/idle          # boot to the idle screen
scripts/cache/cache_run.py --usb stick.img --seconds 120 --out runs/play \
    --script "6000:usb:200,9000:enter:200,11000:rot+1,13000:enter:200,19000:play:200"
```

`cache_run.py` builds the plugin on first use (`build_cache_plugin.sh`; it needs
`cc`, `pkg-config` and glib, which the QEMU build already uses), boots the deck
headless under `-icount`, stops it after `--seconds` or once `--until` appears,
and prints the summary. `--flash` takes a patched flash image, so a mod build
is checked the same way. The plugin also loads by hand:

```sh
qemu-system-sh4eb -M cdj850 ... -plugin libcdj850_cache.dylib,out=runs/x[,check=off][,profile=off][,quiet=on]
```

Output:

- `<out>-report.txt`: CCR1/CCR2 state, hit and miss totals, DMA and exception
  counts, then one row per (kind, pc) finding. Each row holds the first address,
  how often it happened, the bytes the chip would have seen next to QEMU's, and
  the last write that went around the cache to that line (an uncached store's
  pc, or a DMA channel).
- `<out>-profile.csv`: one row per pc that ran or accessed memory.
- stderr: each new finding as it happens, prefixed `cdj850-cache:`.

To put a profile into Ghidra (the project must be closed, so stop pyghidra-mcp
first):

```sh
python scripts/ghidra/apply_cache_profile.py extract/cdj850/ghidra/cdj850.gpr \
    /main_unpacked.bin runs/play-profile.csv --csv runs/play-functions.csv
```

This prints the functions that fill the most cache lines (`--rank uncached`
orders them by accesses through the uncached mirror instead: those never show
as misses, but each costs a full bus cycle on the chip). It sets a `Note`
bookmark in category `CacheProfile` at every function entry with that
function's totals, and one in `CacheProfile-pc` at each of the 50 hottest
single pcs. Names and comments are not touched. A rerun replaces the previous
bookmarks.

## QEMU requirements

Two emulator changes on this branch feed the plugin. A QEMU built before them
still loads it, but the report then carries a `WARNING` line and findings on
stacks are not real. Rebuild with `./build.sh display`.

- **Exception entry.** On SH-2A, exception and interrupt entry pushes SR and PC
  inside a QEMU helper, where no plugin memory callback sees it.
  `patches/target_sh4_helper.c.patch` makes that path call QEMU's standard
  plugin interrupt and exception hooks, as the SH-4 path already does.
- **Registers.** The SH-4 target has no GDB core description, so plugins get no
  registers at all. The board (`hw/cdj/boards/cdj850/board.c`,
  `cdj850_plugin_regs_init`) registers a read-only feature that names r0-r15,
  pc, pr, sr, gbr and vbr. With those the plugin reads R15 at entry, replays the
  push as two CPU stores, and reads Rn for `pref @Rn`. Without a core XML the
  GDB stub advertises no features, so GDB clients see no change.

Emulator knobs that write guest memory from helpers also bypass the model:
`CDJ_NATIVE_COPY`, `CDJ_PPOKE`, `CDJ_PSTORE` and `CDJ_RPOKE`. The plugin warns
when one is set, and `cache_run.py` clears them. `CDJ_NATIVE_LIBC` only reads
memory, so it only skews the profile.

## The model

From the SH7263 Hardware Manual (R01UH0459EJ0400 rev 4.00, section 8):

| | |
| --- | --- |
| Geometry | Separate instruction and operand caches, 4 ways × 128 entries × 16-byte lines (8 KB each). Entry = address bits 10-4, tag = physical bits 28-11. |
| Space | `0x00000000-0x1FFFFFFF` is cache-enabled. The same addresses + `0x20000000` bypass the cache (the firmware's `0x2Cxxxxxx`/`0x2Dxxxxxx` SDRAM mirror, `0x20xxxxxx` flash mirror). On-chip RAM and peripherals are never cached. |
| Replacement | LRU, 6 pairwise bits per entry (table 8.1). With CCR2.LE, ways 2/3 can be locked, and `pref @Rn` loads into a locked way per W2LOAD/W3LOAD (tables 8.3-8.7). |
| Writes | Write-back (CCR1.WT = 0): a write hit marks the line dirty (U); a write miss fills the line first. Write-through: memory always, the line only on a hit. |
| CCR1 `0xFFFC1000` | ICF (bit 11) and OCF (bit 3) drop every line and do **not** write dirty data back; ICE (bit 8), OCE (bit 0), WT (bit 1). |
| Address arrays | `0xF0000000` (I), `0xF0800000` (O): way = bits 12-11, entry = bits 10-4, A = bit 3. Data: tag bits 28-11, LRU bits 9-4, U bit 1, V bit 0. An associative write (A = 1) changes V/U of the way whose tag matches. Any operand line that leaves the cache with U = 1 is written back first (8.4.1, 8.4.3). |
| Data arrays | `0xF1000000` (I), `0xF1800000` (O): way = bits 12-11, entry = bits 10-4, longword = bits 3-2. |
| DMAC | Sees memory only, never the cache (8.4.4). |

How the plugin follows the firmware:

- **Memory.** The shadow SDRAM (32 MB at `0x0C000000`) is copied from the guest
  at the first instruction. After that it changes only the way the real chips
  would: uncached stores, write-through stores, write-backs and DMA. Flash is
  a device the CPU programs through command cycles, so its real contents are
  always QEMU's. A cached flash line still goes stale when the flash is
  reprogrammed. Other cache-enabled space is counted for hits and misses only.
- **Instructions.** Each fetch goes through the instruction cache. A line fills
  from the shadow memory, not from the operand cache, so code written through
  the cache and not written back is caught.
- **DMA** is inferred from the CPU's own DMAC register accesses (`0xFFFE1000`).
  A CHCR store with DE = 1 arms a channel from its SAR/DAR/DMATCR/TS/SM/DM.
  A memory source is checked against the shadow at that moment: the DMAC
  reads memory, so data still dirty in the cache goes out stale. A memory
  destination is copied from QEMU into the shadow once the CPU sees the
  transfer end: a CHCR read with TE set, a DMATCR read of 0, or the channel
  re-armed. A cached line that still covers that destination is then caught
  when it is read.
- **Exception entry.** The SR/PC push is replayed as two cached stores at the
  new R15 (see above). The vector-table read is not replayed.
- **`pref @Rn`** is modelled; the plugin reads Rn through the plugin register API.
- After a finding, the line (shadow and cache) takes QEMU's bytes. That way
  one root cause gives one row, not a cascade.

Finding kinds:

| Kind | Meaning |
| --- | --- |
| `stale-read` | A cached load hit a line older than memory. Memory was written around the cache (uncached store, DMA) after the line filled, and the line was not invalidated. |
| `diverged-fill` | A cached load missed and filled from real memory that differs from QEMU's. Usually a consequence of a dirty line being discarded. |
| `uncached-stale` | An uncached load of data that is still dirty in the operand cache (no write-back first). |
| `writeback-clobber` | A dirty line was written back over data that had been written around the cache after the line filled. |
| `dirty-discard` | OCF flushed dirty data that was never written back. |
| `stale-code` | A fetch hit an instruction-cache line older than the code QEMU runs: the code changed and the I-line was not invalidated. |
| `code-not-in-mem` | A fetch filled from memory that does not hold the code yet: the code is still dirty in the operand cache. |
| `dma-read-stale` | A DMA was armed to read memory whose newest bytes are still dirty in the operand cache. |

## Results (stock v1.13 firmware)

All runs under `-icount shift=2`:

| Run | Fetches | I-miss | O-read miss | Write-backs | DMA armed / RX done | Entries | Findings |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Boot to `NO DISC` | 376 M | 0.10% | 0.55% | 2.3 M | 0 / 0 | 1,372 | 0 |
| USB browse, load, play | 4.9 G | 0.12% | 0.34% | 3.9 M | 7,404 / 1,544 | 38,571 | 0 |
| Loader build (64 KB payload + `tempowide`), boot | 373 M | 0.10% | 0.57% | 2.3 M | 0 / 0 | 1,347 | 0 |

The stock firmware comes out coherent, which is also the check on the model.
Cache maintenance seen:

- The bootloader's flush and enable (`0x544`, on-chip RAM `0xFFF80404/0C`).
- 32 address-array invalidates from its range routine (`0xFFF80472`).
- During USB traffic, MAIN's own `ocache_purge_range` (`0x0C0FB97E`): 382k
  associative operand-cache writes, called by `usb_dma_start` (`0x0C019006`)
  on every buffer before the DMAC is armed.

Where the playback time goes (USB WAV, profile plus a call trace of
`memcpy`/`memset` arguments):

- Every PCM byte is copied three times, which is why `memcpy_block32`
  (`0x0C1105D4`) and `memset` (`0x0C1106EC`) top the line-fill ranking:
  1. `wav_decode_block` (`0x0C058FF2`) copies the file buffer into the output buffer
     at `0x2DFE0000`, in the uncached mirror (see the uncached-buffer map in
     [cdj850-modding.md](cdj850-modding.md#uncached-buffers--dma-map)).
  2. `pcm_rate_copy` (`0x0C0F95E6`) copies it out at 44.1 kHz; other rates are
     resampled.
  3. `pcm_ring_write` (`0x0C0EFB9A`) copies it into the CD-sector-sized slots of
     the PCM ring.
- `wav_decode_block` then byte-swaps every sample in place in that uncached
  buffer. That is 79% of all uncached accesses in the run, the largest
  bus-bound cost of WAV playback. The emulator does not show it.
- `memset`'s bytes come mostly from large clears (`alloc_zeroed` and table
  resets). On the write-allocate operand cache, each missed line is read
  before it is overwritten.

Before the exception-entry hook existed, the push was invisible. The same boot
then reported 7 false findings on interrupt frames (stale-read and
writeback-clobber at RTOS and RTE code). Those are what the `WARNING` line
guards against.

## Results (all mods, build 0.13)

The image: `patch_update.py C850MAIN.UPD out.UPD tempo_wide bpm999 bpm_decimals
quant_loops loop_resize hot_cues auto_standby flac` (44 declared writes, a
358,464-byte payload, 28 calls into it), and the same mods through
`patch_main.py --flash` for the emulator. The two differ only in the version
byte at `0x60010`, so the runs below boot the bytes a deck would get. The
probes and `payload_demo` are left out.

| Run | Fetches | I-miss | O-read miss | DMA armed | Findings |
| --- | --- | --- | --- | --- | --- |
| Boot to `NO DISC` | 373 M | 0.10% | 0.61% | 0 | 0 |
| FLAC folder: load `Candidate.flac`, play the whole 18 s file (`mods/flac/probe.py --recipe folder`) | 4.6 G | 0.11% | 0.43% | 7,379 | 0 |
| FLAC export: WAV and FLAC, cue, call, beat loop, reverse, vinyl, jog, master tempo (`--recipe export`) | 8.6 G | 0.13% | 0.48% | 21,738 | 0 |
| Loops: auto beat loop, CALL halve and double, LOOP IN/OUT, MEMORY, quantize toggle (saved), INFO, TEMPO to WIDE, next track | 12.2 G | 0.12% | 0.33% | 16,296 | 0 |
| Hot cues: mode on (saved), jump A, record C, loop B, delete A, record A, paused jump, mode off (saved) | 10.7 G | 0.12% | 0.34% | 16,619 | 0 |

The last two run on the hot-cue test stick (`tests/test_cdj850_hot_cues.py`,
`build_stick`) with a `--script` in virtual milliseconds. The live tests'
own drivers wait in host time and time out under the plugin, which runs about
30× slower than real time. The profiles show every hook running. In the
hot-cue run, `hot_cues_anlz_write` writes the stick 3 times, the settings save
(`0x0C08E9B0`) runs twice, and `usb_dma_start` runs 3,221 times.

Why it comes out clean:

- The payload does no cache maintenance and never touches the uncached mirror,
  the DMAC or its own code. The only write around the cache is the loader
  stub's copy to `0x2DE00000`. That copy comes before anything caches the
  region, and the I-cache fills payload code from memory.
- Every USB transfer, the ANLZ writes included, goes through
  `usb_dma_start`, and every flash write through the stock settings save.
- FLAC hands the stock WAV path a byte-swapped virtual WAV. Its PCM takes the
  stock route: `memcpy_block32` into `0x2DFE0000`, where the stock in-place
  swap (`0x0C0590F6`/`0x0C059106`) runs on it.
- The cache pressure is the stock firmware's: I-miss 0.10-0.13% here against
  0.10-0.12% stock.

CPU headroom (the deck runs 200 MHz, the emulator counts 250 M instructions
per virtual second and never stalls): the idle loop (`idle_task_state_scan`)
takes 91% of all instructions during FLAC play and 95% during WAV play with
loops and hot cues. The FLAC decoder (mostly
`drflac__decode_samples_with_residual__rice__scalar`) runs about 8 M
instructions per second of audio. Estimated with guessed penalties (24
cycles per line fill, 18 per write-back and uncached read, 9 per uncached
write), that is 11 M cycles/s, 6% of the core, or 5-7% across ±50% on those
penalties. The other mods together come to under 0.2%.

## Not modelled

- Timing: the write-back buffer, fill wrap-around order and miss penalties. The
  profile counts events only.
- The exception vector read, register-bank overflow to the stack, and anything
  else a QEMU helper reads or writes on the CPU's behalf. `CDJ_NATIVE_LIBC`'s
  reads go unseen too, so the profile skips them.
- `loadvm`/snapshot restore: the shadow memory does not follow it.
- DMAC request sources other than those the CPU arms itself, and DMA reload
  registers.

## Tests

`tests/test_cdj850_cache.py` builds a small hand-assembled SH-2A program into an
otherwise erased flash image. It makes each kind of mistake once at a known pc,
next to the correct way of doing the same thing, and checks that the plugin
reports exactly the mistakes. A second test runs only the correct patterns and
expects no findings. A third takes a TRAPA with the stack in cached SDRAM and
runs a `pref @Rn`; it skips on a QEMU without the two changes above.
