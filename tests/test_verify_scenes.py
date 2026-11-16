import json, os, subprocess, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import verify_scenes as vs

VS = os.path.join(os.path.dirname(__file__), "..", "scripts", "verify_scenes.py")
TTS = os.path.join(os.path.dirname(__file__), "..", "scripts", "tts_kokoro.py")

def test_wer_zero_for_identical():
    assert vs.wer(["open", "the", "menu"], ["open", "the", "menu"]) == 0.0

def test_wer_counts_substitution():
    assert vs.wer(["open", "the", "menu"], ["open", "a", "menu"]) == 1 / 3

def test_norm_tokens_collapses_spelled_acronyms():
    # "U R L" (ref, spelled for TTS) vs "URL" (transcript) must agree
    assert vs.norm_tokens("Copy the U R L now") == vs.norm_tokens("Copy the URL now")

def test_norm_tokens_numbers():
    assert vs.norm_tokens("five point nine") == vs.norm_tokens("five point nine.")

def _mk_run(tmp_path):
    (tmp_path / "script.json").write_text(json.dumps({
        "title": "T", "resolution": "1920x1080", "fps": 30, "voice": "af_heart",
        "scenes": [{"id": "01", "narration": "Open the sitemap settings.", "intent": "x",
                    "actions": [{"type": "wait", "target": "t", "text": "1"}],
                    "hold_after_ms": 1, "verify": {"expect_on_screen": "y"}}]}))
    subprocess.run([sys.executable, TTS, "--run-dir", str(tmp_path), "--engine", "stub"], check=True)

