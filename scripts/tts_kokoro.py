#!/usr/bin/env python3
"""Step 5: generate per-scene voiceover and capture exact durations.

Audio is generated BEFORE video so each scene's clip can be paced to its
narration length (the anti-drift principle). Narration is normalized first
(scripts/lib/normalize.py): lexicon IPA for product terms, spoken URLs/versions/
tech tokens. Writes audio/NN.wav, audio/durations.json (scene id -> seconds),
and audio/tts_meta.json (per-scene content hash + the ref_text the audio gate
diffs against). Synthesis is cached in audio/cache/<hash>.wav so narration edits
only re-synthesize changed scenes.

Engines:
  kokoro (default) — Kokoro-82M via the `kokoro` package (Apache-2.0, CPU/MPS OK).
  stub             — silent WAVs ~0.4s per word (pure stdlib), for offline tests/CI.
"""
import argparse
import hashlib
import json
import os
import shutil
import sys
import wave

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "lib"))
import run_dir as rd
import normalize as norm

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
    """~0.4s per word of silence, min 1s — deterministic, dependency-free."""
    words = max(1, len(text.split()))
    seconds = max(1.0, words * 0.4)
    n = int(seconds * SAMPLE_RATE)
    return b"\x00\x00" * n, seconds


def _kokoro_synth(pipeline, text, voice, speed):
    import numpy as np
    audio_chunks = []
    for _, _, audio in pipeline(text, voice=voice, speed=speed):
        audio_chunks.append(audio)
    audio = np.concatenate(audio_chunks) if audio_chunks else np.zeros(SAMPLE_RATE)
    pcm = (np.clip(audio, -1, 1) * 32767).astype("<i2").tobytes()
    return pcm, len(audio) / SAMPLE_RATE


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-dir", required=True)
    ap.add_argument("--voice", default=None, help="override Kokoro voice id")
    ap.add_argument("--speed", type=float, default=None, help="override speaking speed")
    ap.add_argument("--engine", choices=["kokoro", "stub"], default="kokoro")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    script = _load_script(args.run_dir)
    cfg = {}
    cfg_path = os.path.join(args.run_dir, "config.json")
    if os.path.exists(cfg_path):
        cfg = json.load(open(cfg_path))
    voice = args.voice or script.get("voice", cfg.get("voice", "af_heart"))
    speed = args.speed if args.speed is not None else float(cfg.get("speed", 0.9))
    lexicon = norm.load_lexicon(cfg.get("lexicon") or {})

    audio_dir = os.path.join(args.run_dir, "audio")
    cache_dir = os.path.join(audio_dir, "cache")
    os.makedirs(cache_dir, exist_ok=True)

    pipeline = None
    durations, meta = {}, {}
    for scene in script["scenes"]:
        sid = scene["id"]
        tts_text = norm.for_tts(scene["narration"], lexicon)
        ref_text = norm.for_ref(scene["narration"], lexicon)
        key = hashlib.sha256(f"{args.engine}|{voice}|{speed}|{tts_text}".encode()).hexdigest()
        cached = os.path.join(cache_dir, f"{key}.wav")
        out = os.path.join(audio_dir, f"{sid}.wav")

        if not os.path.exists(cached) or args.force:
            if args.engine == "stub":
                pcm, _ = _stub_audio(tts_text)
            else:
                if pipeline is None:
                    from kokoro import KPipeline
                    pipeline = KPipeline(lang_code="a")  # American English
                pcm, _ = _kokoro_synth(pipeline, tts_text, voice, speed)
            _write_wav(cached, pcm)
        shutil.copyfile(cached, out)
        with wave.open(out, "rb") as w:
            secs = w.getnframes() / w.getframerate()
        durations[sid] = round(secs, 3)
        meta[sid] = {"hash": key, "ref_text": ref_text, "tts_text": tts_text}
        print(f"tts: scene {sid} -> {out} ({secs:.2f}s)")

    rd.write_json(os.path.join(audio_dir, "durations.json"), durations)
    rd.write_json(os.path.join(audio_dir, "tts_meta.json"), meta)
    print(f"tts: wrote {audio_dir}/durations.json + tts_meta.json")


if __name__ == "__main__":
    main()
