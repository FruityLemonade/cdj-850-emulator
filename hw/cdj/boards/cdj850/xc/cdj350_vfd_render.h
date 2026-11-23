/* SPDX-License-Identifier: GPL-2.0-or-later */
#ifndef CDJ350_VFD_RENDER_H
#define CDJ350_VFD_RENDER_H

#include "cdj350_display.h"
#include "vfd_render.h"

#define CDJ350_FL_WIDTH 960
#define CDJ350_FL_HEIGHT 360
#define CDJ350_FL_BG  0xff171719u
#define CDJ350_FL_OFF 0xff222224u
#define CDJ350_FL_WHITE 0xfff5f4eau
#define CDJ350_FL_RED 0xfff44536u

static inline void cdj350_fl_digit(FlCanvas *canvas, int x, int y, int w,
                                   int h, uint8_t segments)
{
    /* A, B, C, D, E, F, G in the CDJ-350 wire order. */
    static const uint8_t wire_bits[] = { 1, 2, 16, 64, 32, 4, 8 };
    uint8_t bits = 0;

    for (unsigned i = 0; i < sizeof(wire_bits); i++) {
        if (segments & wire_bits[i]) {
            bits |= 1u << i;
        }
    }
    fl_segments(canvas, x, y, w, h, bits, CDJ350_FL_WHITE, CDJ350_FL_OFF);
}

static inline uint32_t cdj350_fl_light(bool on)
{
    return on ? CDJ350_FL_WHITE : CDJ350_FL_OFF;
}

static inline void cdj350_fl_text(FlCanvas *canvas, const char rows[2][17])
{
    for (unsigned r = 0; r < 2; r++) {
        for (unsigned col = 0; col < 16; col++) {
            unsigned char ch = rows[r][col] ? rows[r][col] : ' ';

            for (int y = 0; y < 7; y++) {
                uint8_t bits = fl_glyph(ch, y);
                if (ch == '<' || ch == '>') {
                    int bit = y < 4 ? y : 6 - y;
                    bits = 1u << (ch == '>' ? 4 - bit : bit);
                }
                for (int x = 0; x < 5; x++) {
                    fl_rect(canvas, 213 + col * 45 + x * 7,
                            222 + r * 65 + y * 7, 5, 5,
                            cdj350_fl_light(bits & (16u >> x)));
                }
            }
        }
    }
}

static inline void cdj350_fl_playing(FlCanvas *canvas, const uint8_t *status)
{
    unsigned bar = (unsigned)(status[2] & 0x3f) << 24 |
                   (unsigned)status[3] << 16 |
                   (unsigned)status[4] << 8 | status[5];
    uint32_t outline = status[2] & 0xc0 || bar ? CDJ350_FL_RED : CDJ350_FL_OFF;

    fl_rect(canvas, 213, 151, 3, 27, outline);
    fl_rect(canvas, 588, 151, 3, 27, outline);
    for (unsigned i = 0; i < 30; i++) {
        int x = 220 + i * 12;

        fl_rect(canvas, x, 151, 8, 20, bar & (1u << (29 - i)) ?
                CDJ350_FL_RED : CDJ350_FL_OFF);
        fl_rect(canvas, x, 175, 9, 3, outline);
    }
}

