# SPDX-License-Identifier: GPL-2.0-or-later
"""loader on the CDJ-850 MAIN 1.13: copy a blob of custom code and data to
free SDRAM (0x0DE00000..0x0DF70000) at boot. See mods/c850loader.py.

The blob's payload is the file $CDJ850_LOADER_PAYLOAD names, or nothing (the
header and both guard words alone) when it is unset. The payload is copied to
0x0DE00020; hooks reach code in it with an ordinary mov.l/jsr.
"""
import c850loader
from sigpatch import ImagePatch, NotApplied

PAYLOAD_ENV = 'CDJ850_LOADER_PAYLOAD'


def payload(env):
    path = env.get(PAYLOAD_ENV)
    if not path:
        return b''
    with open(path, 'rb') as f:
        return f.read()


def apply(image, env):
    try:
        return c850loader.install(image, payload(env))
    except c850loader.LoaderError as e:
        raise NotApplied(str(e))


MODS = {
    'loader': ImagePatch(
        what='loader stub: copy a custom blob to SDRAM 0x0DE00000 at boot',
        target='main',
        fw_versions=('1.13',),
        apply=apply,
    ),
}
