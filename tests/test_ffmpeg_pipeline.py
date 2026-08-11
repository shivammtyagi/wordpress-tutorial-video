"""Integration tests for the FFmpeg-dependent steps.

Skipped automatically when ffmpeg/ffprobe are not installed so the rest of the
suite still runs in minimal environments.
"""
import json
import os
import shutil
import subprocess
import sys

import pytest

ROOT = os.path.join(os.path.dirname(__file__), "..")
HAS_FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))
pytestmark = pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg not installed")

SCRIPT = {
    "title": "Test Tutorial", "resolution": "640x360", "fps": 24, "voice": "af_heart",
    "scenes": [
        {"id": "01", "narration": "Open the menu.", "intent": "x",
         "actions": [{"type": "click", "target": "Menu", "selector": "x"}],
         "focus_selector": "x", "hold_after_ms": 200, "verify": {"expect_on_screen": "x"}},
        {"id": "02", "narration": "Toggle it on and save.", "intent": "y",
         "actions": [{"type": "click", "target": "Toggle", "selector": "y"}],
         "focus_selector": "y", "hold_after_ms": 200, "verify": {"expect_on_screen": "y"}},
    ],
}


def _ffprobe_dur(path):
    out = subprocess.check_output([
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", path])
    return float(out.strip())


def _make_raw_clip(path, seconds, res="640x360", fps=24):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    subprocess.run([
        "ffmpeg", "-y", "-f", "lavfi",
        "-i", f"testsrc=size={res}:rate={fps}:duration={seconds}",
        "-c:v", "libvpx", path], check=True)


def _setup_run(tmp_path, extra_cfg=None):
    run = str(tmp_path)
    cfg = {"resolution": "640x360", "fps": 24}
    cfg.update(extra_cfg or {})
    (tmp_path / "config.json").write_text(json.dumps(cfg))
    (tmp_path / "script.json").write_text(json.dumps(SCRIPT))
    # audio + durations via stub TTS
    subprocess.run([sys.executable, os.path.join(ROOT, "scripts", "tts_kokoro.py"),
                    "--run-dir", run, "--engine", "stub"], check=True)
    return run


def _probe_dims(path):
    out = subprocess.check_output([
        "ffprobe", "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=width,height", "-of", "csv=p=0", str(path)])
    w, h = out.decode().strip().split(",")[:2]
    return int(w), int(h)


def test_postprocess_pads_to_narration(tmp_path):
    run = _setup_run(tmp_path)
    durations = json.loads((tmp_path / "audio" / "durations.json").read_text())
    # raw clip deliberately SHORTER than narration -> must be padded up
    _make_raw_clip(os.path.join(run, "clips", "01.raw.webm"), seconds=0.5)
    subprocess.run([sys.executable, os.path.join(ROOT, "scripts", "postprocess_clip.py"),
                    "--run-dir", run, "--scene-id", "01"], check=True)
    final = os.path.join(run, "clips", "01.final.mp4")
    assert os.path.exists(final)
    assert _ffprobe_dur(final) >= durations["01"] - 0.15


def test_postprocess_downscales_master_and_zooms_focus(tmp_path):
    run = _setup_run(tmp_path)
    # 2x master raw (1280x720) of the 640x360 delivery resolution
    _make_raw_clip(os.path.join(run, "clips", "01.raw.webm"), seconds=2, res="1280x720")
    (tmp_path / "clips" / "01.focus.json").write_text(json.dumps(
        {"box": {"x": 500, "y": 250, "width": 100, "height": 60},
         "viewport": {"width": 640, "height": 360}, "scale": 2}))
    subprocess.run([sys.executable, os.path.join(ROOT, "scripts", "postprocess_clip.py"),
                    "--run-dir", run, "--scene-id", "01", "--zoom"], check=True)
    assert _probe_dims(os.path.join(run, "clips", "01.final.mp4")) == (640, 360)


def test_postprocess_writes_4k_variant(tmp_path):
    run = _setup_run(tmp_path, extra_cfg={"deliver_4k": True, "capture_scale": 2})
    _make_raw_clip(os.path.join(run, "clips", "01.raw.webm"), seconds=1, res="1280x720")
    subprocess.run([sys.executable, os.path.join(ROOT, "scripts", "postprocess_clip.py"),
                    "--run-dir", run, "--scene-id", "01"], check=True)
    assert (tmp_path / "clips" / "01.final.mp4").exists()
    assert _probe_dims(os.path.join(run, "clips", "01.final-4k.mp4")) == (1280, 720)


def _mk_compose_run(tmp_path):
    run = tmp_path
    (run / "clips").mkdir(exist_ok=True)
    (run / "audio").mkdir(exist_ok=True)
    (run / "script.json").write_text(json.dumps({
        "title": "T", "resolution": "320x180", "fps": 30, "voice": "af_heart",
        "scenes": [
            {"id": "01", "narration": "One.", "intent": "First step",
             "actions": [{"type": "wait", "target": "t", "text": "1"}],
             "hold_after_ms": 1, "verify": {"expect_on_screen": "x"}},
            {"id": "02", "narration": "Two.", "intent": "Second step",
             "actions": [{"type": "wait", "target": "t", "text": "1"}],
             "hold_after_ms": 1, "verify": {"expect_on_screen": "x"}}]}))
    (run / "config.json").write_text(json.dumps({"resolution": "320x180", "fps": 30}))
    for sid in ("01", "02"):
        subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i",
                        "testsrc=size=320x180:rate=30", "-t", "1",
                        "-c:v", "libx264", "-crf", "18", "-preset", "medium",
                        "-pix_fmt", "yuv420p", str(run / "clips" / f"{sid}.final.mp4")],
                       check=True, capture_output=True)
        subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i",
                        "sine=frequency=440:duration=1", "-ar", "24000", "-ac", "1",
                        str(run / "audio" / f"{sid}.wav")], check=True, capture_output=True)
    return run


