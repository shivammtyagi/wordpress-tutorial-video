# wordpress-tutorial-video Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a public, open-source Claude Code skill that turns a WordPress documentation URL into a narrated, captioned tutorial MP4 recorded against a user-provided WordPress site.

**Architecture:** An orchestrator playbook (`SKILL.md`) drives a pipeline of resumable steps. Claude personally performs judgment steps (parse, script, selector discovery, vision verify, auto-fix); bundled scripts perform deterministic steps (fetch, TTS, record, post-process, compose, transcribe, captions). All steps communicate through a per-run working directory and record completion in `state.json`.

**Tech Stack:** Python 3 (uv-managed venv) for fetch/TTS/compose/transcribe/captions; Node + Playwright (`page.screencast`) for recording; FFmpeg for compositing; Kokoro-82M for TTS; WhisperX for transcription/alignment; Homebrew for system deps. macOS only.

## Global Constraints

- Platform: **macOS only** (Apple Silicon recommended). Bootstrap via Homebrew.
- No paid dependencies. License: **MIT**. TTS default **Kokoro-82M** (Apache-2.0). Transcribe **WhisperX**.
- Playwright **≥ 1.59** (uses `page.screencast`; never `recordVideo`).
- The skill never provisions a site — the **user provides** a reachable WP site + admin login.
- Every step is **resumable**: reads inputs from the run dir, writes atomically, records input-hash in `state.json`, skips when unchanged, `--force` re-runs, exits non-zero with a clear message on failure.
- Scene schema is authoritative in `references/scene-schema.md`. Recording reads only `script.discovered.json`.
- Defaults: 1080p / 30fps / 16:9; captions on; Kokoro `af_heart`; verify full; `max_fix_iterations=2`; record on network-idle.
- All scripts accept `--run-dir <path>` and `--force`; Python scripts are runnable via the venv; no script hard-codes any plugin/company name.

---

### Task 1: Repo scaffold, metadata, env check

**Files:**
- Create: `SKILL.md` (skeleton with valid frontmatter)
- Create: `README.md`, `LICENSE` (MIT)
- Create: `scripts/check_env.sh`
- Create: `scripts/lib/run_dir.py` (shared run-dir + state helpers)
- Test: `tests/test_run_dir.py`

**Interfaces:**
- Produces: `run_dir.py` exposing `load_config(run_dir) -> dict`, `mark_done(run_dir, step, inputs: list[str])`, `is_done(run_dir, step, inputs: list[str]) -> bool`, `atomic_write(path, data)`, `slug_for(url) -> str`.
- `check_env.sh` prints a table of tool → version → ok/missing and exits 0 only if all required present.

- [ ] **Step 1: Write failing test for state helpers**

```python
# tests/test_run_dir.py
import json, os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts", "lib"))
import run_dir as rd

def test_mark_and_is_done(tmp_path):
    d = str(tmp_path)
    (tmp_path / "a.txt").write_text("hello")
    inputs = [str(tmp_path / "a.txt")]
    assert rd.is_done(d, "step2", inputs) is False
    rd.mark_done(d, "step2", inputs)
    assert rd.is_done(d, "step2", inputs) is True
    (tmp_path / "a.txt").write_text("changed")
    assert rd.is_done(d, "step2", inputs) is False  # input hash changed

def test_slug_for():
    assert rd.slug_for("https://example.com/docs/xml-sitemaps/") == "xml-sitemaps"
```

- [ ] **Step 2: Run test, verify it fails** — `python3 -m pytest tests/test_run_dir.py -v` → FAIL (module not found).

- [ ] **Step 3: Implement `scripts/lib/run_dir.py`**

