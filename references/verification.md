# Verification & auto-fix (steps 5b, 10–11)

Two failure modes make an auto-generated tutorial untrustworthy: the voice says
the wrong words, and the screen shows the wrong thing. v2 splits the checks so
each runs at the cheapest possible moment:

- **Text fidelity moved BEFORE recording** (step 5b, the audio gate) — TTS
  misreads are caught before any recording time is spent.
- **Visual match stays after compose** (step 10, Claude vision) — bounded by
  `max_fix_iterations`.

## Step 5b — the audio gate (`verify_scenes.py`, local, ~free)

For every scene WAV:

1. Transcribe with WhisperX (`--device cpu --compute_type int8`, model from
   `asr_model`, default `small.en`; `large-v3-turbo` for maximum accuracy).
   The transcription is glossary-biased via `initial_prompt` (product terms from
   the lexicon/config) so technical words transcribe as written.
2. Normalize BOTH the intended narration (`ref_text` from `audio/tts_meta.json`)
   and the transcript with Whisper's `EnglishTextNormalizer`, then collapse
   single-letter runs ("U R L" == "URL") — so "5" vs "five" or case differences
   never count as errors.
3. Compute per-scene WER. `WER > wer_threshold` (default 0.15) fails the scene.
4. Write `verify/scenes/NN.json` (`{words, wer, ok}` — word offsets feed recorder
   cues and captions) and `verify/audio_report.json`; exit 1 if any scene failed.

**On failure:** regenerate that scene's audio (`tts_kokoro.py --force`, or fix
the narration/lexicon if the word is genuinely unpronounceable) and re-run
`verify_scenes.py --scene-id NN`. Bound the loop with `max_fix_iterations`.
If TTS keeps mangling a term, add it to `references/lexicon.json` (IPA).

**Apple Silicon note:** if WhisperX's wav2vec2 alignment fails (reported on some
M-series setups), the script automatically falls back to faster-whisper word
timestamps — WER checking is unaffected. `check_env.sh --deep` shows which path
is live.

## Step 10 — audio/video semantic match (Claude vision, the cost)

1. `python3 scripts/grab_frames.py --run-dir <d>` grabs a mid-clip frame per
   scene into `verify/frames/NN.png`.
2. For each scene (or a `verify_sample` subset), judge the frame against the
   scene's `narration` and `verify.expect_on_screen`:

   > "Here is a frame from a tutorial scene. The narration says: '<narration>'.
   > It should show: '<expect_on_screen>'. Does the frame match? Also: are any
   > secrets, emails, or license keys visible? Answer JSON:
   > `{ \"match\": true|false, \"reason\": \"...\", \"secrets\": true|false }`."

3. On `match: false` → the recording is off (wrong page, missed click, panel not
   open). Re-run **selector discovery for that scene**, re-record
   (`record_scene.mjs --force`), re-post-process, recompose.
4. On `secrets: true` → add the element/pattern to `redact_selectors` /
   `redact_patterns` and re-record that scene.

## The loop (bounded)

```
step 5b runs until audio passes (≤ max_fix_iterations)
record → post → compose
for iteration in 1..max_fix_iterations:
    grab_frames + vision verdicts -> verify/report.json
    if no failures: break
    fix each failing scene (re-discover + re-record + re-post)
    recompose
```

Before re-recording a failing scene, copy its current frame to
`verify/evidence/NN.before.png`; after the fix, save `NN.after.png` — the
delivery summary shows what changed. If scenes still fail after the cap, leave
them, write the failures into `report.json`, and tell the user which scenes need
a human look.

## Flags (in `config.json`)

| Flag | Default | Effect |
|------|---------|--------|
| `verify` | `full` | `full` = audio gate + vision; `text` = audio gate only (free); `off` = skip both. |
| `verify_sample` | `0` (all) | If N>0, run the vision check on N evenly-spaced scenes only. |
| `wer_threshold` | `0.15` | Max acceptable word error rate per scene. |
| `asr_model` | `small.en` | WhisperX model for the audio gate. |
| `max_fix_iterations` | `2` | Cap on each fix loop. |

## report.json shape

```json
{
  "scenes": [
    {"id": "01", "wer": 0.0, "text_ok": true,
     "vision_ok": true, "vision_reason": "Shows the sitemap toggle as narrated"}
  ],
  "passed": true,
  "iterations": 1
}
```

## Captions come from the same alignments

`make_captions.py` builds `captions.srt` from the **script text** (ground truth
spelling) timed by the per-scene word offsets + `output/timeline.json` — so
captions never mishear a product name. `verify/transcript.json`
(`transcribe_whisperx.py`, whole-video) remains as a fallback path and an
optional final spot-check.
