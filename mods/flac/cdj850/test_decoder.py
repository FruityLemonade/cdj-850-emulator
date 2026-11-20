# SPDX-License-Identifier: GPL-2.0-or-later
"""Compare sequential and random reads of native FLAC against source PCM."""
from array import array
from pathlib import Path
import random
import subprocess
import sys
import tempfile
import wave

def main():
    exe = Path(sys.argv[1]).absolute()
    frames = 44100 * 5 + 123
    rand = random.Random(850)
    contents = array("h")
    for i in range(frames):
        left = (i * 127) % 65000 - 32500
        right = left // 2 + rand.randrange(-500, 500)
        contents.extend((left, right))
    raw = contents[:]
    if sys.byteorder == "little":
        raw.byteswap()
    expected = raw.tobytes()
    if sys.byteorder == "big":
        contents.byteswap()
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        wav = root / "source.wav"
        flac = root / "source.flac"
        for rate in (44100, 48000):
            with wave.open(str(wav), "wb") as file:
                file.setparams((2, 2, rate, 0, "NONE", ""))
                file.writeframes(contents.tobytes())
            for options in (["-0", "-S", "1s"], ["-8", "--no-seektable"]):
                subprocess.run(["flac", "-f", "-s", *options, "-o", flac, wav], check=True)
                if rate == 48000:
                    result = subprocess.run([exe, flac, "all"], capture_output=True)
                    assert result.returncode == 3 and not result.stdout
                    continue
                assert subprocess.check_output([exe, flac, "all"]) == expected
                indices = [0, 588, 4102, frames - 1, 11, 90300, 510, frames - 500]
                actual = subprocess.check_output([exe, flac, *map(str, indices)])
                wanted = b"".join(expected[i*4:(i+588)*4].ljust(2352, b"\0") for i in indices)
                assert actual == wanted
    print("PASS: bit-exact decode, forward/backward seeks, EOF padding, and format rejection")

if __name__ == "__main__":
    main()
