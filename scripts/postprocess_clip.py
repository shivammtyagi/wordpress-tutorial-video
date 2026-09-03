#!/usr/bin/env python3
"""Step 8: post-process one raw scene clip into its final, narration-paced clip.

THE pipeline's single video encode (compose stream-copies). Responsibilities:
  * normalize the VFR screencast to constant frame rate (fps filter),
  * optionally Ken Burns zoom toward the scene focus box (computed at master/2x
    resolution so zoomed pixels stay sharp after the delivery downscale),
  * downscale the 2x master to the delivery resolution (lanczos),
  * extend the clip to max(clip_len, narration_len) by holding the last frame
    (the anti-drift guarantee: the visual never ends before the sentence does),
  * with deliver_4k, also write a full-resolution master variant.

Reads clips/NN.raw.webm + clips/NN.focus.json + audio/durations.json;
writes clips/NN.final.mp4 (and clips/NN.final-4k.mp4 when configured).
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
    ap.add_argument("--zoom", action="store_true", help="apply a slow Ken Burns zoom toward the focus box")
    ap.add_argument("--tail-pad", type=float, default=0.0,
                    help="extra seconds of last-frame hold beyond max(clip, narration); "
                         "gives crossfade transitions room so they never clip narration")
    ap.add_argument("--tail-cap", type=float, default=None,
                    help="cap the still tail after the narration ends: clip runs at most "
                         "narration + cap seconds (and at least narration + 0.15)")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()
    _require_ffmpeg()

    sid = args.scene_id
    raw = os.path.join(args.run_dir, "clips", f"{sid}.raw.webm")
    final = os.path.join(args.run_dir, "clips", f"{sid}.final.mp4")
    if not os.path.exists(raw):
        raise SystemExit(f"postprocess: missing {raw}")

    focus_path = os.path.join(args.run_dir, "clips", f"{sid}.focus.json")
    inputs = [raw, os.path.join(args.run_dir, "audio", "durations.json")]
    if os.path.exists(focus_path):
        inputs.append(focus_path)
    if rd.is_done(args.run_dir, f"postprocess:{sid}", inputs) and not args.force:
        print(f"postprocess: scene {sid} up to date")
        return

    cfg = {}
    cfg_path = os.path.join(args.run_dir, "config.json")
    if os.path.exists(cfg_path):
        cfg = json.load(open(cfg_path))
    resolution = args.resolution or cfg.get("resolution", "1920x1080")
    fps = args.fps or cfg.get("fps", 30)

    durations = json.load(open(os.path.join(args.run_dir, "audio", "durations.json")))
    narration = float(durations.get(sid, 0))
    clip_len = probe_duration(raw)
    target = max(clip_len, narration) + max(0.0, args.tail_pad)
    # tail cap priority: CLI flag > per-scene script field > config default.
    tail_cap = args.tail_cap
    if tail_cap is None:
        script_path = next((os.path.join(args.run_dir, n) for n in
                            ("script.discovered.json", "script.json")
                            if os.path.exists(os.path.join(args.run_dir, n))), None)
        if script_path:
            scene = next((s for s in json.load(open(script_path)).get("scenes", [])
                          if s.get("id") == sid), {})
            tail_cap = scene.get("tail_cap_s")
    if tail_cap is None:
        tail_cap = cfg.get("tail_cap_s")
    if tail_cap is not None and narration > 0:
        target = min(max(target, narration + 0.15), narration + max(float(tail_cap), 0.15))

    dw, dh = (int(x) for x in resolution.split("x"))
    focus = json.load(open(focus_path)) if os.path.exists(focus_path) else {}
    scale = int(focus.get("scale", cfg.get("capture_scale", 2)))
    mw, mh = dw * scale, dh * scale

    def _vf(deliver_w, deliver_h):
        vf = [f"fps={fps}"]
        if args.zoom:
            box = focus.get("box")
            if box:
                cx = (box["x"] + box["width"] / 2) * scale
                cy = (box["y"] + box["height"] / 2) * scale
            else:
                cx, cy = mw / 2, mh / 2
            vf.append(
                "zoompan=z='min(zoom+0.0005,1.08)'"
                f":x='min(max(0,{cx:.0f}-(iw/zoom/2)),iw-iw/zoom)'"
                f":y='min(max(0,{cy:.0f}-(ih/zoom/2)),ih-ih/zoom)'"
                f":d=1:s={mw}x{mh}:fps={fps}")
        if (deliver_w, deliver_h) != (mw, mh):
            vf.append(f"scale={deliver_w}:{deliver_h}:flags=lanczos")
        vf.append(f"tpad=stop_mode=clone:stop_duration={max(0, target - clip_len):.3f}")
        return ",".join(vf)

    def _encode(out, deliver_w, deliver_h):
        subprocess.run([
            "ffmpeg", "-y", "-i", raw, "-vf", _vf(deliver_w, deliver_h),
            "-t", f"{target:.3f}", "-an",
            "-c:v", "libx264", "-crf", "18", "-preset", "medium",
            "-pix_fmt", "yuv420p", out,
        ], check=True)

    os.makedirs(os.path.dirname(final), exist_ok=True)
    _encode(final, dw, dh)
    if cfg.get("deliver_4k"):
        _encode(final.replace(".final.mp4", ".final-4k.mp4"), mw, mh)
    rd.mark_done(args.run_dir, f"postprocess:{sid}", inputs)
    print(f"postprocess: scene {sid} -> {final} ({target:.2f}s, {dw}x{dh})")


if __name__ == "__main__":
    main()
