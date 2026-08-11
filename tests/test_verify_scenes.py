import json, os, subprocess, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import verify_scenes as vs

VS = os.path.join(os.path.dirname(__file__), "..", "scripts", "verify_scenes.py")
TTS = os.path.join(os.path.dirname(__file__), "..", "scripts", "tts_kokoro.py")

def test_wer_zero_for_identical():
    assert vs.wer(["open", "the", "menu"], ["open", "the", "menu"]) == 0.0

def test_wer_counts_substitution():
    assert vs.wer(["open", "the", "menu"], ["open", "a", "menu"]) == 1 / 3

def test_norm_tokens_collapses_spelled_acronyms():
    # "U R L" (ref, spelled for TTS) vs "URL" (transcript) must agree
    assert vs.norm_tokens("Copy the U R L now") == vs.norm_tokens("Copy the URL now")

def test_norm_tokens_numbers():
    assert vs.norm_tokens("five point nine") == vs.norm_tokens("five point nine.")

def _mk_run(tmp_path):
    (tmp_path / "script.json").write_text(json.dumps({
        "title": "T", "resolution": "1920x1080", "fps": 30, "voice": "af_heart",
        "scenes": [{"id": "01", "narration": "Open the sitemap settings.", "intent": "x",
                    "actions": [{"type": "wait", "target": "t", "text": "1"}],
                    "hold_after_ms": 1, "verify": {"expect_on_screen": "y"}}]}))
    subprocess.run([sys.executable, TTS, "--run-dir", str(tmp_path), "--engine", "stub"], check=True)

def test_stub_engine_passes_and_writes_offsets(tmp_path):
    _mk_run(tmp_path)
    r = subprocess.run([sys.executable, VS, "--run-dir", str(tmp_path), "--engine", "stub"],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    rep = json.loads((tmp_path / "verify" / "audio_report.json").read_text())
    assert rep["passed"] is True and rep["scenes"][0]["wer"] == 0.0
    words = json.loads((tmp_path / "verify" / "scenes" / "01.json").read_text())["words"]
    assert words and words[0]["start"] == 0.0 and words[-1]["end"] > 0
