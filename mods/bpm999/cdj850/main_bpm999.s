! SPDX-License-Identifier: GPL-2.0-or-later
!
! bpm999 hook for CDJ-850 MAIN 1.13 (SH-2A, big-endian). Assembled form:
! main_bpm999.bin (36 bytes). The sh4 binutils are not on the build Mac, so the
! .bin was hand-encoded from this source (encodings in the trailing comments).
!
! Entered from the trampoline after the hooked span 0x0C0642AA..0x0C0642B8 (the
! tail of display_stage_bpm) with r4 = the saved r0..r7. The routine clamps the
! BPM word the TX builder will copy from, the staging word at 0x0C9D0AE0 (page
! 0x01 bytes 8..9): a value above 999 becomes 999. 0xFFFF (blank) is kept.
! It clobbers r0..r2 only; the trampoline restores the saved registers.

	.text
	.align	2
bpm999:
	mov.l	Lbpm,r1			! D105  r1 = 0x0C9D0AE0
	mov.w	@r1,r0			! 6011  r0 = BPM word
	extu.w	r0,r0			! 600D  zero-extend
	mov.l	Lffff,r2		! D205
	cmp/eq	r2,r0			! 3020  blank?
	bt	done			! 8903
	mov.l	L999,r2			! D204
	cmp/hi	r2,r0			! 3026  r0 > 999 (unsigned)?
	bf	done			! 8B00
	mov.w	r2,@r1			! 2121  store 999
done:
	rts				! 000B
	nop				! 0009
	.align	2
Lbpm:	.long	0x0C9D0AE0
Lffff:	.long	0x0000FFFF
L999:	.long	0x000003E7
