#!/bin/bash
# SPDX-License-Identifier: GPL-2.0-or-later
#
# Build the bare-metal SH toolchain the CDJ-850 mods use (binutils 2.47 +
# GCC 15.3, C only, no libc) into tools/sh-elf (gitignored). Sources and build
# trees go to tools/src. Needs curl, gpg, make and a host C/C++ compiler.
#
#   scripts/build/sh_elf_toolchain.sh
#
# Then: tools/sh-elf/bin/sh-elf-as -isa=sh2a -big (cave routines,
# mods/patch_main.py --assemble) and sh-elf-gcc -m2a-nofpu -mb -mrenesas ...
# (payload C, mods/payloadbuild.py).
#
# libgcc caveat (GCC 15.3): the m2a-nofpu multilib of libgcc does not build.
# libgcc/config/sh/lib1funcs.S assembles its sdivsi3_i4 helpers with FPU
# instructions for that multilib too, and the assembler rejects them
# ("opcode not valid for this cpu variant"), so `make all-target-libgcc`
# stops after the default and m2a multilibs. The payload needs only integer
# helpers, so the default multilib's libgcc.a (plain SH code, valid on SH-2A)
# is used instead: after `make install-gcc` this script installs it (and the
# m2a one) by hand. `sh-elf-gcc -m2a-nofpu -print-libgcc-file-name` then
# resolves to that default libgcc.a, which is what payloadbuild.py links.
# Most payload code needs none of it: GCC emits SH-2A divs/divu for division.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
SRC="$ROOT/tools/src"
PREFIX="$ROOT/tools/sh-elf"
B=binutils-2.47; G=gcc-15.3.0
mkdir -p "$SRC"
cd "$SRC"
[ -f gnu-keyring.gpg ] || curl -fsSLO https://ftp.gnu.org/gnu/gnu-keyring.gpg
for u in binutils/$B.tar.xz gcc/$G/$G.tar.xz; do
  n=$(basename $u); [ -f $n ] || curl -fsSLO https://ftp.gnu.org/gnu/$u; [ -f $n.sig ] || curl -fsSLO https://ftp.gnu.org/gnu/$u.sig
  gpg --no-default-keyring --keyring ./gnu-keyring.gpg --verify $n.sig $n
done
[ -d $B ] || tar xf $B.tar.xz
[ -d $G ] || tar xf $G.tar.xz
(cd $G && ./contrib/download_prerequisites)
mkdir -p build-binutils build-gcc
[ -x "$PREFIX/bin/sh-elf-as" ] || (cd build-binutils && ../$B/configure --target=sh-elf --prefix="$PREFIX" --disable-nls --disable-werror --disable-gdb --disable-sim --enable-multilib MAKEINFO=true && make -j8 MAKEINFO=true && make install MAKEINFO=true)
export PATH="$PREFIX/bin:$PATH"
cd build-gcc
[ -f Makefile ] || ../$G/configure --target=sh-elf --prefix="$PREFIX" --disable-nls --enable-languages=c --without-headers --with-newlib --disable-libssp --disable-threads --disable-shared --with-multilib-list=m2a,m2a-nofpu --with-system-zlib MAKEINFO=true
make -j8 all-gcc MAKEINFO=true
make install-gcc MAKEINFO=true
# Expected to fail in the m2a-nofpu multilib (see above); the default and m2a ones are built first.
make -j8 all-target-libgcc MAKEINFO=true || echo "sh_elf_toolchain: libgcc multilib build stopped (m2a-nofpu, expected)"
LIBDIR="$PREFIX/lib/gcc/sh-elf/${G#gcc-}"
for multi in . m2a; do
  lib="sh-elf/$multi/libgcc/libgcc.a"
  [ "$multi" = . ] && lib="sh-elf/libgcc/libgcc.a"
  [ -f "$lib" ] || { echo "sh_elf_toolchain: $lib was not built" >&2; exit 1; }
  install -d "$LIBDIR/$multi"
  install -m 644 "$lib" "$LIBDIR/$multi/libgcc.a"
done
echo BUILD-OK
"$PREFIX/bin/sh-elf-gcc" --version | head -1
"$PREFIX/bin/sh-elf-gcc" -m2a-nofpu -mb -print-libgcc-file-name
