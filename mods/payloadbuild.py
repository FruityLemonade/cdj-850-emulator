#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Build the CDJ-850 shared payload: the C and SH-2A assembly that mods ship in
mods/<mod>/<model>/payload/, compiled and linked into ONE image that the
loader (c850loader.py) copies to 0x0DE00020 at boot.

    usage: payloadbuild.py [--model <id>] <out.bin> <mod> [<mod> ...]
           payloadbuild.py --list

A mod folder contributes every *.c, *.s and *.S file in its payload/ folder.
The sources of the mods a build names are compiled (C: sh-elf-gcc with
CFLAGS below; .s: sh-elf-as -isa=sh2a -big; .S: through the C preprocessor),
in the order of the mod folders' names and then file names, and linked with
mods/payload/payload.ld at 0x0DE00020. The result is the raw image (text,
rodata, data, then .bss as zeros and the end marker ">PLD") and a map of its
global symbols. Hooks (sigpatch.Mod(routine='payload:<symbol>')) and pool
redirects (sigpatch.PoolRedirect) name those symbols.

Symbol names: sh-elf prefixes C names with '_'. A lookup tries the name as
given first and then with the '_' in front, so a mod names the C function
`quant_snap` as 'payload:quant_snap'. Global names must be unique across
every mod that can be built together: prefix them with the mod's name.

Service-mode gates: for every PoolRedirect the built mod folders declare,
the build adds a thunk `__gate_<symbol>` (generated SH-2A assembly, linked
after every mod's code) and sigpatch points the pool word at it instead of
at the symbol. The thunk reads the model's gate bytes (ImageProfile.gate,
mods/players/<model>.py); when any is nonzero (CDJ-850: a power-on firmware
update mode, or the power-on keys not read yet) it jumps to the stock
function `old`, else to the mod's routine. It uses r0, r1 and T only
(caller-saved), leaves r4-r7, r15 and pr as the caller set them, so the
callee sees exactly the stock call. Hook routines are gated by their
trampoline (sigpatch.trampoline). A symbol redirected from two pool words
must have one stock function.

The toolchain is tools/sh-elf/bin/sh-elf-* (scripts/build/sh_elf_toolchain.sh)
unless $CDJ850_SH_ELF names another prefix (e.g. /opt/sh/bin/sh-elf-). The
integer helpers of libgcc come from the default multilib's libgcc.a, since
GCC 15.3 cannot build the m2a-nofpu one (docs/cdj850-modding.md, section 8.4).
"""
import collections
import glob
import hashlib
import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

LINKER_SCRIPT = os.path.join(HERE, 'payload', 'payload.ld')
INCLUDE = os.path.join(HERE, 'payload', 'include')

BASE = 0x0DE00020                  # c850loader.PAYLOAD
END = 0x0DF6FFFC                   # c850loader.GUARD_HI_ADDR
END_MARKER = 0x3E504C44            # ">PLD", the image's last word

# -mrenesas: MAIN was built with the Renesas compiler, whose callers expect
# MACH/MACL to survive a call (GCC's own ABI clobbers them); with it GCC saves
# them in every function that uses them. No FPU, ever: hooks do not save it.
CFLAGS = ['-m2a-nofpu', '-mb', '-mrenesas', '-O2', '-ffreestanding', '-fno-builtin', '-nostdlib',
          '-fno-common', '-fno-asynchronous-unwind-tables', '-fno-unwind-tables', '-fstack-usage',
          '-Wall', '-Werror=implicit-function-declaration']
ASFLAGS = ['-isa=sh2a', '-big']
STACK_LIMIT = 512                  # bytes one payload function's frame may take

SOURCE_KINDS = ('*.c', '*.s', '*.S')

Payload = collections.namedtuple('Payload', 'blob symbols mods map stack')


class PayloadError(Exception):
    pass


def tool_prefix():
    """The sh-elf toolchain prefix: $CDJ850_SH_ELF, else the repo's tools/sh-elf."""
    env = os.environ.get('CDJ850_SH_ELF')
    if env:
        return env
    local = os.path.join(ROOT, 'tools', 'sh-elf', 'bin', 'sh-elf-')
    if os.path.exists(local + 'as'):
        return local
    return 'sh-elf-'


def have_toolchain():
    p = tool_prefix()
    return all(os.path.exists(p + t) or shutil.which(p + t) for t in ('gcc', 'as', 'ld', 'objcopy', 'nm'))


def libgcc():
    """The default multilib's libgcc.a (integer helpers only are used)."""
    out = subprocess.run([tool_prefix() + 'gcc', '-print-libgcc-file-name'],
                         capture_output=True, text=True, check=True).stdout.strip()
    if not os.path.isfile(out):
        raise PayloadError('no libgcc.a at %s' % out)
    return out


def sources(mod, model):
    """The payload sources of one mod folder for `model`, sorted."""
    folder = os.path.join(HERE, mod, model, 'payload')
    return sorted(p for kind in SOURCE_KINDS for p in glob.glob(os.path.join(folder, kind)))


