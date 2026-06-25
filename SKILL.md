---
name: wordpress-tutorial-video
description: Use when someone wants to generate a narrated, captioned tutorial or screencast video from a WordPress documentation URL — turning docs for any WordPress plugin, theme, or admin feature into a finished MP4 recorded against their own WordPress site. macOS only; the user supplies a running WordPress site with an admin login.
---

# WordPress Tutorial Video

## Overview

Turn one WordPress documentation URL into a finished, narrated, captioned tutorial
MP4 recorded against a WordPress site the user provides. The skill reads the doc,
writes a scene script, generates the voiceover first, discovers real selectors on
the live site, records one short clip per scene, composes everything with FFmpeg,
verifies that narration matches the screen, auto-fixes what fails, and outputs the
MP4.

**Core principle — script-locked segments, audio first.** The scene script is the
spine. Generate audio before video so each scene's clip is paced to its narration
length (`max(clip, narration)`), holding the last frame when narration runs long.
This is what keeps audio and video in sync. Record one clip per scene; never one
long take edited down.

## When to use

- "Make a tutorial video from this WordPress doc / help article."
- "Record a screencast of this plugin setting and narrate it."
- Any doc → tutorial-video request where the target is the **WordPress admin**.

**Not for:** non-WordPress sites, arbitrary web recording, or live video editing.

## Requirements (check first)

- **macOS only.** Run `scripts/check_env.sh`. If anything is missing, run
  `scripts/bootstrap.sh` (installs ffmpeg via Homebrew, a uv venv with Kokoro +
  WhisperX, and Playwright + Chromium locally).
- A **user-provided WordPress site**: a reachable URL with an admin login and the
  feature already installed/configured. The skill drives and records the site; it
  never provisions one.
- Admin credentials are read from environment variables named in `config.json`
  (`wp_user_env`, `wp_pass_env`) — never hard-code them.

## Inputs — config.json

Create the run directory `runs/<slug>-<hash>/` and write `config.json`:

```jsonc
{
  "doc_url": "https://example.com/docs/xml-sitemaps/",
  "site_url": "https://my-wp.test",          // base; recorder appends /wp-admin
  "wp_user_env": "WP_ADMIN_USER",            // env var holding the username
  "wp_pass_env": "WP_ADMIN_PASS",            // env var holding the password
  "resolution": "1920x1080",
  "fps": 30,
  "voice": "af_heart",
  "verify": "full",                          // full | text | off
  "verify_sample": 0,                         // 0 = all scenes
  "wer_threshold": 0.15,
  "max_fix_iterations": 2
}
```

`slug_for()` and the run-dir/state helpers live in `scripts/lib/run_dir.py`.

## The pipeline

Run steps in order. Each writes to the run directory and records completion in
`state.json`, so a mid-run failure resumes instead of restarting. Re-run any step
with `--force` to redo it.

| # | Step | How | Reads `references/` |
|---|------|-----|---------------------|
| 0 | Env check | `bash scripts/check_env.sh` (then `bootstrap.sh` if needed) | — |
| 1 | Create run dir + `config.json` | you | — |
| 2 | Fetch & parse the doc | `python3 scripts/fetch_doc.py --run-dir <d>` | — |
| 3 | Write `script.json` | **you** — see below | `scene-schema.md` |
| 4 | Self-review the script | **you** — clarity, pacing, 4–12 scenes | `scene-schema.md` |
| 5 | Voiceover + durations | `tts_kokoro.py --run-dir <d>` (venv) | `voices.md` |
| 6 | Discover selectors | **you** — explore the live site | `selector-discovery.md` |
| 7 | Record each scene | `node scripts/record_scene.mjs --run-dir <d> --scene-id NN --base-url <site>/wp-admin` | `recording-tuning.md` |
| 8 | Post-process each clip | `postprocess_clip.py --run-dir <d> --scene-id NN` | `ffmpeg-recipes.md` |
| 9 | Compose | `compose.py --run-dir <d>` | `ffmpeg-recipes.md` |
| 10 | Verify | `transcribe_whisperx.py` + `make_captions.py` + your vision check | `verification.md` |
| 11 | Auto-fix flagged scenes | **you** — bounded by `max_fix_iterations` | `verification.md` |
| 12 | Deliver `output/final.mp4` | you | — |

### Step 3 — writing the script (your job)

Read `doc.md`. Produce `script.json` per `references/scene-schema.md`:
- One spoken idea per scene; 4–12 scenes for a single doc.
- `narration`: one or two clear, beginner-friendly sentences.
- `intent`: the scene's plain-language goal (also the screencast chapter title).
- `actions`: ordered steps with **human-language `target`s** and `selector: null`
  (discovery fills selectors). Each scene must be reachable from a fresh
  `wp-admin` — see the fresh-browser constraint in the schema doc.
- `verify.expect_on_screen`: what the mid-scene frame should show.
Validate with `scripts/lib/schema.py` (`discovered=False`).

### Step 6 — selector discovery (your job)

Log into the live site and resolve every `target` to a verified selector, writing
`script.discovered.json`. Follow `references/selector-discovery.md` exactly:
prefer role/text/aria selectors, verify each resolves to one visible element, and
**flag rather than guess** when a target can't be resolved. Validate with
`schema.py` (`discovered=True`) before recording.

### Steps 10–11 — verify & auto-fix (your job)

Run WhisperX, then both checks per `references/verification.md`: a local
transcript diff (regenerate audio on failure) and a Claude vision frame check
(re-discover + re-record on mismatch). Loop at most `max_fix_iterations` times,
recompose, and report any scenes still failing.

## Defaults

1080p / 30fps / 16:9 · captions on (soft `mov_text` track; `--burn-captions` if
available) · Kokoro `af_heart` · Chromium-rendered intro/outro cards · native
screencast cursor + click highlights · verify `full` · `max_fix_iterations` 2.

## Troubleshooting

- **`check_env.sh` says ffmpeg/uv missing** → `bash scripts/bootstrap.sh`.
- **Cards have no text / `import 'playwright'` fails** → run `npm install` in the
  skill directory (bootstrap does this). Cards render via Chromium.
- **`drawtext`/`subtitles` "Filter not found"** → expected on Homebrew ffmpeg;
  the skill uses Chromium cards + soft captions instead. See `ffmpeg-recipes.md`.
- **Recorder times out on a selector** → the scene probably assumes state from a
  previous scene. Each scene records fresh; add navigation to its actions.
- **Login fails** → confirm the env vars in `config.json` hold valid admin creds.

## Limitations

macOS only · WordPress admin only · requires a user-provided configured site ·
verification (vision check) consumes credits — tune with `verify` / `verify_sample`
/ `max_fix_iterations` · Kokoro is English-leaning; for branded/cloned voices see
`references/voices.md`.
