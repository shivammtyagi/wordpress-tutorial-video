"""`screen` labels: scenes on one screen must be contiguous (no screen hopping)."""
import copy
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts", "lib"))
import schema  # noqa: E402


def _scene(i, screen):
    return {
        "id": f"{i:02d}", "intent": f"scene {i}", "narration": "Some narration here.",
        "hold_after_ms": 800, "focus_selector": None, "screen": screen,
        "verify": {"expect_on_screen": "something"},
        "actions": [{"type": "goto", "target": "/wp-admin/", "selector": None, "phase": "setup"},
                    {"type": "wait", "target": "settle", "selector": None, "text": "500"}],
    }


def _script(screens):
    return {"title": "t", "resolution": "1920x1080", "fps": 30, "voice": "af_heart",
            "scenes": [_scene(i, sc) for i, sc in enumerate(screens, 1)]}


def test_contiguous_screens_pass():
    assert schema.validate_script(_script(["editor", "editor", "settings", "settings"])) == []


def test_revisited_screen_is_rejected():
    errs = schema.validate_script(_script(["editor", "settings", "editor"]))
    assert any("screen 'editor' reappears after 'settings'" in e for e in errs)


def test_scenes_without_screen_label_are_not_checked():
    assert schema.validate_script(_script([None, "settings", None])) == []


def test_screen_must_be_a_string():
    s = _script(["editor"]); s["scenes"][0]["screen"] = 3
    assert any(".screen: must be a non-empty string" in e for e in schema.validate_script(s))
