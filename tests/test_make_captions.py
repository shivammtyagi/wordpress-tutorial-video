import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
from make_captions import words_to_srt, _ts


def test_ts_format():
    assert _ts(0) == "00:00:00,000"
    assert _ts(3661.5) == "01:01:01,500"


def test_basic_srt():
    words = [{"word": "Hello", "start": 0.0, "end": 0.5},
             {"word": "world", "start": 0.5, "end": 1.0}]
    srt = words_to_srt(words, max_chars=40)
    assert "1\n00:00:00,000 --> 00:00:01,000\nHello world" in srt


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