static inline void cdj350_fl_tempo(FlCanvas *canvas, const uint8_t *tempo,
                                   const uint8_t *bpm, uint8_t range)
{
    const char *range_label = "";
    uint8_t pitch_digits[] = {tempo[2], bpm[2], bpm[4]};
    uint8_t bpm_digits[] = {tempo[1], bpm[1], bpm[3], bpm[5]};
    bool show_bpm = bpm[0] & 0x08;
    uint32_t bpm_color = cdj350_fl_light(show_bpm);

    fl_label_fit(canvas, 690, 22, "TEMPO %", 157, 15,
                 cdj350_fl_light(tempo[0] & 0x10));
    if (tempo[0] & 2) {
        fl_rect(canvas, 674, 70, 14, 3, CDJ350_FL_WHITE);
        if (tempo[0] & 1) {
            fl_rect(canvas, 679, 64, 3, 15, CDJ350_FL_WHITE);
        }
    }
    if (tempo[0] & 0x80) {
        cdj350_fl_digit(canvas, 694, 46, 26, 53, 0x12);
    }
    for (unsigned i = 0; i < 3; i++) {
        cdj350_fl_digit(canvas, 723 + i * 39, 46, 30, 53, pitch_digits[i]);
    }
    fl_rect(canvas, bpm[0] & 0x20 ? 756 : 795, 93, 5, 5,
            cdj350_fl_light(bpm[0] & 0x30));

    fl_box(canvas, 670, 111, 169, 72, "", bpm_color);
    for (unsigned i = 0; i < 4; i++) {
        cdj350_fl_digit(canvas, 681 + i * 37, 121, 31, 51,
                        show_bpm ? bpm_digits[i] : 0);
    }
    fl_rect(canvas, 787, 166, 4, 5, bpm_color);
    fl_label_fit(canvas, 848, 165, "BPM", 61, 15, bpm_color);

    if (range & 0x10) {
        switch (range & 0xe0) {
        case 0x40: range_label = "\xb1" "6"; break;
        case 0x20: range_label = "\xb1" "10"; break;
        case 0x80: range_label = "\xb1" "16"; break;
        }
    } else if (range & 0x04) {
        range_label = "WIDE";
    }
    uint32_t range_color = *range_label ? CDJ350_FL_RED : CDJ350_FL_OFF;
    fl_box(canvas, 851, 42, 75, 107, "", range_color);
    fl_label_fit(canvas, 862, 117, range_label, 53, 19, range_color);
}

static inline void cdj350_fl_render(FlCanvas *canvas, const Cdj350Display *s)
{
    const uint8_t *time = s->segments[2];
    const uint8_t *tempo = s->segments[3];
    const uint8_t *bpm = s->segments[4];
    const uint8_t *status = s->segments[5];
    const char *const units[] = { "M", "S", "F" };
    bool has_time = (time[0] | time[1] | time[2] |
                     time[3] | time[4] | time[5]) != 0;

    fl_rect(canvas, 0, 0, CDJ350_FL_WIDTH, CDJ350_FL_HEIGHT, CDJ350_FL_BG);

    /* RRV4069 p.10: track at left, time across the top, text below the bar. */
    cdj350_fl_digit(canvas, 48, 32, 49, 86, s->segments[0][5]);
    cdj350_fl_digit(canvas, 105, 32, 49, 86, s->segments[1][5]);
    fl_label_fit(canvas, 177, 26, "REMAIN", 95, 14,
                 cdj350_fl_light(status[0] & 1));
    for (unsigned pair = 0; pair < 3; pair++) {
        int x = 213 + pair * 126;

        fl_label_fit(canvas, x + 89, 26, units[pair], 12, 14,
                     cdj350_fl_light(has_time));
        cdj350_fl_digit(canvas, x, 47, 44, 77, time[pair * 2]);
        cdj350_fl_digit(canvas, x + 49, 47, 44, 77, time[pair * 2 + 1]);
        if (pair == 0) {
            fl_rect(canvas, x + 106, 67, 6, 6, cdj350_fl_light(has_time));
            fl_rect(canvas, x + 106, 94, 6, 6, cdj350_fl_light(has_time));
        }
    }

    fl_box(canvas, 26, 145, 145, 33, "A.CUE",
           cdj350_fl_light(bpm[0] & 0x01));
    fl_box(canvas, 26, 216, 145, 39, "INFO", CDJ350_FL_OFF);
    fl_box(canvas, 26, 281, 145, 39, "BROWSE",
           cdj350_fl_light(tempo[0] & 0x04));
    fl_box(canvas, 595, 74, 66, 34, "LOCK", CDJ350_FL_OFF);

    cdj350_fl_playing(canvas, status);
    cdj350_fl_tempo(canvas, tempo, bpm, status[1]);
    cdj350_fl_text(canvas, s->rows);
}

#endif