```python
import hashlib, json, os, re, tempfile

def _hash_inputs(inputs):
    h = hashlib.sha256()
    for p in sorted(inputs):
        h.update(p.encode())
        if os.path.exists(p):
            with open(p, "rb") as f:
                h.update(f.read())
    return h.hexdigest()

def _state_path(run_dir):
    return os.path.join(run_dir, "state.json")

def _load_state(run_dir):
    p = _state_path(run_dir)
    return json.load(open(p)) if os.path.exists(p) else {}

def atomic_write(path, data):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path) or ".")
    with os.fdopen(fd, "wb" if isinstance(data, bytes) else "w") as f:
        f.write(data)
    os.replace(tmp, path)

def mark_done(run_dir, step, inputs):
    st = _load_state(run_dir)
    st[step] = {"input_hash": _hash_inputs(inputs)}
    atomic_write(_state_path(run_dir), json.dumps(st, indent=2))

def is_done(run_dir, step, inputs):
    st = _load_state(run_dir)
    return step in st and st[step].get("input_hash") == _hash_inputs(inputs)

def load_config(run_dir):
    return json.load(open(os.path.join(run_dir, "config.json")))

def slug_for(url):
    parts = [p for p in re.split(r"[/?#]", url) if p]
    tail = parts[-1] if parts else "video"
    return re.sub(r"[^a-z0-9]+", "-", tail.lower()).strip("-") or "video"
```

- [ ] **Step 4: Run test, verify pass** — `python3 -m pytest tests/test_run_dir.py -v` → PASS.

- [ ] **Step 5: Write `scripts/check_env.sh`** — checks `ffmpeg`, `ffprobe`, `uv`, `python3`, `node`, and Playwright version; prints a `tool | version | status` table; exits non-zero listing missing tools. Include a Playwright `>=1.59` numeric check.

- [ ] **Step 6: Write `SKILL.md` frontmatter skeleton, `README.md`, `LICENSE`** — frontmatter `name: wordpress-tutorial-video`, the description from the spec, and the section headers (filled in Task 12). README: what/why/requirements (macOS)/install/usage/limitations. LICENSE: MIT.

- [ ] **Step 7: Commit** — `git add -A && git commit -m "feat: scaffold skill, env check, run-dir state helpers"`.

---

### Task 2: bootstrap.sh

**Files:**
- Create: `scripts/bootstrap.sh`
- Test: `tests/test_bootstrap_dryrun.sh`

**Interfaces:**
- Consumes: `check_env.sh`.
- Produces: idempotent installer. Supports `--dry-run` (prints the plan, installs nothing).

- [ ] **Step 1: Write failing test** — `bash scripts/bootstrap.sh --dry-run` should exit 0 and mention `ffmpeg`, `uv`, `playwright`, `kokoro`, `whisperx`. Assert with `grep`.
- [ ] **Step 2: Run, verify fail** (script missing).
- [ ] **Step 3: Implement `bootstrap.sh`** — detect macOS; `brew install ffmpeg` if missing; install `uv` if missing; `uv venv .venv`; `uv pip install kokoro soundfile whisperx`; `npm i -g playwright@latest || true` and `npx playwright install chromium`. Each step guarded by a presence check. `--dry-run` short-circuits each install with an echo.
- [ ] **Step 4: Run, verify pass.**
- [ ] **Step 5: Commit** — `feat: idempotent macOS bootstrap`.

---

### Task 3: Scene schema reference + validator

**Files:**
- Create: `references/scene-schema.md`
- Create: `scripts/lib/schema.py` (validation)
- Test: `tests/test_schema.py`

**Interfaces:**
- Produces: `schema.py` exposing `validate_script(obj) -> list[str]` (returns list of error strings, empty == valid) for both `script.json` (selectors may be null) and `script.discovered.json` (selectors required non-null).

- [ ] **Step 1: Write failing tests**

```python
# tests/test_schema.py
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts", "lib"))
import schema

VALID = {"title":"T","resolution":"1920x1080","fps":30,"voice":"af_heart",
  "scenes":[{"id":"01","narration":"Hi","intent":"open","actions":[
    {"type":"click","target":"Menu","selector":None,"highlight":False}],
    "focus_selector":None,"hold_after_ms":800,"verify":{"expect_on_screen":"x"}}]}

def test_valid_predisco():
    assert schema.validate_script(VALID, discovered=False) == []

def test_missing_narration():
    bad = {**VALID, "scenes":[{**VALID["scenes"][0]}]}; del bad["scenes"][0]["narration"]
    assert any("narration" in e for e in schema.validate_script(bad, discovered=False))

def test_discovered_requires_selector():
    errs = schema.validate_script(VALID, discovered=True)
    assert any("selector" in e for e in errs)
```

