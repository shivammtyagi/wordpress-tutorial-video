#!/usr/bin/env python3
"""Step 5b — the audio gate. Transcribe each scene WAV, compute normalized WER against
the intended narration (ref_text from tts_meta.json), and write per-scene word offsets.

Runs BEFORE recording: catches TTS misreads before any recording time is spent, and the
word offsets drive recorder cues (narrate-then-act) and caption timing.

Engines:
  whisperx (default) — faster-whisper transcription + wav2vec2 forced alignment.
  faster             — faster-whisper word_timestamps (fallback when alignment fails on MPS/M-series).
  stub               — synthesizes a perfect transcript from ref_text + duration (tests/CI).
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


def _faster_words(wav, model_name, glossary):
    from faster_whisper import WhisperModel
    model = WhisperModel(model_name, device="cpu", compute_type="int8")
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
    glossary = cfg.get("glossary") or "WordPress, wp-admin, plugin, sitemap, " + ", ".join(
        (cfg.get("lexicon") or {}).keys())

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
        rd.write_json(os.path.join(scenes_dir, f"{sid}.json"),
                      {"words": words, "wer": w, "ok": ok})
        report["scenes"].append({"id": sid, "wer": w, "ok": ok})
        report["passed"] = report["passed"] and ok
        print(f"verify_scenes: {sid} wer={w:.3f} {'ok' if ok else 'FAIL'}")

    rd.write_json(os.path.join(args.run_dir, "verify", "audio_report.json"), report)
    if not report["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
