# Voices & narration audio

Engine choice is the user's call per channel:

| Tier | Engine | Runs | Cost | Character |
|------|--------|------|------|-----------|
| Standard | Kokoro (`tts_kokoro.py`) | on this Mac | free | deterministic, clean, even; natural-pacing mode below |
| Standard | Chatterbox (`tts_chatterbox.py`) | on this Mac | free | expressive prosody, stochastic |
| Premium | ElevenLabs (`tts_elevenlabs.py`) | ElevenLabs API | per character, user's own plan | the most natural read; studio voices; exact word timings |

Channels that want an even, controllable delivery tend to pick Kokoro's
natural-pacing mode; channels that want more expressive reads pick Chatterbox;
channels that want the best-sounding result and have an ElevenLabs plan pick
Premium. All three write the same files, so everything after step 5 is
identical.

## Premium: ElevenLabs

`scripts/tts_elevenlabs.py` — standard-library Python plus ffmpeg, so it runs
with any `python3` and needs no model download. Set `engine: "elevenlabs"` in
`config.json` and put the key in the environment:

    export ELEVENLABS_API_KEY=…        # in the user's shell profile, never in a file of the run

A restricted key needs **Text to Speech** access, plus **Voices (read)** to
resolve a voice by name.

### How a scene is voiced

1. The whole scene goes to the API in **one request** (`/with-timestamps`), so
   the read flows across sentences the way a person would say it. The previous
   and next scenes' narration ride along as context (`previous_text` /
   `next_text`) so intonation carries across cuts (`elevenlabs_context: false`
   turns that off).
2. The response carries the audio and a **per-character alignment**. From it
   the script derives exact per-word timings in the script's own spelling
   (`audio/words/NN.json`) — recorder cues and captions use them directly.
3. **Pauses are shaped on the audio, not in the text.** Eleven v4 has no pause
   tag, and splitting the text into sentences would flatten the prosody.
   Instead the silence at every sentence and paragraph boundary is measured
   and lengthened to a minimum; pauses the voice made longer are kept (up to
   `tts_max_pause_s`); commas and other natural pauses are never touched.

```jsonc
"tts_sentence_gap_s": 0.65,   // minimum pause between sentences (default 0.5)
"tts_paragraph_gap_s": 1.0,   // minimum pause at a "\n" in the narration (default 0.85)
"tts_min_words": 5,           // a boundary next to a shorter sentence keeps its natural pause
"tts_lead_s": 0.35,           // room before the first word
"tts_tail_s": 0.5,            // room after the last word
"tts_max_pause_s": 1.6        // cap for an over-long pause
```

These are the same keys Kokoro's natural pacing uses, with the same meaning,
so a channel's pacing carries over when it switches engine. A value of `0`
leaves that kind of boundary alone. **Do not run `trim_audio.py`** on this
output.

### Models

| `elevenlabs_model` | Use | Bills |
|--------------------|-----|-------|
| `eleven_v4` (default) | ElevenLabs' highest-quality narration model | 1 per character |
| `eleven_multilingual_v2` | very stable long-form; honours `speed` natively | 1 per character |
| `eleven_flash_v2_5` | drafts and bulk; noticeably plainer | 0.5 per character |

Measured behaviour worth knowing (November 2026):

- **v4 ignores `voice_settings.speed`** (the request succeeds, the audio does
  not change). `elevenlabs_speed` and a scene's `tts_speed` are therefore
  applied locally on v4/v3 with a pitch-preserving `atempo` stretch, and sent
  natively to the other models. Keep within 0.85–1.1.
- **v4 has no SSML / `<break>` tags** — hence the pause shaping above.
- **v4 reads inline IPA** written between slashes. A run-config `lexicon`
  entry is sent that way on v4 (`/tɹuˌɛsˌiˈoʊ/`); other models ignore IPA, so
  prefer `tts_spoken` respellings, which work everywhere.
- Output is `pcm_48000` (falls back to `pcm_24000` when the plan refuses it;
  44.1 kHz PCM needs the Pro tier, 48 kHz does not).

### Choosing a voice

    python3 scripts/tts_elevenlabs.py --list-voices
    python3 scripts/tts_elevenlabs.py --audition "Two real sentences from the script." \
        --voices "Eric,Sarah,Matilda" --out ~/Desktop/voice-auditions