- [ ] **Step 2: Run, verify fail.**
- [ ] **Step 3: Implement `schema.py`** — pure-python checks (no jsonschema dep): top-level `title/resolution/fps/voice/scenes`; each scene `id/narration/intent/actions/hold_after_ms/verify.expect_on_screen`; each action `type in {click,type,scroll,hover,wait,goto}`, `target`; when `discovered=True`, every action `selector` and each scene `focus_selector` must be non-null.
- [ ] **Step 4: Run, verify pass.**
- [ ] **Step 5: Write `references/scene-schema.md`** — authoritative prose contract matching `schema.py`, with the annotated JSON example from the spec and the action-type table.
- [ ] **Step 6: Commit** — `feat: scene schema + validator`.

---

### Task 4: fetch_doc.py

**Files:**
- Create: `scripts/fetch_doc.py`
- Test: `tests/test_fetch_doc.py`

**Interfaces:**
- Consumes: `config.json` (`doc_url`).
- Produces: `doc.md` (cleaned text + a `## Images` list of extracted image URLs/alt). CLI: `fetch_doc.py --run-dir <d> [--force]`.

- [ ] **Step 1: Write failing test** — feed a local HTML fixture file via `--html-file` (test hook), assert `doc.md` contains the heading text and excludes `<script>`/`<style>` content and nav chrome.
- [ ] **Step 2: Run, verify fail.**
- [ ] **Step 3: Implement** — use `urllib` for fetch, a minimal HTML-to-text (strip script/style/nav/footer, keep headings/paragraphs/list items/images). Respect run-dir contract; honor `is_done`/`--force`. `--html-file` test hook bypasses network.
- [ ] **Step 4: Run, verify pass.**
- [ ] **Step 5: Commit** — `feat: doc fetch + clean`.

---

### Task 5: tts_kokoro.py

**Files:**
- Create: `scripts/tts_kokoro.py`
- Test: `tests/test_tts_contract.py`

**Interfaces:**
- Consumes: `script.json` (or `script.discovered.json`), `voice`.
- Produces: `audio/NN.wav` per scene + `audio/durations.json` mapping `id -> seconds`. CLI: `tts_kokoro.py --run-dir <d> [--voice af_heart] [--force]`.

- [ ] **Step 1: Write failing contract test** — with a `--engine stub` flag (writes 1s of silence per scene via `soundfile` or raw WAV header), assert two wavs are produced and `durations.json` has positive floats for each scene id. (Keeps the test runnable without the Kokoro model.)
- [ ] **Step 2: Run, verify fail.**
- [ ] **Step 3: Implement** — default engine loads Kokoro `KPipeline`, synthesizes each scene's narration to a 24kHz wav, measures duration via frames/samplerate; `--engine stub` path for tests. Write `durations.json`. Run-dir contract.
- [ ] **Step 4: Run, verify pass** (stub engine).
- [ ] **Step 5: Commit** — `feat: per-scene Kokoro TTS + durations`.

---

### Task 6: record_scene.mjs + static test page

**Files:**
- Create: `scripts/record_scene.mjs`
- Create: `tests/fixtures/page.html` (a static admin-like page with a menu + button)
- Test: `tests/test_record_scene.mjs` (node test)

**Interfaces:**
- Consumes: `script.discovered.json` (one scene by `--scene-id`), `durations.json`.
- Produces: `clips/NN.raw.webm`. CLI: `node record_scene.mjs --run-dir <d> --scene-id 01 [--base-url <url>] [--force]`.

- [ ] **Step 1: Write failing node test** — launch against `file://tests/fixtures/page.html`, run a 2-action scene (click menu, click button with `highlight`), assert a non-empty `clips/01.raw.webm` is produced.
- [ ] **Step 2: Run, verify fail.**
- [ ] **Step 3: Implement** — Playwright Chromium; set viewport to scene resolution; login flow when `base-url` is a WP site (reads creds from env vars named in config); `page.screencast` start/stop; per-action: wait for selector, human-speed move/type, click-ripple overlay (pointer-events:none injected div) when `highlight`; wait network-idle; `hold_after_ms`; pace toward `durations.json[id]`. Against `file://` fixture, skip login.
- [ ] **Step 4: Run, verify pass.**
- [ ] **Step 5: Commit** — `feat: deterministic per-scene screencast recorder`.

