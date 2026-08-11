import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts", "lib"))
import normalize as N

LEX = {"AIOSEO": "ˌeɪˌaɪˌoʊˌɛsˌiˈoʊ"}

def test_versions_are_spoken():
    out = N.for_tts("Update to v5.9.3 now.", {})
    assert "version five point nine point three" in out

def test_plain_numbers_are_left_for_misaki():
    # misaki handles bare numbers; we only expand versions/URLs/tech tokens
    assert N.for_tts("There are 3 options.", {}) == "There are 3 options."

def test_url_is_spoken():
    out = N.for_tts("Visit https://aioseo.com/docs/ for help.", {})
    assert "https" not in out
    assert "aioseo dot com slash docs" in out

def test_wp_admin_token():
    assert "W P admin" in N.for_tts("Open wp-admin to begin.", {})

def test_file_extension_spelled():
    assert "dot P H P" in N.for_tts("Edit functions.php carefully.", {})

def test_lexicon_applies_ipa_for_tts_only():
    tts = N.for_tts("AIOSEO makes SEO easy.", LEX)
    ref = N.for_ref("AIOSEO makes SEO easy.", LEX)
    assert "[AIOSEO](/ˌeɪˌaɪˌoʊˌɛsˌiˈoʊ/)" in tts
    assert "AIOSEO" in ref and "(/" not in ref

def test_load_lexicon_merges_overrides(tmp_path):
    lex = N.load_lexicon({"MyPlugin": "maɪplʌɡɪn"})
    assert "MyPlugin" in lex and "WordPress" in lex
