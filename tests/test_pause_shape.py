"""pause_shape: word timings + deliberate pauses from a character alignment."""
import math
import os
import sys
from array import array

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts", "lib"))
import pause_shape as ps  # noqa: E402

SR = 8000


def _linear(text, per_char=0.05):
    """Fake alignment: every char takes per_char seconds."""
    starts = [i * per_char for i in range(len(text))]
    return starts, [s + per_char for s in starts]


def _tone(seconds, amp=8000):
    return array("h", [int(amp * math.sin(2 * math.pi * 220 * i / SR)) for i in range(int(seconds * SR))])


def _silence(seconds):
    return array("h", bytes(2 * int(seconds * SR)))


def _quiet_runs(samples, min_s=0.05):
    """[(start_s, length_s)] of digital-silence runs."""
    runs, start = [], None
    for i, v in enumerate(samples):
        if abs(v) < 40:
            start = i if start is None else start
        elif start is not None:
            if (i - start) / SR >= min_s:
                runs.append((start / SR, (i - start) / SR))
            start = None
    if start is not None and (len(samples) - start) / SR >= min_s:
        runs.append((start / SR, (len(samples) - start) / SR))
    return runs


# ---- text -----------------------------------------------------------------

def test_build_spoken_replaces_terms_and_maps_chars_back():
    spoken, cmap = ps.build_spoken("Open TruSEO now.", {"TruSEO": "True SEO"})
    assert spoken == "Open True SEO now."
    assert cmap[0] == ("p", 0)
    assert cmap[5][0] == "t" and cmap[5][1:3] == (5, 11)
    assert cmap[-1] == ("p", len("Open TruSEO now.") - 1)


def test_build_spoken_longest_term_wins_and_respects_word_boundaries():
    spoken, _ = ps.build_spoken("AIOSEO Pro and AIOSEOX.", {"AIOSEO": "A I O SEO", "AIOSEO Pro": "A I O SEO Pro"})
    assert spoken == "A I O SEO Pro and AIOSEOX."


def test_word_timings_keep_the_script_spelling():
    ref = "Open TruSEO, then save."
    spoken, cmap = ps.build_spoken(ref, {"TruSEO": "/tɹuˌɛsˌiˈoʊ/"})
    starts, ends = _linear(spoken)
    words = ps.word_timings(ref, cmap, starts, ends)
    assert [w["word"] for w in words] == ["Open", "TruSEO,", "then", "save."]
    assert all(a["end"] <= b["start"] + 1e-6 for a, b in zip(words, words[1:]))
    # the replaced term spans the whole IPA string plus its trailing comma
    ipa_len = len("/tɹuˌɛsˌiˈoʊ/")
    assert abs(words[1]["start"] - 5 * 0.05) < 1e-6
    assert abs(words[1]["end"] - (5 + ipa_len + 1) * 0.05) < 1e-6


def test_word_timings_split_a_multi_word_term_in_order():
    ref = "Welcome to All in One SEO today."
    spoken, cmap = ps.build_spoken(ref, {"All in One SEO": "All-in-One S E O"})
    starts, ends = _linear(spoken)
    words = ps.word_timings(ref, cmap, starts, ends)
    assert [w["word"] for w in words] == ref.split()
    assert all(a["start"] < b["start"] for a, b in zip(words, words[1:]))


def test_word_timings_reject_a_mismatched_alignment():
    spoken, cmap = ps.build_spoken("Hello there.", {})
    try:
        ps.word_timings("Hello there.", cmap, [0.0], [0.1])
    except ValueError:
        return
    raise AssertionError("expected ValueError")


def test_boundaries_kinds_and_abbreviations():
    text = "The first sentence sits right here. The second one follows it closely.\nA new thought starts on this line, e.g. this one."
    starts, ends = _linear(text)
    b = ps.find_boundaries(text, starts, ends)
    assert [x["kind"] for x in b] == ["sentence", "paragraph"]
    assert b[0]["t_prev"] < b[0]["t_next"]


def test_boundaries_bare_newline_is_a_paragraph():
    text = "Let's open the Basics tab\nThe checks are sorted into two groups."
    starts, ends = _linear(text)
    assert [x["kind"] for x in ps.find_boundaries(text, starts, ends)] == ["paragraph"]


def test_boundaries_short_sentence_keeps_its_natural_pause_but_paragraphs_always_count():
    text = "Think of it as a guide, not a target. Let's try it. Now open the tab and look around."
    starts, ends = _linear(text)
    assert len(ps.find_boundaries(text, starts, ends, min_words=5)) == 1
    assert len(ps.find_boundaries(text, starts, ends, min_words=0)) == 2
    text2 = "That's it!\nYou now know how to read the score it gives you."
    s2, e2 = _linear(text2)
    assert [x["kind"] for x in ps.find_boundaries(text2, s2, e2, min_words=5)] == ["paragraph"]


# ---- audio ----------------------------------------------------------------

