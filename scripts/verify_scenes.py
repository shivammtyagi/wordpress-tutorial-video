#!/usr/bin/env python3
"""Step 5b — the audio gate. Transcribe each scene WAV, compute normalized WER against
the intended narration (ref_text from tts_meta.json), and write per-scene word offsets.

Runs BEFORE recording: catches TTS misreads before any recording time is spent, and the
word offsets drive recorder cues (narrate-then-act) and caption timing.

Engines:
  whisperx (default) — faster-whisper transcription + wav2vec2 forced alignment.
  faster             — faster-whisper word_timestamps (fallback when alignment fails on MPS/M-series).
  stub               — synthesizes a perfect transcript from ref_text + duration (tests/CI).

Two additions on top of the WER check:

  * Engine word timings. When the TTS engine wrote audio/words/NN.json (exact
    per-word times from its own alignment — the ElevenLabs path does), those
    become the scene's "words" for recorder cues and captions, after a sanity
    comparison against the transcription. The transcribed words are kept as
    "asr_words".

  * Pronunciation check. The WER pass biases the recognizer toward product
    names (a glossary), which is what makes it robust — and also what makes it
    blind to a mispronounced name: "trusio" is written down as "TruSEO". For
    every term with a known correct hearing (config `tts_spoken` respellings
    and `pronunciation_checks`), scenes that contain the term get a second,
    UNBIASED transcription, and the term must be heard as one of its accepted
    forms. A miss fails the scene like a WER miss does.
"""
import argparse
import json
import os
import re
import sys
import wave

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "lib"))
import run_dir as rd

_LETTER_RUN = re.compile(r"\b(?:[a-z] )+[a-z]\b")


