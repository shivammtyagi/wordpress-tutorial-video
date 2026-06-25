# Voices

## Default: Kokoro-82M

[Kokoro-82M](https://huggingface.co/hexgrad/Kokoro-82M) is the default TTS:
82M parameters, Apache-2.0 license, runs on CPU faster than real time, free for
commercial use, no voice cloning. `scripts/tts_kokoro.py` loads it via the
`kokoro` package (`KPipeline`).

Set the voice with the `voice` field in the scene script, or `--voice` on the CLI.

Common American-English voices (`lang_code="a"`):

| Voice id | Character |
|----------|-----------|
| `af_heart` | Warm, friendly female (default). |
| `af_bella` | Bright female. |
| `af_nicole` | Calm female. |
| `am_adam` | Neutral male. |
| `am_michael` | Deeper male. |

British-English voices use `lang_code="b"` (e.g. `bf_emma`, `bm_george`). Pick a
voice that fits a tutorial: clear, unhurried, not overly expressive. Run a short
sample before committing a whole video to one voice.

## Upgrade: Chatterbox-Turbo (branded / cloned voice)

For a branded or cloned voice, swap in
[Chatterbox-Turbo](https://github.com/resemble-ai/chatterbox) (MIT license,
zero-shot voice cloning, English). In Resemble AI's own blind test it was
preferred over ElevenLabs by a wide margin (vendor-run, so treat as directional).

Notes for Apple Silicon:
- Prefers a GPU; on M-series it runs on the Metal (MPS) backend or CPU (slower).
- Provide a short, clean reference clip of the target voice for cloning.
- Keep the same per-scene generation flow so `audio/durations.json` is still
  produced — the rest of the pipeline is unchanged.

To use it, add a sibling `tts_chatterbox.py` that implements the same contract as
`tts_kokoro.py` (writes `audio/NN.wav` + `audio/durations.json`) and point the
orchestrator at it. Start with Kokoro; switch to a Chatterbox clone once the
pipeline is proven for your content.
