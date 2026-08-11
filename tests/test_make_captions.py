import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
from make_captions import words_to_srt, _ts

CAPS = os.path.join(os.path.dirname(__file__), "..", "scripts", "make_captions.py")


def test_ts_format():
    assert _ts(0) == "00:00:00,000"
    assert _ts(3661.5) == "01:01:01,500"


def test_basic_srt():
    words = [{"word": "Hello", "start": 0.0, "end": 0.5},
             {"word": "world", "start": 0.5, "end": 1.0}]
    srt = words_to_srt(words, max_chars=40)
    # cue gets a +0.3s readability tail
    assert "1\n00:00:00,000 --> 00:00:01,300\nHello world" in srt


def test_wraps_on_max_chars():
    words = [{"word": "word", "start": i * 0.5, "end": i * 0.5 + 0.5} for i in range(10)]
    srt = words_to_srt(words, max_chars=12)
    # multiple cues produced
    assert "2\n" in srt


def test_wraps_on_max_secs():
    words = [{"word": "a", "start": 0.0, "end": 5.0},
             {"word": "b", "start": 5.0, "end": 6.0}]
    srt = words_to_srt(words, max_chars=100, max_secs=3.5)
    assert "2\n" in srt


def test_consecutive_cues_do_not_overlap():
    words = [{"word": "w", "start": i * 0.4, "end": i * 0.4 + 0.4} for i in range(12)]
    srt = words_to_srt(words, max_chars=8)
    blocks = [b.splitlines() for b in srt.strip().split("\n\n")]
    times = [(b[1].split(" --> ")[0], b[1].split(" --> ")[1]) for b in blocks]
    for (s1, e1), (s2, e2) in zip(times, times[1:]):
        assert e1 <= s2, f"cue overlap: {e1} > {s2}"


def test_align_script_words_uses_script_spelling():
    import make_captions as mc
    words = [{"word": "open", "start": 0.0, "end": 0.3},
             {"word": "ayo", "start": 0.3, "end": 0.9},      # misheard "AIOSEO"
             {"word": "settings", "start": 0.9, "end": 1.4}]
    out = mc.align_script_words(["Open", "AIOSEO", "settings"], words)
    assert [w["word"] for w in out] == ["Open", "AIOSEO", "settings"]
    assert out[1]["start"] >= 0.3 and out[1]["end"] <= 0.9 + 0.01


def test_alignment_mode_builds_absolute_cues(tmp_path):
    run = tmp_path
    (run / "verify" / "scenes").mkdir(parents=True)
    (run / "output").mkdir()
    (run / "script.json").write_text(json.dumps({
        "title": "T", "resolution": "1920x1080", "fps": 30, "voice": "af_heart",
        "scenes": [{"id": "01", "narration": "Open the settings.", "intent": "x",
                    "actions": [{"type": "wait", "target": "t", "text": "1"}],
                    "hold_after_ms": 1, "verify": {"expect_on_screen": "y"}}]}))
    (run / "verify" / "scenes" / "01.json").write_text(json.dumps({
        "words": [{"word": "Open", "start": 0.0, "end": 0.4},
                  {"word": "the", "start": 0.4, "end": 0.6},
                  {"word": "settings.", "start": 0.6, "end": 1.2}],
        "wer": 0.0, "ok": True}))
    (run / "output" / "timeline.json").write_text(json.dumps({
        "segments": [{"kind": "intro", "id": None, "intent": None, "start": 0.0, "end": 2.0},
                     {"kind": "scene", "id": "01", "intent": "x", "start": 2.0, "end": 4.0}]}))
    subprocess.run([sys.executable, CAPS, "--run-dir", str(run)], check=True)
    srt = (run / "captions.srt").read_text()
    assert "00:00:02,000 -->" in srt      # scene words offset by intro (2.0s)
    assert "Open the settings." in srt
