import os
import sys
import wave

import pytest

np = pytest.importorskip("numpy")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
from trim_audio import trim_wav


def _write(path, segments, sr=24000):
    """segments: list of (seconds, loud?) tuples."""
    parts = []
    for secs, loud in segments:
        n = int(secs * sr)
        if loud:
            t = np.arange(n) / sr
            parts.append((np.sin(2 * np.pi * 440 * t) * 12000).astype("<i2"))
        else:
            parts.append(np.zeros(n, dtype="<i2"))
    data = np.concatenate(parts)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(data.tobytes())


def test_trims_lead_and_tail(tmp_path):
    p = tmp_path / "01.wav"
    _write(p, [(1.0, False), (2.0, True), (1.5, False)])
    old, new, _ = trim_wav(str(p))
    assert old == pytest.approx(4.5, abs=0.05)
    assert new < 2.5  # lead+tail mostly gone, small keeps remain


def test_compresses_long_internal_pause(tmp_path):
    p = tmp_path / "02.wav"
    _write(p, [(1.0, True), (1.5, False), (1.0, True)])
    old, new, compressed = trim_wav(str(p))
    assert compressed == 1
    assert new < old - 0.8  # 1.5s pause squeezed toward 0.45s


def test_short_pause_untouched(tmp_path):
    p = tmp_path / "03.wav"
    _write(p, [(1.0, True), (0.4, False), (1.0, True)])
    _, _, compressed = trim_wav(str(p))
    assert compressed == 0
