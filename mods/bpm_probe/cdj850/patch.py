# SPDX-License-Identifier: GPL-2.0-or-later
"""bpm_probe on the CDJ-850 MAIN 1.13: a glass test, not a feature.

Hooks the same span as bpm999 (display_stage_bpm tail, 0x0C0642AA..0x0C0642B8,
see mods/bpm999/cdj850/patch.py), so it runs on every display_stage_bpm call.
Before the TX builder copies the staging words, the routine writes
BPM 199 to the staging word 0x0C9D0AE0 and a frame value 75..99 to the staging
byte 0x0C9D0ADE (page 0x01 byte 7). The frame steps once every 128 TX builds.
"""
from sigpatch import Mod

BPM_SITE = dict(
    sig=[0xD62E, 0x6260, 0x2228, 0x8902, 0xD62A, 0x6061, 0x8154],
    start=0,
    end=7,
)

MODS = {
    'bpm_probe': Mod(
        what='BPM probe: BPM box forced to 199, frame digits cycle 75..99',
        target='main',
        fw_versions=('1.13',),
        args=[],
        literal=False,
        hook=True,
        **BPM_SITE,
    ),
}
