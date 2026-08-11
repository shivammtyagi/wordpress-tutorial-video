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


def _valid_scene():
    return {
        "id": "01", "narration": "Open the menu.", "intent": "Open menu",
        "actions": [{"type": "click", "target": "Menu", "selector": "role=link[name='Menu']",
                     "highlight": False}],
        "focus_selector": "#main", "hold_after_ms": 500,
        "verify": {"expect_on_screen": "The menu page"},
    }


def _valid_script(scene):
    return {"title": "T", "resolution": "1920x1080", "fps": 30, "voice": "af_heart",
            "scenes": [scene]}


def test_phase_accepts_setup_and_recorded():
    sc = _valid_scene()
    sc["actions"][0]["phase"] = "setup"
    assert schema.validate_script(_valid_script(sc), discovered=True) == []
    sc["actions"][0]["phase"] = "recorded"
    assert schema.validate_script(_valid_script(sc), discovered=True) == []


def test_phase_rejects_unknown_value():
    sc = _valid_scene()
    sc["actions"][0]["phase"] = "hidden"
    errs = schema.validate_script(_valid_script(sc), discovered=True)
    assert any("phase" in e for e in errs)


def test_cue_must_be_nonempty_string_when_present():
    sc = _valid_scene()
    sc["actions"][0]["cue"] = ""
    errs = schema.validate_script(_valid_script(sc), discovered=False)
    assert any("cue" in e for e in errs)


def test_goto_and_wait_need_no_selector_after_discovery():
    sc = _valid_scene()
    sc["actions"] = [
        {"type": "goto", "target": "/wp-admin/admin.php?page=x", "selector": None,
         "highlight": False, "phase": "setup"},
        {"type": "wait", "target": "settle", "text": "500", "selector": None,
         "highlight": False},
        {"type": "click", "target": "Menu", "selector": "role=link[name='Menu']",
         "highlight": True},
    ]
    assert schema.validate_script(_valid_script(sc), discovered=True) == []
