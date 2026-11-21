# SPDX-License-Identifier: GPL-2.0-or-later
"""loader on the CDJ-850 MAIN 1.13: copy a blob of custom code and data to
free SDRAM (0x0DE00000..0x0DF70000) at boot. See mods/c850loader.py.

The blob's payload is, in this order: the payload patch_main.py built from
the payload sources of the mods named with it (mods/<mod>/cdj850/payload/,
payloadbuild.py), which also adds this patch on its own; else the file
$CDJ850_LOADER_PAYLOAD names; else nothing (the header and both guard words
alone). When both a built payload and $CDJ850_LOADER_PAYLOAD are there, the
file must hold exactly the built payload, since the mods' hooks call its
symbols. The payload is copied to 0x0DE00020; hooks reach code in it with an
ordinary mov.l/jsr.
"""
import c850loader
from sigpatch import ImagePatch, NotApplied

PAYLOAD_ENV = 'CDJ850_LOADER_PAYLOAD'
BUILT_KEY = 'cdj850.payload_blob'           # patch_main.PAYLOAD_BLOB_KEY


def payload(env):
    built = env.get(BUILT_KEY)
    path = env.get(PAYLOAD_ENV)
    given = None
    if path:
        with open(path, 'rb') as f:
            given = f.read()
    if built is not None:
        if given is not None and given != built:
            raise NotApplied('%s names a payload other than the one the selected mods build and call into; '
                             'unset it' % PAYLOAD_ENV)
        return built
    return given or b''


def apply(image, env):
    try:
        blob = payload(env)
        return c850loader.install(image, blob)
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
