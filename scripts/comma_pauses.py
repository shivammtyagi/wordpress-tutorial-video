#!/usr/bin/env python3
"""Step 5c (optional): make every comma in the narration an audible pause.

TTS engines honour commas unevenly — Kokoro can leave as little as 0.08s after
one. Using the word timings from the audio gate (verify/scenes/NN.json), this
pads the silence after each comma'd word up to a minimum (default 0.35s),
inserting it inside the existing gap so no speech is touched.

Run AFTER verify_scenes.py, then re-run verify_scenes.py so word offsets (used
by recorder cues and captions) reflect the new audio. Do not run trim_audio.py
afterwards. Engine word timings (audio/words/NN.json, written by the ElevenLabs
engine) are shifted by the same amounts, so they stay exact.

  comma_pauses.py --run-dir <d> [--scene-id NN] [--min-pause 0.35]

Config: `comma_pause_s` (default 0.35). Requires numpy (present in the skill venv).
"""
import argparse
import json
import os
import re
import sys
import wave

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "lib"))
import run_dir as rd

_norm = lambda s: re.sub(r"[^a-z0-9']+", "", s.lower())


def comma_words(narration):
    """Normalized words that are followed by a comma in the narration, in order."""
    return [_norm(t) for t in narration.split() if t.rstrip("\"')").endswith(",")]


def plan_insertions(narration, words, min_pause):
    """[(insert_at, extra, lo, hi)] for comma gaps shorter than min_pause.

    lo..hi bounds where the pad may go: from just inside the comma'd word's end
    to just inside the next word's start — never past a neighbouring word, so
    two close commas can't both snap to the same gap, and a clip's leading
    silence is never chosen for its first comma.

    Matches each comma'd script word to the next transcript word with the same
    normalized text (monotonic), then looks at the gap to the following word.
    """
    targets = comma_words(narration)
    out, cursor = [], 0
    for tgt in targets:
        for i in range(cursor, len(words) - 1):
            if _norm(words[i]["word"]) == tgt:
                gap = words[i + 1]["start"] - words[i]["end"]
                if gap < min_pause:
                    end, nxt = words[i]["end"], words[i + 1]["start"]
                    out.append((end + max(gap, 0) / 2, min_pause - max(gap, 0),
                                end - 0.08, max(nxt, end) + 0.08))
                cursor = i + 1
                break
    return out


def quietest_point(audio, sr, lo, hi, frame_s=0.01):
    """Centre (seconds) of the quietest 10ms frame in [lo, hi] — where speech is
    guaranteed absent, so inserted silence can never split a word even when the
    transcript's word boundaries are a little off (common on a clip's first word)."""
    import numpy as np
    mono = audio if audio.ndim == 1 else audio.mean(axis=1)
    a, b = max(0, int(lo * sr)), min(len(mono), int(hi * sr))
    n = max(1, int(frame_s * sr))
    if b - a < n:
        return (lo + hi) / 2
    frames = [(float(np.sqrt(np.mean(mono[i:i + n] ** 2))), i) for i in range(a, b - n + 1, n)]
    rms, i = min(frames)
    # Prefer the middle of the longest truly quiet stretch: the single quietest
    # frame can sit right against a word's soft release or next onset, and a
    # pad there sounds like the audio breaking up.
    thresh = max(0.0015, rms * 3)
    best, run_start, best_span = None, None, 0
    for r, j in frames + [(1.0, b)]:
        if r <= thresh:
            run_start = j if run_start is None else run_start
        elif run_start is not None:
            span = j - run_start
            if span > best_span:
                best_span, best = span, (run_start + j) / 2
            run_start = None
    if best is not None and best_span >= 2 * n:
        return best / sr
    return (i + n / 2) / sr


