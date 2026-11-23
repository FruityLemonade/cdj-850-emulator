/* SPDX-License-Identifier: GPL-2.0-or-later */
#ifndef CDJ350_DISPLAY_H
#define CDJ350_DISPLAY_H

#include <stdbool.h>
#include <stdint.h>
#include <string.h>
#include "panel_wire.h"

typedef struct Cdj350Display {
    char rows[2][17];
    uint8_t segments[6][6];
} Cdj350Display;

/* Return the indicator page number, or zero for a non-indicator pair. */
static inline unsigned cdj350_display_page(const uint8_t tx[][PANEL_FRAME_LEN])
{
    unsigned first = tx[0][0];

    if ((first & 0x0f) == 1 && first >= 0x11 && first <= 0x61 &&
        tx[1][0] == first + 1 && !tx[1][13] && !tx[1][14] && !tx[1][15]) {
        return first >> 4;
    }
    return 0;
}

static inline bool cdj350_indicator_valid(const uint8_t tx[][PANEL_FRAME_LEN])
{
    uint8_t sum = 0;

    /* MAIN sums slot bytes 0..27 modulo 256 and stores it in byte 28. */
    for (unsigned i = 0; i < PANEL_FRAME_LEN; i++) {
        sum += tx[0][i];
    }
    for (unsigned i = 0; i < 12; i++) {
        sum += tx[1][i];
    }
    return sum == tx[1][12];
}

static inline bool cdj350_display_feed(Cdj350Display *s,
                                      const uint8_t tx[][PANEL_FRAME_LEN])
{
    unsigned page = cdj350_display_page(tx);
    char rows[2][17] = { 0 };

    if (page) {
        /* The decoded segment groups are in frame 2 bytes 6..11. */
        uint8_t *segments = s->segments[page - 1];
        const uint8_t *payload = &tx[1][6];

        if (!cdj350_indicator_valid(tx) ||
            !memcmp(segments, payload, sizeof(s->segments[0]))) {
            return false;
        }
        memcpy(segments, payload, sizeof(s->segments[0]));
        return true;
    }

    for (unsigned row = 0; row < 2; row++) {
        for (unsigned col = 0; col < PANEL_FRAME_LEN; col++) {
            uint8_t c = tx[row][col];

            if (c == 0 || c == 0x0a) {
                rows[row][col] = ' ';
            } else if (c == 0x81 || c == 0x82) {
                rows[row][col] = c == 0x81 ? '>' : '<';
            } else if (c >= 0x20 && c < 0x7f) {
                rows[row][col] = c;
            } else {
                /* Out-of-step transactions can contain half an indicator. */
                return false;
            }
        }
    }
    if (!memcmp(s->rows, rows, sizeof(rows))) {
        return false;
    }
    memcpy(s->rows, rows, sizeof(rows));
    return true;
}

#endif