def payload_mods(model):
    """Mod folders that ship payload sources for `model`."""
    return sorted(m for m in os.listdir(HERE) if os.path.isdir(os.path.join(HERE, m)) and sources(m, model))


def _run(cmd, cwd):
    r = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd)
    if r.returncode:
        raise PayloadError('%s failed:\n%s%s' % (os.path.basename(cmd[0]), r.stdout, r.stderr))
    return r.stdout


GATE_PREFIX = '__gate_'


def gate_name(symbol):
    """The payload symbol of the service-mode gate in front of `symbol`."""
    return GATE_PREFIX + symbol


def gate_bytes(model):
    """The model's service-mode gate bytes (ImageProfile.gate), () if none."""
    import modelfw
    try:
        return tuple(modelfw.player(model).MAIN_PROFILE.gate)
    except (ImportError, AttributeError):
        return ()


def redirect_gates(mods, model):
    """{symbol: stock function} of every PoolRedirect the mod folders declare
    for `model`; the payload gets a gate in front of each symbol."""
    import modelfw
    import sigpatch
    out = {}
    for mod in mods:
        if not os.path.exists(os.path.join(HERE, mod, model, 'patch.py')):
            continue
        for name, patch in modelfw.mod_patch(mod, model).MODS.items():
            if not isinstance(patch, sigpatch.PoolRedirect):
                continue
            symbol = sigpatch.payload_symbol(patch)
            if out.get(symbol, patch.old) != patch.old:
                raise PayloadError('%s is redirected from two stock functions (%#x, %#x): one gate cannot '
                                   'return to both' % (symbol, out[symbol], patch.old))
            out[symbol] = patch.old
    return out


def gate_source(gates, defined, gate):
    """SH-2A assembly of one gate per {symbol: stock}; `defined` is the set of
    global names the objects define (a C function `f` is `_f`)."""
    lines = ['! generated by payloadbuild.py: service-mode gates, one per redirected symbol',
             '\t.text', '\t.align 2']
    for k, (symbol, old) in enumerate(sorted(gates.items())):
        real = symbol if symbol in defined else '_' + symbol
        if real not in defined:
            raise PayloadError('redirected symbol %s is defined by no payload source' % symbol)
        lines += ['\t.global %s' % gate_name(symbol), '\t.type %s, @function' % gate_name(symbol),
                  '\t.align 2', '%s:' % gate_name(symbol)]
        for j in range(len(gate)):
            lines += ['\tmov.l\t.Lg%d_%d, r1' % (k, j), '\tmov.b\t@r1, r1']
            lines += ['\tmov\tr1, r0' if j == 0 else '\tor\tr1, r0']
        lines += ['\ttst\tr0, r0', '\tbf\t.Lstock%d' % k,
                  '\tmov.l\t.Lmod%d, r1' % k, '\tjmp\t@r1', '\tnop',
                  '.Lstock%d:' % k, '\tmov.l\t.Lold%d, r1' % k, '\tjmp\t@r1', '\tnop', '\t.align 2']
        lines += ['.Lg%d_%d:\t.long\t%#010x' % (k, j, a) for j, a in enumerate(gate)]
        lines += ['.Lmod%d:\t.long\t%s' % (k, real), '.Lold%d:\t.long\t%#010x' % (k, old),
                  '\t.size %s, . - %s' % (gate_name(symbol), gate_name(symbol))]
    return '\n'.join(lines) + '\n'


def _digest(mods, model):
    h = hashlib.sha256()
    h.update(repr((sorted(redirect_gates(mods, model).items()), gate_bytes(model))).encode())
    h.update(tool_prefix().encode())
    for path in [LINKER_SCRIPT] + sorted(glob.glob(os.path.join(INCLUDE, '*'))) + \
            [p for m in mods for p in sources(m, model)]:
        h.update(path.encode())
        with open(path, 'rb') as f:
            h.update(f.read())
    return h.hexdigest()


_CACHE = {}


