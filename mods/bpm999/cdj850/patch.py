# SPDX-License-Identifier: GPL-2.0-or-later
"""bpm999 on the CDJ-850 MAIN 1.13: BPM display up to 999.0.

Three changes, see README.md:

* FUN_0c073788 (beat-grid tempo) clamps tenths to 0x708 = 180.0. The movi20
  immediate is raised to 9990 (999.0). DataPatch, no code.
* FUN_0c0d1c42 range-checks the base tempo 60.0..199.9 (cmp/gt 0x7CF) and
  falls back to 130.0 outside it. A 200+ track would otherwise be replaced by
  130.0 in that path, so the upper bound is raised to 9990. DataPatch.
* The pitched whole BPM (FUN_0c0d9b44, FUN_0c0726ec and FUN_0c072824 tails)
  is not clamped by any constant. display_stage_bpm (0x0C064118) is the
  writer of the staging BPM word 0x0C9D0AE0 that TX page 0x01 bytes 8..9 are
  copied from. A hook on its last two stores clamps that word to 999 in
  place. The hook span is 0x0C0642AA..0x0C0642B8 (7 halfwords). The site is
  outside the boot-critical set, which the validator checks. The TX builder
  itself (0x0C065D9E) is boot-critical and is not touched.

The auto BPM counter (FUN_0c0726ec, FUN_0c072824: reject > 0x710, clamp
0x708) is the 70-180 detection window and stays as it is.
"""
from sigpatch import DataPatch, Edit, Mod

# Tail of display_stage_bpm: the second conditional store of the BPM word
# (mov.w r0,@(8,r5) at 0x0C0642B6) and the join at 0x0C0642B8 (the epilogue).
# The span is taken from 0x0C0642AA, where both paths of the tail meet; a bt
# inside it lands on the span end. The routine (main_bpm999.s) runs after the
# span and clamps the word at 0x0C9D0AE0 in place.
BPM_SITE = dict(
    sig=[0xD62E, 0x6260, 0x2228, 0x8902, 0xD62A, 0x6061, 0x8154],
    start=0,
    end=7,
)

MODS = {
    'bpm999': Mod(
        what='BPM 999: clamp the TX BPM word to 999 (pitched values above 999)',
        target='main',
        fw_versions=('1.13',),
        args=[],
        literal=False,
        hook=True,
        **BPM_SITE,
    ),
    # movi20 #0x708 -> #0x2706 (9990) in FUN_0c073788 (beat-grid tenths clamp).
# MOVI20 is 0000nnnn 0000iiii followed by imm[15:0]; 9990 fits in 16 bits, so only
# the second word changes.
    # The window is the 10 bytes before the movi20 plus the 4-byte movi20.
    'bpm999grid': DataPatch(
        what='BPM 999: beat-grid tempo clamp 180.0 -> 999.0 (FUN_0c073788)',
        target='main',
        fw_versions=('1.13',),
        table=bytes.fromhex('60530e2d64ed74053145'),
        before=0,
        reach=14,
        edits=(Edit(bytes.fromhex('04000708'), bytes.fromhex('04002706')),),
    ),
    # cmp/gt movi20 #0x7CF (1999) -> #0x2706 (9990) in FUN_0c0d1c42 (base tempo
    # range check 60.0..199.9 that defaults to 130.0).
    'bpm999range': DataPatch(
        what='BPM 999: base tempo range check 60.0..199.9 -> 60.0..999.0 (FUN_0c0d1c42)',
        target='main',
        fw_versions=('1.13',),
        table=bytes.fromhex('8b1dd44c3641900026688908'),
        before=0,
        reach=16,
        edits=(Edit(bytes.fromhex('050007cf'), bytes.fromhex('05002706')),),
    ),
}
