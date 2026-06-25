#!/usr/bin/env python3
"""Step 10a: transcribe final audio with WhisperX + forced alignment.

Produces verify/transcript.json with word-level timestamps used for (a) caption
generation and (b) the text-fidelity diff in the verification loop.

Engines:
  whisperx (default) — faster-whisper transcription + alignment.
  stub               — treats each scene's narration as a perfectly-aligned
                       transcript (offline tests / CI without the model).
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "lib"))
import run_dir as rd


def _load_script(run_dir):
    for name in ("script.discovered.json", "script.json"):
        p = os.path.join(run_dir, name)
        if os.path.exists(p):
            return json.load(open(p))
    raise SystemExit("transcribe: no script found in run dir")


def _stub_transcript(run_dir):
    """Build a synthetic aligned transcript from narration + audio durations."""
    script = _load_script(run_dir)
    durations = json.load(open(os.path.join(run_dir, "audio", "durations.json")))
    words, t = [], 0.0
    for scene in script["scenes"]:
        toks = scene["narration"].split()
        span = durations.get(scene["id"], len(toks) * 0.4)
        step = span / max(1, len(toks))
        for tok in toks:
            words.append({"word": tok, "start": round(t, 3), "end": round(t + step, 3),
                          "scene": scene["id"]})
            t += step
    return {"text": " ".join(w["word"] for w in words), "words": words}


def _whisperx_transcript(audio_path, device, model_name):
    import whisperx
    model = whisperx.load_model(model_name, device, compute_type="int8")
    audio = whisperx.load_audio(audio_path)
    result = model.transcribe(audio)
    align_model, meta = whisperx.load_align_model(result["language"], device)
    aligned = whisperx.align(result["segments"], align_model, meta, audio, device)
    words = [{"word": w["word"], "start": w.get("start", 0.0), "end": w.get("end", 0.0)}
             for seg in aligned["segments"] for w in seg.get("words", [])]
    return {"text": " ".join(w["word"] for w in words), "words": words}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-dir", required=True)
    ap.add_argument("--engine", choices=["whisperx", "stub"], default="whisperx")
    ap.add_argument("--audio", default=None, help="audio file (defaults to output/final.mp4)")
    ap.add_argument("--device", default="cpu")
    ap.add_argument("--model", default="base.en")
    args = ap.parse_args()

    if args.engine == "stub":
        transcript = _stub_transcript(args.run_dir)
    else:
        audio = args.audio or os.path.join(args.run_dir, "output", "final.mp4")
        transcript = _whisperx_transcript(audio, args.device, args.model)

    out = os.path.join(args.run_dir, "verify", "transcript.json")
    rd.write_json(out, transcript)
    print(f"transcribe: wrote {out} ({len(transcript['words'])} words)")


if __name__ == "__main__":
    main()