def test_stub_engine_passes_and_writes_offsets(tmp_path):
    _mk_run(tmp_path)
    r = subprocess.run([sys.executable, VS, "--run-dir", str(tmp_path), "--engine", "stub"],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    rep = json.loads((tmp_path / "verify" / "audio_report.json").read_text())
    assert rep["passed"] is True and rep["scenes"][0]["wer"] == 0.0
    words = json.loads((tmp_path / "verify" / "scenes" / "01.json").read_text())["words"]
    assert words and words[0]["start"] == 0.0 and words[-1]["end"] > 0


# ---- pronunciation check (unbiased second listen) -------------------------

TERMS = {"TruSEO": ["TruSEO", "True SEO"], "AIOSEO": ["AIOSEO"]}

def test_pronunciation_terms_come_from_respellings_and_explicit_checks():
    cfg = {"tts_spoken": {"TruSEO": "True SEO", "Foo": "/fu/"},
           "pronunciation_checks": {"AIOSEO": ["A I O SEO"], "TruSEO": "Tru S E O"},
           "lexicon": {"WooCommerce": "wu"}}
    terms = vs.pronunciation_terms(cfg)
    assert terms["TruSEO"] == ["TruSEO", "True SEO", "Tru S E O"]
    assert terms["AIOSEO"] == ["AIOSEO", "A I O SEO"]
    assert terms["Foo"] == ["Foo"]            # an IPA override adds no accepted hearing
    assert "WooCommerce" not in terms         # lexicon-only terms are not checked

def test_correct_hearing_passes_in_any_spacing_or_case():
    for heard in ("Now you know what true SEO measures.", "what TrueSEO measures", "what Tru-SEO measures"):
        r = vs.check_pronunciations("Now you know what TruSEO measures.", heard, TERMS)
        assert r == [{"term": "TruSEO", "needed": 1, "heard": 1, "ok": True}], heard

def test_mispronounced_brand_name_fails():
    for heard in ("Now you know what TrueCO measures.", "what TruCO measures", "what true CO measures"):
        r = vs.check_pronunciations("Now you know what TruSEO measures.", heard, TERMS)
        assert r[0]["ok"] is False and r[0]["heard"] == 0, heard

def test_every_occurrence_must_be_heard():
    ref = "TruSEO scores it. Then TruSEO updates as AIOSEO saves."
    ok = vs.check_pronunciations(ref, "True SEO scores it. Then true SEO updates as AIO-SEO saves.", TERMS)
    assert [c["ok"] for c in ok] == [True, True] and ok[0]["needed"] == 2
    bad = vs.check_pronunciations(ref, "True SEO scores it. Then TrueCO updates as AIO SEO saves.", TERMS)
    assert [(c["term"], c["ok"]) for c in bad] == [("TruSEO", False), ("AIOSEO", True)]

def test_terms_absent_from_the_scene_are_not_checked():
    assert vs.check_pronunciations("Open the sitemap settings.", "Open the sitemap settings.", TERMS) == []

def test_overlapping_accepted_forms_never_double_count():
    r = vs.check_pronunciations("Use TruSEO twice: TruSEO.", "Use true SEO twice.",
                                {"TruSEO": ["TruSEO", "True SEO", "SEO"]})
    assert r[0]["heard"] == 1 and r[0]["ok"] is False


# ---- engine word timings ---------------------------------------------------

def test_timing_agreement_measures_start_differences():
    eng = [{"word": "Open", "start": 0.0, "end": 0.3}, {"word": "TruSEO,", "start": 0.4, "end": 0.9},
           {"word": "now.", "start": 1.0, "end": 1.3}]
    asr = [{"word": "Open", "start": 0.05, "end": 0.3}, {"word": "true", "start": 0.4, "end": 0.6},
           {"word": "SEO", "start": 0.6, "end": 0.9}, {"word": "now", "start": 1.1, "end": 1.3}]
    median, share = vs.timing_agreement(eng, asr)
    assert abs(median - 0.1) < 1e-6 and abs(share - 2 / 3) < 1e-6
    assert vs.timing_agreement(eng, []) == (None, 0.0)

def _engine_words_file(tmp_path, hash_):
    words = [{"word": "Open", "start": 0.31, "end": 0.5}, {"word": "the", "start": 0.5, "end": 0.6},
             {"word": "sitemap", "start": 0.6, "end": 1.0}, {"word": "settings.", "start": 1.0, "end": 1.4}]
    d = tmp_path / "audio" / "words"
    d.mkdir(parents=True, exist_ok=True)
    (d / "01.json").write_text(json.dumps({"hash": hash_, "source": "elevenlabs", "words": words}))
    return words

def test_engine_words_become_the_scene_timings(tmp_path):
    _mk_run(tmp_path)
    meta = json.loads((tmp_path / "audio" / "tts_meta.json").read_text())
    words = _engine_words_file(tmp_path, meta["01"]["hash"])
    subprocess.run([sys.executable, VS, "--run-dir", str(tmp_path), "--engine", "stub"], check=True)
    scene = json.loads((tmp_path / "verify" / "scenes" / "01.json").read_text())
    assert scene["timing"] == "engine" and scene["words"] == words
    assert scene["asr_words"][0]["start"] == 0.0
    rep = json.loads((tmp_path / "verify" / "audio_report.json").read_text())
    assert rep["scenes"][0]["timing"] == "engine"

def test_stale_engine_words_are_ignored(tmp_path):
    _mk_run(tmp_path)
    _engine_words_file(tmp_path, "hash-of-older-audio")
    subprocess.run([sys.executable, VS, "--run-dir", str(tmp_path), "--engine", "stub"], check=True)
    scene = json.loads((tmp_path / "verify" / "scenes" / "01.json").read_text())
    assert scene["timing"] == "asr" and "asr_words" not in scene

def test_stub_gate_reports_pronunciation_for_scenes_with_checked_terms(tmp_path):
    _mk_run(tmp_path)
    (tmp_path / "config.json").write_text(json.dumps({"tts_spoken": {"sitemap": "site map"}}))
    subprocess.run([sys.executable, VS, "--run-dir", str(tmp_path), "--engine", "stub"], check=True)
    scene = json.loads((tmp_path / "verify" / "scenes" / "01.json").read_text())
    assert scene["pronunciation"] == [{"term": "sitemap", "needed": 1, "heard": 1, "ok": True}]
