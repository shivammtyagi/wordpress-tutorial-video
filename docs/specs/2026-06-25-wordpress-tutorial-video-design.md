# Design: `wordpress-tutorial-video` Claude Code Skill

**Date:** 2026-06-25
**Status:** Approved (direction); ready for implementation planning
**Author:** Generated with Claude Code

## 1. Summary

A public, open-source Claude Code **skill** that turns a **WordPress documentation URL** into a
finished, narrated, captioned tutorial **MP4**, recorded against a WordPress site the **user
provides**. The pipeline reads the doc, writes a beginner-friendly scene script, generates the
voiceover first, discovers and verifies real DOM selectors on the live site, records one short
clip per scene with deterministic Playwright, composes clips + audio + captions with FFmpeg,
verifies that narration matches what is on screen, auto-fixes scenes that fail, and outputs the
final MP4.

The skill is **plugin-agnostic**: the only hard assumption is "the target is WordPress." Anyone can
point it at any WordPress doc and their own site.

## 2. Goals & non-goals

### Goals
- One input that matters: a WordPress documentation URL (+ a reachable site with admin login).
- Produce a polished, captioned tutorial MP4 with synchronized voiceover.
- Reproducible and resumable: discrete steps, each writing output to disk.
- Verified output: narration text fidelity + on-screen semantic match, with bounded auto-fix.
- Distributable on public GitHub and installable as a standard Claude Code skill.

### Non-goals
- Not a general-purpose web recorder — WordPress admin UI only.
- Does **not** provision or seed a WordPress site. The user supplies a ready site.
- Not cross-platform — **macOS only** (Apple Silicon recommended).
- No paid dependencies (Remotion company license excluded; FFmpeg is the backbone).

## 3. Decisions (locked)

| Decision | Choice | Rationale |
|---|---|---|
| Deliverable | Skill package only | Author the tool; do not require a finished video this session. |
| Audience | Public, open-source, any WordPress user | Generalized beyond AIOSEO/any company. |
| Target site | **User provides** a running WP site + admin creds | Most portable; no Docker dependency; skill only drives & records. |
| Action discovery | **Live discovery pass** on the real site | Robust to any plugin; resolves verified selectors before recording. |
| OS support | **macOS only** | Matches brief; simplest Homebrew bootstrap. |
| Verification | **Full, tunable**, default on | Best quality out of the box; flags to dial cost down. |
| Skill structure | **Orchestrator playbook + bundled step scripts** | Separates "needs a brain" from "needs to be reproducible." |
| Compositor | FFmpeg | Free, scriptable, headless, no licensing concerns. |
| TTS default | Kokoro-82M (Apache-2.0, CPU) | Free for commercial use; Chatterbox-Turbo is documented opt-in upgrade. |
| Verify/transcribe | WhisperX (faster-whisper + forced alignment) | Sub-100ms word timestamps → text diff **and** free aligned captions. |
| Recording | Scripted Playwright + `page.screencast` (≥1.59) | Real bitrate/quality; avoids `recordVideo`'s ~1Mbit VP8. |

## 4. Core principle: script-locked segments, audio first

The scene **script is the spine**. The video is recorded as one short clip per scene, not a long
take edited afterward. **Voiceover is generated before video** so each scene's narration duration
is known; each scene's final length is `max(clip, narration)`, holding the last frame or applying
a slow zoom when narration runs long. This is the anti-drift mechanism that keeps audio and video
in sync.

## 5. Package layout

```
wordpress-tutorial-video/            # repo root == the skill (SKILL.md at root)
├── SKILL.md                         # orchestrator playbook (progressive disclosure)
├── README.md                        # GitHub landing: what/why/install/usage/limits
├── LICENSE                          # MIT
├── scripts/
│   ├── check_env.sh                 # detect ffmpeg/uv/python/node/playwright + versions
│   ├── bootstrap.sh                 # install missing deps (brew, uv venv, playwright)
│   ├── fetch_doc.py                 # step 2: URL → clean text + image refs
│   ├── tts_kokoro.py                # step 5: scene narration → wav + duration JSON
│   ├── record_scene.mjs             # step 7: per-scene Playwright + page.screencast
│   ├── postprocess_clip.py          # step 8: crop/zoom/cursor/speed-ramp/pad (ffmpeg)
│   ├── compose.py                   # step 9: concat+mux+transitions+intro/outro+captions
│   ├── transcribe_whisperx.py       # step 10a: final audio → word-timestamped transcript
│   └── make_captions.py             # WhisperX words → .srt/.ass
├── references/
│   ├── scene-schema.md              # the scene JSON contract (authoritative)
│   ├── selector-discovery.md        # how Claude explores the live site for selectors
│   ├── recording-tuning.md          # pacing, screencast opts, highlight overlays
│   ├── ffmpeg-recipes.md            # exact compose/zoom/caption command templates
│   ├── verification.md              # WhisperX diff + vision check + flags + thresholds
│   └── voices.md                    # Kokoro voices; Chatterbox upgrade path
├── assets/
│   ├── intro_template.*             # neutral, doc-title-driven intro/outro cards
│   └── cursor.*                     # cursor spotlight / click-ripple overlay asset
└── docs/specs/                      # this design doc and future specs
```

