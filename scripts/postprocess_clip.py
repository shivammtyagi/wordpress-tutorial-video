#!/usr/bin/env python3
"""Step 8: post-process one raw scene clip into its final, narration-paced clip.

Responsibilities:
  * normalize to target resolution + fps,
  * optionally Ken Burns zoom toward the scene focus region,
  * extend the clip to max(clip_len, narration_len) by holding the last frame
    (the anti-drift guarantee: the visual never ends before the sentence does).

Reads clips/NN.raw.webm + audio/durations.json; writes clips/NN.final.mp4.
Requires ffmpeg/ffprobe (install via scripts/bootstrap.sh).
"""
import argparse
import json
import os
import shutil
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "lib"))
import run_dir as rd


def _require_ffmpeg():
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        raise SystemExit("postprocess: ffmpeg/ffprobe not found. Run scripts/bootstrap.sh.")


def probe_duration(path):
    out = subprocess.check_output([
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", path,
    ])
    return float(out.strip())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-dir", required=True)
    ap.add_argument("--scene-id", required=True)
    ap.add_argument("--resolution", default=None)
    ap.add_argument("--fps", type=int, default=None)
    ap.add_argument("--zoom", action="store_true", help="apply a slow Ken Burns zoom")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()
    _require_ffmpeg()

    sid = args.scene_id
    raw = os.path.join(args.run_dir, "clips", f"{sid}.raw.webm")
    final = os.path.join(args.run_dir, "clips", f"{sid}.final.mp4")
    if not os.path.exists(raw):
        raise SystemExit(f"postprocess: missing {raw}")

    inputs = [raw, os.path.join(args.run_dir, "audio", "durations.json")]
    if rd.is_done(args.run_dir, f"postprocess:{sid}", inputs) and not args.force:
        print(f"postprocess: scene {sid} up to date")
        return

    # resolution/fps from config when not overridden
    cfg = {}
    cfg_path = os.path.join(args.run_dir, "config.json")
    if os.path.exists(cfg_path):
        cfg = json.load(open(cfg_path))
    resolution = args.resolution or cfg.get("resolution", "1920x1080")
    fps = args.fps or cfg.get("fps", 30)
    w, h = resolution.split("x")

    durations = json.load(open(os.path.join(args.run_dir, "audio", "durations.json")))
    narration = float(durations.get(sid, 0))
    clip_len = probe_duration(raw)
    target = max(clip_len, narration)

    vf = [
        f"scale={w}:{h}:force_original_aspect_ratio=decrease",
        f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:black",
        f"fps={fps}",
    ]
    if args.zoom:
        # gentle 1.0 -> 1.08 zoom across the clip
        vf.append(f"zoompan=z='min(zoom+0.0005,1.08)':d=1:s={w}x{h}:fps={fps}")
    # hold the last frame out to the target duration
    vf.append(f"tpad=stop_mode=clone:stop_duration={max(0, target - clip_len):.3f}")

    os.makedirs(os.path.dirname(final), exist_ok=True)
    cmd = [
        "ffmpeg", "-y", "-i", raw,
        "-vf", ",".join(vf),
        "-t", f"{target:.3f}",
        "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "medium", final,
    ]
    subprocess.run(cmd, check=True)
    rd.mark_done(args.run_dir, f"postprocess:{sid}", inputs)
    print(f"postprocess: scene {sid} -> {final} ({target:.2f}s)")


if __name__ == "__main__":
    main()