---

### Task 7: postprocess_clip.py

**Files:**
- Create: `scripts/postprocess_clip.py`
- Test: `tests/test_postprocess.py`

**Interfaces:**
- Consumes: `clips/NN.raw.webm`, `durations.json`, scene `focus_selector` bbox (optional sidecar `clips/NN.focus.json` written by recorder).
- Produces: `clips/NN.final.mp4` at target res/fps, length == `max(clip_len, narration_len)`. CLI: `postprocess_clip.py --run-dir <d> --scene-id 01 [--force]`.

- [ ] **Step 1: Write failing test** — generate a 2s synthetic webm via ffmpeg `testsrc` (skip test with clear message if ffmpeg absent), narration 4s → assert output mp4 duration ≈ 4s (ffprobe).
- [ ] **Step 2: Run, verify fail.**
- [ ] **Step 3: Implement** — ffmpeg: scale/pad to target, optional zoom/pan toward focus bbox (Ken Burns), pad/hold last frame to narration length (`tpad`), normalize fps. Skip cleanly if ffmpeg missing.
- [ ] **Step 4: Run, verify pass.**
- [ ] **Step 5: Commit** — `feat: clip post-process + narration padding`.

---

### Task 8: compose.py

**Files:**
- Create: `scripts/compose.py`
- Test: `tests/test_compose.py`

**Interfaces:**
- Consumes: `clips/*.final.mp4`, `audio/*.wav`, `captions.srt`, `assets/intro_template`.
- Produces: `output/final.mp4`. CLI: `compose.py --run-dir <d> [--no-captions] [--force]`.

- [ ] **Step 1: Write failing test** — two synthetic clips + two silent wavs + a tiny SRT → assert `output/final.mp4` is playable and duration ≈ sum (ffprobe). Skip if ffmpeg absent.
- [ ] **Step 2: Run, verify fail.**
- [ ] **Step 3: Implement** — per scene mux clip+wav; concat with crossfade (`xfade`); prepend/append intro/outro card (drawtext doc title); burn captions (`subtitles=`) unless `--no-captions`. Use filter_complex via a generated script file.
- [ ] **Step 4: Run, verify pass.**
- [ ] **Step 5: Commit** — `feat: ffmpeg compose with transitions, intro/outro, captions`.

---

### Task 9: transcribe_whisperx.py + make_captions.py

**Files:**
- Create: `scripts/transcribe_whisperx.py`
- Create: `scripts/make_captions.py`
- Test: `tests/test_make_captions.py`

**Interfaces:**
- `transcribe_whisperx.py`: consumes `output/final.mp4` (or per-scene wavs) → `verify/transcript.json` (words with start/end).
- `make_captions.py`: consumes `verify/transcript.json` → `captions.srt`. Exposes `words_to_srt(words) -> str`.

- [ ] **Step 1: Write failing test for `words_to_srt`**

```python
# tests/test_make_captions.py
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
from make_captions import words_to_srt

def test_basic_srt():
    words=[{"word":"Hello","start":0.0,"end":0.5},{"word":"world","start":0.5,"end":1.0}]
    srt = words_to_srt(words, max_chars=40)
    assert "1\n00:00:00,000 --> 00:00:01,000\nHello world" in srt
```

- [ ] **Step 2: Run, verify fail.**
- [ ] **Step 3: Implement `make_captions.py`** — group words into ≤max_chars/≤~3.5s cues; format SRT timestamps `HH:MM:SS,mmm`. `transcribe_whisperx.py` loads faster-whisper + alignment, writes `transcript.json`; `--engine stub` reads narration as the "transcript" for offline tests.
- [ ] **Step 4: Run, verify pass.**
- [ ] **Step 5: Commit** — `feat: WhisperX transcribe + SRT captions`.

---

### Task 10: References (selector-discovery, recording-tuning, ffmpeg-recipes, verification, voices)

**Files:**
- Create: `references/selector-discovery.md`, `references/recording-tuning.md`, `references/ffmpeg-recipes.md`, `references/verification.md`, `references/voices.md`

