#!/usr/bin/env python3
"""Step 5a: tighten the narration WAVs, then rewrite audio/durations.json.

Three deterministic passes per scene WAV (any engine):
  * trim leading silence (keep --lead seconds of breath),
  * trim trailing silence (keep --tail seconds),
  * compress internal pauses longer than --max-pause down to --pause-to.

This is the anti-"robotic gap" step: TTS engines pad sentences with dead air
that reads as hesitation on camera. Run AFTER tts_*.py and BEFORE
verify_scenes.py (the audio gate re-derives word offsets from the trimmed
audio, which keeps recorder cues and captions accurate).

Requires numpy (present in the skill venv). Idempotent: re-running on already
trimmed audio changes nothing meaningful.
"""
import argparse
import json
import os
import sys
import wave

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "lib"))
import run_dir as rd


def trim_wav(path, thresh=250, lead_keep=0.06, tail_keep=0.18,
             max_pause=0.60, pause_to=0.45):
    """Trim/compress one WAV in place; returns (old_secs, new_secs, n_compressed)."""
    import numpy as np
    with wave.open(path, "rb") as w:
        sr = w.getframerate()
        x = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").astype(np.int32)
    n = len(x)
    win = max(1, int(0.02 * sr))
    nwin = n // win
    if nwin == 0:
        return n / sr, n / sr, 0
    env = np.abs(x[:nwin * win]).reshape(nwin, win).max(axis=1)
    loud = env > thresh
    if not loud.any():
        return n / sr, n / sr, 0
    first = int(np.argmax(loud))
    last = nwin - 1 - int(np.argmax(loud[::-1]))
    start = max(0, first * win - int(lead_keep * sr))
    end = min(n, (last + 1) * win + int(tail_keep * sr))
    y = x[start:end]

    nw2 = len(y) // win
    env2 = np.abs(y[:nw2 * win]).reshape(nw2, win).max(axis=1)
    loud2 = env2 > thresh
    keep = np.ones(nw2, dtype=bool)
    i = compressed = 0
    while i < nw2:
        if not loud2[i]:
            j = i
            while j < nw2 and not loud2[j]:
                j += 1
            run = (j - i) * win / sr
            if run > max_pause and i > 0 and j < nw2:
                excess = int(round((run - pause_to) * sr / win))
                mid = (i + j) // 2
                keep[mid - excess // 2: mid - excess // 2 + excess] = False
                compressed += 1
            i = j
        else:
            i += 1
    parts = [y[k * win:(k + 1) * win] for k in range(nw2) if keep[k]]
    parts.append(y[nw2 * win:])
    z = np.concatenate(parts).astype("<i2") if parts else y.astype("<i2")
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(z.tobytes())
    return n / sr, len(z) / sr, compressed


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-dir", required=True)
    ap.add_argument("--scene-id", default=None, help="only trim this scene")
    ap.add_argument("--lead", type=float, default=0.06)
    ap.add_argument("--tail", type=float, default=0.18)
    ap.add_argument("--max-pause", type=float, default=0.60)
    ap.add_argument("--pause-to", type=float, default=0.45)
    args = ap.parse_args()

    audio_dir = os.path.join(args.run_dir, "audio")
    dur_path = os.path.join(audio_dir, "durations.json")
    durations = json.load(open(dur_path)) if os.path.exists(dur_path) else {}

    for f in sorted(os.listdir(audio_dir)):
        sid = f[:-4]
        if not (f.endswith(".wav") and sid.isdigit()):
            continue
        if args.scene_id and sid != args.scene_id:
            continue
        old, new, comp = trim_wav(
            os.path.join(audio_dir, f), lead_keep=args.lead, tail_keep=args.tail,
            max_pause=args.max_pause, pause_to=args.pause_to)
        durations[sid] = round(new, 3)
        print(f"trim: scene {sid} {old:.2f}s -> {new:.2f}s (pauses compressed: {comp})")

    rd.write_json(dur_path, durations)
    print(f"trim: rewrote {dur_path}")


if __name__ == "__main__":
    main()
