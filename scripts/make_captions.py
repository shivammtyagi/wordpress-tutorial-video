#!/usr/bin/env python3
"""Step 10b: turn WhisperX word timestamps into an SRT caption file.

Words are grouped into cues bounded by a max character count and a max cue
duration, so captions stay readable and aligned. The cue timing comes straight
from WhisperX's forced-alignment word timestamps (sub-100ms), so captions match
the spoken audio for free.
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "lib"))
import run_dir as rd


def _ts(seconds):
    if seconds < 0:
        seconds = 0
    ms = int(round(seconds * 1000))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def words_to_srt(words, max_chars=42, max_secs=3.5):
    """words: list of {word, start, end}. Returns SRT text."""
    cues = []
    cur = []
    cur_len = 0
    for w in words:
        token = w["word"].strip()
        if not token:
            continue
        add = len(token) + (1 if cur else 0)
        too_long = cur and (cur_len + add > max_chars)
        too_slow = cur and (w["end"] - cur[0]["start"] > max_secs)
        if too_long or too_slow:
            cues.append(cur)
            cur, cur_len = [], 0
            add = len(token)
        cur.append(w)
        cur_len += add
    if cur:
        cues.append(cur)

    out = []
    for i, cue in enumerate(cues, 1):
        start = _ts(cue[0]["start"])
        end = _ts(cue[-1]["end"])
        text = " ".join(w["word"].strip() for w in cue)
        out.append(f"{i}\n{start} --> {end}\n{text}")
    return "\n\n".join(out) + ("\n" if out else "")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-dir", required=True)
    ap.add_argument("--max-chars", type=int, default=42)
    args = ap.parse_args()

    transcript = json.load(open(os.path.join(args.run_dir, "verify", "transcript.json")))
    words = transcript.get("words", [])
    srt = words_to_srt(words, max_chars=args.max_chars)
    rd.atomic_write(os.path.join(args.run_dir, "captions.srt"), srt)
    print(f"make_captions: wrote captions.srt ({srt.count(chr(10) + chr(10)) + 1} cues)")


if __name__ == "__main__":
    main()
