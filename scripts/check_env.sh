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
check "brew"    "brew --version | awk 'NR==1{print \$2}'"      no

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

echo ""
if [ "$missing" -gt 0 ]; then
  echo "RESULT: $missing required tool(s) missing. Run scripts/bootstrap.sh to install." >&2
  exit 1
fi
echo "RESULT: all required tools present."
