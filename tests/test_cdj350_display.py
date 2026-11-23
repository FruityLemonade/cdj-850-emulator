# SPDX-License-Identifier: GPL-2.0-or-later
"""Exercise the CDJ-350's SCIF1 display decoder and rasterizer together."""

import os
import shutil
import subprocess

import pytest

from helpers import path


HARNESS = r"""
#include <assert.h>
#include "cdj350_vfd_render.h"

static uint32_t pixels[CDJ350_FL_WIDTH * CDJ350_FL_HEIGHT];
static FlCanvas canvas = {
    (uint8_t *)pixels, CDJ350_FL_WIDTH, CDJ350_FL_HEIGHT, CDJ350_FL_WIDTH * 4
};

static uint32_t pixel(int x, int y)
{
    return pixels[y * CDJ350_FL_WIDTH + x];
}

static void feed_page(Cdj350Display *s, unsigned page, const uint8_t data[6])
{
    uint8_t tx[2][16] = {{(page << 4) | 1}, {(page << 4) | 2}};

    memcpy(&tx[1][6], data, 6);
    tx[1][12] = tx[0][0] + tx[1][0];
    for (unsigned i = 0; i < 6; i++) {
        tx[1][12] += data[i];
    }
    assert(cdj350_display_feed(s, tx));
    assert(!cdj350_display_feed(s, tx));

    /* A mismatched checksum must leave the previous page intact. */
    tx[1][9] ^= 1;
    assert(!cdj350_display_feed(s, tx));
    assert(!memcmp(s->segments[page - 1], data, 6));
}

int main(void)
{
    assert(!strcmp(cdj350_fl_range_label(0x5b), "\xb1" "10"));
    assert(!strcmp(cdj350_fl_range_label(0x3b), "\xb1" "16"));
    assert(!strcmp(cdj350_fl_range_label(0x0f), "WIDE"));
    assert(!strcmp(cdj350_fl_range_label(0x9b), "\xb1" "6"));

    Cdj350Display state = {0};
    const uint8_t text[2][16] = {
        "ABCDEFGHIJKLMNOP",
        {0x81, 'T', 'R', 'A', 'C', 'K', 0x82, 0x0a, 0, 'Z'},
    };

    assert(cdj350_display_feed(&state, text));
    assert(!strcmp(state.rows[0], "ABCDEFGHIJKLMNOP"));
    assert(!strcmp(state.rows[1], ">TRACK<  Z      "));
    assert(!cdj350_display_feed(&state, text));

    const uint8_t printable_headers[2][16] = {"A row of text   ", "B row of text   "};
    assert(cdj350_display_feed(&state, printable_headers));
    assert(!strcmp(state.rows[0], "A row of text   "));
    assert(cdj350_display_feed(&state, text));

    const uint8_t mixed[2][16] = {{0x11, 0x01}, "                "};
    assert(!cdj350_display_feed(&state, mixed));
    assert(!strcmp(state.rows[0], "ABCDEFGHIJKLMNOP"));

    feed_page(&state, 1, (const uint8_t[]){0, 0, 0, 0, 0, 0x12});
    feed_page(&state, 2, (const uint8_t[]){0, 0, 0, 0, 0, 0x13});
    feed_page(&state, 3, (const uint8_t[]){0x77, 0x6b, 0x1e, 0x7f, 0x5b, 0x5f});
    feed_page(&state, 4, (const uint8_t[]){0x13, 0x12, 0x77, 0, 0x30, 0});
    feed_page(&state, 5, (const uint8_t[]){0x29, 0x6b, 0x77, 0x5d, 0x6b, 0x77});
    feed_page(&state, 6, (const uint8_t[]){1, 0x50, 0xc0, 0, 3, 0xff});
    cdj350_fl_render(&canvas, &state);

    assert(pixel(54, 61) == CDJ350_FL_OFF);       /* 1 has no left segment */
    assert(pixel(219, 70) == CDJ350_FL_WHITE);    /* minutes tens, upper left */
    assert(pixel(214, 175) == CDJ350_FL_RED);     /* playing-address bracket */
    assert(pixel(220, 154) == CDJ350_FL_OFF);     /* first bar segment */
    assert(pixel(568, 154) == CDJ350_FL_RED);     /* last bar segment */
    assert(pixel(675, 71) == CDJ350_FL_WHITE);    /* tempo sign */
    assert(pixel(756, 93) == CDJ350_FL_WHITE);    /* tempo decimal */
    assert(pixel(787, 166) == CDJ350_FL_WHITE);   /* BPM decimal */
    assert(pixel(875, 42) == CDJ350_FL_RED);      /* range frame */
    assert(pixel(45, 145) == CDJ350_FL_WHITE);    /* auto cue */
    assert(pixel(220, 222) == CDJ350_FL_WHITE);   /* row 1, A */
    assert(pixel(213, 287) == CDJ350_FL_WHITE);   /* row 2, > */

    assert(pixel(45, 216) == CDJ350_FL_OFF);    /* INFO dark before page 5 changes */
    assert(pixel(610, 74) == CDJ350_FL_OFF);    /* LOCK dark before page 4 changes */

    feed_page(&state, 4, (const uint8_t[]){0x1b, 0x12, 0x77, 0xf2, 0x20, 0});
    feed_page(&state, 5, (const uint8_t[]){0xc8, 0x6b, 0x77, 0x7f, 0x77, 0x12});
    cdj350_fl_render(&canvas, &state);
    assert(pixel(45, 216) == CDJ350_FL_WHITE);  /* INFO follows page 5 bit 7 */
    assert(pixel(610, 74) == CDJ350_FL_RED);    /* LOCK follows page 4 bit 3 */

    feed_page(&state, 4, (const uint8_t[]){0x17, 0x12, 0x77, 0xf2, 0x20, 0});
    feed_page(&state, 5, (const uint8_t[]){0x48, 0x6b, 0x77, 0x7f, 0x77, 0x12});
    cdj350_fl_render(&canvas, &state);
    assert(pixel(45, 216) == CDJ350_FL_OFF);    /* INFO clears in BROWSE */
    assert(pixel(45, 281) == CDJ350_FL_WHITE);
    assert(pixel(610, 74) == CDJ350_FL_OFF);    /* LOCK can blink off */

    cdj350_fl_render(&canvas, &(Cdj350Display){0});
    assert(pixel(219, 70) == CDJ350_FL_OFF);
    assert(pixel(568, 154) == CDJ350_FL_OFF);
    assert(pixel(45, 145) == CDJ350_FL_OFF);
    return 0;
}
"""


def test_cdj350_display(tmp_path):
    cc = shutil.which(os.environ.get("CC", "cc"))
    if not cc:
        pytest.skip("requires a C compiler")
    src = tmp_path / "display.c"
    exe = tmp_path / "display"
    src.write_text(HARNESS)
    subprocess.run([cc, "-std=c11", "-I", path("hw", "cdj", "boards", "cdj850", "xc"),
                    str(src), "-o", str(exe)], check=True)
    subprocess.run([str(exe)], check=True, cwd=tmp_path)
