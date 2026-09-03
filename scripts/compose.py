#!/usr/bin/env python3
"""Step 9: compose final per-scene clips + audio into the finished MP4.

v2 rules:
  * video is NEVER re-encoded here — postprocess_clip.py did the single x264
    encode; per-scene mux and the concat both stream-copy video.
  * narration audio gets one filter chain at mux time:
    loudnorm (-16 LUFS, voice-first) -> 48kHz resample -> apad to the clip
    length (fixes the v1 `-shortest` truncation when actions outlast narration).
  * intro/outro cards are encoded ONCE with identical x264/audio parameters so
    the copy-concat stays valid.
  * writes output/timeline.json (segment start/end map), then invokes
    make_captions.py (script-text captions from per-scene alignments), then the
    final mux embeds MP4 chapter metadata (scene intents) + the soft caption
    track + `+faststart`.

Captions are a soft `mov_text` subtitle track by default (portable);
--burn-captions hard-burns them only when the ffmpeg `subtitles` filter exists.
Writes output/final.mp4. Requires ffmpeg/ffprobe.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "lib"))
import run_dir as rd

X264 = ["-c:v", "libx264", "-crf", "18", "-preset", "medium", "-pix_fmt", "yuv420p"]
AUD = ["-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2"]


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


def probe_duration(path):
    out = subprocess.check_output([
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", path])
    return float(out.strip())


def _render_card_png(title, subtitle, png, resolution, template=None):
    """Render a card to PNG via Chromium (portable; no ffmpeg drawtext needed)."""
    w, h = resolution.split("x")
    cmd = [
        "node", os.path.join(HERE, "render_card.mjs"),
        "--title", title, "--subtitle", subtitle, "--out", png,
        "--width", w, "--height", h,
    ]
    if template:
        cmd += ["--template", template]
    try:
        subprocess.run(cmd, check=True)
        return os.path.exists(png)
    except Exception as e:
        print(f"compose: card render skipped ({e}); using plain card")
        return False


def _card(text, out, resolution, fps, seconds=2.0, subtitle="", template=None):
    w, h = resolution.split("x")
    png = out + ".png"
    if _render_card_png(text, subtitle, png, resolution, template):
        vin = ["-loop", "1", "-i", png]
    else:
        # fallback: plain colored background (no text) — still a valid segment
        vin = ["-f", "lavfi", "-i", f"color=c=#0b1f3a:s={w}x{h}:r={fps}"]
    subprocess.run([
        "ffmpeg", "-y", *vin,
        "-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=48000",
        "-t", f"{seconds}", *X264, "-r", str(fps), "-vf", f"scale={w}:{h}",
        *AUD, "-shortest", out,
    ], check=True)
    if os.path.exists(png):
        os.unlink(png)


def _mux_scene(clip, wav, out, fps):
    # video stream-copied; audio normalized then padded with silence to the
    # exact clip length (never truncate the video to the audio!)
    # loudnorm NaNs out on digitally-silent audio (stub WAVs, empty narration),
    # so fall back to a plain resample+pad chain if the normalize pass fails.
    clip_len = probe_duration(clip)

    def _run(af):
        return subprocess.run([
            "ffmpeg", "-y", "-i", clip, "-i", wav,
            "-map", "0:v:0", "-map", "1:a:0",
            "-c:v", "copy", "-af", af,
            *AUD, "-t", f"{clip_len:.3f}", out,
        ], capture_output=True)

    r = _run("loudnorm=I=-16:TP=-1.5:LRA=11,aresample=48000,apad")
    if r.returncode != 0:
        print(f"compose: loudnorm failed on {os.path.basename(wav)} "
              "(silent audio?); muxing without normalization")
        r = _run("aresample=48000,apad")
        if r.returncode != 0:
            raise SystemExit(f"compose: mux failed for {clip}:\n"
                             + r.stderr.decode(errors="replace")[-800:])


def _write_chapters(path, timeline, title):
    lines = [";FFMETADATA1", f"title={title}"]
    for seg in timeline["segments"]:
        if seg["kind"] != "scene":
            continue
        lines += ["[CHAPTER]", "TIMEBASE=1/1000",
                  f"START={int(seg['start'] * 1000)}", f"END={int(seg['end'] * 1000)}",
                  f"title={seg['intent']}"]
    with open(path, "w") as f:
        f.write("\n".join(lines) + "\n")


def _concat_intro_fade(segpaths, out, fps, duration=1.0):
    """Eased dissolve ONLY between the first two segments (intro card → first
    scene); every other boundary is a hard cut (stream-copy concat). Audio is
    never faded: the intro card is silent and the first scene's narration is
    simply time-shifted to the overlapped start."""
    d0 = probe_duration(segpaths[0])
    offset = max(0.0, d0 - duration)
    head = out + ".head.mp4"
    delay = int(round(offset * 1000))
    subprocess.run([
        "ffmpeg", "-y", "-i", segpaths[0], "-i", segpaths[1],
        "-filter_complex",
        f"[0:v][1:v]xfade=transition=fade:duration={duration}:offset={offset:.3f}[v];"
        f"[1:a]adelay={delay}|{delay}[a]",
        "-map", "[v]", "-map", "[a]",
        *X264, "-r", str(fps), *AUD, head,
    ], check=True)
    listfile = out + ".list.txt"
    with open(listfile, "w") as f:
        f.write(f"file '{os.path.abspath(head)}'\n")
        for p in segpaths[2:]:
            f.write(f"file '{os.path.abspath(p)}'\n")
    subprocess.run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", listfile,
                    "-c", "copy", out], check=True)


def _concat_fade(segpaths, out, fps, duration=0.5):
    # xfade/acrossfade chain requires a re-encode; used only when transitions=fade
    inputs = []
    for p in segpaths:
        inputs += ["-i", p]
    filters, last, offset = [], "0:v", 0.0
    for i in range(1, len(segpaths)):
        offset += probe_duration(segpaths[i - 1]) - duration
        out_lbl = f"v{i}"
        filters.append(f"[{last}][{i}:v]xfade=transition=fade:duration={duration}"
                       f":offset={offset:.3f}[{out_lbl}]")
        last = out_lbl
    afilters, alast = [], "0:a"
    for i in range(1, len(segpaths)):
        out_lbl = f"a{i}"
        afilters.append(f"[{alast}][{i}:a]acrossfade=d={duration}[{out_lbl}]")
        alast = out_lbl
    subprocess.run(["ffmpeg", "-y", *inputs,
                    "-filter_complex", ";".join(filters + afilters),
                    "-map", f"[{last}]", "-map", f"[{alast}]",
                    *X264, "-r", str(fps), *AUD, out], check=True)


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
    fade = cfg.get("transitions") == "fade"
    intro_fade = cfg.get("transitions") == "intro"
    intro_fade_dur = 1.0

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
    segments = []  # (kind, id, intent, path)

    def _tmpl(name):
        p = os.path.join(args.run_dir, "assets", name)
        return p if os.path.exists(p) else None

    if not args.no_intro:
        intro = os.path.join(work, "intro.mp4")
        _card(title, intro, resolution, fps,
              seconds=float(cfg.get("intro_seconds", 2.0)),
              subtitle=cfg.get("intro_subtitle", ""),
              template=_tmpl("card_intro.html"))
        segments.append(("intro", None, None, intro))

    scene_clips = []
    for scene in script["scenes"]:
        sid = scene["id"]
        clip = os.path.join(clips_dir, f"{sid}.final.mp4")
        wav = os.path.join(audio_dir, f"{sid}.wav")
        if not os.path.exists(clip):
            raise SystemExit(f"compose: missing {clip}")
        scene_clips.append(clip)
        seg = os.path.join(work, f"seg_{sid}.mp4")
        _mux_scene(clip, wav, seg, fps)
        segments.append(("scene", sid, scene.get("intent", sid), seg))

    if not args.no_intro:
        outro = os.path.join(work, "outro.mp4")
        _card("Thanks for watching", outro, resolution, fps,
              seconds=float(cfg.get("outro_seconds", 2.0)),
              subtitle=cfg.get("outro_subtitle", ""),
              template=_tmpl("card_outro.html"))
        segments.append(("outro", None, None, outro))

    # timeline from actual segment durations (fade overlaps shift later starts)
    t, timeline = 0.0, {"segments": []}
    for i, (kind, sid, intent, path) in enumerate(segments):
        d = probe_duration(path)
        if fade:
            shift = 0.5 * i
        elif intro_fade and i >= 1:
            shift = intro_fade_dur  # single overlap at the intro boundary
        else:
            shift = 0.0
        start = max(0.0, t - shift)
        timeline["segments"].append({"kind": kind, "id": sid, "intent": intent,
                                     "start": round(start, 3), "end": round(start + d, 3)})
        t += d
    rd.write_json(os.path.join(out_dir, "timeline.json"), timeline)

    # captions BEFORE the final mux (script-text + alignments; falls back internally)
    if not args.no_captions:
        subprocess.run([sys.executable, os.path.join(HERE, "make_captions.py"),
                        "--run-dir", args.run_dir], check=False)

    concat_out = os.path.join(work, "concat.mp4")
    if intro_fade and len(segments) > 1:
        _concat_intro_fade([s[3] for s in segments], concat_out, fps,
                           duration=intro_fade_dur)
    elif fade and len(segments) > 1:
        _concat_fade([s[3] for s in segments], concat_out, fps)
    else:
        listfile = os.path.join(work, "list.txt")
        with open(listfile, "w") as f:
            for _, _, _, s in segments:
                f.write(f"file '{os.path.abspath(s)}'\n")
        subprocess.run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", listfile,
                        "-c", "copy", concat_out], check=True)

    chapters = os.path.join(work, "chapters.ffmeta")
    _write_chapters(chapters, timeline, title)

    final = os.path.join(out_dir, "final.mp4")
    captions = os.path.join(args.run_dir, "captions.srt")
    have_captions = (not args.no_captions and os.path.exists(captions)
                     and os.path.getsize(captions) > 0)

    if have_captions and args.burn_captions and _has_filter("subtitles"):
        subprocess.run(["ffmpeg", "-y", "-i", concat_out, "-i", chapters,
                        "-map_metadata", "1",
                        "-vf", f"subtitles='{captions}'",
                        *X264, "-c:a", "copy", "-movflags", "+faststart", final], check=True)
    elif have_captions:
        if args.burn_captions:
            print("compose: 'subtitles' filter unavailable; muxing soft captions instead")
        subprocess.run(["ffmpeg", "-y", "-i", concat_out, "-i", captions, "-i", chapters,
                        "-map", "0", "-map", "1", "-map_metadata", "2",
                        "-c", "copy", "-c:s", "mov_text",
                        "-metadata:s:s:0", "language=eng",
                        "-movflags", "+faststart", final], check=True)
    else:
        subprocess.run(["ffmpeg", "-y", "-i", concat_out, "-i", chapters,
                        "-map_metadata", "1", "-c", "copy",
                        "-movflags", "+faststart", final], check=True)

    rd.mark_done(args.run_dir, "compose", scene_clips)
    shutil.rmtree(work, ignore_errors=True)
    print(f"compose: wrote {final}")


if __name__ == "__main__":
    main()
