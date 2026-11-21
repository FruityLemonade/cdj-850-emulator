! SPDX-License-Identifier: GPL-2.0-or-later
!
! bpm_probe hook for CDJ-850 MAIN 1.13 (SH-2A, big-endian). Assembled form:
! main_bpm_probe.bin (96 bytes), hand-encoded from this source with mask 127
! (a frame step every 128 TX builds). The sh4 binutils are not on the build Mac.
!
! Entered from the trampoline after the hooked span 0x0C0642AA..0x0C0642B8
! (tail of display_stage_bpm), with
! r4 = the saved r0..r7. Writes the BPM box value 199 to 0x0C9D0AE0 and the frame
! value to 0x0C9D0ADE (page 0x01 byte 7) before the TX builder copies them.
! tick and frame are two longwords at 0x0DDF0C40 and 0x0DDF0C44: unused SDRAM
! (docs/cdj850-modding.md section 8, between the bootloader's dead LZSS ring and
! the loader region at 0x0DE00000). MAIN's own image is read-only at run time
! (pflash), so the counters cannot live in the hook blob.
! Clobbers r0..r3 only.

	.text
	.align	2
bpm_probe:
	mov.l	Ltick,r1		! tick++
	mov.l	@r1,r2
	add	#1,r2
	mov.l	r2,@r1
	mov	#127,r3
	tst	r3,r2			! T = (tick & 127) == 0
	bf	nostep
	mov.l	Lframe,r1		! frame: 75..99, step on tick & 127 == 0
	mov.l	@r1,r2
	mov	#75,r3
	cmp/ge	r3,r2
	bf	reset
	mov	#99,r3
	cmp/ge	r3,r2
	bt	reset
	add	#1,r2
	bra	store
	nop
reset:
	mov	#75,r2
store:
	mov.l	r2,@r1
nostep:
	mov.l	Lframe,r1		! any frame outside 75..99 (garbage, first call) -> 75
	mov.l	@r1,r2
	add	#-75,r2
	mov	#24,r3
	cmp/hi	r3,r2			! unsigned (frame-75) > 24
	bf	setbpm
	mov	#75,r2
	mov.l	r2,@r1
setbpm:
	mov.l	Lbpm,r1			! BPM box = 199
	mov.l	L199,r3
	mov.w	r3,@r1
	mov.l	Lframe,r1
	mov.l	@r1,r2
	mov.l	Lfbyte,r1		! frame digits byte (page 0x01 byte 7)
	mov.b	r2,@r1
	rts
	nop
	.align	2
Ltick:	.long	0x0DDF0C40
Lframe:	.long	0x0DDF0C44
Lbpm:	.long	0x0C9D0AE0
L199:	.long	199
Lfbyte:	.long	0x0C9D0ADE