def _fallback_normalize(text):
    text = text.lower()
    text = re.sub(r"[^a-z0-9' ]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def norm_tokens(text):
    try:
        from whisper.normalizers import EnglishTextNormalizer
        text = EnglishTextNormalizer()(text)
    except Exception:
        text = _fallback_normalize(text)
    text = _fallback_normalize(text)
    text = _LETTER_RUN.sub(lambda m: m.group(0).replace(" ", ""), text)
    return text.split()


def wer(ref, hyp):
    d = [[0] * (len(hyp) + 1) for _ in range(len(ref) + 1)]
    for i in range(len(ref) + 1):
        d[i][0] = i
    for j in range(len(hyp) + 1):
        d[0][j] = j
    for i in range(1, len(ref) + 1):
        for j in range(1, len(hyp) + 1):
            d[i][j] = min(d[i - 1][j] + 1, d[i][j - 1] + 1,
                          d[i - 1][j - 1] + (ref[i - 1] != hyp[j - 1]))
    return d[len(ref)][len(hyp)] / max(1, len(ref))


def _squash(text):
    """Lowercase alphanumerics only — 'True SEO' / 'true-seo' / 'TrueSEO' all match."""
    return re.sub(r"[^a-z0-9]+", "", text.lower())


def pronunciation_terms(cfg):
    """{term: [accepted unbiased hearings]} from tts_spoken + pronunciation_checks."""
    terms = {}
    for term, say in (cfg.get("tts_spoken") or {}).items():
        terms.setdefault(term, [term])
        if say and not say.startswith("/"):
            terms[term].append(say)
    for term, heard in (cfg.get("pronunciation_checks") or {}).items():
        terms.setdefault(term, [term])
        terms[term].extend([heard] if isinstance(heard, str) else list(heard))
    return terms


def check_pronunciations(ref_text, heard_text, terms):
    """Each term in ref_text must be heard (unbiased) as an accepted form, as
    many times as it is written. Returns [{term, needed, heard, ok}]."""
    heard = _squash(heard_text)
    results = []
    for term, accepted in terms.items():
        needed = len(re.findall(rf"(?<!\w){re.escape(term)}(?!\w)", ref_text))
        if not needed:
            continue
        forms = sorted({_squash(a) for a in accepted if _squash(a)}, key=len, reverse=True)
        found, rest = 0, heard
        for form in forms:  # longest first, consuming matches so forms never double-count
            found += rest.count(form)
            rest = rest.replace(form, " ")
        results.append({"term": term, "needed": needed, "heard": found, "ok": found >= needed})
    return results


def timing_agreement(engine_words, asr_words):
    """Median |start difference| in seconds between engine and transcribed
    words that match by spelling, and the share of engine words matched."""
    import difflib
    a = [_squash(w["word"]) for w in engine_words]
    b = [_squash(w["word"]) for w in asr_words]
    diffs = []
    for m in difflib.SequenceMatcher(a=a, b=b, autojunk=False).get_matching_blocks():
        for k in range(m.size):
            diffs.append(abs(engine_words[m.a + k]["start"] - asr_words[m.b + k]["start"]))
    if not diffs:
        return None, 0.0
    diffs.sort()
    return diffs[len(diffs) // 2], len(diffs) / max(1, len(a))


def _engine_words(run_dir, sid, expect_hash):
    p = os.path.join(run_dir, "audio", "words", f"{sid}.json")
    if not os.path.exists(p):
        return None
    d = json.load(open(p))
    if d.get("hash") != expect_hash or not d.get("words"):
        return None  # stale: written for different audio
    return d["words"]


def _wav_seconds(path):
    with wave.open(path, "rb") as w:
        return w.getnframes() / w.getframerate()


def _stub_words(ref_text, seconds):
    toks = ref_text.split()
    step = seconds / max(1, len(toks))
    return [{"word": t, "start": round(i * step, 3), "end": round((i + 1) * step, 3)}
            for i, t in enumerate(toks)]


def _whisperx_words(wav, model_name, glossary):
    import whisperx
    device = "cpu"
    model = whisperx.load_model(model_name, device, compute_type="int8",
                                asr_options={"initial_prompt": glossary} if glossary else None)
    audio = whisperx.load_audio(wav)
    result = model.transcribe(audio)
    align_model, meta = whisperx.load_align_model(result["language"], device)
    aligned = whisperx.align(result["segments"], align_model, meta, audio, device)
    return [{"word": w.get("word", "").strip(), "start": float(w.get("start", 0.0)),
             "end": float(w.get("end", 0.0))}
            for seg in aligned["segments"] for w in seg.get("words", [])
            if w.get("word", "").strip()]


_FASTER = {}


def _faster_words(wav, model_name, glossary):
    from faster_whisper import WhisperModel
    if model_name not in _FASTER:
        _FASTER[model_name] = WhisperModel(model_name, device="cpu", compute_type="int8")
    model = _FASTER[model_name]
    segments, _ = model.transcribe(wav, word_timestamps=True, initial_prompt=glossary or None)
    return [{"word": w.word.strip(), "start": float(w.start), "end": float(w.end)}
            for seg in segments for w in (seg.words or []) if w.word.strip()]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-dir", required=True)
    ap.add_argument("--engine", choices=["whisperx", "faster", "stub"], default="whisperx")
    ap.add_argument("--model", default=None,
                    help="ASR model (default from config asr_model or small.en)")
    ap.add_argument("--scene-id", default=None, help="only this scene (auto-fix loop)")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    cfg = {}
    cfg_path = os.path.join(args.run_dir, "config.json")
    if os.path.exists(cfg_path):
        cfg = json.load(open(cfg_path))
    model_name = args.model or cfg.get("asr_model", "small.en")
    threshold = float(cfg.get("wer_threshold", 0.15))

    meta_path = os.path.join(args.run_dir, "audio", "tts_meta.json")
    if not os.path.exists(meta_path):
        raise SystemExit("verify_scenes: audio/tts_meta.json missing — run tts first")
    meta = json.load(open(meta_path))
    terms = pronunciation_terms(cfg)
    glossary = cfg.get("glossary") or "WordPress, wp-admin, plugin, sitemap, " + ", ".join(
        dict.fromkeys(list((cfg.get("lexicon") or {}).keys()) + list(terms.keys())))

    scenes_dir = os.path.join(args.run_dir, "verify", "scenes")
    os.makedirs(scenes_dir, exist_ok=True)
    report = {"scenes": [], "passed": True, "threshold": threshold}

    for sid, m in sorted(meta.items()):
        if args.scene_id and sid != args.scene_id:
            continue
        wav = os.path.join(args.run_dir, "audio", f"{sid}.wav")
        if not os.path.exists(wav):
            raise SystemExit(f"verify_scenes: missing {wav}")
        if args.engine == "stub":
            words = _stub_words(m["ref_text"], _wav_seconds(wav))
        elif args.engine == "faster":
            words = _faster_words(wav, model_name, glossary)
        else:
            try:
                words = _whisperx_words(wav, model_name, glossary)
            except Exception as e:
                print(f"verify_scenes: whisperx alignment failed ({e}); "
                      f"falling back to faster-whisper word timestamps", file=sys.stderr)
                words = _faster_words(wav, model_name, glossary)

        hyp = norm_tokens(" ".join(w["word"] for w in words))
        ref = norm_tokens(m["ref_text"])
        w = round(wer(ref, hyp), 4)
        ok = w <= threshold
        result = {"words": words, "wer": w, "ok": ok, "timing": "asr"}
        entry = {"id": sid, "wer": w, "ok": ok}

        # engine word timings (exact) replace the transcribed ones when they agree
        eng = _engine_words(args.run_dir, sid, m.get("hash"))
        if eng:
            median, share = timing_agreement(eng, words)
            if args.engine == "stub" or (median is not None and median <= 0.4 and share >= 0.5):
                result.update({"words": eng, "asr_words": words, "timing": "engine"})
            else:
                print(f"verify_scenes: {sid} engine word timings disagree with the transcription "
                      f"(median {median}s, {share:.0%} matched) — using transcribed timings",
                      file=sys.stderr)
        entry["timing"] = result["timing"]

        # pronunciation: an unbiased second listen for terms with a known hearing
        in_scene = {t: a for t, a in terms.items()
                    if re.search(rf"(?<!\w){re.escape(t)}(?!\w)", m["ref_text"])}
        if in_scene:
            if args.engine == "stub":
                heard_text = m["ref_text"]
            else:
                heard_text = " ".join(x["word"] for x in _faster_words(wav, model_name, None))
            checks = check_pronunciations(m["ref_text"], heard_text, in_scene)
            pron_ok = all(c["ok"] for c in checks)
            result.update({"pronunciation": checks, "heard_unbiased": heard_text})
            entry["pronunciation_ok"] = pron_ok
            if not pron_ok:
                ok = False
                result["ok"] = entry["ok"] = False
                bad = ", ".join(c["term"] for c in checks if not c["ok"])
                print(f"verify_scenes: {sid} pronunciation FAIL ({bad}) — heard: {heard_text}")

        rd.write_json(os.path.join(scenes_dir, f"{sid}.json"), result)
        report["scenes"].append(entry)
        report["passed"] = report["passed"] and ok
        print(f"verify_scenes: {sid} wer={w:.3f} timing={result['timing']} {'ok' if ok else 'FAIL'}")

    rd.write_json(os.path.join(args.run_dir, "verify", "audio_report.json"), report)
    if not report["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
