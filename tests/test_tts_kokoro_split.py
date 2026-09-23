"""split_units(): sentence/paragraph units for Kokoro natural pacing mode."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
from tts_kokoro import split_units  # noqa: E402


def test_sentences_within_paragraph_get_sentence_gaps_and_last_has_none():
    units = split_units("The first sentence sits right here. The second sentence follows it closely. The third sentence closes the thought.")
    assert [k for _, k in units] == ["sentence", "sentence", None]


def test_newline_marks_a_paragraph_gap():
    units = split_units("Welcome to the product overview.\nIn this video we look at the score it gives.")
    assert units == [
        ("Welcome to the product overview.", "paragraph"),
        ("In this video we look at the score it gives.", None),
    ]


def test_short_sentence_merges_into_predecessor():
    units = split_units("Think of it as a guide, not a target. Let's try it. Now open the tab and look around.")
    assert units[0][0] == "Think of it as a guide, not a target. Let's try it."
    assert units[1][0] == "Now open the tab and look around."


def test_short_opening_sentence_pulls_next_one_in():
    units = split_units("Let's try it.\nI'll head over to the Keywords tab and add a focus keyword.")
    # too short to stand alone, but it opens a paragraph: it merges with nothing before it,
    # so the paragraph gap after it is kept and it is voiced as its own unit
    assert units[0] == ("Let's try it.", "paragraph")


def test_ipa_links_survive_splitting():
    text = "[TruSEO](/tɹuˌɛsˌiˈoʊ/) works in the block editor. It reads from the visual editor."
    units = split_units(text)
    assert units[0][0].startswith("[TruSEO](/tɹuˌɛsˌiˈoʊ/)")
    assert len(units) == 2


def test_blank_lines_and_whitespace_are_ignored():
    units = split_units("\n\n  One idea per scene here.  \n\n\n Another idea follows after that.\n")
    assert [u for u, _ in units] == ["One idea per scene here.", "Another idea follows after that."]
    assert [k for _, k in units] == ["paragraph", None]
