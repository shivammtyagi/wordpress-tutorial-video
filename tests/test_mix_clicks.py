import os
import sys
import wave

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
from mix_clicks import synth_click


def test_synth_click_writes_valid_wav(tmp_path):
    p = tmp_path / "click.wav"
    synth_click(str(p))
    with wave.open(str(p), "rb") as w:
        assert w.getframerate() == 48000
        assert w.getnchannels() == 1
        dur = w.getnframes() / w.getframerate()
    assert 0.1 < dur < 0.2


def test_synth_click_is_deterministic(tmp_path):
    a, b = tmp_path / "a.wav", tmp_path / "b.wav"
    synth_click(str(a))
    synth_click(str(b))
    assert a.read_bytes() == b.read_bytes()
