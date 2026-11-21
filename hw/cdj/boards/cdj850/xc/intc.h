/* SPDX-License-Identifier: GPL-2.0-or-later */
/* CDJ-850 interrupt controller, external IRQ0-7 part (xc/intc.c). */
#ifndef CDJ850_INTC_H
#define CDJ850_INTC_H
#include "cdj_common.h"

#define CDJ850_INTC_IPR08 0x404 /* 0xFFFE0C04 */
#define CDJ850_IPR08_CMT0_MASK 0x00f0u

void cdj850_intc_init(MemoryRegion *sysmem, SuperHCPU *cpu);

/* Drive the pin routed to IRQn (0-7). Edges are latched per ICR1. */
void cdj850_intc_set_pin(unsigned irq, bool level);
bool cdj850_intc_get_pin(unsigned irq);
/* A 16-bit INTC register as last written (e.g. IPR08 at +0x404). */
uint16_t cdj850_intc_reg16(unsigned off);
#endif
