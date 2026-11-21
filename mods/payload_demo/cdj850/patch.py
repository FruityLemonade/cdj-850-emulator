# SPDX-License-Identifier: GPL-2.0-or-later
"""payload_demo on the CDJ-850 MAIN 1.13: the reference for payload mods.

Its code is C in payload/demo.c, built into the shared payload the loader
copies to 0x0DE00020 (mods/payloadbuild.py); naming either patch adds the
loader. Nothing the deck does changes: both routines only count.

* payload_demo: a hook (sigpatch.Mod) whose routine is the payload symbol
  payload_demo_count, on the tail of display_stage_bpm (0x0C0642AA..0x0C0642B8,
  the span bpm999 and bpm_probe hook too, so it shares their trampoline).
* payload_demo_redirect: a PoolRedirect of the pool word 0x0C0B9310, which the
  1 ms tick routine 0x0C0B92CC loads at 0x0C0B92EA (and nothing else reads) to
  call 0x0C0EF8C8, the power/standby part of the tick that drives the
  auto-standby seconds counter. The payload's payload_demo_tick counts the
  call and tail-jumps to 0x0C0EF8C8, so auto standby works as before.
"""
from sigpatch import Anchor, Mod, PoolRedirect

# The tail of display_stage_bpm, as in mods/bpm999/cdj850/patch.py.
BPM_SITE = dict(
    sig=[0xD62E, 0x6260, 0x2228, 0x8902, 0xD62A, 0x6061, 0x8154],
    start=0,
    end=7,
)

MODS = {
    'payload_demo': Mod(
        what='payload demo: count display_stage_bpm runs in the payload',
        target='main',
        fw_versions=('1.13',),
        args=[],
        literal=False,
        hook=True,
        routine='payload:payload_demo_count',
        **BPM_SITE,
    ),
    # The 1 ms tick routine 0x0C0B92CC: sts.l pr,@-r15; tick++ (0x0CE49F54); then
    # seven handlers, each mov.l @(pool),rN; jsr/n @rN. Index 15 is the mov.l at
    # 0x0C0B92EA that loads 0x0C0EF8C8 from 0x0C0B9310.
    'payload_demo_redirect': PoolRedirect(
        what='payload demo: 1 ms power/standby tick called through the payload',
        target='main',
        fw_versions=('1.13',),
        anchor=Anchor(sig=[0x4F22, 0xD70A, 0x6172, 0x7101, 0xD409, 0x440B, 0x2712, 0xD509, 0x454B,
                           0xD609, 0x464B, 0xD709, 0x474B, 0xD109, 0x414B, 0xD409, 0x444B, 0xD509,
                           0x454B, 0xD209, 0x422B, 0x4F26],
                      index=15, literal=True),
        old=0x0C0EF8C8,
        routine='payload:payload_demo_tick',
    ),
}
