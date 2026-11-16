import json
import os
import subprocess
import sys
import wave

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import comma_pauses as cp


def W(word, start, end):
    return {"word": word, "start": start, "end": end}


def test_comma_words_include_oxford_comma():
    assert cp.comma_words("Tabs for Issues, Warnings, and Good.") == ["issues", "warnings"]


def test_short_comma_gap_is_padded_at_its_midpoint():
    words = [W("issues,", 1.0, 1.4), W("warnings,", 1.48, 2.0), W("and", 2.4, 2.5)]
    ins = cp.plan_insertions("Issues, Warnings, and Good.", words, 0.35)
    # "issues," gap 0.08 -> pad 0.27 at 1.44; "warnings," gap 0.40 -> already long enough
    assert len(ins) == 1
    at, extra, lo, hi = ins[0]
    assert abs(at - 1.44) < 1e-9 and abs(extra - 0.27) < 1e-9
    assert lo < 1.4 and hi > 1.48 and hi < 2.0  # stays inside the Issues/Warnings gap


def test_words_without_commas_are_ignored():
    words = [W("hello", 0.0, 0.3), W("world", 0.31, 0.6)]
    assert cp.plan_insertions("Hello world.", words, 0.35) == []


def test_shift_times_moves_only_what_follows_the_pad():
    words = [W("issues,", 1.0, 1.4), W("warnings,", 1.48, 2.0), W("and", 2.4, 2.5)]
    out = cp.shift_times(words, [(1.44, 0.27)])
    assert (out[0]["start"], out[0]["end"]) == (1.0, 1.4)       # before the pad: untouched
    assert (out[1]["start"], out[1]["end"]) == (1.75, 2.27)     # after it: +0.27
    assert out[2]["start"] == 2.67
    assert words[1]["start"] == 1.48                            # input not mutated


def test_quietest_point_finds_the_real_gap():
    np = pytest.importorskip("numpy")
    sr = 1000
    audio = np.ones(1000) * 0.5          # 1s of "speech"
    audio[600:650] = 0.0                 # real pause at 0.60–0.65s
    at = cp.quietest_point(audio, sr, 0.45, 0.95)
    assert 0.60 <= at <= 0.65


def test_already_padded_detects_inserted_zeros():
    np = pytest.importorskip("numpy")
    sr = 1000
    a = np.ones(1000) * 0.3
    assert not cp.already_padded(a, sr, 0.2, 0.8)
    a[400:450] = 0.0
    assert cp.already_padded(a, sr, 0.2, 0.8)


def test_frame_rms_flags_mid_word_points():
    np = pytest.importorskip("numpy")
    sr = 1000
    a = np.ones(1000) * 0.08
    a[500:560] = 0.0
    assert cp.frame_rms(a, sr, 0.53) < cp.SILENT_RMS < cp.frame_rms(a, sr, 0.2)


def test_end_to_end_pads_the_wav_and_keeps_timings_in_step(tmp_path):
    """Real file round trip: a tone, a 0.08s gap after the comma, a tone."""
    np = pytest.importorskip("numpy")
    sr = 16000
    tone = (0.3 * np.sin(2 * np.pi * 220 * np.arange(int(0.5 * sr)) / sr))
    gap = np.zeros(int(0.08 * sr)) + 1e-4      # near-silent, but not exact zeros
    x = np.concatenate([tone, gap, tone])
    run = tmp_path / "run"
    (run / "audio" / "words").mkdir(parents=True)
    (run / "verify" / "scenes").mkdir(parents=True)
    with wave.open(str(run / "audio" / "01.wav"), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(sr)
        w.writeframes(np.round(x * 32767).astype("<i2").tobytes())
    words = [W("First,", 0.0, 0.5), W("second.", 0.58, 1.08)]
    (run / "config.json").write_text("{}")
    (run / "script.json").write_text(json.dumps({"scenes": [{"id": "01", "narration": "First, second."}]}))
    (run / "audio" / "durations.json").write_text(json.dumps({"01": 1.08}))
    (run / "verify" / "scenes" / "01.json").write_text(json.dumps({"words": words}))
    (run / "audio" / "words" / "01.json").write_text(json.dumps({"hash": "h", "source": "elevenlabs", "words": words}))

    script = os.path.join(os.path.dirname(__file__), "..", "scripts", "comma_pauses.py")
    for _ in range(2):   # second run must be a no-op
        subprocess.run([sys.executable, script, "--run-dir", str(run)], check=True)

    with wave.open(str(run / "audio" / "01.wav"), "rb") as w:
        new_len = w.getnframes() / w.getframerate()
    assert abs(new_len - (1.08 + 0.27)) < 0.01
    assert abs(json.loads((run / "audio" / "durations.json").read_text())["01"] - new_len) < 0.01
    eng = json.loads((run / "audio" / "words" / "01.json").read_text())
    assert eng["hash"] == "h" and eng["words"][0]["end"] == 0.5
    assert abs(eng["words"][1]["start"] - 0.85) < 0.01
