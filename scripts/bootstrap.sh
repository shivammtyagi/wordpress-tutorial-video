#!/usr/bin/env bash
# bootstrap.sh — idempotent installer for the wordpress-tutorial-video skill.
# macOS only. Installs: ffmpeg (brew), uv, a uv-managed venv with kokoro +
# whisperx, and Playwright >= 1.59 + Chromium.
#
#   scripts/bootstrap.sh            # install what is missing
#   scripts/bootstrap.sh --dry-run  # print the plan, install nothing
set -uo pipefail

DRY=0
[ "${1:-}" = "--dry-run" ] && DRY=1

HERE="$(cd "$(dirname "$0")/.." && pwd)"
VENV="$HERE/.venv"

say() { echo "[bootstrap] $*"; }
run() { if [ "$DRY" -eq 1 ]; then echo "  DRY-RUN: $*"; else eval "$*"; fi; }

if [ "$(uname -s)" != "Darwin" ]; then
  echo "ERROR: macOS only (detected $(uname -s))." >&2
  exit 2
fi

# 1. Homebrew
if ! command -v brew >/dev/null 2>&1; then
  say "Homebrew not found. Install it from https://brew.sh, then re-run."
  [ "$DRY" -eq 1 ] || exit 1
fi

# 2. ffmpeg / ffprobe
if command -v ffmpeg >/dev/null 2>&1 && command -v ffprobe >/dev/null 2>&1; then
  say "ffmpeg present — skipping."
else
  say "Installing ffmpeg via Homebrew."
  run "brew install ffmpeg"
fi

# 3. uv (Python package/venv manager)
if command -v uv >/dev/null 2>&1; then
  say "uv present — skipping."
else
  say "Installing uv via Homebrew."
  run "brew install uv"
fi

# 4. Python venv with Kokoro + WhisperX
if [ -d "$VENV" ]; then
  say "venv present at $VENV — ensuring packages."
else
  say "Creating venv at $VENV (Python 3.11 recommended for ML wheels)."
  run "uv venv --python 3.11 \"$VENV\""
fi
say "Installing kokoro, soundfile, whisperx into venv."
run "uv pip install --python \"$VENV/bin/python\" kokoro soundfile whisperx"

# 5. Playwright >= 1.59 (installed LOCALLY so `import 'playwright'` resolves
#    for record_scene.mjs and render_card.mjs) + Chromium browser.
say "Installing Playwright locally (npm) + Chromium."
run "cd \"$HERE\" && npm install"
run "cd \"$HERE\" && npx playwright install chromium"

echo ""
say "Done. Run scripts/check_env.sh to confirm."