def _two_sentences(gap_s):
    audio = _silence(0.2) + _tone(1.0) + _silence(gap_s) + _tone(1.0) + _silence(0.3)
    boundary = {"kind": "sentence", "t_prev": 1.2, "t_next": 1.2 + gap_s}
    return audio, boundary


def test_short_pause_is_lengthened_to_the_minimum():
    audio, b = _two_sentences(0.2)
    out, edits = ps.shape_pauses(audio, SR, [b], {"sentence": 0.65}, lead_s=0.3, tail_s=0.5)
    runs = _quiet_runs(out)
    assert len(runs) == 3
    assert abs(runs[0][1] - (0.3 + 0.04)) < 0.03   # lead + the breath margin kept at the edge
    assert abs(runs[1][1] - 0.65) < 0.03           # the sentence pause
    assert abs(runs[2][1] - (0.5 + 0.04)) < 0.03   # tail + margin
    assert abs(len(out) / SR - (0.3 + 1.0 + 0.65 + 1.0 + 0.5)) < 0.1
    assert any(e[1] > 0.4 for e in edits)


def test_a_pause_already_long_enough_is_kept():
    audio, b = _two_sentences(0.9)
    out, _ = ps.shape_pauses(audio, SR, [b], {"sentence": 0.65}, lead_s=0.3, tail_s=0.5)
    assert abs(_quiet_runs(out)[1][1] - 0.9) < 0.03


def test_an_overlong_pause_is_capped():
    audio, b = _two_sentences(2.4)
    out, edits = ps.shape_pauses(audio, SR, [b], {"sentence": 0.65}, max_pause_s=1.5)
    assert abs(_quiet_runs(out)[1][1] - 1.5) < 0.04
    assert any(e[1] < -0.8 for e in edits)


def test_run_together_sentences_still_get_a_pause():
    audio = _tone(1.0) + _tone(1.0)  # no silence at all at the boundary
    out, _ = ps.shape_pauses(audio, SR, [{"kind": "paragraph", "t_prev": 0.98, "t_next": 1.02}],
                             {"paragraph": 1.0}, lead_s=0.0, tail_s=0.0)
    runs = _quiet_runs(out, min_s=0.5)
    assert len(runs) == 1 and abs(runs[0][1] - 1.0) < 0.05


def test_zero_gap_leaves_that_boundary_kind_alone():
    audio, b = _two_sentences(0.2)
    out, _ = ps.shape_pauses(audio, SR, [b], {"sentence": 0, "paragraph": 1.0})
    assert abs(_quiet_runs(out)[1][1] - 0.2) < 0.03


def test_word_times_follow_the_edits():
    audio, b = _two_sentences(0.2)
    out, edits = ps.shape_pauses(audio, SR, [b], {"sentence": 0.65}, lead_s=0.3, tail_s=0.5)
    words = [{"word": "one", "start": 0.2, "end": 1.2}, {"word": "two", "start": 1.4, "end": 2.4}]
    moved = ps.shift_words(words, edits, total_s=len(out) / SR)
    runs = _quiet_runs(out)
    # word one now starts where the lead ends; word two where the lengthened pause ends
    assert abs(moved[0]["start"] - runs[0][1]) < 0.06
    assert abs(moved[1]["start"] - (runs[1][0] + runs[1][1])) < 0.06
    assert moved[1]["end"] <= len(out) / SR


def test_empty_audio_is_returned_untouched():
    out, edits = ps.shape_pauses(b"", SR, [], {"sentence": 0.6})
    assert len(out) == 0 and edits == []


def test_a_word_never_spans_an_inserted_pause():
    # "it!" has its end timestamp inside the silence that gets lengthened
    words = [{"word": "it!", "start": 0.5, "end": 1.3}, {"word": "You", "start": 1.4, "end": 1.6}]
    moved = ps.shift_words(words, [(1.25, 0.8)])
    assert moved[0] == {"word": "it!", "start": 0.5, "end": 1.25}
    assert moved[1] == {"word": "You", "start": 2.2, "end": 2.4}


def test_a_word_ends_where_its_trailing_silence_begins():
    # the engine stretched "tab." to 1.3s although speech stopped at 1.0s; the pause was lengthened at 1.25s
    words = [{"word": "tab.", "start": 0.5, "end": 1.3}, {"word": "The", "start": 1.5, "end": 1.7}]
    moved = ps.shift_words(words, [(1.25, 0.8, 1.0)])
    assert moved[0]["end"] == 1.0 and moved[1]["start"] == 2.3
    # a pause that needed no change still marks the end of speech
    assert ps.shift_words(words, [(1.25, 0.0, 1.0)])[0]["end"] == 1.0


def test_shape_pauses_reports_where_each_silence_begins():
    audio, b = _two_sentences(0.9)                      # long enough already
    _, edits = ps.shape_pauses(audio, SR, [b], {"sentence": 0.65})
    marks = [e for e in edits if e[2] is not None]
    assert len(marks) == 1 and marks[0][1] == 0.0 and abs(marks[0][2] - 1.2) < 0.02
