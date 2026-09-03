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
      "tts_sentence_wpm": 172,   # per-SENTENCE articulated pace ceiling
      "tts_max_attempts": 3,     # redraws per fast sentence before stretching it
      "tts_target_wpm": 185,     # whole-scene backstop ceiling
      "tts_voice_prompt": null   # optional path to a reference WAV to clone
  * Pace control is per SENTENCE, not per scene. Chatterbox is stochastic per
    generation and reads briskly (~200-260 wpm on some draws); a scene-average
    gate hides a 260 wpm sentence behind a slow neighbor — the exact sentence a
    reviewer hears as "rushed". Each sentence draw is edge-silence-trimmed (so
    random trailing silence cannot dilute the measurement), measured, redrawn
    up to tts_max_attempts times if over its threshold, then pitch-safe
    atempo-stretched (floor 0.85x) if still hot. Short sentences get a relaxed
    threshold (thr = sentence_wpm * (1 + 0.06 * max(0, 6 - words))) — brief
    interjections naturally measure fast. A whole-scene stretch to
    tts_target_wpm remains as the final backstop.
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
    sentence_wpm = float(cfg.get("tts_sentence_wpm", 172))
    max_attempts = int(cfg.get("tts_max_attempts", 3))
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

    def trim_edges(audio, lead_keep=0.05, tail_keep=0.10, thresh=0.0076):
        """Cut lead/tail silence from one draw so wpm measures speech, not air."""
        win = max(1, int(0.02 * sr))
        n = (len(audio) // win) * win
        if n == 0:
            return audio
        env = np.abs(audio[:n]).reshape(-1, win).max(axis=1)
        loud = env > thresh
        if not loud.any():
            return audio
        first = int(np.argmax(loud))
        last = len(loud) - 1 - int(np.argmax(loud[::-1]))
        start = max(0, first * win - int(lead_keep * sr))
        end = min(len(audio), (last + 1) * win + int(tail_keep * sr))
        return audio[start:end]

    def stretch(audio, factor):
        """Pitch-preserving slow-down of one float32 buffer via ffmpeg atempo."""
        import subprocess
        import tempfile
        pcm = (np.clip(audio, -1, 1) * 32767).astype("<i2").tobytes()
        with tempfile.TemporaryDirectory() as td:
            a, b = os.path.join(td, "a.wav"), os.path.join(td, "b.wav")
            _write_wav(a, pcm, sr)
            subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", a,
                            "-af", f"atempo={factor:.3f}", b], check=True)
            with wave.open(b, "rb") as w:
                out = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2")
        return out.astype(np.float32) / 32767.0

    STRETCH_FLOOR = 0.80  # atempo below this audibly smears speech

    def paced_sentence(sent, allow_split=True):
        """Draw a sentence until it speaks at or under its threshold.

        Escalation for a sentence that keeps drawing hot: (1) redraws pick the
        slowest take, (2) a pitch-safe stretch down to STRETCH_FLOOR, (3) for a
        clause-heavy sentence the stretch cannot fix, re-synthesize it clause
        by clause with short natural pauses — how a narrator actually delivers
        a caveat — pacing each clause independently.
        """
        words = max(1, len(sent.split()))
        thr = sentence_wpm * (1 + 0.06 * max(0, 6 - words))
        # short sentences draw hot and cannot clause-split — buy extra draws
        # to find a calm take instead of leaning on the stretch floor.
        attempts = max(1, max_attempts) + (2 if words < 8 else 0)
        best = None
        for _ in range(attempts):
            audio = trim_edges(synth(sent))
            dur = len(audio) / sr
            wpm = words / dur * 60 if dur > 0.05 else thr
            if best is None or wpm < best[1]:
                best = (audio, wpm)
            if wpm <= thr:
                break
        audio, wpm = best
        if wpm <= thr:
            return audio, wpm
        if thr / wpm >= STRETCH_FLOOR:
            audio = stretch(audio, thr / wpm)
            return audio, words / (len(audio) / sr) * 60
        clauses = [c.strip() for c in re.split(r"(?<=[,;]) ", sent) if c.strip()]
        if allow_split and len(clauses) > 1 and words >= 8:
            pieces = []
            for ci, clause in enumerate(clauses):
                caudio, _ = paced_sentence(clause, allow_split=False)
                pieces.append(caudio)
                if ci < len(clauses) - 1:
                    pieces.append(np.zeros(int(0.22 * sr), dtype=caudio.dtype))
            audio = np.concatenate(pieces)
            wpm = words / (len(audio) / sr) * 60
            if wpm > thr * 1.05:
                audio = stretch(audio, max(STRETCH_FLOOR, thr / wpm))
                wpm = words / (len(audio) / sr) * 60
            return audio, wpm
        audio = stretch(audio, STRETCH_FLOOR)
        return audio, words / (len(audio) / sr) * 60

    for scene in script["scenes"]:
        sid = scene["id"]
        if args.scene_id and sid != args.scene_id:
            continue
        tts_text = norm.for_ref(scene["narration"], {})
        ref_text = tts_text
        key = hashlib.sha256(
            f"chatterbox-v2|{voice_prompt}|{exaggeration}|{cfg_weight}|{gap_s}|{sentence_wpm}|{max_attempts}|{tts_text}"
            .encode()).hexdigest()
        cached = os.path.join(cache_dir, f"{key}.wav")
        out = os.path.join(audio_dir, f"{sid}.wav")

        if not os.path.exists(cached) or args.force:
            sentences = [s.strip() for s in _SENT.split(tts_text) if s.strip()]
            pieces, sent_wpms = [], []
            for si, sent in enumerate(sentences):
                audio, wpm = paced_sentence(sent)
                sent_wpms.append(wpm)
                pieces.append(audio)
                if si < len(sentences) - 1:
                    pieces.append(np.zeros(int(gap_s * sr), dtype=audio.dtype))
            full = np.concatenate(pieces) if pieces else np.zeros(sr)
            words = len(tts_text.split())
            wpm = words / (len(full) / sr) * 60
            if sent_wpms:
                print(f"tts: scene {sid} sentence rates: "
                      + " ".join(f"{w:.0f}" for w in sent_wpms) + " wpm")
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
