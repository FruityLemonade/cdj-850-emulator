# SPDX-License-Identifier: GPL-2.0-or-later
"""Compare sequential and random reads of native FLAC against source PCM."""
from array import array
from pathlib import Path
import math
import random
import subprocess
import sys
import tempfile
import wave

def main():
    exe = Path(sys.argv[1]).absolute()
    rand = random.Random(850)
    contents = array("h")
    clip = lambda v: max(-32768, min(32767, int(v)))
    # ramp with a correlated, noisy right channel
    for i in range(44100 * 5 + 123):
        left = (i * 127) % 65000 - 32500
        contents.extend((left, left // 2 + rand.randrange(-500, 500)))
    # dense, loud material: high LPC orders and coefficient precision, so
    # dr_flac would take its 64-bit predictor
    for i in range(44100 * 3):
        t = i / 44100
        tone = sum(a * math.sin(2 * math.pi * f * t) for a, f in
                   ((9000, 55), (7000, 110.5), (5000, 331), (3000, 1210), (1500, 5003)))
        contents.extend((clip(tone + rand.gauss(0, 900)), clip(0.8 * tone + rand.gauss(0, 900))))
    # wasted bits (every sample a multiple of 8)
    wasted = range(len(contents) // 2, len(contents) // 2 + 44100)
    for i in range(44100):
        v = int(20000 * math.sin(i / 37.0)) & ~7
        contents.extend((v, (v // 3) & ~7))
    # identical, then opposite channels (left/side, right/side, mid/side)
    for i in range(44100):
        v = clip(25000 * math.sin(i / 11.0) + rand.randrange(-300, 300))
        contents.extend((v, v) if i < 22050 else (v, -v if v > -32768 else 32767))
    frames = len(contents) // 2
    # flac_read_sector returns the WAV's own (little-endian) data bytes
    if sys.byteorder == "big":
        contents.byteswap()
    expected = contents.tobytes()
    # The same material at 24 bits: random low bytes (zero where the wasted-bit
    # section is), so the decoder's top 16 bits must equal the 16-bit source.
    wide = bytearray()
    for i in range(frames * 2):
        low = 0 if i // 2 in wasted else rand.randrange(256)
        wide += bytes((low,)) + expected[i * 2:i * 2 + 2]
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        wav = root / "source.wav"
        flac = root / "source.flac"
        for rate, width in ((44100, 2), (48000, 2), (44100, 3), (48000, 3),
                            (32000, 2), (96000, 2), (96000, 3)):
            with wave.open(str(wav), "wb") as file:
                file.setparams((2, width, rate, 0, "NONE", ""))
                file.writeframes(expected if width == 2 else bytes(wide))
            for options in (["-0", "-S", "1s"], ["-8", "--no-seektable"],
                            ["-8", "--lax", "-l", "32", "-b", "4096", "-S", "2s"]):
                subprocess.run(["flac", "-f", "-s", *options, "-o", flac, wav], check=True,
                               stderr=subprocess.DEVNULL)  # legacy-WAVE warning at 24 bits
                if rate not in (44100, 48000):
                    result = subprocess.run([exe, flac, "all"], capture_output=True)
                    assert result.returncode == 3 and not result.stdout, (rate, width)
                    continue
                assert subprocess.check_output([exe, flac, "rate"]) == b"%d\n" % rate
                assert subprocess.check_output([exe, flac, "all"]) == expected
                indices = [0, 588, 4102, frames - 1, 11, 90300, 510, frames - 500,
                           44100 * 6 + 7, 44100 * 8 + 4095, 44100 * 9 + 22049, 44100 * 5 + 400]
                actual = subprocess.check_output([exe, flac, *map(str, indices)])
                wanted = b"".join(expected[i*4:(i+588)*4].ljust(2352, b"\0") for i in indices)
                assert actual == wanted
    print("PASS: bit-exact decode (LPC orders to 32, wasted bits, side channels), forward/backward seeks, EOF padding at 16/24 bits and 44.1/48 kHz, and rejection of other rates")

if __name__ == "__main__":
    main()
