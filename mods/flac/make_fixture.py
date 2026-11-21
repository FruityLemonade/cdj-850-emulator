#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Create paired WAV/native FLAC tracks and two disposable USB images."""
import argparse
from array import array
import math
from pathlib import Path
import shutil
import subprocess
import sys
import wave

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--out", type=Path, default=Path("build/fixture"))
    args = parser.parse_args()
    args.out = args.out.resolve()
    args.out.mkdir(parents=True, exist_ok=True)
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(args.repo / "scripts/media"))
    from collection_xml import document, track_xml

    music = args.out / "music"
    music.mkdir(exist_ok=True)
    wav = music / "Baseline.wav"
    flac = music / "Candidate.flac"
    rate, seconds = 44100, 18
    data = array("h")
    for n in range(rate * seconds):
        beat, tick = divmod(n, rate // 2)
        phase = tick / rate
        envelope = math.exp(-25 * phase)
        left = int((15000 + 150 * beat) * math.sin(2 * math.pi * 110 * phase) * envelope)
        right = int((9000 + 100 * beat) * math.sin(2 * math.pi * 155 * phase) * envelope)
        data.extend((left, right))
    if sys.byteorder == "big":
        data.byteswap()
    with wave.open(str(wav), "wb") as file:
        file.setparams((2, 2, rate, 0, "NONE", ""))
        file.writeframes(data.tobytes())
    subprocess.run(["flac", "-f", "-s", "-8", "--no-seektable", "-o", flac, wav], check=True)

    xml = args.out / "collection.xml"
    tags = dict(title="", artist="FLAC CHECK", album="", genre="", duration=seconds,
                bitrate=0, sample_rate=rate)
    tracks = [track_xml(i, str(path), tags, None, [], tempo=(120, 0), marks=((2, -1), (8, -1)))
              for i, path in enumerate((wav, flac), 1)]
    xml.write_text(document(tracks, "Codec parity"))
    device = args.out / "device"
    device.mkdir(exist_ok=True)
    analyzer = args.repo / "tools/baken/baken"
    subprocess.run([str(analyzer) if analyzer.is_file() else shutil.which("baken") or "baken",
                    "expressport", "--no-settings", "--generate-analysis",
                    "--device", device, xml], check=True)
    make_image = args.repo / "scripts/media/make_usb_image.py"
    for folder, name in ((device, "export.img"), (music, "folder.img")):
        subprocess.run([sys.executable, make_image, folder, args.out / name, "32"], check=True)

if __name__ == "__main__":
    main()
