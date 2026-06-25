import json
import os
import subprocess
import sys

ROOT = os.path.join(os.path.dirname(__file__), "..")
DRY = os.path.join(ROOT, "scripts", "dry_run.py")

GOOD_SCRIPT = {
    "title": "T", "resolution": "1920x1080", "fps": 30, "voice": "af_heart",
    "scenes": [{"id": "01", "narration": "Hi", "intent": "open",
                "actions": [{"type": "click", "target": "Menu", "selector": None}],
                "focus_selector": None, "hold_after_ms": 800,
                "verify": {"expect_on_screen": "x"}}],
}


def test_dry_run_ready(tmp_path):
    (tmp_path / "config.json").write_text(json.dumps({"doc_url": "https://x/docs/y"}))
    (tmp_path / "script.json").write_text(json.dumps(GOOD_SCRIPT))
    r = subprocess.run([sys.executable, DRY, "--run-dir", str(tmp_path)],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "READY" in r.stdout


def test_dry_run_flags_missing_doc_url(tmp_path):
    (tmp_path / "config.json").write_text(json.dumps({}))
    r = subprocess.run([sys.executable, DRY, "--run-dir", str(tmp_path)],
                       capture_output=True, text=True)
    assert r.returncode == 1
    assert "doc_url" in r.stdout


def test_dry_run_flags_bad_script(tmp_path):
    (tmp_path / "config.json").write_text(json.dumps({"doc_url": "https://x/docs/y"}))
    bad = json.loads(json.dumps(GOOD_SCRIPT))
    del bad["scenes"][0]["narration"]
    (tmp_path / "script.json").write_text(json.dumps(bad))
    r = subprocess.run([sys.executable, DRY, "--run-dir", str(tmp_path)],
                       capture_output=True, text=True)
    assert r.returncode == 1
    assert "narration" in r.stdout