def build(mods, model='cdj850', keep=None):
    """The Payload of the given mod folders' sources. `keep`, when given, is a
    directory that receives the objects, payload.elf, payload.bin and
    payload.map. Raises PayloadError when nothing is to be built, the
    toolchain fails, or the image breaks a rule (layout, end marker, stack)."""
    mods = sorted(set(mods))
    files = [p for m in mods for p in sources(m, model)]
    if not files:
        raise PayloadError('none of %s ships payload sources for %s' % (', '.join(mods) or 'no mods', model))
    key = (_digest(mods, model), keep)
    if key in _CACHE:
        return _CACHE[key]
    if not have_toolchain():
        raise PayloadError('the sh-elf toolchain is not installed (tools/sh-elf, see '
                           'scripts/build/sh_elf_toolchain.sh, or set $CDJ850_SH_ELF)')
    with tempfile.TemporaryDirectory() as tmp:
        work = keep or tmp
        os.makedirs(work, exist_ok=True)
        pre = tool_prefix()
        objs, stack = [], {}
        for k, src in enumerate(files):
            mod = os.path.relpath(src, HERE).split(os.sep)[0]
            obj = os.path.join(work, '%02d_%s_%s.o' % (k, mod, os.path.basename(src).replace('.', '_')))
            if src.endswith('.s'):
                _run([pre + 'as', *ASFLAGS, '-I', INCLUDE, '-o', obj, src], work)
            else:
                _run([pre + 'gcc', *CFLAGS, '-I', INCLUDE, '-c', '-o', obj, src], work)
                su = obj[:-2] + '.su'
                if os.path.exists(su):
                    for line in open(su):
                        where, size, _ = line.rstrip('\n').split('\t')
                        stack['%s:%s' % (mod, where.split(':')[-1])] = int(size)
            objs.append(obj)
        gates, gate = redirect_gates(mods, model), gate_bytes(model)
        if gates and gate:
            defined = set()
            for obj in objs:
                for line in _run([pre + 'nm', '-g', '--defined-only', obj], work).splitlines():
                    defined.add(line.split()[-1])
            src = os.path.join(work, 'zz_gates.s')
            with open(src, 'w') as f:
                f.write(gate_source(gates, defined, gate))
            obj = os.path.join(work, '%02d_gates_s.o' % len(objs))
            _run([pre + 'as', *ASFLAGS, '-o', obj, src], work)
            objs.append(obj)
        elf = os.path.join(work, 'payload.elf')
        mapfile = os.path.join(work, 'payload.ld.map')
        _run([pre + 'ld', '-EB', '-T', LINKER_SCRIPT, '--orphan-handling=error', '-Map', mapfile,
              '-o', elf, *objs, libgcc()], work)
        binary = os.path.join(work, 'payload.bin')
        _run([pre + 'objcopy', '-O', 'binary', elf, binary], work)
        with open(binary, 'rb') as f:
            blob = f.read()
        symbols = {}
        for line in _run([pre + 'nm', '-n', elf], work).splitlines():
            m = re.match(r'([0-9a-f]{8}) ([A-Za-z]) (\S+)$', line)
            if m and m.group(2) in 'TtDdBbRrAa':
                symbols[m.group(3)] = int(m.group(1), 16)
    check(blob, symbols, stack)
    text = map_text(symbols, mods, stack)
    if keep:
        with open(os.path.join(keep, 'payload.map'), 'w') as f:
            f.write(text)
    _CACHE[key] = Payload(blob, symbols, tuple(mods), text, stack)
    return _CACHE[key]


def check(blob, symbols, stack):
    """The rules of a payload image, in addition to what the linker checks."""
    start, end = symbols.get('__payload_start'), symbols.get('__payload_end')
    if start != BASE:
        raise PayloadError('the payload starts at %#x, not %#x' % (start or 0, BASE))
    if end is None or end > END or end - start != len(blob):
        raise PayloadError('the image is %d bytes, the symbols say %#x..%#x' % (len(blob), start, end or 0))
    if blob[-4:] != END_MARKER.to_bytes(4, 'big'):
        raise PayloadError('the image does not end in its marker word')
    bss = blob[symbols['__bss_start'] - BASE:symbols['__bss_end'] - BASE]
    if any(bss):
        raise PayloadError('.bss is not zero in the image')
    big = {f: n for f, n in stack.items() if n > STACK_LIMIT}
    if big:
        raise PayloadError('stack frames over %d bytes: %s' % (STACK_LIMIT, big))


def lookup(symbols, name):
    """The address of `name` (or of its C form '_name'), else None."""
    if name in symbols:
        return symbols[name]
    return symbols.get('_' + name)


def map_text(symbols, mods, stack):
    lines = ['# CDJ-850 payload, mods: %s' % ', '.join(mods),
             '# address   symbol']
    lines += ['%08x  %s' % (a, n) for n, a in sorted(symbols.items(), key=lambda s: (s[1], s[0]))]
    lines += ['# stack frame bytes per function'] + ['# %5d  %s' % (n, f) for f, n in sorted(stack.items())]
    return '\n'.join(lines) + '\n'


def main(argv):
    import modelfw
    model, args = modelfw.model_id(argv)
    if args == ['--list']:
        for m in payload_mods(model):
            print('%-16s %s' % (m, ' '.join(os.path.relpath(p, HERE) for p in sources(m, model))))
        return 0
    if len(args) < 2:
        print(__doc__)
        return 2
    out = os.path.abspath(args[0])
    keep = os.path.splitext(out)[0] + '.build'
    p = build(args[1:], model, keep=keep)
    shutil.copyfile(os.path.join(keep, 'payload.bin'), out)
    with open(os.path.splitext(out)[0] + '.map', 'w') as f:
        f.write(p.map)
    print('payloadbuild: %d bytes at %#x for %s, map in %s' % (len(p.blob), BASE, ', '.join(p.mods),
                                                               os.path.splitext(out)[0] + '.map'))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
