# payload_demo (CDJ-850)

The reference for **payload mods**: C code that lives in the shared SDRAM payload
(`0x0DE00020`, copied there at boot by the [`loader`](../loader/README.md)) and is
reached from MAIN by symbol. It changes nothing the deck does; both routines only
count. Off by default.

| knob | values |
|---|---|
| `CDJ_MAIN_PAYLOAD_DEMO` | hook on the `display_stage_bpm` tail, `1` on / `0` off (default) |
| `CDJ_MAIN_PAYLOAD_DEMO_REDIRECT` | redirect of the 1 ms tick's pool word, `1` on / `0` off (default) |

Firmware: CDJ-850 MAIN 1.13 only.

## Files

| file | what |
|---|---|
| `cdj850/payload/demo.c` | the payload code: `payload_demo_count` (hook) and `payload_demo_tick` (redirect target), three `.bss` counters |
| `cdj850/patch.py` | `Mod(routine='payload:payload_demo_count')` and `PoolRedirect(..., routine='payload:payload_demo_tick')` |

## What it changes

| where | what |
|---|---|
| `0x0C0642AA..0x0C0642B8` | the `display_stage_bpm` tail jumps to the trampoline it shares with `bpm999`/`bpm_probe`; the trampoline calls `payload_demo_count` (`r4` = saved `r0..r7`, `r5` = `r15`), which counts in `payload_demo_bpm_calls` and stores `r15` in `payload_demo_last_r15` |
| `0x0C0B9310` | pool word of the 1 ms tick routine `0x0C0B92CC` (loaded only at `0x0C0B92EA`): `0x0C0EF8C8` becomes the address of `payload_demo_tick`, which counts in `payload_demo_tick_calls` and tail-jumps (`jmp`) to `0x0C0EF8C8`, so the auto-standby seconds counter still runs |
| loader | added automatically, carrying the 64-byte payload |

Cave cost: 16 bytes (one more hook slot in the existing trampoline). Addresses of the
symbols come from the build (`python3 mods/payloadbuild.py /tmp/p.bin payload_demo`
writes `/tmp/p.map`); in the current build `payload_demo_count` = `0x0DE00020`,
`payload_demo_tick` = `0x0DE00038`.

## Checking it

```bash
python3 mods/patch_update.py --model cdj850 C850MAIN.UPD out/C850MAIN.UPD payload_demo
python3 -m pytest tests/test_cdj850_payload.py
```

In the emulator (`test_live_payload_demo_hook_and_redirect_run`) the tick counter
equals the 1 ms tick counter `0x0CE49F54` exactly, the seconds counter `0x0C1E10AE`
keeps counting one per 1000 ticks, and the hook counter rises with the display updates.
Nothing here has run on a real deck.
