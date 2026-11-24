# Browse

Optional CDJ-850 MAIN 1.13 mod for rekordbox USB exports.

- A track gets a `*` prefix in BROWSE after more than 20 seconds of playback. The mod tracks up to 1,024 track IDs per mount and accumulates playback time across pauses, loops and reloads. USB stop, dismount or power-off clears the marks.
- Tap MENU in BROWSE to cycle through BPM, Key and Default. The deck briefly shows the selected order. Open the **TRACK** category to apply it. BPM uses the export's analyzed tempo. Key groups tracks by the exported key.

Enable **Browse** when configuring mods in the emulator. The firmware patcher takes `browse` as the mod name. See the [firmware notes](../../docs/cdj850-modding.md#browse-history-and-sort) for the hooks.

## CDJ-350 (MAIN 1.14)

The same markers and sorting, with two differences: the order is set in UTILITY (hold
DISPLAY/UTILITY, turn to `SORT`, push to cycle DEFAULT / BPM / KEY; saved), since the CDJ-350's
MENU key is DISPLAY; and a `*` appears the next time the TRACK list is built, not on the open
row. See `docs/cdj350.md`, "Browse".

    .venv/bin/python mods/patch_update.py --model cdj350 C350MAIN.UPD out.UPD browse
