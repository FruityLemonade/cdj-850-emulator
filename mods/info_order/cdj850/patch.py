# SPDX-License-Identifier: GPL-2.0-or-later
"""info_order on the CDJ-850 MAIN 1.13: the INFO screens in the order
Track, BPM, waveform / Artist, Rating, Key / Album, Comment.

Stock order is the order the track-metadata reader got the fields in
(title, artist, album, BPM, comment, key, rating on a rekordbox export),
copied into the loaded track's DB record (+0x851 count, +0x852.. codes) and
walked by the INFO screens; the first screen always shows title and artist.
It is not a rekordbox setting: swapping the category order tables (17, 18)
of export.pdb did not change it. See payload/info_order.c and
docs/cdj850-modding.md section 4b.8.

* info_order: hook (payload routine info_order_hook) on the tail of
  display_stage_bpm, 0x0C0642AA..0x0C0642B8 (the bpm999 / bpm_decimals
  span), before bpm_decimals: puts each record's list in the order above and
  shows the BPM item in the title view's second row (bpm_decimals then makes
  it the actual BPM). RAM only; the list is read by FUN_0C0669D6 and the row
  filler FUN_0C06543E, which are boot-critical and not patched.
"""
from sigpatch import Mod

BPM_SITE = dict(
    sig=[0xD62E, 0x6260, 0x2228, 0x8902, 0xD62A, 0x6061, 0x8154],
    start=0,
    end=7,
)

MODS = {
    'info_order': Mod(
        what='INFO order: Track, BPM, waveform / Artist, Rating, Key / Album, Comment',
        target='main',
        fw_versions=('1.13',),
        args=[],
        literal=False,
        hook=True,
        routine='payload:info_order_hook',
        **BPM_SITE,
    ),
}
