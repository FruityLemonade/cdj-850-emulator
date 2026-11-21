#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-2.0-or-later
#
# Build the SH7263 cache-model TCG plugin (cdj850_cache.c) against the QEMU
# tree's plugin header. The plugin is a separate shared library: QEMU itself
# is not rebuilt, and any qemu-system-sh4eb built with plugin support (the
# default) loads it with -plugin.
#
#   usage: scripts/cache/build_cache_plugin.sh [output]
#          (default output: $QEMU_EB_BUILD or ~/qemu-build-eb, file
#           libcdj850_cache.dylib on macOS, .so elsewhere, .dll on Windows)
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$HERE/../cdj_paths.sh"
SRC="$CDJ_ROOT/qemu-src"
HDR="$SRC/include/plugins/qemu-plugin.h"
[ -f "$HDR" ] || { echo "no QEMU plugin header at $HDR (run ./build.sh source first)" >&2; exit 1; }

case "$(uname -s)" in
    Darwin) EXT=dylib; LINK=(-bundle "-Wl,-undefined,dynamic_lookup") ;;
    MINGW* | MSYS* | CYGWIN*) EXT=dll; LINK=(-shared) ;;
    *) EXT=so; LINK=(-shared -fPIC) ;;
esac
BUILD="${QEMU_EB_BUILD:-$HOME/qemu-build-eb}"
OUT="${1:-$BUILD/libcdj850_cache.$EXT}"
mkdir -p "$(dirname "$OUT")"

# shellcheck disable=SC2046
${CC:-cc} -O2 -g -std=gnu11 -Wall -Wextra -Wno-unused-parameter -fPIC \
    -I"$SRC/include/plugins" $(pkg-config --cflags glib-2.0) \
    "$HERE/cdj850_cache.c" -o "$OUT" "${LINK[@]}" $(pkg-config --libs glib-2.0)
echo "$OUT"
