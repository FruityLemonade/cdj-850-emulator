# SPDX-License-Identifier: GPL-2.0-or-later
# Shared by the CDJ MAIN board; the board's Kconfig entry selects CDJ_COMMON.
sh4_ss.add(when: 'CONFIG_CDJ_COMMON', if_true: files(
  'cdj_env.c', 'sh4_board.c', 'sh4_periph.c',
  'cdj_pnl_stateout.c', 'cdj_gui_keys.c', 'cdj_host_audio.c', 'cdj_loop_mute.c',
  'usb_r8a66597.c', 'usbf_r8a66597.c', 'usbf_host.c',
))