**Interfaces:** Prose contracts loaded on demand by SKILL.md (progressive disclosure).

- [ ] **Step 1: Write `selector-discovery.md`** — exact procedure: log in, navigate per scene intent, use Playwright accessibility snapshot, prefer role/text/aria selectors, verify single visible match, resolve `focus_selector`, flag-don't-guess on failure, write `script.discovered.json`.
- [ ] **Step 2: Write `recording-tuning.md`** — `page.screencast` options, human-speed typing/scroll constants, click-ripple overlay snippet, network-idle waits, CDP→FFmpeg fallback note.
- [ ] **Step 3: Write `ffmpeg-recipes.md`** — copy-paste command templates for pad-to-duration, Ken Burns zoom, xfade concat, drawtext intro/outro, subtitles burn-in, audio mux.
- [ ] **Step 4: Write `verification.md`** — text-diff WER computation + threshold, vision frame-grab + `expect_on_screen` prompt template + verdict schema, flags (`verify`, `verify_sample`, `wer_threshold`, `max_fix_iterations`), auto-fix decision tree.
- [ ] **Step 5: Write `voices.md`** — Kokoro voice ids + selection guidance; Chatterbox-Turbo opt-in upgrade (MPS/CPU, English) instructions.
- [ ] **Step 6: Commit** — `docs: pipeline reference guides`.

---

### Task 11: SKILL.md orchestrator playbook + assets

**Files:**
- Modify: `SKILL.md` (full body)
- Create: `assets/intro_card.html` (rendered to card via screenshot or drawtext), `assets/cursor.svg`

**Interfaces:** The end-to-end playbook Claude follows; references the run-dir contract, each script's CLI, and links each `references/*.md` at the step that needs it.

- [ ] **Step 1: Write the full SKILL.md body** — sections: When to use / Requirements (macOS, user-provided site) / Inputs & config.json shape / Step-by-step pipeline (0–12) each naming the exact command or the Claude action and the run-dir artifacts / Resumability rules / Verification & auto-fix loop with iteration cap / Flags / Troubleshooting / Limitations. Keep step bodies tight; defer depth to `references/*`.
- [ ] **Step 2: Create assets** — neutral intro/outro card source + cursor spotlight SVG.
- [ ] **Step 3: Validate** — run `scripts/check_env.sh`; run the full pytest + node test suite; confirm SKILL.md frontmatter parses (name/description present, description starts with a trigger phrase).
- [ ] **Step 4: Commit** — `feat: orchestrator playbook + intro/cursor assets`.

---

### Task 12: End-to-end dry run + README polish

**Files:**
- Modify: `README.md`
- Create: `tests/test_dryrun.sh`

- [ ] **Step 1: Write `--dry-run` orchestration check** — a script/test that, given a sample `config.json`, validates env + config + schema without recording, and prints the planned run-dir layout.
- [ ] **Step 2: Run full test suite** — `python3 -m pytest -q` and node tests; record pass/fail honestly.
- [ ] **Step 3: Polish README** — badges, requirements, install (`bootstrap.sh`), quickstart, config reference, cost note (verification), limitations, license.
- [ ] **Step 4: Commit** — `docs: README + dry-run validation`.

## Self-Review

**Spec coverage:** Every spec section maps to a task — layout/metadata (T1), bootstrap/deps (T2,§13), schema (T3,§7), fetch (T4,§8.2), TTS (T5,§8.5), record (T6,§10), post-process (T7,§10), compose (T8,§11), transcribe+captions (T9,§12), discovery (T10 ref + SKILL step §9), verification (T10 ref + SKILL §12), playbook (T11,§5/§8), testing (all tasks + T12,§15). No gaps.

**Placeholder scan:** No TBD/TODO; each code task has real test + impl code or a concrete content outline for prose files.

**Type consistency:** `run_dir` helpers (`is_done/mark_done/load_config/slug_for/atomic_write`), `schema.validate_script(obj, discovered=)`, `make_captions.words_to_srt(words, max_chars)` referenced consistently across tasks. Artifact filenames match the run-dir layout in §6 throughout.
