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

Natural pacing mode (on when config sets `tts_sentence_gap_s` or
`tts_paragraph_gap_s`): the narration is synthesized one sentence at a time and
the pieces are joined with deliberate silences — `tts_sentence_gap_s` between
sentences, `tts_paragraph_gap_s` at a newline in the narration (write "\n"
where a new thought starts), plus `tts_lead_s` / `tts_tail_s` of room at the
edges. Sentences under `tts_min_words` words merge into a neighbour so Kokoro
never voices a fragment alone. Do NOT run trim_audio.py's pause compression on
this output (it would undo the pauses); the edges are already trimmed.
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
        audio_chunks.append(np.asarray(audio, dtype=np.float32))
    audio = np.concatenate(audio_chunks) if audio_chunks else np.zeros(SAMPLE_RATE, dtype=np.float32)
    pcm = (np.clip(audio, -1, 1) * 32767).astype("<i2").tobytes()
    return pcm, len(audio) / SAMPLE_RATE


_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


def split_units(text, min_words=5):
    """Break narration into synthesis units: [(sentence, gap_kind), ...].

    Paragraphs are newline-separated; sentences split on . ! ? followed by
    whitespace. A sentence shorter than `min_words` merges into its
    predecessor (or pulls the next one in when it opens the paragraph) so the
    engine never voices a fragment alone. gap_kind is "sentence" between
    sentences of one paragraph, "paragraph" at a paragraph end, and None
    after the final unit.
    """
    units = []
    paragraphs = [p.strip() for p in re.split(r"\n+", text) if p.strip()]
    for pi, para in enumerate(paragraphs):
        sents = [x.strip() for x in _SENTENCE_END.split(para) if x.strip()]
        merged = []
        for x in sents:
            if merged and (len(x.split()) < min_words or len(merged[-1].split()) < min_words):
                merged[-1] = merged[-1] + " " + x
            else:
                merged.append(x)
        for si, x in enumerate(merged):
            last_in_para = si == len(merged) - 1
            if last_in_para and pi == len(paragraphs) - 1:
                kind = None
            elif last_in_para:
                kind = "paragraph"
            else:
                kind = "sentence"
            units.append((x, kind))
    return units


def _trim_edges(audio, sr, thresh=0.01, keep_s=0.04):
    import numpy as np
    idx = np.where(np.abs(audio) > thresh)[0]
    if len(idx) == 0:
        return audio
    a = max(0, int(idx[0]) - int(keep_s * sr))
    b = min(len(audio), int(idx[-1]) + int(keep_s * sr))
    return audio[a:b]


def _kokoro_synth_paced(pipeline, text, voice, speed, pacing):
    """Natural pacing mode: per-sentence synthesis joined with deliberate gaps."""
    import numpy as np
    sr = SAMPLE_RATE
    gaps = {"sentence": float(pacing["sentence_gap_s"]), "paragraph": float(pacing["paragraph_gap_s"])}
    parts = [np.zeros(int(float(pacing["lead_s"]) * sr), dtype=np.float32)]
    for unit, kind in split_units(text, int(pacing["min_words"])):
        chunks = [np.asarray(a, dtype=np.float32) for _, _, a in pipeline(unit, voice=voice, speed=speed)]
        audio = np.concatenate(chunks) if chunks else np.zeros(sr, dtype=np.float32)
        parts.append(_trim_edges(audio, sr))
        if kind:
            parts.append(np.zeros(int(gaps[kind] * sr), dtype=np.float32))
    parts.append(np.zeros(int(float(pacing["tail_s"]) * sr), dtype=np.float32))
    audio = np.concatenate(parts)
    pcm = (np.clip(audio, -1, 1) * 32767).astype("<i2").tobytes()
    return pcm, len(audio) / sr


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
    pacing = None
    if "tts_sentence_gap_s" in cfg or "tts_paragraph_gap_s" in cfg:
        pacing = {
            "sentence_gap_s": cfg.get("tts_sentence_gap_s", 0.6),
            "paragraph_gap_s": cfg.get("tts_paragraph_gap_s", 1.0),
            "min_words": cfg.get("tts_min_words", 5),
            "lead_s": cfg.get("tts_lead_s", 0.3),
            "tail_s": cfg.get("tts_tail_s", 0.45),
        }
        print(f"tts: natural pacing mode {pacing}")

    audio_dir = os.path.join(args.run_dir, "audio")
    cache_dir = os.path.join(audio_dir, "cache")
    os.makedirs(cache_dir, exist_ok=True)

    pipeline = None
    durations, meta = {}, {}
    for scene in script["scenes"]:
        sid = scene["id"]
        tts_text = norm.for_tts(scene["narration"], lexicon)
        ref_text = norm.for_ref(scene["narration"], lexicon)
        # per-scene override: slow a list-heavy or dense scene without touching the rest
        scene_speed = float(scene.get("tts_speed", speed))
        pace_key = json.dumps(pacing, sort_keys=True) if pacing else ""
        key = hashlib.sha256(f"{args.engine}|{voice}|{scene_speed}|{pace_key}|{tts_text}".encode()).hexdigest()
        cached = os.path.join(cache_dir, f"{key}.wav")
        out = os.path.join(audio_dir, f"{sid}.wav")

        if not os.path.exists(cached) or args.force:
            if args.engine == "stub":
                pcm, _ = _stub_audio(tts_text)
            else:
                if pipeline is None:
                    from kokoro import KPipeline
                    pipeline = KPipeline(lang_code="a")  # American English
                if pacing:
                    pcm, _ = _kokoro_synth_paced(pipeline, tts_text, voice, scene_speed, pacing)
                else:
                    pcm, _ = _kokoro_synth(pipeline, tts_text, voice, scene_speed)
            _write_wav(cached, pcm)
        shutil.copyfile(cached, out)
        with wave.open(out, "rb") as w:
            secs = w.getnframes() / w.getframerate()
        durations[sid] = round(secs, 3)
        meta[sid] = {"hash": key, "ref_text": ref_text, "tts_text": tts_text}
        wpm = len(ref_text.split()) / secs * 60 if secs else 0
        note = f", speed {scene_speed}" if scene_speed != speed else ""
        print(f"tts: scene {sid} -> {out} ({secs:.2f}s, {wpm:.0f} wpm incl. pauses{note})")

    rd.write_json(os.path.join(audio_dir, "durations.json"), durations)
    rd.write_json(os.path.join(audio_dir, "tts_meta.json"), meta)
    print(f"tts: wrote {audio_dir}/durations.json + tts_meta.json")


if __name__ == "__main__":
    main()
