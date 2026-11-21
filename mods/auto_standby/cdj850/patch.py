# SPDX-License-Identifier: GPL-2.0-or-later
"""auto_standby on the CDJ-850 MAIN 1.13: AUTO STANDBY timeout in minutes.

The idle countdown is FUN_0c0ef844 (0x0C0EF844). It counts whole seconds in
the u16 at 0x0C1E10AE and, once the seconds count reaches the limit read from
the table at 0x0C18CCDC, asks for standby. The table is indexed by
(FUN_0c08f186() & 0xff), where FUN_0c08f186 returns the 3-bit field
(UTILITY byte 0x0C9DA23E >> 4) & 7. Index 0 (AUTO STANDBY OFF) is gated out
before the comparison, and index 1 (AUTO STANDBY ON) holds the stock limit
0x3840 = 14400 s = 4 hours (u16 BE). Only that one u16 is rewritten, so the
OFF gate, the playing/source gate and the standby request path are unchanged.

Knob: CDJ850_STANDBY_MINUTES (integer, 1..1092; default 20). 1092 minutes is
the largest value whose seconds count fits the u16 (65520 s).
"""
import os

from sigpatch import DataPatch, Edit, Mod

STOCK_LIMIT = bytes.fromhex('3840')   # 14400 s = 4 hours, big-endian u16
DEFAULT_MINUTES = 20
MAX_MINUTES = 0xFFFF // 60            # 1092

# Table at 0x0C18CCDC: u16 BE limits per UTILITY index 0..7. The 6-byte anchor
# is unique in MAIN (file offset 0x18CCDC); the limit is at +2.
STANDBY_TABLE = bytes.fromhex('000038400901')


def standby_limit(env):
    raw = env.get('CDJ850_STANDBY_MINUTES', str(DEFAULT_MINUTES)).strip()
    try:
        minutes = int(raw, 10)
    except ValueError:
        raise SystemExit('auto_standby: CDJ850_STANDBY_MINUTES must be an integer, got %r' % raw)
    if not 1 <= minutes <= MAX_MINUTES:
        raise SystemExit('auto_standby: CDJ850_STANDBY_MINUTES must be 1..%d (u16 seconds), got %d'
                         % (MAX_MINUTES, minutes))
    return (minutes * 60).to_bytes(2, 'big')


def _edits(env):
    return (Edit(STOCK_LIMIT, standby_limit(env)),)


MODS = {
    'autostandby': DataPatch(
        what='AUTO STANDBY timeout: CDJ850_STANDBY_MINUTES (default 20) instead of 4 hours',
        target='main',
        fw_versions=('1.13',),
        table=STANDBY_TABLE,
        before=0,
        reach=6,
        edits=_edits,
        kind='data',
    ),
}
