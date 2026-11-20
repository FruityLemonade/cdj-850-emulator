/* SPDX-License-Identifier: GPL-2.0-or-later */
/* The shared state-4 tick c850_loop_tick and no-op hooks, all weak (c850.h).
 * Kept apart from quant_loops.c so that its strong hook replaces the no-op. */
#include "c850.h"

C850_LOOP_TICK_DEFINE
