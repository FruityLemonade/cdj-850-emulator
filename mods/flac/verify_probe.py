# SPDX-License-Identifier: GPL-2.0-or-later
"""Check FLAC audio and transport in the two synthetic QEMU probes."""
import argparse
from array import array
from pathlib import Path
import re
import struct
import sys
import wave

def elapsed(log, begin, end):
    points = re.findall(r"display time=(\d\d):(\d\d):(\d\d) remain=0 \((\d+) ms\)", log)
    return [int(m) * 4500 + int(s) * 75 + int(f)
            for m, s, f, when in points if begin <= int(when) <= end]

def diagnostics(directory):
    opened, pcm_bytes, seeks, failures = struct.unpack(
        ">4I", (directory / "diagnostics.bin").read_bytes())
    assert opened > 0 and pcm_bytes >= 44100 * 4, (opened, pcm_bytes)
    assert seeks > 0 and failures == 0, (seeks, failures)
    return seeks

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("wav", type=Path)
    parser.add_argument("folder", type=Path)
    parser.add_argument("export", type=Path)
    args = parser.parse_args()

    with wave.open(str(args.wav), "rb") as source:
        pcm = array("h")
        pcm.frombytes(source.readframes(source.getnframes()))
    if sys.byteorder == "little":
        pcm.byteswap()
    reference = pcm.tobytes()
    diagnostics(args.folder)
    folder = (args.folder / "panel.log").read_text()
    assert "[Candidate.flac]" in folder and "display track=02" in folder
    assert "Not Loaded" not in folder
    ring = (args.folder / "pcm-ring.bin").read_bytes()
    assert reference in ring + ring, "full reference PCM missing from the emulated ring"

    seeks = diagnostics(args.export)
    export = (args.export / "panel.log").read_text()
    assert "[Baseline] | [FLAC CHECK]" in export
    assert "[Candidate] | [FLAC CHECK]" in export
    assert "Not Loaded" not in export
    assert "display bpm=120" in export and "display playing wave=1 memory=2" in export
    assert 8 * 75 in elapsed(export, 18300, 20000), "8-second memory cue missed"
    loop = elapsed(export, 21700, 24000)
    assert any(first - second >= 40 for first, second in zip(loop, loop[1:])), "loop did not wrap"
    rev = elapsed(export, 25200, 27000)
    assert rev and rev[0] - rev[-1] >= 75, "reverse did not move the playhead back"
    assert "input jog -9" in export and "input jog +7" in export
    assert "display master_tempo=1" in export
    print(f"PASS: entire {len(reference):,}-byte PCM match, WAV/FLAC load, cue, loop, "
          f"reverse, jog input and Master Tempo ({seeks} FLAC decoder seeks)")

if __name__ == "__main__":
    main()
