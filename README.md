# WordPress Tutorial Video

A [Claude Code](https://claude.com/claude-code) skill that turns a **WordPress
documentation URL** into a finished, narrated, captioned tutorial **MP4** —
recorded against your own WordPress site.

Give it a doc URL and a running WordPress site with an admin login. It reads the
doc, writes a beginner-friendly scene script, generates the voiceover, discovers
the real UI selectors on your live site, records one short clip per scene, composes
everything with FFmpeg, verifies that the narration matches what's on screen,
auto-fixes the scenes that don't, and hands you the final video.

Works for **any** WordPress plugin, theme, or admin feature. The only assumption is
that the target is WordPress.

> **Status:** macOS only (Apple Silicon recommended). Free and open source (MIT).
> No paid dependencies.

## How it works

```
doc URL ─▶ parse ─▶ script ─▶ voiceover ─▶ discover selectors ─▶ record scenes
        ─▶ post-process ─▶ compose (FFmpeg) ─▶ verify (WhisperX + vision) ─▶ MP4
```

Two ideas make it reliable:

1. **The script is the spine.** Everything hangs off a scene JSON contract.
2. **Audio first.** Each scene's clip is paced to its narration length
   (`max(clip, narration)`), so audio and video never drift.

## Requirements

- **macOS** (Homebrew). Apple Silicon recommended.
- A reachable **WordPress site** with an admin login and the feature already set up.
  The skill records your site — it does not create one.
- Node ≥ 18 and Python 3. Everything else is installed by the bootstrap script.

## Install

```bash
git clone <this-repo> wordpress-tutorial-video
cd wordpress-tutorial-video
bash scripts/bootstrap.sh        # ffmpeg, uv venv (Kokoro + WhisperX), Playwright + Chromium
bash scripts/check_env.sh        # confirm everything is present
```

To use it as a personal Claude Code skill, place this directory in your skills
folder (e.g. `~/.claude/skills/wordpress-tutorial-video`).

## Usage

In Claude Code, point the skill at a doc and your site:

> "Use the wordpress-tutorial-video skill to make a tutorial from
> `https://example.com/docs/xml-sitemaps/` against my site `https://my-wp.test`."

Provide admin credentials via environment variables (referenced by name in
`config.json`, never stored in the repo):

```bash
export WP_ADMIN_USER="admin"
export WP_ADMIN_PASS="…"
```

The skill creates `runs/<slug>/`, runs the pipeline step by step, and writes the
result to `runs/<slug>/output/final.mp4`.

## Configuration

See `config.json` in [SKILL.md](SKILL.md#inputs--configjson). Key knobs:

| Setting | Default | Notes |
|---------|---------|-------|
| `resolution` / `fps` | `1920x1080` / `30` | 16:9. |
| `voice` | `af_heart` | Kokoro voice (see [references/voices.md](references/voices.md)). |
| `verify` | `full` | `full` (text + vision), `text` (free), or `off`. |
| `verify_sample` | `0` | Run the vision check on N scenes only (0 = all). |
| `max_fix_iterations` | `2` | Cap on auto-fix rounds. |

## Cost note

The verification loop sends a frame per scene to Claude vision — that's the
credit-heavy step, and it's on by default for quality. Dial it down with
`verify=text`, `verify_sample`, or `verify=off`. The transcript diff and captions
are local and free.

## The stack

| Layer | Tool |
|-------|------|
| Parse & script | Claude Code |
| Voiceover | [Kokoro-82M](https://huggingface.co/hexgrad/Kokoro-82M) (Apache-2.0); Chatterbox-Turbo optional |
| Record | Playwright `page.screencast` (≥ 1.59) |
| Compose | FFmpeg |
| Verify & captions | [WhisperX](https://github.com/m-bain/whisperX) + Claude vision |

## Limitations

macOS only · WordPress admin only · needs a user-provided configured site ·
verification consumes credits (tunable) · Kokoro is English-leaning.

## Development

```bash
python3 -m pytest -q          # python steps (ffmpeg tests skip if ffmpeg absent)
node tests/test_record_scene.mjs   # recorder against a static fixture (no WordPress)
```

## License

MIT — see [LICENSE](LICENSE).
