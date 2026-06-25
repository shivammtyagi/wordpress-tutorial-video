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
