import json
import os
import subprocess
import sys
import wave

ROOT = os.path.join(os.path.dirname(__file__), "..")

SCRIPT = {
    "title": "T", "resolution": "1920x1080", "fps": 30, "voice": "af_heart",
    "scenes": [
        {"id": "01", "narration": "Open the SEO menu and click Sitemaps.",
         "intent": "x", "actions": [{"type": "click", "target": "Menu", "selector": "x"}],
         "focus_selector": "x", "hold_after_ms": 800, "verify": {"expect_on_screen": "x"}},
        {"id": "02", "narration": "Toggle enable sitemap on and save.",
         "intent": "x", "actions": [{"type": "click", "target": "Toggle", "selector": "y"}],
         "focus_selector": "y", "hold_after_ms": 800, "verify": {"expect_on_screen": "y"}},
    ],
}


def test_tts_stub_produces_wavs_and_durations(tmp_path):
    (tmp_path / "script.json").write_text(json.dumps(SCRIPT))
    subprocess.run(
        [sys.executable, os.path.join(ROOT, "scripts", "tts_kokoro.py"),
         "--run-dir", str(tmp_path), "--engine", "stub"],
        check=True,
    )
    audio = tmp_path / "audio"
    assert (audio / "01.wav").exists() and (audio / "02.wav").exists()

    durations = json.loads((audio / "durations.json").read_text())
    assert set(durations) == {"01", "02"}
    assert all(isinstance(v, (int, float)) and v > 0 for v in durations.values())

    # WAV is valid and its frame count matches the reported duration
    with wave.open(str(audio / "01.wav")) as w:
        secs = w.getnframes() / w.getframerate()
    assert abs(secs - durations["01"]) < 0.05


TTS = os.path.join(ROOT, "scripts", "tts_kokoro.py")


def test_stub_writes_meta_and_uses_cache(tmp_path):
    run = tmp_path
    (run / "script.json").write_text(json.dumps({
        "title": "T", "resolution": "1920x1080", "fps": 30, "voice": "af_heart",
        "scenes": [{"id": "01", "narration": "Open wp-admin now.", "intent": "x",
                    "actions": [{"type": "wait", "target": "t", "text": "1"}],
                    "hold_after_ms": 1, "verify": {"expect_on_screen": "y"}}]}))
    subprocess.run([sys.executable, TTS, "--run-dir", str(run), "--engine", "stub"], check=True)
    meta = json.loads((run / "audio" / "tts_meta.json").read_text())
    assert "01" in meta and "W P admin" in meta["01"]["ref_text"]
    cache = list((run / "audio" / "cache").glob("*.wav"))
    assert len(cache) == 1
    first_mtime = (run / "audio" / "01.wav").stat().st_mtime
    subprocess.run([sys.executable, TTS, "--run-dir", str(run), "--engine", "stub"], check=True)
    assert (run / "audio" / "01.wav").stat().st_mtime >= first_mtime  # re-copied from cache
    assert len(list((run / "audio" / "cache").glob("*.wav"))) == 1
