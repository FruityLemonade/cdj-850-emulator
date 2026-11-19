! SPDX-License-Identifier: GPL-2.0-or-later
!
! Loader stub for CDJ-850 MAIN 1.13 (SH7263, SH-2A, big-endian).
!
! Not assembled by patch_main.py --assemble: c850loader.py writes the stub
! itself (STUB_CODE), since its pool depends on the blob's length. This file
! is the source those words were hand-assembled from; the column after each
! "!=" is the halfword c850loader.STUB_CODE holds at that offset, and
! tests/test_cdj850_loader.py checks the two agree.
!
! Placed at 0x0C1E2800, in the statics MAIN clears at start-up, and reached
! from MAIN's entry through its first literal (0x0C000010), which used to
! hold the clear routine 0x0C0B92AC:
!
!   0C000000  mov.l @(0x0C000010),r3 ; jsr @r3 ; mov.l @(0x0C000014),r3
!
! So on entry r3 already holds the entry's second callee (0x0C0B8FF4) and PR
! points back into the entry: the stub keeps both, and chains to the clear
! with jmp, so the clear's rts/n returns to the entry as before.
!
! Copies WORDS words from SRC (the blob behind the stub in the image) to
! 0x0DE00000 through the uncached mirror, writes the top guard word, and
! jumps to the clear, which then wipes the stub and the blob's source.

	.text
	.align	2
loader:
	mov.l	src,r1		!= +00 d106  blob source in the image
	mov.l	dst,r2		!= +02 d207  0x2DE00000 (0x0DE00000 uncached)
	mov.l	words,r4	!= +04 d407  words to copy, header included
copy:
	mov.l	@r1+,r0		!= +06 6016
	mov.l	r0,@r2		!= +08 2202
	dt	r4		!= +0a 4410
	bf/s	copy		!= +0c 8ffb
	add	#4,r2		!= +0e 7204  delay slot
	mov.l	guard_at,r5	!= +10 d505  0x2DF6FFFC (0x0DF6FFFC uncached)
	mov.l	guard,r6	!= +12 d606  0x3E4C4452 '>LDR'
	mov.l	r6,@r5		!= +14 2562
	mov.l	clear,r0	!= +16 d006  0x0C0B92AC
	jmp	@r0		!= +18 402b
	nop			!= +1a 0009

	.align	2
src:	.long	0x0C1E2840	! filled in by c850loader.stub()
dst:	.long	0x2DE00000
words:	.long	0		! filled in by c850loader.stub()
guard_at: .long	0x2DF6FFFC
guard:	.long	0x3E4C4452
clear:	.long	0x0C0B92AC