**Ownership split:** Claude personally performs the steps needing judgment (parse doc, write
script, self-review, **selector discovery**, vision verify, auto-fix). Bundled scripts perform the
deterministic steps (fetch, TTS, record, post-process, compose, transcribe, captions). All
communication is via the run directory.

## 6. Run directory & resumable-step contract

```
runs/<slug>-<doc-hash>/
├── config.json                 # inputs: doc URL, site URL, creds ref, flags, defaults
├── doc.md                      # step 2 output: cleaned doc text + extracted image refs
├── script.json                 # steps 3–4: scene script (human-language targets)
├── script.discovered.json      # step 6: scene script with VERIFIED selectors
├── audio/{NN.wav, durations.json}
├── clips/{NN.raw.webm, NN.final.mp4}
├── captions.srt
├── verify/{transcript.json, report.json, frames/NN.png}
├── output/final.mp4
└── state.json                  # completed steps + input hashes (resume marker)
```

**Step contract (every script + Claude step obeys):**
1. Read inputs only from the run directory.
2. Write outputs atomically, then record completion + input-hash in `state.json`.
3. On re-run, **skip** if inputs unchanged (idempotent); `--force` re-runs.
4. On failure, exit non-zero with a clear message; never leave a half-written artifact.

## 7. Scene schema (authoritative contract)

```jsonc
{
  "title": "Setting up XML Sitemaps",
  "resolution": "1920x1080",
  "fps": 30,
  "voice": "af_heart",                          // Kokoro voice id (default)
  "scenes": [
    {
      "id": "01",
      "narration": "Head to the SEO menu and open Sitemaps.",
      "intent": "Open the Sitemaps settings page",          // plain-language goal
      "actions": [                                          // selectors filled by discovery
        {"type": "click", "target": "All in One SEO menu", "selector": null, "highlight": false},
        {"type": "click", "target": "Sitemaps submenu",    "selector": null, "highlight": true}
      ],
      "focus_selector": null,                                // resolved during discovery
      "hold_after_ms": 800,
      "verify": {"expect_on_screen": "Sitemap settings with enable toggle"}  // vision target
    }
  ]
}
```

Action `type`s: `click`, `type` (with `text`), `scroll`, `hover`, `wait`, `goto` (path).
- Claude writes `script.json` with `intent`, `narration`, and **human-language `target`s** (no DOM
  knowledge required).
- The discovery pass resolves each `target` → a verified `selector`, fills `focus_selector`, and
  writes `script.discovered.json`.
- Recording reads **only** `script.discovered.json`.
- `verify.expect_on_screen` is what the vision frame-check compares against.

## 8. Pipeline steps

| # | Step | Owner | Output |
|---|------|-------|--------|
| 0 | Env check + bootstrap | `check_env.sh` / `bootstrap.sh` | deps ready |
| 1 | Read inputs, create run dir | Claude | `config.json`, run dir |
| 2 | Fetch & parse doc | `fetch_doc.py` | `doc.md` |
| 3 | Write scene script | **Claude** | `script.json` |
| 4 | Self-review script (clarity/pacing/scene count) | **Claude** | `script.json` v2 |
| 5 | Voiceover per scene + durations | `tts_kokoro.py` | `audio/`, `durations.json` |
| 6 | **Live selector discovery** | **Claude** (Playwright MCP + a11y snapshots) | `script.discovered.json` |
| 7 | Record each scene, paced to narration | `record_scene.mjs` (`page.screencast`) | `clips/*.raw.webm` |
| 8 | Post-process: crop, focus-zoom, cursor spotlight, speed-ramp idle, pad to `max(clip,narration)` | `postprocess_clip.py` | `clips/*.final.mp4` |
| 9 | Compose: concat + mux + transitions + intro/outro + burned captions | `compose.py` | `output/final.mp4` |
| 10 | Verify: WhisperX diff + vision frame-check | `transcribe_whisperx.py` + **Claude** | `verify/report.json` |
| 11 | Auto-fix flagged scenes, re-render (bounded) | **Claude** + scripts | updated artifacts |
| 12 | Deliver final MP4 + summary | Claude | `output/final.mp4` |

