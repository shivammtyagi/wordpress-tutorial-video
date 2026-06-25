#!/usr/bin/env python3
"""Step 5: generate per-scene voiceover and capture exact durations.

Audio is generated BEFORE video so each scene's clip can be paced to its
narration length (the anti-drift principle). Writes audio/NN.wav per scene and
audio/durations.json mapping scene id -> seconds.

Engines:
  kokoro (default) — Kokoro-82M via the `kokoro` package (Apache-2.0, CPU OK).
  stub             — silent WAVs (pure stdlib), for offline tests/CI.
"""
import argparse
import json
import os
import sys
import wave

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "lib"))
import run_dir as rd

SAMPLE_RATE = 24000


def _load_script(run_dir):
    for name in ("script.discovered.json", "script.json"):
        p = os.path.join(run_dir, name)
        if os.path.exists(p):
            return json.load(open(p))
    raise SystemExit("tts_kokoro: no script.json / script.discovered.json in run dir")


def _write_wav(path, samples_int16, sample_rate=SAMPLE_RATE):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(samples_int16)


def _stub_audio(text):
    """~0.06s per word of silence, min 1s — deterministic, dependency-free."""
    words = max(1, len(text.split()))
    seconds = max(1.0, words * 0.4)
    n = int(seconds * SAMPLE_RATE)
    return b"\x00\x00" * n, seconds


def _kokoro_synth(pipeline, text, voice):
    import numpy as np
    audio_chunks = []
    for _, _, audio in pipeline(text, voice=voice):
        audio_chunks.append(audio)
    audio = np.concatenate(audio_chunks) if audio_chunks else np.zeros(SAMPLE_RATE)
    pcm = (np.clip(audio, -1, 1) * 32767).astype("<i2").tobytes()
    return pcm, len(audio) / SAMPLE_RATE


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-dir", required=True)
    ap.add_argument("--voice", default=None, help="override Kokoro voice id")
    ap.add_argument("--engine", choices=["kokoro", "stub"], default="kokoro")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    script = _load_script(args.run_dir)
    voice = args.voice or script.get("voice", "af_heart")
    audio_dir = os.path.join(args.run_dir, "audio")

    pipeline = None
    if args.engine == "kokoro":
        from kokoro import KPipeline
        pipeline = KPipeline(lang_code="a")  # American English

    durations = {}
    for scene in script["scenes"]:
        sid = scene["id"]
        out = os.path.join(audio_dir, f"{sid}.wav")
        text = scene["narration"]
        if args.engine == "stub":
            pcm, secs = _stub_audio(text)
        else:
            pcm, secs = _kokoro_synth(pipeline, text, voice)
        _write_wav(out, pcm)
        durations[sid] = round(secs, 3)
        print(f"tts: scene {sid} -> {out} ({secs:.2f}s)")

    rd.write_json(os.path.join(audio_dir, "durations.json"), durations)
    print(f"tts: wrote {audio_dir}/durations.json")


if __name__ == "__main__":
    main()
