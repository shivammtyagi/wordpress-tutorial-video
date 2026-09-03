# Voices & narration audio

## Default: Kokoro-82M

[Kokoro-82M](https://huggingface.co/hexgrad/Kokoro-82M) is the default TTS:
82M parameters, Apache-2.0 license, ~14x realtime on Apple Silicon (MPS), free
for commercial use. Crucially it is **deterministic and non-autoregressive** —
it cannot hallucinate or invent words, only mispronounce them, which is what
keeps the audio gate's transcript diff meaningful. `scripts/tts_kokoro.py`
loads it via the `kokoro` package (`KPipeline`), pinned `>=0.9.4` (earlier
versions silently skipped out-of-vocabulary words).

**Requirements:** Python 3.10–3.12 venv (bootstrap uses 3.11) and `espeak-ng`
(brew) — the OOV fallback. Set `PYTORCH_ENABLE_MPS_FALLBACK=1` when running on
Apple Silicon.

### Voices (quality tiers from the model's own grades)

| Voice id | Grade | Character |
|----------|-------|-----------|
| `af_heart` | A | Warm, friendly female (default). |
| `af_bella` | A- | Bright female. |
| `af_nicole` | B- | Calm, soft "headphone" register. |
| `bf_emma` | B- | British female. |
| `am_michael` | C+ | Best male option. |

Pick a voice that fits a tutorial: clear, unhurried, not overly expressive. Run
a short sample before committing a whole video. **Voice blending** is supported:
average two voice tensors with weights (e.g. 60% `af_bella` + 40% `af_nicole`)
for a distinctive narrator.

### Pacing

- `speed` (config, default **0.9**): 0.85–0.95 reads as unhurried tutorial pace.
- Write narration as 1–3 sentence lines — Kokoro is best at 100–200 tokens;
  under ~20 tokens voices get odd, over ~400 they rush.
- There is **no SSML/pause tag**. For explicit pauses, split the narration into
  separate lines/scenes; the pipeline joins per-line audio with silence gaps
  deterministically.

### Pronunciation control (the audio-gate insurance)

1. **Lexicon** — `references/lexicon.json` maps product terms to IPA; add
   per-run terms via the `lexicon` key in `config.json`. Terms are injected as
   Kokoro inline links: `[AIOSEO](/ˌeɪˌaɪˌoʊˌɛsˌiˈoʊ/)`.
2. **Automatic normalization** (`scripts/lib/normalize.py`): URLs are spoken
   ("aioseo dot com slash docs"), versions expanded ("version five point nine
   point three"), `wp-admin` → "W P admin", file extensions spelled.
3. Known weak spots: non-Anglo proper nouns and heteronyms (lead/live/read).
   If the audio gate keeps flagging a term, add a lexicon entry.

## Upgrade: Chatterbox (expressive / cloned voice — opt-in, FOSS)

[Chatterbox](https://github.com/resemble-ai/chatterbox) (MIT, Resemble AI) is
the credible local upgrade for a more expressive or cloned voice: zero-shot
cloning from a short reference clip, runs on Apple Silicon via MPS (Python 3.11
venv with `chatterbox-tts`, `torch`, `torchaudio`).

**Trade-off:** it is a sampling model — occasional artifacts ("ghost" noises,
odd breaths), so it needs the audio gate as a retry harness: chunk to ≤450
characters, verify each scene's WER, regenerate failures. Keep the same
contract as `tts_kokoro.py` (write `audio/NN.wav` + `audio/durations.json` +
`audio/tts_meta.json`) via a sibling `tts_chatterbox.py` and the rest of the
pipeline is unchanged. Start with Kokoro; switch once the pipeline is proven
for your content.

## Chatterbox — the default narration engine

Kokoro reads cleanly but flat; questions sound like statements and long
tutorials feel robotic. **Chatterbox** (Resemble AI, MIT — fine for commercial
use) has real prosody and is the default engine: `scripts/tts_chatterbox.py`,
running in its own venv (`.venv-cbx`, created by bootstrap; note the
`setuptools<81` pin for resemble-perth). Same output contract as the Kokoro
script, so the audio gate, captions, and recorder cues work unchanged.

Knobs (config.json): `tts_exaggeration` (0.4 default — calm tutorial),
`tts_cfg` (0.35), `tts_gap_s` (0.30 between sentences), `tts_target_wpm`
(185) and `tts_max_attempts` (2). Chatterbox naturally reads ~200–225 wpm and
cfg barely moves that, so the script regenerates fast scenes and then applies
a pitch-preserving `atempo` stretch (never below 0.85x) to land the target.
Generation is stochastic — the WER gate stays the arbiter; regenerate failing
scenes with `--force --scene-id NN`.

Voice cloning: pass `tts_voice_prompt` (a ~10s clean reference WAV) to speak
in a specific voice — for a brand channel's narrator, get the narrator's
explicit consent first. Runs on Apple Silicon via MPS at roughly 5–6x
realtime cost (a 90s narration synthesizes in ~8–10 minutes).

Note on Kokoro `speed`: 1.0 sounds most natural; the old 0.9 guidance made
narration draggy. Prefer trimming silences (`trim_audio.py`) over slowing
speech.
