#!/usr/bin/env python3
"""Step 5 (alternate engine): per-scene voiceover via Chatterbox (Resemble AI).

Same contract as tts_kokoro.py — writes audio/NN.wav, audio/durations.json and
audio/tts_meta.json (ref_text for the audio gate) — but synthesizes with the
Chatterbox 0.5B model: far more natural prosody than Kokoro (real question
intonation, emphasis), MIT-licensed, runs on Apple Silicon (MPS).

Differences from the Kokoro path:
  * No IPA lexicon — Chatterbox reads plain English; narration text is
    normalized with normalize.for_ref() on both the TTS and reference sides.
  * Synthesis is per sentence, joined with a fixed inter-sentence gap, which
    keeps long scenes stable and gives deterministic pause lengths.
  * Pace/expressiveness knobs come from config.json:
      "tts_exaggeration": 0.4,   # 0..1 emotion intensity
      "tts_cfg": 0.35,           # generation-guidance weight
      "tts_gap_s": 0.30,         # silence between sentences
      "tts_target_wpm": 185,     # spoken pace ceiling; see below
      "tts_max_attempts": 2,     # regenerations before pitch-safe stretch
      "tts_voice_prompt": null   # optional path to a reference WAV to clone
  * Pace control: Chatterbox tends to read briskly (~200-225 wpm) and cfg_weight
    barely moves it. Scenes measuring above tts_target_wpm are regenerated up to
    tts_max_attempts times; a scene still over the target is slowed with a
    pitch-preserving ffmpeg atempo stretch (never below 0.85x).
  * Generation is stochastic: the audio gate (verify_scenes.py) remains the
    arbiter; regenerate failing scenes with --force --scene-id NN.

Run inside .venv-cbx (bootstrap: uv venv .venv-cbx --python 3.11 &&
uv pip install --python .venv-cbx/bin/python chatterbox-tts "setuptools<81").
"""
import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import wave

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "lib"))
import run_dir as rd
import normalize as norm

_SENT = re.compile(r'(?<=[.!?])\s+(?=[A-Z"\'])')


def _load_script(run_dir):
    for name in ("script.discovered.json", "script.json"):
        p = os.path.join(run_dir, name)
        if os.path.exists(p):
            return json.load(open(p))
    raise SystemExit("tts_chatterbox: no script.json / script.discovered.json in run dir")


def _write_wav(path, samples_int16, sample_rate):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(samples_int16)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-dir", required=True)
    ap.add_argument("--scene-id", default=None, help="only (re)generate this scene")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    script = _load_script(args.run_dir)
    cfg = {}
    cfg_path = os.path.join(args.run_dir, "config.json")
    if os.path.exists(cfg_path):
        cfg = json.load(open(cfg_path))
    exaggeration = float(cfg.get("tts_exaggeration", 0.4))
    cfg_weight = float(cfg.get("tts_cfg", 0.35))
    gap_s = float(cfg.get("tts_gap_s", 0.30))
    target_wpm = float(cfg.get("tts_target_wpm", 185))
    max_attempts = int(cfg.get("tts_max_attempts", 2))
    voice_prompt = cfg.get("tts_voice_prompt") or None

    audio_dir = os.path.join(args.run_dir, "audio")
    cache_dir = os.path.join(audio_dir, "cache")
    os.makedirs(cache_dir, exist_ok=True)

    dur_path = os.path.join(audio_dir, "durations.json")
    meta_path = os.path.join(audio_dir, "tts_meta.json")
    durations = json.load(open(dur_path)) if os.path.exists(dur_path) else {}
    meta = json.load(open(meta_path)) if os.path.exists(meta_path) else {}

    model = None
    sr = 24000
    import numpy as np

    def synth(text):
        nonlocal model, sr
        if model is None:
            import torch
            from chatterbox.tts import ChatterboxTTS
            device = "mps" if torch.backends.mps.is_available() else "cpu"
            model = ChatterboxTTS.from_pretrained(device=device)
            sr = model.sr
        kwargs = dict(exaggeration=exaggeration, cfg_weight=cfg_weight)
        if voice_prompt:
            kwargs["audio_prompt_path"] = voice_prompt
        wav = model.generate(text, **kwargs)
        return wav.squeeze(0).cpu().numpy()

    for scene in script["scenes"]:
        sid = scene["id"]
        if args.scene_id and sid != args.scene_id:
            continue
        tts_text = norm.for_ref(scene["narration"], {})
        ref_text = tts_text
        key = hashlib.sha256(
            f"chatterbox|{voice_prompt}|{exaggeration}|{cfg_weight}|{gap_s}|{tts_text}"
            .encode()).hexdigest()
        cached = os.path.join(cache_dir, f"{key}.wav")
        out = os.path.join(audio_dir, f"{sid}.wav")

        if not os.path.exists(cached) or args.force:
            words = len(tts_text.split())
            best = None
            for attempt in range(max(1, max_attempts)):
                sentences = [s.strip() for s in _SENT.split(tts_text) if s.strip()]
                pieces = []
                for si, sent in enumerate(sentences):
                    audio = synth(sent)
                    pieces.append(audio)
                    if si < len(sentences) - 1:
                        pieces.append(np.zeros(int(gap_s * sr), dtype=audio.dtype))
                full = np.concatenate(pieces) if pieces else np.zeros(sr)
                wpm = words / (len(full) / sr) * 60
                if best is None or wpm < best[1]:
                    best = (full, wpm)
                if wpm <= target_wpm * 1.02:
                    break
                print(f"tts: scene {sid} attempt {attempt + 1} at {wpm:.0f} wpm "
                      f"(target {target_wpm:.0f}) — retrying" if attempt + 1 < max_attempts
                      else f"tts: scene {sid} still {wpm:.0f} wpm after "
                           f"{attempt + 1} attempts — will stretch")
            full, wpm = best
            pcm = (np.clip(full, -1, 1) * 32767).astype("<i2").tobytes()
            _write_wav(cached, pcm, sr)
            if wpm > target_wpm * 1.02:
                factor = max(0.85, target_wpm / wpm)
                import subprocess
                tmp = cached + ".stretch.wav"
                subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", cached,
                                "-af", f"atempo={factor:.3f}", tmp], check=True)
                os.replace(tmp, cached)
        shutil.copyfile(cached, out)
        with wave.open(out, "rb") as w:
            secs = w.getnframes() / w.getframerate()
        durations[sid] = round(secs, 3)
        meta[sid] = {"hash": key, "ref_text": ref_text, "tts_text": tts_text}
        words = len(tts_text.split())
        print(f"tts: scene {sid} -> {out} ({secs:.2f}s, {words / secs * 60:.0f} wpm)")

    rd.write_json(dur_path, durations)
    rd.write_json(meta_path, meta)
    print(f"tts: wrote {dur_path} + tts_meta.json")


if __name__ == "__main__":
    main()
