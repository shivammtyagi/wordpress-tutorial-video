#!/usr/bin/env bash
# check_env.sh — report presence + versions of every tool the pipeline needs.
# Exits 0 only when all REQUIRED tools are present. macOS only.
set -uo pipefail

missing=0
printf "%-14s %-14s %s\n" "TOOL" "VERSION" "STATUS"
printf "%-14s %-14s %s\n" "----" "-------" "------"

check() {
  # $1=name $2=version-cmd $3=required(yes/no)
  local name="$1" vercmd="$2" required="$3" ver status
  if command -v "${name%% *}" >/dev/null 2>&1; then
    ver="$(eval "$vercmd" 2>/dev/null | head -1)"
    status="ok"
  else
    ver="-"
    if [ "$required" = "yes" ]; then status="MISSING"; missing=$((missing+1)); else status="optional"; fi
  fi
  printf "%-14s %-14s %s\n" "$name" "${ver:0:14}" "$status"
}

# OS guard
if [ "$(uname -s)" != "Darwin" ]; then
  echo "ERROR: this skill supports macOS only (detected $(uname -s))." >&2
  exit 2
fi

check "ffmpeg"  "ffmpeg -version | awk '{print \$3; exit}'"   yes
check "ffprobe" "ffprobe -version | awk '{print \$3; exit}'"  yes
check "python3" "python3 --version | awk '{print \$2}'"        yes
check "node"    "node --version"                               yes
check "uv"      "uv --version | awk '{print \$2}'"             yes
check "espeak-ng" "espeak-ng --version | awk '{print \$4}'"    yes
check "brew"    "brew --version | awk 'NR==1{print \$2}'"      no

# Python venv with the TTS/ASR stack (created by bootstrap.sh)
VENV="$(cd "$(dirname "$0")/.." && pwd)/.venv"
venv_status="MISSING"; venv_ver="-"
if [ -x "$VENV/bin/python" ]; then
  venv_ver="$("$VENV/bin/python" --version 2>/dev/null | awk '{print $2}')"
  venv_status="ok"
else
  missing=$((missing+1))
fi
printf "%-14s %-14s %s\n" "venv(py)" "${venv_ver:0:14}" "$venv_status"

for pkg in kokoro whisperx; do
  st="MISSING"
  if [ -x "$VENV/bin/python" ] && "$VENV/bin/python" -c "import $pkg" >/dev/null 2>&1; then
    st="ok"
  else
    missing=$((missing+1))
  fi
  printf "%-14s %-14s %s\n" "$pkg" "-" "$st"
done

# Chatterbox venv (default narration engine, created by bootstrap.sh)
VENV_CBX="$(cd "$(dirname "$0")/.." && pwd)/.venv-cbx"
cbx_status="MISSING"; cbx_ver="-"
if [ -x "$VENV_CBX/bin/python" ]; then
  cbx_ver="$("$VENV_CBX/bin/python" --version 2>/dev/null | awk '{print $2}')"
  if "$VENV_CBX/bin/python" -c "import chatterbox" >/dev/null 2>&1; then
    cbx_status="ok"
  else
    missing=$((missing+1))
  fi
else
  missing=$((missing+1))
fi
printf "%-14s %-14s %s\n" "venv-cbx(py)" "${cbx_ver:0:14}" "$cbx_status"

# Playwright >= 1.59 check (via npx; may be slow on first run)
pw_status="MISSING"; pw_ver="-"
if pw_raw="$(npx --yes playwright --version 2>/dev/null)"; then
  pw_ver="$(echo "$pw_raw" | awk '{print $2}')"
  major="$(echo "$pw_ver" | cut -d. -f1)"; minor="$(echo "$pw_ver" | cut -d. -f2)"
  if [ "${major:-0}" -gt 1 ] || { [ "${major:-0}" -eq 1 ] && [ "${minor:-0}" -ge 59 ]; }; then
    pw_status="ok"
  else
    pw_status="TOO OLD (<1.59)"; missing=$((missing+1))
  fi
else
  missing=$((missing+1))
fi
printf "%-14s %-14s %s\n" "playwright" "${pw_ver:0:14}" "$pw_status"

if [ "${1:-}" = "--deep" ] && [ -x "$VENV/bin/python" ]; then
  echo ""
  echo "Running TTS + alignment self-test (~30s first run: model downloads)..."
  if "$VENV/bin/python" - <<'PY' 2>/dev/null
import numpy as np, tempfile, wave, os
from kokoro import KPipeline
p = KPipeline(lang_code="a")
chunks = [a for _, _, a in p("Testing alignment.", voice="af_heart")]
audio = np.concatenate(chunks)
f = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
with wave.open(f.name, "wb") as w:
    w.setnchannels(1); w.setsampwidth(2); w.setframerate(24000)
    w.writeframes((np.clip(audio, -1, 1) * 32767).astype("<i2").tobytes())
import whisperx
m = whisperx.load_model("tiny.en", "cpu", compute_type="int8")
a = whisperx.load_audio(f.name)
r = m.transcribe(a)
am, meta = whisperx.load_align_model("en", "cpu")
al = whisperx.align(r["segments"], am, meta, a, "cpu")
assert any(s.get("words") for s in al["segments"])
os.unlink(f.name)
PY
  then echo "alignment: whisperx ok"
  else echo "alignment: whisperx FAILED — pipeline will fall back to faster-whisper word timestamps"
  fi
fi

echo ""
if [ "$missing" -gt 0 ]; then
  echo "RESULT: $missing required tool(s) missing. Run scripts/bootstrap.sh to install." >&2
  exit 1
fi
echo "RESULT: all required tools present."
