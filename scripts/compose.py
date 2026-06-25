#!/usr/bin/env python3
"""Step 9: compose final per-scene clips + audio into the finished MP4.

For each scene: mux clips/NN.final.mp4 with audio/NN.wav. Concatenate scenes,
prepend a title intro card and append an outro card (rendered via Chromium).
Captions are muxed as a soft mov_text subtitle track by default (portable);
--burn-captions hard-burns them when the ffmpeg `subtitles` filter is available.

Writes output/final.mp4. Requires ffmpeg/ffprobe.
"""
import argparse
import json
import os
import shutil
import subprocess
import tempfile


HERE = os.path.dirname(os.path.abspath(__file__))


def _require_ffmpeg():
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        raise SystemExit("compose: ffmpeg/ffprobe not found. Run scripts/bootstrap.sh.")


def _has_filter(name):
    try:
        out = subprocess.check_output(["ffmpeg", "-hide_banner", "-filters"],
                                      stderr=subprocess.DEVNULL, text=True)
        return any(line.split()[1:2] == [name] for line in out.splitlines() if line.strip())
    except Exception:
        return False


def _render_card_png(title, subtitle, png, resolution):
    """Render a card to PNG via Chromium (portable; no ffmpeg drawtext needed).

    Returns True on success, False if Playwright/node is unavailable.
    """
    w, h = resolution.split("x")
    try:
        subprocess.run([
            "node", os.path.join(HERE, "render_card.mjs"),
            "--title", title, "--subtitle", subtitle, "--out", png,
            "--width", w, "--height", h,
        ], check=True)
        return os.path.exists(png)
    except Exception as e:
        print(f"compose: card render skipped ({e}); using plain card")
        return False


def _card(text, out, resolution, fps, seconds=2.0, subtitle=""):
    w, h = resolution.split("x")
    png = out + ".png"
    if _render_card_png(text, subtitle, png, resolution):
        vin = ["-loop", "1", "-i", png]
    else:
        # fallback: plain colored background (no text) — still a valid segment
        vin = ["-f", "lavfi", "-i", f"color=c=#0b1f3a:s={w}x{h}:r={fps}"]
    subprocess.run([
        "ffmpeg", "-y", *vin,
        "-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=44100",
        "-t", f"{seconds}", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", str(fps),
        "-vf", f"scale={w}:{h}", "-c:a", "aac", "-shortest", out,
    ], check=True)
    if os.path.exists(png):
        os.unlink(png)


def _mux_scene(clip, wav, out, fps):
    # video from clip, audio from wav; pad/trim audio to video length
    subprocess.run([
        "ffmpeg", "-y", "-i", clip, "-i", wav,
        "-map", "0:v:0", "-map", "1:a:0",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", str(fps),
        "-c:a", "aac", "-ar", "44100", "-shortest", out,
    ], check=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-dir", required=True)
    ap.add_argument("--no-captions", action="store_true")
    ap.add_argument("--no-intro", action="store_true")
    ap.add_argument("--burn-captions", action="store_true",
                    help="hard-burn captions (needs ffmpeg 'subtitles' filter); "
                         "otherwise captions are muxed as a soft subtitle track")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()
    _require_ffmpeg()

    cfg_path = os.path.join(args.run_dir, "config.json")
    cfg = json.load(open(cfg_path)) if os.path.exists(cfg_path) else {}
    resolution = cfg.get("resolution", "1920x1080")
    fps = int(cfg.get("fps", 30))

    script_path = next((os.path.join(args.run_dir, n) for n in
                        ("script.discovered.json", "script.json")
                        if os.path.exists(os.path.join(args.run_dir, n))), None)
    if not script_path:
        raise SystemExit("compose: no script found")
    script = json.load(open(script_path))
    title = script.get("title", "Tutorial")

    clips_dir = os.path.join(args.run_dir, "clips")
    audio_dir = os.path.join(args.run_dir, "audio")
    out_dir = os.path.join(args.run_dir, "output")
    os.makedirs(out_dir, exist_ok=True)

    work = tempfile.mkdtemp(prefix="compose_", dir=out_dir)
    segments = []

    if not args.no_intro:
        intro = os.path.join(work, "intro.mp4")
        _card(title, intro, resolution, fps)
        segments.append(intro)

    for scene in script["scenes"]:
        sid = scene["id"]
        clip = os.path.join(clips_dir, f"{sid}.final.mp4")
        wav = os.path.join(audio_dir, f"{sid}.wav")
        if not os.path.exists(clip):
            raise SystemExit(f"compose: missing {clip}")
        seg = os.path.join(work, f"seg_{sid}.mp4")
        _mux_scene(clip, wav, seg, fps)
        segments.append(seg)

    if not args.no_intro:
        outro = os.path.join(work, "outro.mp4")
        _card("Thanks for watching", outro, resolution, fps)
        segments.append(outro)

    # concat via demuxer (re-encode for uniformity)
    listfile = os.path.join(work, "list.txt")
    with open(listfile, "w") as f:
        for s in segments:
            f.write(f"file '{os.path.abspath(s)}'\n")

    concat_out = os.path.join(work, "concat.mp4")
    subprocess.run([
        "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", listfile,
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", str(fps),
        "-c:a", "aac", "-ar", "44100", concat_out,
    ], check=True)

    final = os.path.join(out_dir, "final.mp4")
    captions = os.path.join(args.run_dir, "captions.srt")
    have_captions = (not args.no_captions and os.path.exists(captions)
                     and os.path.getsize(captions) > 0)

    if have_captions and args.burn_captions and _has_filter("subtitles"):
        subprocess.run([
            "ffmpeg", "-y", "-i", concat_out,
            "-vf", f"subtitles='{captions}'",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "copy", final,
        ], check=True)
    elif have_captions:
        if args.burn_captions:
            print("compose: 'subtitles' filter unavailable; muxing soft captions instead")
        # portable soft subtitle track (toggleable in any player)
        subprocess.run([
            "ffmpeg", "-y", "-i", concat_out, "-i", captions,
            "-map", "0", "-map", "1", "-c", "copy", "-c:s", "mov_text",
            "-metadata:s:s:0", "language=eng", final,
        ], check=True)
    else:
        shutil.move(concat_out, final)

    shutil.rmtree(work, ignore_errors=True)
    print(f"compose: wrote {final}")


if __name__ == "__main__":
    main()
