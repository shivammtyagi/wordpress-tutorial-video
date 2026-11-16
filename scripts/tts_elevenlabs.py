#!/usr/bin/env python3
"""Step 5 (premium engine): per-scene voiceover via the ElevenLabs API.

Same contract as tts_kokoro.py / tts_chatterbox.py — writes audio/NN.wav,
audio/durations.json and audio/tts_meta.json (ref_text for the audio gate) —
plus audio/words/NN.json: exact per-word timings taken from the service's
character alignment, which the audio gate hands to recorder cues and captions.

How it differs from the local engines:
  * Each scene is voiced in ONE request, so the read flows naturally across
    sentences. Deliberate pauses are shaped afterwards on the audio: the
    silence at every sentence / paragraph boundary is lengthened to a minimum
    (`tts_sentence_gap_s`, `tts_paragraph_gap_s`; write "\n" in the narration
    where a new thought starts), the edges are trimmed and padded
    (`tts_lead_s`, `tts_tail_s`). Do NOT run trim_audio.py on this output.
  * Neighbouring scenes' narration is sent as context (`previous_text` /
    `next_text`) so intonation carries from scene to scene.
  * Pronunciation overrides are sent in place of the written term while
    captions keep the script's spelling: `tts_spoken` respellings
    ({"TruSEO": "True SEO"}) on every model, and run-config `lexicon` IPA
    (sent as /ipa/) on the Eleven v4 family.
  * Speed: `elevenlabs_speed` / a scene's `tts_speed` is sent natively to
    models that honour it; Eleven v4 and v3 ignore the setting, so there it is
    applied locally with a pitch-preserving ffmpeg atempo stretch.
  * Billing-aware caching: the raw take of each scene is cached by
    (model, voice, settings, text). Changing pauses or speed re-shapes the
    cached take for free; only new or edited narration is billed. --estimate
    prints what a run would bill without calling the speech endpoint.

The API key is read from the environment variable named by config
`elevenlabs_key_env` (default ELEVENLABS_API_KEY). It is never written to the
run directory or printed. Narration text is sent to ElevenLabs — do not use
this engine for text that must stay on the machine.

Pure standard library + ffmpeg (only for the local tempo stretch): runs with
any python3, no local model download.

Utilities:
  --list-voices                         voices available to the key
  --audition "text" --voices "A,B" --out DIR   short samples to choose from
"""
import argparse
import base64
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import wave
from array import array

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "lib"))
import run_dir as rd
import normalize as norm
import pause_shape as ps

DEFAULT_MODEL = "eleven_v4"
DEFAULT_VOICE = "Eric"
DEFAULT_FORMAT = "pcm_48000"
FALLBACK_FORMAT = "pcm_24000"
_VOICE_ID = re.compile(r"^[A-Za-z0-9]{20}$")
# models that accept voice_settings.speed but ignore it (measured) -> stretch locally
_LOCAL_TEMPO = ("eleven_v4", "eleven_v3")
# models that read /IPA/ transcriptions inline
_IPA_MODELS = ("eleven_v4",)


class ApiError(Exception):
    def __init__(self, code, status, message):
        super().__init__(message)
        self.code, self.status, self.message = code, status, message


def _base():
    return os.environ.get("ELEVENLABS_API_BASE", "https://api.elevenlabs.io").rstrip("/")


