/* SPDX-License-Identifier: GPL-2.0-or-later */
/*
 * payload_demo: the reference for payload mods (see ../patch.py).
 *
 * payload_demo_count  hook routine (sigpatch.Mod, routine='payload:...') on
 *                     the tail of display_stage_bpm (0x0C0642AA, the span
 *                     bpm999 and bpm_probe hook too); counts its runs.
 * payload_demo_tick   target of a pool redirect: the word 0x0C0B9310, which
 *                     the 1 ms tick routine 0x0C0B92CC loads at 0x0C0B92EA
 *                     to call 0x0C0EF8C8, the power/standby part of the tick
 *                     (it drives the auto-standby seconds counter 0x0C1E10AE).
 *                     It counts the call and tail-jumps to 0x0C0EF8C8, so the
 *                     seconds counter still runs. 0x0C0B92CC is reached from
 *                     the kernel's timer path (pool 0x0C0C6B6C; whether at
 *                     interrupt level was not traced): treat it as interrupt
 *                     context, keep such a routine short, no RTOS calls.
 */
#include "c850.h"

volatile u32 payload_demo_bpm_calls;     /* .bss: zero after the loader's copy */
volatile u32 payload_demo_tick_calls;
volatile u32 payload_demo_last_r15;

void payload_demo_count(u32 *regs, u32 *sp)
{
    (void)regs;
    payload_demo_bpm_calls++;
    payload_demo_last_r15 = (u32)sp;
}

void payload_demo_tick(void)
{
    payload_demo_tick_calls++;
    c850_power_tick_1ms();                  /* compiled as a tail jump */
}