## 9. Selector discovery (the generalization engine)

After the voiceover exists, Claude logs into the user's site and, scene by scene, uses Playwright
(navigation + accessibility/snapshot tree) to resolve each human-language `target` into a real,
verified selector:
- Prefer stable, role/text-based selectors (`getByRole`, `text=`, `aria-label`) over brittle CSS.
- Verify each selector actually resolves to exactly one visible element before accepting it.
- Resolve `focus_selector` to the container the compositor should zoom/hold on.
- If a target can't be resolved, flag the scene for script revision rather than guessing.

Detail and heuristics live in `references/selector-discovery.md`.

## 10. Recording & post-processing

- Use `page.screencast` (Playwright ≥1.59): start/stop mid-run, click highlighting via
  pointer-events-none overlays, real bitrate. **Avoid `recordVideo`.** CDP→FFmpeg recorder is the
  documented fallback for 60fps/high bitrate.
- Deterministic, paced scripts: human-speed typing, smooth scrolls, deliberate pauses, wait for
  network-idle to avoid loading jank.
- Post-process each clip: crop, zoom on `focus_selector`, cursor spotlight + click ripple, speed-
  ramp idle moments, and **pad/extend to `max(clip, narration)`** (hold last frame or slow zoom).

## 11. Compositing (FFmpeg)

`compose.py` concatenates the per-scene final clips, muxes each scene's audio, adds crossfade
transitions, prepends/append a neutral doc-title-driven intro/outro card, and burns in captions
generated from WhisperX word timestamps. Exact command templates live in
`references/ffmpeg-recipes.md`.

## 12. Verification loop (default on, tunable)

1. **Text fidelity:** WhisperX transcript vs. intended narration, normalized word-error diff; fail
   over threshold → regenerate that scene's audio.
2. **Semantic match:** mid-scene frame + scene `expect_on_screen` → Claude vision verdict
   (pass/fail + reason); fail → re-discover/re-record that scene, then re-render.
3. **Flags:** `verify=full|text|off`, `verify_sample=N`, `wer_threshold`,
   `max_fix_iterations` (default 2 — bounds credit/runaway).
4. **Free win:** WhisperX word timestamps → aligned `.srt` captions (accessibility + SEO-on-brand).

## 13. Dependencies & bootstrap

- `check_env.sh`: report presence + versions of `ffmpeg`/`ffprobe`, `uv`, `python3`, `node`,
  Playwright + Chromium; print a clear ready/not-ready summary.
- `bootstrap.sh` (idempotent): install `ffmpeg` via Homebrew; create a `uv`-managed Python venv
  with Kokoro + WhisperX; ensure Playwright ≥1.59 + Chromium.
- Confirmed local baseline: macOS 27.0, Apple M5/16GB, Node 25, Python 3.14, Playwright 1.61.1.
  `ffmpeg`, `uv`, Kokoro, WhisperX not yet installed (bootstrap installs them).

## 14. Defaults

1080p / 30fps / 16:9; captions on; Kokoro `af_heart`; neutral intro/outro from doc title; cursor
spotlight + click ripple; record on network-idle; verify full; `max_fix_iterations=2`. All
overridable in `config.json`.

## 15. Testing strategy

- Per-script fixture tests runnable without a full pipeline:
  - `tts_kokoro.py`: 2-line mini-script → wavs with plausible non-zero durations.
  - `compose.py`: sample clips + wavs → a playable MP4 (ffprobe-validated).
  - `make_captions.py`: sample WhisperX words → valid SRT.
  - `record_scene.mjs`: record against a bundled static local HTML page (no WP needed).
- Dry-run mode: validate `config.json` + env without recording.
- `references/*` files are the human-readable contracts the tests assert against.

## 16. Gotchas

- User site must be reachable with admin login and the feature already installed/configured.
- No CUDA on Apple Silicon; Kokoro + faster-whisper run on CPU. Chatterbox/WhisperX may use MPS.
- Credit cost is the user's; vision verification is the heavy step — default on, tunable down.
- Selector discovery can fail on unusual themes/plugins; the skill flags rather than guesses.

## 17. Open item

Pilot doc is left to the user/end-user; the skill is generic. For internal shake-out, a short,
visual AIOSEO doc (e.g. XML Sitemaps or a single settings toggle) is the recommended first target.