def _call(key, method, path, body=None, query=None, timeout=240):
    """One API call -> (json, headers). Retries rate limits and server errors."""
    url = _base() + path + ("?" + urllib.parse.urlencode(query) if query else "")
    data = json.dumps(body).encode() if body is not None else None
    last = None
    for wait in (0, 2, 6, 15):
        if wait:
            time.sleep(wait)
        req = urllib.request.Request(url, data=data, method=method, headers={
            "xi-api-key": key, "Content-Type": "application/json", "Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.load(r), r.headers
        except urllib.error.HTTPError as e:
            raw = e.read()
            try:
                detail = json.loads(raw).get("detail") or {}
            except Exception:
                detail = {}
            if not isinstance(detail, dict):
                detail = {"message": str(detail)}
            status = detail.get("status") or detail.get("code") or ""
            message = detail.get("message") or raw[:200].decode("utf-8", "replace")
            last = ApiError(e.code, status, message)
            if e.code == 429 or e.code >= 500:
                retry_after = e.headers.get("Retry-After")
                if retry_after and retry_after.isdigit():
                    time.sleep(min(30, int(retry_after)))
                continue
            raise last
        except (urllib.error.URLError, TimeoutError) as e:
            last = ApiError(0, "network", str(getattr(e, "reason", e)))
            continue
    raise last


def _die(msg, code=1):
    print(f"tts_elevenlabs: {msg}", file=sys.stderr)
    raise SystemExit(code)


def _explain(err, key_env):
    """Turn an ApiError into an actionable message + exit code."""
    if err.status == "quota_exceeded" or "quota" in err.message.lower():
        _die(f"ElevenLabs quota exhausted ({err.message}). Already-voiced scenes are "
             "cached; top up the plan or finish the remaining scenes with a local engine.", 5)
    if err.status == "missing_permissions":
        _die(f"the API key in ${key_env} lacks a permission ({err.message}).", 4)
    if err.code == 401 or err.status in ("invalid_api_key", "missing_api_key"):
        _die(f"ElevenLabs rejected the API key in ${key_env} ({err.message}). "
             "Check the key, or switch config engine back to a local voice.", 4)
    if err.status == "network":
        _die(f"could not reach ElevenLabs ({err.message}). Voiced scenes are cached; re-run to resume.", 6)
    _die(f"ElevenLabs API error {err.code} {err.status}: {err.message}", 6)


def _key(cfg):
    env = cfg.get("elevenlabs_key_env") or "ELEVENLABS_API_KEY"
    key = os.environ.get(env, "").strip()
    if not key:
        _die(f"no API key: set the ${env} environment variable (never put the key in "
             "config.json), or use a local engine (tts_kokoro.py / tts_chatterbox.py).", 4)
    return key, env


def list_voices(key):
    data, _ = _call(key, "GET", "/v2/voices", query={"page_size": 100})
    return data.get("voices") or []


def resolve_voice(key, voice, cache_path=None):
    """Voice name or id -> (voice_id, display name)."""
    if _VOICE_ID.match(voice):
        return voice, voice
    cache = {}
    if cache_path and os.path.exists(cache_path):
        cache = json.load(open(cache_path))
        if voice in cache:
            return cache[voice]["id"], cache[voice]["name"]
    want = voice.strip().lower()
    voices = list_voices(key)
    exact = [v for v in voices if v["name"].lower() == want]
    lead = [v for v in voices if re.split(r"\s+[-–—]\s+|\s*\(", v["name"].lower())[0].strip() == want]
    pick = (exact or lead or [v for v in voices if v["name"].lower().startswith(want)])
    if not pick:
        names = ", ".join(sorted(re.split(r"\s+[-–—]\s+", v["name"])[0] for v in voices))
        _die(f"voice '{voice}' not found for this key. Available: {names}. "
             "Use a name from --list-voices or a 20-character voice id.", 2)
    if cache_path:
        cache[voice] = {"id": pick[0]["voice_id"], "name": pick[0]["name"]}
        rd.write_json(cache_path, cache)
    return pick[0]["voice_id"], pick[0]["name"]


def _rate(fmt):
    m = re.match(r"pcm_(\d+)$", fmt)
    if not m:
        _die(f"elevenlabs_output_format must be a pcm_<rate> format, got '{fmt}'", 2)
    return int(m.group(1))


def synthesize(key, voice_id, text, model, fmt, settings=None, seed=None,
               previous_text=None, next_text=None):
    """One speech request -> (pcm bytes, sample rate, alignment dict|None, billed chars, request id)."""
    body = {"text": text, "model_id": model}
    if settings:
        body["voice_settings"] = settings
    if seed is not None:
        body["seed"] = int(seed)
    if previous_text:
        body["previous_text"] = previous_text
    if next_text:
        body["next_text"] = next_text
    path = f"/v1/text-to-speech/{voice_id}/with-timestamps"
    try:
        data, headers = _call(key, "POST", path, body, {"output_format": fmt})
    except ApiError as e:
        if e.status == "output_format_not_allowed" and fmt != FALLBACK_FORMAT:
            print(f"tts: {fmt} is not available on this plan — using {FALLBACK_FORMAT}")
            fmt = FALLBACK_FORMAT
            data, headers = _call(key, "POST", path, body, {"output_format": fmt})
        else:
            raise
    pcm = base64.b64decode(data.get("audio_base64") or "")
    billed = headers.get("character-cost")
    return (pcm, _rate(fmt), data.get("alignment"),
            int(billed) if billed and billed.isdigit() else len(text), headers.get("request-id"))


def _write_wav(path, samples, sr):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(samples.tobytes() if isinstance(samples, array) else samples)


def _atempo(pcm, sr, factor):
    """Pitch-preserving tempo change of raw 16-bit mono PCM via ffmpeg."""
    out = subprocess.run(
        ["ffmpeg", "-loglevel", "error", "-f", "s16le", "-ar", str(sr), "-ac", "1", "-i", "pipe:0",
         "-af", f"atempo={factor:.4f}", "-f", "s16le", "-ar", str(sr), "-ac", "1", "pipe:1"],
        input=pcm, stdout=subprocess.PIPE, check=True).stdout
    return out


def overrides_for(cfg, model):
    """Pronunciation overrides for this model: {term: say}."""
    out = {}
    if model.startswith(_IPA_MODELS):
        for term, ipa in (cfg.get("lexicon") or {}).items():
            out[term] = "/" + ipa.strip("/") + "/"
    elif cfg.get("lexicon"):
        only_ipa = [t for t in cfg["lexicon"] if t not in (cfg.get("tts_spoken") or {})]
        if only_ipa:
            print(f"tts: note — {model} does not read IPA; add tts_spoken respellings for: "
                  + ", ".join(only_ipa))
    out.update(cfg.get("tts_spoken") or {})
    return out


def pacing_for(cfg):
    return {
        "sentence": float(cfg.get("tts_sentence_gap_s", 0.5)),
        "paragraph": float(cfg.get("tts_paragraph_gap_s", 0.85)),
        "min_words": int(cfg.get("tts_min_words", 5)),
        "lead_s": float(cfg.get("tts_lead_s", 0.3)),
        "tail_s": float(cfg.get("tts_tail_s", 0.45)),
        "max_pause_s": float(cfg.get("tts_max_pause_s", 1.6)),
    }


def finish_take(pcm, sr, alignment, ref_text, spoken, cmap, pacing, tempo=1.0):
    """Raw take -> (shaped samples, words|None). Deterministic and free."""
    starts = ends = None
    if alignment and len(alignment.get("characters") or []) == len(spoken):
        starts = list(alignment["character_start_times_seconds"])
        ends = list(alignment["character_end_times_seconds"])
    if abs(tempo - 1.0) > 1e-3:
        pcm = _atempo(pcm, sr, tempo)
        if starts:
            starts, ends = ps.scale_times(starts, tempo), ps.scale_times(ends, tempo)
    gaps = {"sentence": pacing["sentence"], "paragraph": pacing["paragraph"]}
    bounds = ps.find_boundaries(spoken, starts, ends, pacing["min_words"]) if starts else []
    samples, edits = ps.shape_pauses(pcm, sr, bounds, gaps, lead_s=pacing["lead_s"],
                                     tail_s=pacing["tail_s"], max_pause_s=pacing["max_pause_s"])
    words = None
    if starts:
        words = ps.shift_words(ps.word_timings(ref_text, cmap, starts, ends), edits,
                               total_s=len(samples) / sr)
    return samples, words


def _load_script(run_dir):
    for name in ("script.discovered.json", "script.json"):
        p = os.path.join(run_dir, name)
        if os.path.exists(p):
            return json.load(open(p))
    _die("no script.json / script.discovered.json in run dir", 2)


def _context(text, tail):
    text = re.sub(r"\s+", " ", text).strip()
    return text[-300:] if tail else text[:300]


def run(args):
    script = _load_script(args.run_dir)
    cfg = {}
    cfg_path = os.path.join(args.run_dir, "config.json")
    if os.path.exists(cfg_path):
        cfg = json.load(open(cfg_path))
    model = cfg.get("elevenlabs_model") or DEFAULT_MODEL
    fmt = cfg.get("elevenlabs_output_format") or DEFAULT_FORMAT
    _rate(fmt)
    speed = float(cfg.get("elevenlabs_speed", 1.0))
    local_tempo = model.startswith(_LOCAL_TEMPO)
    settings = {}
    if cfg.get("elevenlabs_stability") is not None:
        settings["stability"] = float(cfg["elevenlabs_stability"])
    if cfg.get("elevenlabs_similarity") is not None:
        settings["similarity_boost"] = float(cfg["elevenlabs_similarity"])
    seed = cfg.get("elevenlabs_seed")
    use_context = cfg.get("elevenlabs_context", True)
    max_attempts = max(1, int(cfg.get("tts_max_attempts", 2)))
    overrides = overrides_for(cfg, model)
    pacing = pacing_for(cfg)

    audio_dir = os.path.join(args.run_dir, "audio")
    cache_dir = os.path.join(audio_dir, "cache")
    words_dir = os.path.join(audio_dir, "words")
    os.makedirs(cache_dir, exist_ok=True)
    os.makedirs(words_dir, exist_ok=True)

    key, key_env = _key(cfg)
    try:
        voice_id, voice_name = resolve_voice(
            key, cfg.get("elevenlabs_voice") or DEFAULT_VOICE,
            os.path.join(audio_dir, "elevenlabs_voice.json"))
    except ApiError as e:
        _explain(e, key_env)

    dur_path = os.path.join(audio_dir, "durations.json")
    meta_path = os.path.join(audio_dir, "tts_meta.json")
    usage_path = os.path.join(audio_dir, "elevenlabs_usage.json")
    durations = json.load(open(dur_path)) if os.path.exists(dur_path) else {}
    meta = json.load(open(meta_path)) if os.path.exists(meta_path) else {}
    usage = json.load(open(usage_path)) if os.path.exists(usage_path) else {"total_chars": 0, "requests": []}

    scenes = script["scenes"]
    refs = [norm.for_ref(s["narration"], {}) for s in scenes]
    billed_now, cached_n, to_bill = 0, 0, 0
    print(f"tts: ElevenLabs {model}, voice {voice_name} ({voice_id}), pauses {pacing}")

    for i, scene in enumerate(scenes):
        sid = scene["id"]
        if args.scene_id and sid != args.scene_id:
            continue
        ref_text = refs[i]
        spoken, cmap = ps.build_spoken(ref_text, overrides)
        scene_speed = float(scene.get("tts_speed", speed))
        native = dict(settings)
        tempo = 1.0
        if abs(scene_speed - 1.0) > 1e-3:
            if local_tempo:
                tempo = scene_speed
            else:
                native["speed"] = scene_speed
        raw_key = hashlib.sha256(json.dumps(
            ["elevenlabs", model, voice_id, native, fmt, spoken], sort_keys=True,
            ensure_ascii=False).encode()).hexdigest()
        key_full = hashlib.sha256(json.dumps(
            [raw_key, pacing, round(tempo, 4)], sort_keys=True).encode()).hexdigest()
        raw_pcm = os.path.join(cache_dir, f"el-{raw_key}.pcm")
        raw_meta = os.path.join(cache_dir, f"el-{raw_key}.json")
        have_raw = os.path.exists(raw_pcm) and os.path.exists(raw_meta) and not args.force

        if args.estimate:
            if have_raw:
                cached_n += 1
            else:
                to_bill += len(spoken)
            print(f"tts: scene {sid}: {'cached' if have_raw else f'{len(spoken)} characters to bill'}")
            continue

        if not have_raw:
            take = None
            for attempt in range(max_attempts):
                try:
                    pcm, sr, alignment, billed, req_id = synthesize(
                        key, voice_id, spoken, model, fmt, native or None,
                        seed if (seed is not None and not args.force and attempt == 0) else None,
                        _context(refs[i - 1], True) if use_context and i > 0 else None,
                        _context(refs[i + 1], False) if use_context and i + 1 < len(refs) else None)
                except ApiError as e:
                    _explain(e, key_env)
                billed_now += billed
                usage["total_chars"] += billed
                usage["requests"].append({"scene": sid, "chars": billed, "model": model,
                                          "request_id": req_id,
                                          "at": time.strftime("%Y-%m-%dT%H:%M:%S")})
                rd.write_json(usage_path, usage)
                aligned = bool(alignment) and len(alignment.get("characters") or []) == len(spoken)
                take = (pcm, sr, alignment if aligned else None)
                if len(pcm) > sr // 2 and aligned:
                    break
                print(f"tts: scene {sid} take {attempt + 1} "
                      f"{'has no usable alignment' if len(pcm) > sr // 2 else 'came back empty'}"
                      f"{' — retrying' if attempt + 1 < max_attempts else ''}")
            pcm, sr, alignment = take
            if len(pcm) <= sr // 2:
                _die(f"scene {sid}: ElevenLabs returned no audio after {max_attempts} attempts", 6)
            rd.atomic_write(raw_pcm, pcm)
            rd.write_json(raw_meta, {"sr": sr, "alignment": alignment, "spoken": spoken})
        else:
            cached_n += 1
            pcm = open(raw_pcm, "rb").read()
            m = json.load(open(raw_meta))
            sr, alignment = m["sr"], m["alignment"]

        samples, words = finish_take(pcm, sr, alignment, ref_text, spoken, cmap, pacing, tempo)
        out = os.path.join(audio_dir, f"{sid}.wav")
        _write_wav(out, samples, sr)
        secs = len(samples) / sr
        durations[sid] = round(secs, 3)
        meta[sid] = {"hash": key_full, "ref_text": ref_text, "tts_text": spoken,
                     "engine": "elevenlabs", "model": model, "voice_id": voice_id}
        words_path = os.path.join(words_dir, f"{sid}.json")
        if words:
            rd.write_json(words_path, {"hash": key_full, "source": "elevenlabs", "words": words})
        else:
            if os.path.exists(words_path):
                os.unlink(words_path)
            print(f"tts: scene {sid} has no usable alignment — the audio gate will time it by ear")
        wpm = len(ref_text.split()) / secs * 60 if secs else 0
        note = f", speed {scene_speed}" if abs(scene_speed - 1.0) > 1e-3 else ""
        print(f"tts: scene {sid} -> {out} ({secs:.2f}s, {wpm:.0f} wpm incl. pauses{note}"
              f"{', cached take' if have_raw else ''})")

    if args.estimate:
        print(f"tts: estimate — {to_bill} characters would be billed "
              f"({cached_n} scene(s) already cached)")
        return
    rd.write_json(dur_path, durations)
    rd.write_json(meta_path, meta)
    print(f"tts: wrote {dur_path} + tts_meta.json + words/")
    print(f"tts: ElevenLabs billed {billed_now} characters this run "
          f"({cached_n} scene(s) reused from cache; {usage['total_chars']} total for this video)")


def cmd_list_voices(args):
    key, key_env = _key({"elevenlabs_key_env": args.key_env})
    try:
        voices = list_voices(key)
    except ApiError as e:
        _explain(e, key_env)
    for v in voices:
        lab = v.get("labels") or {}
        tags = " ".join(str(lab[k]) for k in ("gender", "accent", "age", "use_case") if lab.get(k))
        print(f"{v['voice_id']}  {v['name']}  [{v.get('category', '')}; {tags}]")
    print(f"{len(voices)} voice(s). Set config elevenlabs_voice to a name (the part before ' - ') or an id.")


def cmd_audition(args):
    key, key_env = _key({"elevenlabs_key_env": args.key_env})
    os.makedirs(args.out, exist_ok=True)
    total = 0
    for n, name in enumerate([v.strip() for v in args.voices.split(",") if v.strip()], 1):
        try:
            vid, full = resolve_voice(key, name)
            pcm, sr, _, billed, _ = synthesize(key, vid, args.audition, args.model, DEFAULT_FORMAT)
        except ApiError as e:
            _explain(e, key_env)
        total += billed
        path = os.path.join(args.out, f"{n}-{re.sub(r'[^a-z0-9]+', '-', name.lower()).strip('-')}.wav")
        _write_wav(path, pcm, sr)
        print(f"audition: {full} -> {path} ({len(pcm) / 2 / sr:.1f}s)")
    print(f"audition: billed {total} characters")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run-dir")
    ap.add_argument("--scene-id", default=None, help="only (re)generate this scene")
    ap.add_argument("--force", action="store_true", help="discard the cached take and bill a new one")
    ap.add_argument("--estimate", action="store_true", help="print characters to bill; no speech calls")
    ap.add_argument("--list-voices", action="store_true")
    ap.add_argument("--audition", metavar="TEXT", help="voice a short sample with each of --voices")
    ap.add_argument("--voices", default=DEFAULT_VOICE)
    ap.add_argument("--out", default="auditions")
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--key-env", default="ELEVENLABS_API_KEY")
    args = ap.parse_args()
    if args.list_voices:
        return cmd_list_voices(args)
    if args.audition:
        return cmd_audition(args)
    if not args.run_dir:
        ap.error("--run-dir is required (or use --list-voices / --audition)")
    run(args)


if __name__ == "__main__":
    main()
