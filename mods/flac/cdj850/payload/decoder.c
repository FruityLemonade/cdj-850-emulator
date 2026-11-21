/* SPDX-License-Identifier: GPL-2.0-or-later */
#include "decoder.h"

#define DR_FLAC_IMPLEMENTATION
#define DR_FLAC_NO_STDIO
#define DR_FLAC_NO_OGG
#define DR_FLAC_NO_SIMD
#define DRFLAC_ASSERT(expression) ((void)0)
#define DRFLAC_MALLOC(size) ((void)size, (void *)0)
#define DRFLAC_REALLOC(ptr, size) ((void)ptr, (void)size, (void *)0)
#define DRFLAC_FREE(ptr) ((void)ptr)
/* Only the calls below are used: with the API static, the rest of dr_flac
 * (and its 16 KB-frame read-all helpers) is left out of the payload. */
#define DRFLAC_API static
#pragma GCC diagnostic ignored "-Wunused-function"
#include "vendor/dr_flac.h"

enum { ARENA_BYTES = 256 * 1024, MAX_BLOCK_FRAMES = 4608 };

static union {
    uint64_t alignment;
    uint8_t bytes[ARENA_BYTES];
} arena;
static size_t used;
static FlacSource input;
static uint32_t offset;
static drflac *decoder;
static uint32_t remaining;
static int16_t samples[FLAC_SECTOR_FRAMES * 2];

static void *allocate(size_t size, void *context)
{
    (void)context;
    if (size > ARENA_BYTES - used)
        return NULL;
    size_t aligned = (size + 7u) & ~(size_t)7u;
    if (aligned > ARENA_BYTES - used)
        return NULL;
    void *result = arena.bytes + used;
    used += aligned;
    return result;
}

static void release(void *address, void *context)
{
    (void)address;
    (void)context;
}

static size_t read_bytes(void *context, void *dst, size_t size)
{
    (void)context;
    if (size > input.size - offset)
        size = input.size - offset;
    size_t actual = input.read_at(input.context, offset, dst, size);
    if (actual > size)
        return 0;
    offset += (uint32_t)actual;
    return actual;
}

static drflac_bool32 seek_bytes(void *context, int distance, drflac_seek_origin origin)
{
    (void)context;
    uint32_t base = 0;
    switch (origin) {
    case DRFLAC_SEEK_SET: break;
    case DRFLAC_SEEK_CUR: base = offset; break;
    case DRFLAC_SEEK_END: base = input.size; break;
    default: return DRFLAC_FALSE;
    }
    if (distance < 0) {
        uint32_t back = 0u - (uint32_t)distance;
        if (back > base)
            return DRFLAC_FALSE;
        offset = base - back;
    } else {
        if ((uint32_t)distance > input.size - base)
            return DRFLAC_FALSE;
        offset = base + (uint32_t)distance;
    }
    return DRFLAC_TRUE;
}

static drflac_bool32 tell_bytes(void *context, drflac_int64 *position)
{
    (void)context;
    *position = offset;
    return DRFLAC_TRUE;
}

void flac_close(void)
{
    if (decoder != NULL)
        drflac_close(decoder);
    decoder = NULL;
    used = 0;
    remaining = 0;
}

uint32_t flac_open(FlacSource source)
{
    uint8_t info[26];
    flac_close();
    if (!source.read_at || source.size < 42 || source.size > INT32_MAX ||
        source.read_at(source.context, 0, info, sizeof(info)) != sizeof(info))
        return 0;

    /* Validate native STREAMINFO before allocating or parsing frame data. */
    unsigned max_block = (unsigned)info[10] << 8 | info[11];
    unsigned rate = (unsigned)info[18] << 12 | (unsigned)info[19] << 4 | info[20] >> 4;
    unsigned channels = ((info[20] >> 1) & 7u) + 1u;
    unsigned bits = ((info[20] & 1u) << 4 | info[21] >> 4) + 1u;
    if (memcmp(info, "fLaC", 4) || (info[4] & 0x7f) ||
        info[5] || info[6] || info[7] != 34 || max_block < 16 ||
        max_block > MAX_BLOCK_FRAMES || rate != 44100 || channels != 2 || bits != 16 ||
        (info[21] & 0xf))
        return 0;

    input = source;
    offset = 0;
    const drflac_allocation_callbacks memory = {NULL, allocate, NULL, release};
    decoder = drflac_open(read_bytes, seek_bytes, tell_bytes, NULL, &memory);
    if (!decoder || !decoder->totalPCMFrameCount || decoder->totalPCMFrameCount > UINT32_MAX) {
        flac_close();
        return 0;
    }
    remaining = (uint32_t)decoder->totalPCMFrameCount;
    return remaining;
}

int flac_seek(uint32_t frame)
{
    if (!decoder || frame >= decoder->totalPCMFrameCount)
        return -1;
    if (decoder->totalPCMFrameCount - remaining == frame)
        return 0;
    if (!drflac_seek_to_pcm_frame(decoder, frame)) {
        flac_close();
        return -1;
    }
    remaining = (uint32_t)decoder->totalPCMFrameCount - frame;
    return 0;
}

int flac_read_sector(uint8_t *pcm)
{
    if (!decoder)
        return -1;
    unsigned take = remaining < FLAC_SECTOR_FRAMES ? remaining : FLAC_SECTOR_FRAMES;
    if (!take)
        return 0;
    if (drflac_read_pcm_frames_s16(decoder, take, samples) != take) {
        flac_close();
        return -1;
    }
    for (unsigned i = 0; i < take * 2; ++i) {
        uint16_t value = (uint16_t)samples[i];
        pcm[i * 2] = (uint8_t)(value >> 8);
        pcm[i * 2 + 1] = (uint8_t)value;
    }
    memset(pcm + take * 4, 0, FLAC_SECTOR_BYTES - take * 4);
    remaining -= take;
    return (int)take;
}
