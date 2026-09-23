import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import postprocess_clip as pp


def _write(tmp_path, events):
    p = tmp_path / "01.events.json"
    p.write_text(json.dumps({"events": events}))
    return str(p)


def test_missing_log_returns_none(tmp_path):
    assert pp._action_end_s(str(tmp_path / "nope.json")) is None


def test_empty_log_returns_none(tmp_path):
    assert pp._action_end_s(_write(tmp_path, [])) is None


def test_typing_counts_its_full_duration(tmp_path):
    # a click at 1s, then 50 chars typed at 60ms from 2s -> ends at 5s
    path = _write(tmp_path, [
        {"kind": "click", "t": 1000},
        {"kind": "type", "t": 2000, "chars": 50, "delay": 60},
    ])
    assert pp._action_end_s(path) == 5.0


def test_measured_type_end_beats_the_estimate(tmp_path):
    # estimate says 2s + 50*60ms = 5s, but typing actually finished at 6.3s
    path = _write(tmp_path, [
        {"kind": "type", "t": 2000, "chars": 50, "delay": 60},
        {"kind": "type_end", "t": 6300},
    ])
    assert pp._action_end_s(path) == 6.3


def test_last_click_wins_over_earlier_typing(tmp_path):
    path = _write(tmp_path, [
        {"kind": "type", "t": 1000, "chars": 10, "delay": 60},
        {"kind": "press", "t": 2500, "key": "Enter"},
        {"kind": "click", "t": 4200},
    ])
    assert pp._action_end_s(path) == 4.2


def test_recorder_actions_end_marker_wins(tmp_path):
    p = tmp_path / "01.events.json"
    p.write_text(json.dumps({"events": [{"kind": "click", "t": 1000}], "actions_end_ms": 7800}))
    assert pp._action_end_s(str(p)) == 7.8
