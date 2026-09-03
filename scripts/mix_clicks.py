#!/usr/bin/env python3
"""Post-compose step: overlay click sounds onto the final MP4.

Reads output/timeline.json (scene start offsets, fade-aware) and each scene's
clips/NN.events.json (action timestamps logged by record_scene.mjs relative to
capture start). Every 'click' event becomes one instance of the click sample
mixed into the narration track at scene_start + t. Video and subtitle streams
are stream-copied; chapters are preserved; only audio is re-encoded.

If the click sample does not exist it is synthesized (a soft two-burst
mouse click, pure stdlib) into <run>/audio/click.wav on first use.

  python3 mix_clicks.py --run-dir <dir> [--click audio/click.wav]
                        [--in output/final.mp4] [--out output/final.mp4]
"""
import argparse
import json
import math
import os
import random
import struct
import subprocess
import wave


def synth_click(path, sr=48000, seed=7):
    """Soft two-burst mouse click: damped tone + band-limited noise, ~0.14s."""
    rng = random.Random(seed)

    def burst(dur, freq, noise_amt, decay, amp):
        n = int(dur * sr)
        raw = [rng.gauss(0, 1) for _ in range(n)]
        hp = [raw[0]] + [raw[i] - raw[i - 1] for i in range(1, n)]  # crude high-pass
        sm = [sum(hp[max(0, i - 5):i + 1]) / 6 for i in range(n)]   # crude low-pass
        out = []
        for i in range(n):
            t = i / sr
            env = math.exp(-t * decay)
            tone = math.sin(2 * math.pi * freq * t)
            out.append((tone * (1 - noise_amt) + sm[i] * noise_amt * 3.0) * env * amp)
        return out

    total = int(0.14 * sr)
    click = [0.0] * total
    for off, piece in ((0, burst(0.05, 2350, 0.55, 150, 1.0)),
                       (int(0.062 * sr), burst(0.04, 1900, 0.6, 190, 0.55))):
        for i, v in enumerate(piece):
            if off + i < total:
                click[off + i] += v
    peak = max(abs(v) for v in click) or 1.0
    pcm = b"".join(struct.pack("<h", int(max(-1, min(1, v / peak * 0.32)) * 32767))
                   for v in click)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-dir", required=True)
    ap.add_argument("--click", default=None, help="click sample (default <run>/audio/click.wav)")
    ap.add_argument("--in", dest="inp", default=None)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    rd = args.run_dir
    click = args.click or os.path.join(rd, "audio", "click.wav")
    if not os.path.exists(click):
        synth_click(click)
        print(f"mix_clicks: synthesized default click sample at {click}")
    inp = args.inp or os.path.join(rd, "output", "final.mp4")
    out = args.out or inp
    timeline = json.load(open(os.path.join(rd, "output", "timeline.json")))

    times = []
    for seg in timeline["segments"]:
        if seg["kind"] != "scene":
            continue
        ev_path = os.path.join(rd, "clips", f"{seg['id']}.events.json")
        if not os.path.exists(ev_path):
            continue
        for ev in json.load(open(ev_path)).get("events", []):
            if ev.get("kind") == "click":
                times.append(seg["start"] + ev["t"] / 1000.0)

    if not times:
        print("mix_clicks: no click events found; nothing to do")
        return

    n = len(times)
    parts = [f"[1:a]asplit={n}" + "".join(f"[s{i}]" for i in range(n))]
    for i, t in enumerate(times):
        ms = int(round(t * 1000))
        parts.append(f"[s{i}]adelay={ms}|{ms}[c{i}]")
    if n > 1:
        parts.append("".join(f"[c{i}]" for i in range(n))
                     + f"amix=inputs={n}:normalize=0[clicks]")
        clicks = "[clicks]"
    else:
        clicks = "[c0]"
    parts.append(f"[0:a]{clicks}amix=inputs=2:duration=first:normalize=0[aout]")
    fc = ";".join(parts)

    tmp = out + ".tmp.mp4"
    subprocess.run([
        "ffmpeg", "-y", "-i", inp, "-i", click,
        "-filter_complex", fc,
        "-map", "0:v", "-map", "[aout]", "-map", "0:s?",
        "-c:v", "copy", "-c:s", "mov_text",
        "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
        "-movflags", "+faststart",
        tmp,
    ], check=True)
    os.replace(tmp, out)
    print(f"mix_clicks: mixed {n} clicks into {out}")


if __name__ == "__main__":
    main()
