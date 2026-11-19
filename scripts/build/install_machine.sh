#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-2.0-or-later
#
# Install the CDJ-2000NXS2 machine model into a QEMU source tree.
#
#   usage: ./install_machine.sh [path-to-qemu-tree]     (default: ~/qemu)
#
# Idempotent. Everything goes into hw/sh4/cdj/, which this script owns (stale
# files there are removed); hw/sh4/meson.build gains one line, subdir('cdj').
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
Q="${1:-$HOME/qemu}"
D="$Q/hw/sh4/cdj"

if [ ! -d "$Q/hw/sh4" ]; then
    echo "error: '$Q' does not look like a QEMU tree (no hw/sh4)." >&2
    echo "clone it first:" >&2
    echo "  git clone --depth 1 --branch v11.1.2 https://gitlab.com/qemu-project/qemu.git ~/qemu" >&2
    exit 1
fi

# Copy one file, dropping CRLF: a Windows checkout must build the same bytes.
# Written only when the content changed: a fresh mtime on a meson.build forces
# a reconfigure, and on a source a recompile. Every path written is recorded,
# so anything else left in hw/sh4/cdj/ can be removed as stale at the end.
INSTALLED=()
put_file() {   # <temp file with the final content> <tree file>
    mkdir -p "$(dirname "$2")"
    if cmp -s "$1" "$2"; then rm -f "$1"; else mv "$1" "$2"; fi
    INSTALLED+=("$2")
}
install_src() {   # <project file> <tree file>
    local t
    t="$(mktemp)"
    sed 's/\r$//' "$1" > "$t"
    put_file "$t" "$2"
}

# --- the old flat layout -----------------------------------------------------
# Removed so a stale hw/sh4/cdj2000nxs2.c cannot be mistaken for the built one.
rm -rf "$Q/hw/sh4/c6x"
rm -f "$Q/hw/sh4/cdj2000nxs2.c" "$Q/hw/sh4/sh7269gui.c" \
      "$Q/hw/sh4/cdj_panelkeys.h" "$Q/hw/sh4/minimp3.h"

# --- board sources ----------------------------------------------------------
mkdir -p "$D"
# The whole hw/cdj/ tree is installed; hw/cdj/meson.build lists what is built.
while IFS= read -r f; do
    install_src "$f" "$D/${f#$HERE/../../hw/cdj/}"
done < <(find "$HERE/../../hw/cdj" -type f \( -name '*.[ch]' -o -name meson.build \) -print | sort)
echo "installed hw/sh4/cdj/ (board sources)"

# Anything in hw/sh4/cdj/ this run did not write is stale (a file deleted from
# the project, an old split output): remove it so it cannot be built or read.
# Not a pipe into grep -q: with pipefail, grep's early exit can kill printf
# with SIGPIPE, and a file that WAS installed gets removed as stale at random.
INSTALLED_LIST="$(mktemp)"
printf '%s\n' "${INSTALLED[@]}" > "$INSTALLED_LIST"
while IFS= read -r f; do
    grep -qxF -- "$f" "$INSTALLED_LIST" || { rm -f "$f"; echo "removed stale ${f#$Q/}"; }
done < <(find "$D" -type f)
rm -f "$INSTALLED_LIST"

# --- hw/sh4/meson.build -----------------------------------------------------
# Strip every earlier form (the two flat board lines, the marked c6x block and
# its unmarked first cut) and leave one subdir('cdj'). Rewritten only on a
# real change: touching meson.build forces a reconfigure.
SH4_MESON="$(mktemp)"
sed -e "/^sh4_ss.add(when: 'CONFIG_CDJ2000NXS2', if_true: files('cdj2000nxs2.c'))$/d" \
    -e "/^sh4_ss.add(when: 'CONFIG_SH7269GUI', if_true: files('sh7269gui.c'))$/d" \
    -e '/^# cdjc6x begin/,/^# cdjc6x end/d' \
    -e '/^cdjc6x = static_library/,/link_whole: cdjc6x))/d' \
    -e "/^subdir('cdj')$/d" \
    "$Q/hw/sh4/meson.build" > "$SH4_MESON"
echo "subdir('cdj')" >> "$SH4_MESON"
if cmp -s "$SH4_MESON" "$Q/hw/sh4/meson.build"; then
    echo "hw/sh4/meson.build unchanged"
else
    cat "$SH4_MESON" > "$Q/hw/sh4/meson.build"
    echo "wrote hw/sh4/meson.build (subdir('cdj'))"
fi
rm -f "$SH4_MESON"

# --- Kconfig --------------------------------------------------------------
# sh_serial.c is built by CONFIG_SH_SCI; there is no SH_SERIAL symbol.
# The same entries as patches/hw_sh4_Kconfig.patch, for a tree the patches were
# not applied to. A tree with the older single-board entry needs the patches.
if grep -q '^config CDJ_COMMON' "$Q/hw/sh4/Kconfig"; then
    echo "Kconfig already patched"
elif grep -q '^config CDJ2000NXS2' "$Q/hw/sh4/Kconfig"; then
    echo "error: hw/sh4/Kconfig has the old single-board entry; run ./build.sh patches" >&2
    exit 1
else
    cat >> "$Q/hw/sh4/Kconfig" <<'EOF'

# What every CDJ MAIN board shares (hw/cdj/common/).
config CDJ_COMMON
    bool
    select SH_INTC
    select SH_SCI
    select SH_TIMER
    select UNIMP
    select PFLASH_CFI02
    select SD
    select USB

# Also builds the XDJ-1000 machines, whose 16550 UARTs are serial_mm_init().
config CDJ2000NXS2
    bool
    default y
    depends on SH4
    select CDJ_COMMON
    select SERIAL_MM

# The CDJ-2000 and CDJ-2000NXS (SH7763), machines cdj2000 and cdj2000nxs.
config CDJ2000
    bool
    default y
    depends on SH4
    select CDJ_COMMON

# The CDJ-850 (SH7263/SH-2A, big-endian), machine cdj850.
config CDJ850
    bool
    default y
    depends on SH4
    select CDJ_COMMON

config SH7269GUI
    bool
    default y
    depends on SH4
    select UNIMP
EOF
    echo "patched hw/sh4/Kconfig"
fi

echo
echo "next:"
echo "  cd $Q && ./configure --target-list=sh4-softmmu --disable-werror"
echo "  ninja -C build qemu-system-sh4"
