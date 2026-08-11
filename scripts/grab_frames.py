#!/usr/bin/env python3
"""Grab one mid-clip frame per scene for the Claude-vision check (step 10)."""
import argparse
import json
import os
import subprocess


def probe_duration(path):
    out = subprocess.check_output([
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", path])
    return float(out.strip())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-dir", required=True)
    ap.add_argument("--scene-id", default=None)
    args = ap.parse_args()

    script_path = next((os.path.join(args.run_dir, n) for n in
                        ("script.discovered.json", "script.json")
                        if os.path.exists(os.path.join(args.run_dir, n))), None)
    if not script_path:
        raise SystemExit("grab_frames: no script found")
    script = json.load(open(script_path))
    frames_dir = os.path.join(args.run_dir, "verify", "frames")
    os.makedirs(frames_dir, exist_ok=True)

    for scene in script["scenes"]:
        sid = scene["id"]
        if args.scene_id and sid != args.scene_id:
            continue
        clip = os.path.join(args.run_dir, "clips", f"{sid}.final.mp4")
        if not os.path.exists(clip):
            raise SystemExit(f"grab_frames: missing {clip}")
        mid = probe_duration(clip) / 2
        out = os.path.join(frames_dir, f"{sid}.png")
        subprocess.run(["ffmpeg", "-y", "-ss", f"{mid:.3f}", "-i", clip,
                        "-frames:v", "1", out], check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        print(f"grab_frames: {sid} -> {out}")


if __name__ == "__main__":
    main()