def frame_rms(audio, sr, at, frame_s=0.01):
    """RMS of the 10ms frame centred on `at` — how silent an insertion point is."""
    import numpy as np
    mono = audio if audio.ndim == 1 else audio.mean(axis=1)
    n = max(1, int(frame_s * sr)); c = int(at * sr)
    seg = mono[max(0, c - n // 2):c + n // 2]
    return float(np.sqrt(np.mean(seg ** 2))) if len(seg) else 0.0


def already_padded(audio, sr, lo, hi, min_s=0.03):
    """True when lo..hi already holds an inserted run of exact zeros (TTS never
    emits those), so a second pass never pads the same comma twice."""
    import numpy as np
    mono = audio if audio.ndim == 1 else audio.mean(axis=1)
    seg = np.abs(mono[max(0, int(lo * sr)):int(hi * sr)]) < 1e-9
    run = best = 0
    for z in seg:
        run = run + 1 if z else 0
        best = max(best, run)
    return best / sr >= min_s


def shift_times(words, inserts):
    """Move word timings to match audio that had silence inserted.

    `inserts` is [(at_seconds, extra_seconds)] in the ORIGINAL timeline. A time
    after an insertion point moves later by that insertion's length; a word that
    straddles one (its end is past the point) only has its end moved.
    """
    out = []
    for w in words:
        v = dict(w)
        for key in ("start", "end"):
            if key in v and v[key] is not None:
                t = float(v[key])
                v[key] = round(t + sum(extra for at, extra in inserts if t > at), 3)
        out.append(v)
    return out


# An insertion point louder than this is inside a sound, not between words.
SILENT_RMS = 0.006


def _read_wav(path):
    """16-bit PCM WAV -> (float array in -1..1, sample rate, channels)."""
    import numpy as np
    with wave.open(path, "rb") as w:
        sr, ch, width = w.getframerate(), w.getnchannels(), w.getsampwidth()
        raw = w.readframes(w.getnframes())
    if width != 2:
        raise SystemExit(f"comma_pauses: {path} is not 16-bit PCM")
    x = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    return (x.reshape(-1, ch) if ch > 1 else x), sr, ch


def _write_wav(path, audio, sr, ch):
    import numpy as np
    pcm = np.clip(np.round(audio * 32768.0), -32768, 32767).astype("<i2")
    with wave.open(path, "wb") as w:
        w.setnchannels(ch)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm.tobytes())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-dir", required=True)
    ap.add_argument("--scene-id", default=None)
    ap.add_argument("--min-pause", type=float, default=None)
    args = ap.parse_args()
    import numpy as np

    cfg = rd.load_config(args.run_dir)
    min_pause = args.min_pause if args.min_pause is not None else float(cfg.get("comma_pause_s", 0.35))
    script = json.load(open(os.path.join(args.run_dir, "script.json")))
    dpath = os.path.join(args.run_dir, "audio", "durations.json")
    durations = json.load(open(dpath))
    for sc in script["scenes"]:
        sid = sc["id"]
        if args.scene_id and sid != args.scene_id:
            continue
        wpath = os.path.join(args.run_dir, "verify", "scenes", f"{sid}.json")
        if not os.path.exists(wpath):
            print(f"comma_pauses: scene {sid} has no word timings (run verify_scenes.py first)")
            continue
        ins = plan_insertions(sc["narration"], json.load(open(wpath)).get("words", []), min_pause)
        if not ins:
            continue
        wav = os.path.join(args.run_dir, "audio", f"{sid}.wav")
        audio, sr, ch = _read_wav(wav)
        # Snap each insertion to the quietest point inside its own gap, so the
        # pad always lands in real silence between the two words.
        snapped = []
        for at, extra, lo, hi in ins:
            if already_padded(audio, sr, lo, hi):
                continue  # padded on an earlier run
            q = quietest_point(audio, sr, lo, hi)
            if frame_rms(audio, sr, q) > SILENT_RMS:
                print(f"comma_pauses: scene {sid} skipped a pad at {q:.2f}s (no silence there; "
                      "padding would cut a word)")
                continue
            snapped.append((q, extra))
        ins = sorted(snapped)
        if not ins:
            continue
        pieces, last = [], 0
        for at, extra in ins:
            cut = int(round(at * sr))
            pieces += [audio[last:cut], np.zeros((int(round(extra * sr)),) + audio.shape[1:], dtype=audio.dtype)]
            last = cut
        pieces.append(audio[last:])
        out = np.concatenate(pieces)
        _write_wav(wav, out, sr, ch)
        durations[sid] = round(len(out) / sr, 3)
        # Engine word timings (ElevenLabs) describe the audio exactly; keep them
        # exact by moving them with the audio instead of leaving them stale.
        epath = os.path.join(args.run_dir, "audio", "words", f"{sid}.json")
        if os.path.exists(epath):
            eng = json.load(open(epath))
            if isinstance(eng.get("words"), list):
                eng["words"] = shift_times(eng["words"], ins)
                rd.write_json(epath, eng)
        print(f"comma_pauses: scene {sid} padded {len(ins)} comma pause(s) to {min_pause:.2f}s "
              f"(+{sum(e for _, e in ins):.2f}s)")
    rd.write_json(dpath, durations)


if __name__ == "__main__":
    main()
