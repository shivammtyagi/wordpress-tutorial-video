# Verification & auto-fix loop (steps 10–11)

This is the credit-heavy step, by design. It catches the two failure modes that
make an auto-generated tutorial untrustworthy: the voice says the wrong words,
and the screen shows the wrong thing. Default is **full** verification; it is
tunable down via flags.

## Inputs

- `verify/transcript.json` — WhisperX word timestamps for the final audio
  (`transcribe_whisperx.py`).
- `script.discovered.json` — the intended narration and each scene's
  `verify.expect_on_screen`.
- `verify/frames/NN.png` — a frame grabbed from the middle of each scene.

## Check 1 — text fidelity (local, ~free)

Diff the WhisperX transcript against the intended narration, per scene.

1. Normalize both: lowercase, strip punctuation, collapse whitespace, expand
   common contractions.
2. Compute word error rate (WER = edits / reference words).
3. If `WER > wer_threshold` (default `0.15`), the TTS dropped or mangled words →
   **regenerate that scene's audio** (`tts_kokoro.py`, that scene) and, because
   its duration may change, re-pace/re-pad its clip (`postprocess_clip.py`).

## Check 2 — audio/video semantic match (Claude vision, the cost)

For each scene (or a sample — see flags):

1. Grab a mid-scene frame:
   `ffmpeg -ss <mid> -i clips/NN.final.mp4 -frames:v 1 verify/frames/NN.png`.
2. Send the frame + that scene's `narration` + `expect_on_screen` to Claude
   vision with a verdict request:

   > "Here is a frame from a tutorial scene. The narration says: '<narration>'.
   > It should show: '<expect_on_screen>'. Does the frame match? Answer JSON:
   > `{ \"match\": true|false, \"reason\": \"...\" }`."

3. On `match: false` → the recording is off (wrong page, missed click, panel not
   open). Re-run **selector discovery for that scene**, re-record, re-post-process.

## The loop (bounded)

```
for iteration in 1..max_fix_iterations (default 2):
    run check 1 + check 2 -> verify/report.json
    if no failures: break
    fix each failing scene (audio regen and/or re-discover+re-record)
    recompose
```

`max_fix_iterations` bounds credit spend and prevents infinite loops. If scenes
still fail after the cap, leave them, write the failures into `report.json`, and
tell the user which scenes need a human look.

## Flags (in `config.json` or CLI)

| Flag | Default | Effect |
|------|---------|--------|
| `verify` | `full` | `full` = text + vision; `text` = text only (free); `off` = skip. |
| `verify_sample` | `0` (all) | If N>0, run the vision check on N evenly-spaced scenes only. |
| `wer_threshold` | `0.15` | Max acceptable word error rate for text fidelity. |
| `max_fix_iterations` | `2` | Cap on auto-fix rounds. |

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

## Free captions

The same WhisperX word timestamps drive `make_captions.py`, so captions are
perfectly aligned to the spoken audio at no extra cost.
