#!/usr/bin/env python3
"""Step 9a: build the SRT caption file.

v2 (default): caption TEXT comes from the scene script (ground truth — technical
terms are never misheard), TIMING comes from the audio gate's per-scene word
alignments (verify/scenes/NN.json) offset by the compose timeline
(output/timeline.json). Mapping is a monotonic sequence match; unmatched script
words interpolate between matched neighbors.

Fallback (v1 behavior): when alignments or the timeline are missing, read
verify/transcript.json (whole-video transcription) directly.

Cue segmentation is phrase-aware: cues break at sentence ends and (when long
enough) at commas; a soft length break backtracks so no cue ends on a dangling
connective ("...and a" / "...of the"); orphan fragments shorter than 14 chars
merge into the previous cue. Cues get a +0.35s readability tail and are
de-overlapped by 0.06s.
"""
import argparse
import difflib
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "lib"))
import run_dir as rd


def _ts(seconds):
    if seconds < 0:
        seconds = 0
    ms = int(round(seconds * 1000))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _norm(t):
    return "".join(c for c in t.lower() if c.isalnum() or c == "'")


def align_script_words(script_tokens, words):
    """Map script tokens onto transcript word timings (monotonic match)."""
    hyp = [_norm(w["word"]) for w in words]
    ref = [_norm(t) for t in script_tokens]
    sm = difflib.SequenceMatcher(a=ref, b=hyp, autojunk=False)
    mapped = [None] * len(ref)
    for a, b, n in (m for m in sm.get_matching_blocks() if m.size):
        for k in range(n):
            mapped[a + k] = (words[b + k]["start"], words[b + k]["end"])
    out = []
    for i, tok in enumerate(script_tokens):
        if mapped[i] is None:
            prev_end = next((mapped[j][1] for j in range(i - 1, -1, -1) if mapped[j]), 0.0)
            nxt_start = next((mapped[j][0] for j in range(i + 1, len(ref)) if mapped[j]),
                             prev_end + 0.3)
            mapped[i] = (prev_end, max(prev_end, nxt_start))
        out.append({"word": tok, "start": mapped[i][0], "end": mapped[i][1]})
    return out


def _alignment_words(run_dir):
    """Absolute-time script words from per-scene alignments + compose timeline."""
    tl_path = os.path.join(run_dir, "output", "timeline.json")
    script_path = next((os.path.join(run_dir, n) for n in
                        ("script.discovered.json", "script.json")
                        if os.path.exists(os.path.join(run_dir, n))), None)
    if not (os.path.exists(tl_path) and script_path):
        return None
    timeline = json.load(open(tl_path))
    script = json.load(open(script_path))
    scenes = {s["id"]: s for s in script["scenes"]}
    all_words = []
    for seg in timeline["segments"]:
        if seg["kind"] != "scene":
            continue
        sw_path = os.path.join(run_dir, "verify", "scenes", f"{seg['id']}.json")
        if not os.path.exists(sw_path):
            return None  # incomplete alignments → caller falls back
        words = json.load(open(sw_path))["words"]
        toks = scenes[seg["id"]]["narration"].split()
        for w in align_script_words(toks, words):
            all_words.append({"word": w["word"],
                              "start": round(seg["start"] + w["start"], 3),
                              "end": round(seg["start"] + w["end"], 3)})
    return all_words


CONNECTIVES = {"a", "an", "the", "and", "or", "of", "in", "on", "to", "with",
               "your", "for", "is", "are", "it", "at", "by", "into", "how",
               "so", "you", "we", "they", "i", "i'm", "i'll"}

MERGE_ORPHAN_CHARS = 14   # trailing fragments shorter than this merge backward
MERGE_MAX_CHARS = 60      # ...if the merged cue stays under this


def words_to_srt(words, max_chars=46, max_secs=6.0):
    """words: list of {word, start, end}. Returns phrase-aware SRT text."""
    def text_of(chunk):
        return " ".join(w["word"].strip() for w in chunk)

    cues, cur = [], []
    for w in words:
        token = w["word"].strip()
        if not token:
            continue
        if cur and (len(text_of(cur + [w])) > max_chars
                    or w["end"] - cur[0]["start"] > max_secs):
            # soft break: never end a cue on a dangling connective
            cut = len(cur)
            while cut > 1 and cur[cut - 1]["word"].strip().lower().strip(".,!?") in CONNECTIVES:
                cut -= 1
            cues.append(cur[:cut])
            cur = cur[cut:]
        cur.append(w)
        if token[-1] in ".!?":
            cues.append(cur)
            cur = []
        elif token.endswith(",") and len(text_of(cur)) >= 26:
            cues.append(cur)
            cur = []
    if cur:
        cues.append(cur)

    # merge sentence-final orphan fragments ("way.", "results.") into the
    # previous cue; soft-break chunks are left alone.
    merged = []
    for chunk in cues:
        if (merged and chunk[-1]["word"].strip()[-1:] in ".!?"
                and len(text_of(chunk)) < MERGE_ORPHAN_CHARS
                and chunk[0]["start"] - merged[-1][-1]["end"] < 0.5
                and len(text_of(merged[-1])) + len(text_of(chunk)) + 1 <= MERGE_MAX_CHARS):
            merged[-1] = merged[-1] + chunk
        else:
            merged.append(chunk)

    out = []
    for i, cue in enumerate(merged, 1):
        start = cue[0]["start"]
        end = cue[-1]["end"] + 0.35  # readability tail
        if i < len(merged):
            end = min(end, merged[i][0]["start"] - 0.06)  # de-overlap
        out.append(f"{i}\n{_ts(start)} --> {_ts(max(end, start + 0.2))}\n{text_of(cue)}")
    return "\n\n".join(out) + ("\n" if out else "")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-dir", required=True)
    ap.add_argument("--max-chars", type=int, default=46)
    args = ap.parse_args()

    words = _alignment_words(args.run_dir)
    if words is None:
        transcript_path = os.path.join(args.run_dir, "verify", "transcript.json")
        if not os.path.exists(transcript_path):
            raise SystemExit("make_captions: no per-scene alignments and no "
                             "verify/transcript.json — run verify_scenes.py or "
                             "transcribe_whisperx.py first")
        words = json.load(open(transcript_path)).get("words", [])
    srt = words_to_srt(words, max_chars=args.max_chars)
    rd.atomic_write(os.path.join(args.run_dir, "captions.srt"), srt)
    print(f"make_captions: wrote captions.srt ({srt.count(chr(10) + chr(10)) + 1} cues)")


if __name__ == "__main__":
    main()
