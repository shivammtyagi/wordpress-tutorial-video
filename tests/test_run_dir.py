import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts", "lib"))
import run_dir as rd


def test_mark_and_is_done(tmp_path):
    d = str(tmp_path)
    (tmp_path / "a.txt").write_text("hello")
    inputs = [str(tmp_path / "a.txt")]
    assert rd.is_done(d, "step2", inputs) is False
    rd.mark_done(d, "step2", inputs)
    assert rd.is_done(d, "step2", inputs) is True
    (tmp_path / "a.txt").write_text("changed")
    assert rd.is_done(d, "step2", inputs) is False  # input hash changed


def test_atomic_write_roundtrip(tmp_path):
    p = str(tmp_path / "nested" / "out.json")
    rd.write_json(p, {"k": 1})
    assert os.path.exists(p)
    import json
    assert json.load(open(p)) == {"k": 1}


def test_slug_for():
    assert rd.slug_for("https://example.com/docs/xml-sitemaps/") == "xml-sitemaps"
    assert rd.slug_for("https://example.com/") == "video"
