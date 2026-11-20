/* SPDX-License-Identifier: GPL-2.0-or-later */
#include <string.h>

void *memcpy(void *dst, const void *src, size_t size)
{
    unsigned char *out = dst;
    const unsigned char *in = src;
    for (size_t i = 0; i < size; ++i)
        out[i] = in[i];
    return dst;
}

void *memmove(void *dst, const void *src, size_t size)
{
    unsigned char *out = dst;
    const unsigned char *in = src;
    if ((size_t)out <= (size_t)in)
        return memcpy(dst, src, size);
    while (size) {
        --size;
        out[size] = in[size];
    }
    return dst;
}

void *memset(void *dst, int value, size_t size)
{
    unsigned char *out = dst;
    for (size_t i = 0; i < size; ++i)
        out[i] = (unsigned char)value;
    return dst;
}

int memcmp(const void *a, const void *b, size_t size)
{
    const unsigned char *left = a, *right = b;
    for (size_t i = 0; i < size; ++i) {
        if (left[i] != right[i])
            return left[i] - right[i];
    }
    return 0;
}
