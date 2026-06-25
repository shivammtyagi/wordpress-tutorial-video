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


def _setup_run(tmp_path):
    run = str(tmp_path)
    (tmp_path / "config.json").write_text(json.dumps(
        {"resolution": "640x360", "fps": 24}))
    (tmp_path / "script.json").write_text(json.dumps(SCRIPT))
    # audio + durations via stub TTS
    subprocess.run([sys.executable, os.path.join(ROOT, "scripts", "tts_kokoro.py"),
                    "--run-dir", run, "--engine", "stub"], check=True)
    return run


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
