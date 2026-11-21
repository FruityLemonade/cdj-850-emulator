# SPDX-License-Identifier: GPL-2.0-or-later
"""Present native FLAC as seekable PCM to the v1.13 USB audio task.

The code is C in payload/ (the shared SDRAM payload, mods/payloadbuild.py):
firmware.c (the six entry points), decoder.c (dr_flac), flac_stack.s (the
decoder's private stack), stock.S (the classifier's moved entry). Eight
writes to MAIN, all reached through the service-mode gate:

* flac_classify: an EntryRedirect at the directory classifier 0x0C040C90
  (called by bsr from 0x0C040ACE and 0x0C0408A0, so no pool word to redirect):
  its 12 entry bytes become mov.l/jmp to __gate_flac_file_classify, which runs
  flac_file_classify (".flac" -> type 11, like WAV) or, in service mode,
  flac_stock_classify (stock.S: the 12 bytes, then 0x0C040C9C).
* seven PoolRedirects of the USB audio calls (pool word, stock function,
  reader of the pool word):
  0x0C08CBF0 0x0C000CC4 (WAV header read)   0x0C08CAD4, 0x0C08CB3C
  0x0C0585F4 0x0C0BE3E4 (WAV PCM read)      0x0C05845E
  0x0C057F1C 0x0C10E7C8 (file size)         0x0C057E4A
  0x0C05814C 0x0C10E7C8 (file size)         0x0C057F58
  0x0C058150 0x0C10E246 (file close)        0x0C057F82
  0x0C0BE79C 0x0C057CE4 (audio open)        0x0C0BE67C
  0x0C0BED94 0x0C057CE4 (audio open)        0x0C0BEBF4, 0x0C0BECF2

None of the written bytes is in the boot-critical closure (mods/bootcrit.py).
Two of the readers are: 0x0C057E4A and 0x0C0BE67C (tests/test_cdj850_flac_patch.py).
"""
from sigpatch import Anchor, EntryRedirect, PoolRedirect

CLASSIFIER = 0x0C040C90
CLASSIFIER_PROLOGUE = bytes.fromhex("4cf07fdc6c5362526d43842b")

# (name, pool word, stock function, payload symbol)
AUDIO_CALLS = (
    ("flac_header", 0x0C08CBF0, 0x0C000CC4, "flac_wav_header_read"),
    ("flac_pcm", 0x0C0585F4, 0x0C0BE3E4, "flac_wav_pcm_read"),
    ("flac_size", 0x0C057F1C, 0x0C10E7C8, "flac_audio_source_size"),
    ("flac_size2", 0x0C05814C, 0x0C10E7C8, "flac_audio_source_size"),
    ("flac_file_close", 0x0C058150, 0x0C10E246, "flac_file_close"),
    ("flac_audio_open", 0x0C0BE79C, 0x0C057CE4, "flac_audio_open"),
    ("flac_audio_open2", 0x0C0BED94, 0x0C057CE4, "flac_audio_open"),
)

# The halfwords around one reader of each pool word (the mov.l at index 2),
# unique in MAIN 1.13 (tests/test_cdj850_flac_patch.py).
READER_SIGS = {
    "flac_header": [0x7708, 0x55F9, 0xD22C, 0x420B, 0x2F12, 0x2008],   # 0x0C08CB3C
    "flac_pcm": [0x6411, 0x67B3, 0xD265, 0x420B, 0x6683, 0x7F08],      # 0x0C05845E
    "flac_size": [0x2602, 0x6403, 0xDD34, 0x4D0B, 0x65C3, 0x65C2],     # 0x0C057E4A
    "flac_size2": [0x3420, 0x8904, 0xD27C, 0x420B, 0x65C3, 0xA002],    # 0x0C057F58
    "flac_file_close": [0xA00B, 0x2562, 0xD273, 0x424B, 0x6E03, 0xE600],    # 0x0C057F82
    "flac_audio_open": [0x6651, 0x770C, 0xD247, 0x420B, 0x6511, 0x7F04],     # 0x0C0BE67C
    "flac_audio_open2": [0x6422, 0x7710, 0xDE67, 0x4E0B, 0x6511, 0x6403],    # 0x0C0BEBF4
}

# Stack frames over the payload's 512-byte rule, allowed because these
# functions run only on flac_stack.s's 16 KB private stack (payloadbuild.py):
# decoder.c is called only from firmware.c's decoder(), through flac_on_stack.
PAYLOAD_STACK_LIMITS = {"decoder.c": 4608}


def _redirect(name, old, routine):
    return PoolRedirect(what="FLAC: %s" % name.replace("flac_", "").replace("_", " ").replace("2", " (2nd reader)"),
                        target="main", fw_versions=("1.13",),
                        anchor=Anchor(sig=READER_SIGS[name], index=2, literal=True),
                        old=old, routine="payload:" + routine)


MODS = {
    "flac": EntryRedirect(
        what="FLAC: .flac files in the directory classifier",
        target="main", fw_versions=("1.13",),
        # the stock code behind the 12 replaced bytes (0x0C040C9C..), unique in MAIN 1.13
        anchor=Anchor(sig=[0x656C, 0x6E73, 0x8709, 0x8F02, 0x6603, 0xA134], index=-6, literal=False),
        old=CLASSIFIER_PROLOGUE,
        routine="payload:flac_file_classify",
        stock="payload:flac_stock_classify"),
}
MODS.update({name: _redirect(name, old, routine) for name, _, old, routine in AUDIO_CALLS})
