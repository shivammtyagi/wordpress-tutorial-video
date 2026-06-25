import copy
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts", "lib"))
import schema

VALID = {
    "title": "T", "resolution": "1920x1080", "fps": 30, "voice": "af_heart",
    "scenes": [{
        "id": "01", "narration": "Hi", "intent": "open",
        "actions": [{"type": "click", "target": "Menu", "selector": None, "highlight": False}],
        "focus_selector": None, "hold_after_ms": 800,
        "verify": {"expect_on_screen": "x"},
    }],
}


def test_valid_predisco():
    assert schema.validate_script(copy.deepcopy(VALID), discovered=False) == []


def test_missing_narration():
    bad = copy.deepcopy(VALID)
    del bad["scenes"][0]["narration"]
    assert any("narration" in e for e in schema.validate_script(bad, discovered=False))


def test_bad_action_type():
    bad = copy.deepcopy(VALID)
    bad["scenes"][0]["actions"][0]["type"] = "teleport"
    assert any("type" in e for e in schema.validate_script(bad, discovered=False))


def test_type_action_requires_text():
    bad = copy.deepcopy(VALID)
    bad["scenes"][0]["actions"][0] = {"type": "type", "target": "field", "selector": "x"}
    assert any("text" in e for e in schema.validate_script(bad, discovered=False))


def test_discovered_requires_selector():
    errs = schema.validate_script(copy.deepcopy(VALID), discovered=True)
    assert any("selector" in e for e in errs)
    assert any("focus_selector" in e for e in errs)


def test_discovered_valid_when_filled():
    good = copy.deepcopy(VALID)
    good["scenes"][0]["actions"][0]["selector"] = "role=menuitem[name='Menu']"
    good["scenes"][0]["focus_selector"] = "#panel"
    assert schema.validate_script(good, discovered=True) == []
