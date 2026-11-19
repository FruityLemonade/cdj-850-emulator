# SPDX-License-Identifier: GPL-2.0-or-later
# The Pioneer CDJ-850 board. scripts/build/install_machine.sh copies this tree to
# qemu-src/hw/sh4/cdj/; hw/sh4/meson.build only says subdir('cdj').

sh4_ss.add(declare_dependency(include_directories: include_directories('.', 'common')))

subdir('common')
subdir('boards/cdj850')
