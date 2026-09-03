#!/usr/bin/env bash
# bootstrap.sh — idempotent installer for the wordpress-tutorial-video skill.
# macOS only. Installs: ffmpeg + espeak-ng (brew), uv, TWO uv-managed venvs
# (.venv: kokoro + whisperx for the audio gate and fallback TTS; .venv-cbx:
# chatterbox-tts, the default narration engine), and Playwright + Chromium.
#
#   scripts/bootstrap.sh            # install what is missing
#   scripts/bootstrap.sh --dry-run  # print the plan, install nothing
#
# Fails loudly: any install error aborts (set -e), and the script only reports
# success after scripts/check_env.sh verifies every required tool is present.
# On Apple Silicon the Python interpreter is pinned to an arm64 build even when
# Homebrew/uv are Intel binaries under Rosetta (x86_64 venvs cannot install
# PyTorch — there are no macOS x86_64 wheels anymore).
set -euo pipefail

DRY=0
[ "${1:-}" = "--dry-run" ] && DRY=1

HERE="$(cd "$(dirname "$0")/.." && pwd)"
VENV="$HERE/.venv"
VENV_CBX="$HERE/.venv-cbx"

say() { echo "[bootstrap] $*"; }
run() { if [ "$DRY" -eq 1 ]; then echo "  DRY-RUN: $*"; else eval "$*"; fi; }

if [ "$(uname -s)" != "Darwin" ]; then
  echo "ERROR: macOS only (detected $(uname -s))." >&2
  exit 2
fi

# Pin the interpreter architecture: on Apple Silicon always request an arm64
# CPython, regardless of the arch of brew/uv themselves.
PYSPEC="3.11"
if [ "$(uname -m)" = "arm64" ]; then
  PYSPEC="cpython-3.11-macos-aarch64-none"
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

# 2b. espeak-ng (Kokoro OOV fallback — REQUIRED; without it unknown words are silently skipped)
if command -v espeak-ng >/dev/null 2>&1; then
  say "espeak-ng present — skipping."
else
  say "Installing espeak-ng via Homebrew."
  run "brew install espeak-ng"
fi

# 3. uv (Python package/venv manager)
if command -v uv >/dev/null 2>&1; then
  say "uv present — skipping."
else
  say "Installing uv via Homebrew."
  run "brew install uv"
fi

_check_arch() {
  # $1 = venv path — on Apple Silicon the venv python must be arm64.
  [ "$(uname -m)" = "arm64" ] || return 0
  [ "$DRY" -eq 1 ] && return 0
  local m
  m="$("$1/bin/python" -c 'import platform; print(platform.machine())')"
  if [ "$m" != "arm64" ]; then
    echo "ERROR: $1 is $m, not arm64 — PyTorch has no macOS x86_64 wheels." >&2
    echo "       Delete $1 and re-run bootstrap (it pins $PYSPEC)." >&2
    exit 1
  fi
}

# 4. Audio-gate venv: kokoro (fallback TTS) + whisperx (WER gate + alignments)
if [ -d "$VENV" ]; then
  say "venv present at $VENV — ensuring packages."
else
  say "Creating venv at $VENV ($PYSPEC)."
  run "uv venv --python \"$PYSPEC\" \"$VENV\""
fi
_check_arch "$VENV"
say "Installing pinned kokoro + whisperx (+ spaCy model) into venv."
run "uv pip install --python \"$VENV/bin/python\" 'kokoro>=0.9.4,<1' soundfile 'whisperx>=3.8.6'"
run "uv pip install --python \"$VENV/bin/python\" 'en_core_web_sm@https://github.com/explosion/spacy-models/releases/download/en_core_web_sm-3.8.0/en_core_web_sm-3.8.0-py3-none-any.whl'"

# 4b. Narration venv: Chatterbox (default engine — natural prosody, MIT).
#     setuptools<81 is REQUIRED: newer setuptools removed pkg_resources, which
#     chatterbox's resemble-perth watermarker still imports.
if [ -d "$VENV_CBX" ]; then
  say "chatterbox venv present at $VENV_CBX — ensuring packages."
else
  say "Creating chatterbox venv at $VENV_CBX ($PYSPEC)."
  run "uv venv --python \"$PYSPEC\" \"$VENV_CBX\""
fi
_check_arch "$VENV_CBX"
say "Installing chatterbox-tts into chatterbox venv."
run "uv pip install --python \"$VENV_CBX/bin/python\" chatterbox-tts 'setuptools<81'"

# 5. Playwright >= 1.62 (installed LOCALLY so `import 'playwright'` resolves
#    for record_scene.mjs and render_card.mjs) + Chromium browser.
say "Installing Playwright locally (npm) + Chromium."
run "cd \"$HERE\" && npm install"
run "cd \"$HERE\" && npx playwright install chromium"

# 6. Verify — bootstrap only reports success when check_env agrees.
echo ""
if [ "$DRY" -eq 1 ]; then
  say "Dry run complete. Run scripts/check_env.sh after a real install."
else
  say "Verifying with check_env.sh:"
  if bash "$HERE/scripts/check_env.sh"; then
    say "Done — environment verified."
  else
    say "FAILED — one or more required tools are missing (see table above)." >&2
    exit 1
  fi
fi