def test_compose_timeline_chapters_faststart(tmp_path):
    run = _mk_compose_run(tmp_path)
    subprocess.run([sys.executable, os.path.join(ROOT, "scripts", "compose.py"),
                    "--run-dir", str(run)], check=True)
    tl = json.loads((run / "output" / "timeline.json").read_text())
    kinds = [s["kind"] for s in tl["segments"]]
    assert kinds == ["intro", "scene", "scene", "outro"]
    assert tl["segments"][1]["start"] > 0
    probe = subprocess.check_output(
        ["ffprobe", "-v", "error", "-show_chapters", "-of", "json",
         str(run / "output" / "final.mp4")], text=True)
    chapters = json.loads(probe)["chapters"]
    assert len(chapters) == 2  # one per scene, titled by intent


def test_full_compose_chain(tmp_path):
    run = _setup_run(tmp_path)
    for sid in ("01", "02"):
        _make_raw_clip(os.path.join(run, "clips", f"{sid}.raw.webm"), seconds=0.5)
        subprocess.run([sys.executable, os.path.join(ROOT, "scripts", "postprocess_clip.py"),
                        "--run-dir", run, "--scene-id", sid], check=True)
    # transcript (stub) -> captions
    subprocess.run([sys.executable, os.path.join(ROOT, "scripts", "transcribe_whisperx.py"),
                    "--run-dir", run, "--engine", "stub"], check=True)
    subprocess.run([sys.executable, os.path.join(ROOT, "scripts", "make_captions.py"),
                    "--run-dir", run], check=True)
    assert os.path.getsize(os.path.join(run, "captions.srt")) > 0
    # compose
    subprocess.run([sys.executable, os.path.join(ROOT, "scripts", "compose.py"),
                    "--run-dir", run], check=True)
    final = os.path.join(run, "output", "final.mp4")
    assert os.path.exists(final)
    # intro + 2 scenes + outro -> clearly longer than the two narrations alone
    assert _ffprobe_dur(final) > 3.0