Audition with real script lines that contain the product names, and let the
user pick by ear — do not pick for them from a transcript. Voices labelled
`informative_educational` or `conversational` with an American accent suit
tutorials (Eric, Sarah, Matilda, Chris, Bella among the stock voices). Set
`elevenlabs_voice` to the name before " - " or to the voice id.

### Product names — respell them, always

Premium voices do not pronounce a coined name the same way twice. In testing,
"TruSEO" came out right in one request and as "TruCO" in the next **with the
same voice**, and three of five stock voices said it wrong on the first try.
A respelling fixed it on every voice and take tested:

```jsonc
"tts_spoken": { "TruSEO": "True SEO", "AIOSEO": "A I O SEO" }
```

The respelling is what gets sent; captions, cue matching and the word timings
keep the script's spelling. The audio gate then verifies each name with an
unbiased listen (see `verification.md`) — the ordinary WER pass cannot catch
this, because its glossary writes down the right spelling for a wrong sound.

### Cost, caching, retakes

- Billing is per character of narration: a 3-minute tutorial is roughly
  2,000–2,500 characters; a 6-minute one about 5,000.
- `--estimate` prints the characters a run would bill, without calling the
  speech endpoint. `audio/elevenlabs_usage.json` keeps the running total for
  the video.
- The raw take of every scene is cached by (model, voice, settings, text).
  Re-running, changing any pause setting, or changing speed on v4 re-shapes
  the cached take **for free**. Editing a scene's narration bills that scene
  only; its neighbours are not re-voiced even though they were its context.
- `--force --scene-id NN` discards the cached take and bills a new one — the
  retake path when the gate rejects a scene.
- Plans: the free tier's characters are enough to try one short video but its
  licence is non-commercial; published videos need a paid plan. The key's
  owner should confirm the plan before production use.

### What stays on the machine

Only narration text is sent to ElevenLabs. The screen recording, the site, and
the credentials never leave the Mac. For text that must not leave the machine,
use a Standard engine.

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

### Natural pacing mode (deliberate pauses)

Kokoro articulates at ~200 wpm and pads sentences with almost no air, which
reads as rushed. Set these in `config.json` and `tts_kokoro.py` switches to
per-sentence synthesis joined with explicit silences:

```jsonc
"tts_sentence_gap_s": 0.65,   // pause between sentences (0.75 read as slightly slow)
"tts_paragraph_gap_s": 1.0,   // pause at a "\n" in the narration (new thought)
"tts_min_words": 5,           // shorter sentences merge into a neighbour
"tts_lead_s": 0.35,           // room before the first word
"tts_tail_s": 0.5             // room after the last word
```

Write the narration with `\n` where the speaker would take a breath before a
new idea ("Let's open the Basics tab.\nThe checks are sorted…"). Captions and
the WER gate treat the newline as whitespace. With pauses included, delivery
lands ~160–175 wpm at `speed` 0.95 — an unhurried tutorial pace (0.9 was judged slightly slow). **Skip
`trim_audio.py`** (or run it with `--max-pause 5`) on this output: its pause
compression would undo the deliberate gaps. Pair it with a longer still tail
per scene (`tail_cap_s` ~1.2) so the picture breathes too.

Per-scene rate: a scene may carry `"tts_speed": 0.88` to slow just that scene
(list-heavy lines read as rushed at the run's pace). See `scene-schema.md`.

### Pronunciation control (the audio-gate insurance)

1. **Lexicon** — `references/lexicon.json` ships generic WordPress terms; add
   your brand's product names via the `lexicon` key in `config.json` (probe each
   entry: synthesize + transcribe before relying on it). Terms are injected as
   Kokoro inline links: `[AIOSEO](/ˌeɪˌaɪˌoʊˌɛsˌiˈoʊ/)`.
2. **Automatic normalization** (`scripts/lib/normalize.py`): URLs are spoken
   ("aioseo dot com slash docs"), versions expanded ("version five point nine
   point three"), `wp-admin` → "W P admin", file extensions spelled.
3. Known weak spots: non-Anglo proper nouns and heteronyms (lead/live/read).
   A lexicon entry can also make things WORSE: `plugin` → `ˈplʌɡɪn` came out
   as "up-plugin" while the plain word reads fine. Probe every new entry
   (synthesize + transcribe) and only keep entries that fix a real misread.
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
