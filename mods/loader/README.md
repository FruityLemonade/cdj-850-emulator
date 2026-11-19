# loader (CDJ-850)

Infrastructure for bigger CDJ-850 mods. At boot, MAIN copies a blob of custom
code and data to SDRAM that no firmware code uses. Hooks in MAIN can then call
into it with an ordinary `mov.l`/`jsr`. On its own the mod changes nothing the
deck does: without a payload it copies only a 32-byte header and sets two
guard words.

| knob | values |
|---|---|
| `CDJ_MAIN_LOADER` | `1` on, `0` off (default) |
| `CDJ850_LOADER_PAYLOAD` | path of the payload file (raw bytes, run from `0x0DE00020`); unset = no payload |

Firmware: CDJ-850 MAIN 1.13 only. The engine is `mods/c850loader.py`; the
RAM map behind it is in [the modding reference](../../docs/cdj850-modding.md)
(section 8.4).

## What it changes

| where | what |
|---|---|
| `0x0C000010` | MAIN's entry calls its first routine through this literal. It changes from `0x0C0B92AC` (the routine that clears the statics) to `0x0C1E2800`, the stub. |
| `0x0C1E2800` | The stub (0x40 bytes), then the blob: a header and the payload. This stretch is zeros in stock MAIN, inside the unpacked image and covered by the bootloader's checksum. The start-up clear wipes it right after the stub has run. |

At boot the stub copies the blob to `0x0DE00000` through the uncached
SDRAM mirror, writes the top guard word, and jumps to `0x0C0B92AC`. Start-up
then goes on exactly as before. The stub leaves r3 and PR alone, because the
entry still needs them.

## In SDRAM after boot

| address | contents |
|---|---|
| `0x0DE00000` | `LDR<`: low guard, the first word of the header |
| `0x0DE00004` | format 1, header size 0x20 |
| `0x0DE00008` | payload length in bytes |
| `0x0DE0000C` | 32-bit sum of the payload's big-endian words |
| `0x0DE00010` | region end `0x0DF70000`, high guard address `0x0DF6FFFC` |
| `0x0DE00020` | the payload |
| `0x0DF6FFFC` | `>LDR`: high guard |

The region `0x0DE00000–0x0DF70000` (1.44 MB) lies above the kernel heap and
below the CD sector cache. No firmware code addresses it. To check a running
emulator, dump the region with the QEMU monitor
(`pmemsave 0x0DE00000 0x170000 "region.bin"`), then run:

```bash
python3 mods/c850loader.py ram region.bin
```

This reports a guard word or payload that has changed.

## Building

```bash
CDJ850_LOADER_PAYLOAD=blob.bin python3 mods/patch_update.py --model cdj850 C850MAIN.UPD out/C850MAIN.UPD loader
```

`validate.py` accepts the entry write only when the code it now reaches is,
byte for byte, the stub `c850loader.py` writes, and the blob has a valid
header and lies over zeros of the cleared statics. Any other write to the
entry fails as a change to boot-critical code. `python3 mods/c850loader.py check <stock.bin> <main.bin>`
runs the same check on two unpacked images.

The packed MAIN may end anywhere up to `0x3F0000` (`c850_repack.MAIN_END`;
the stock layout stops at `0x200000`, the updater programs to `0x3F0000`).
Even a payload that fills the whole region with random bytes packs to an
end of `0x370E5F`, so the region's 1,507,292 bytes are the only limit.
