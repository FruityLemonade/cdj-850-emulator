/* SPDX-License-Identifier: GPL-2.0-or-later */
#ifndef CDJ850_FLAC_DECODER_H
#define CDJ850_FLAC_DECODER_H

#include <stddef.h>
#include <stdint.h>

enum { FLAC_SECTOR_FRAMES = 588, FLAC_SECTOR_BYTES = 2352 };

typedef struct {
    void *context;
    size_t (*read_at)(void *context, uint32_t offset, void *dst, size_t bytes);
    uint32_t size;
} FlacSource;

/* One decoder. Calls belong to the audio producer task. */
uint32_t flac_open(FlacSource source);
int flac_seek(uint32_t frame);
/* The next sector: 588 stereo frames as they sit in a 16-bit PCM WAV
 * (little-endian), zero-padded at the end of the track. */
int flac_read_sector(uint32_t *pcm);
void flac_close(void);

#endif
