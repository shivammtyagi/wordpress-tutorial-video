# Design: Production Hardening — `wordpress-tutorial-video` v2

**Date:** 2026-08-12
**Status:** Approved (approach B); ready for implementation planning
**Supersedes/extends:** `2026-06-25-wordpress-tutorial-video-design.md` (architecture unchanged; this spec hardens it)

## 1. Summary

The v1 skill has a validated architecture but has never produced a video, and code review +
field research surfaced concrete defects and missing operational practice. This spec makes the
skill production-ready: fix every confirmed bug, adopt the researched reliability/quality layer,
capture at 2x for crisp text, verify audio *before* recording, and prove the whole pipeline with
a live end-to-end pilot against `aioseo.local`.

Research provenance: AM Skills marketplace sweep (20 video skills), GitHub sweep (~40 repos),
Reddit sweep (30+ threads, 2025–2026), TTS/ASR state-of-the-art audit. Full notes in the session
research file; key findings inline below.

## 2. Locked decisions (user, 2026-08-12)

| Decision | Choice |
|---|---|
| Approach | **B — production hardening + quality engine** (not bugfix-only; ambitious extras deferred) |
| Platform | macOS only, hardened. No Linux/Windows work. |
| Dependencies | **Strictly FOSS.** No paid code paths, even optional. Paid alternatives docs-only. |
| Capture | **2x deviceScaleFactor always** → 4K master. Deliver crisp 1080p by default; `deliver_4k` flag adds full-4K output. |
| Chapter cards | **Off by default** (they blur ~2s at every scene start). Scene intents become embedded MP4 chapter metadata. Config can re-enable. |
| Stack | Keep Kokoro-82M, WhisperX, FFmpeg, Playwright screencast — all re-validated as the 2026 FOSS optimum. |
| Pilot | End-to-end run on `aioseo.local` (Local by Flywheel), doc = <https://aioseo.com/docs/how-to-create-an-xml-sitemap/>, `claude` admin user via wp-cli/DB socket. |

## 3. Confirmed defects to fix (from code review)

| # | Defect | Fix |
|---|---|---|
| D1 | `compose.py` `_mux_scene` uses `-shortest` → scene **video truncated to audio length** when actions outlast narration | Mux with `-af apad -t <video_len>`; audio padded with silence to clip length |
| D2 | `record_scene.mjs` pacing timer starts **before login** → scenes under-record; frozen-frame padding | Reset timer at `screencast.start()`; pace only recorded time |
| D3 | Ken Burns `zoompan` defaults to x=0,y=0 → zooms into **top-left corner**; `NN.focus.json` written but never read | Center default; when focus box exists, pan/zoom toward the box (computed at master resolution) |
| D4 | **Triple lossy encode** (postprocess x264 → mux x264 → concat x264) | Single x264 encode in postprocess (`-crf 18 -preset medium`); mux and concat use `-c:v copy` (uniform params guaranteed by postprocess) |
| D5 | No loudness normalization; no faststart | `loudnorm=I=-16:TP=-1.5:LRA=11` on narration during mux (voice-first; −16 LUFS — field warning: −14 makes TTS sound robotic); `-movflags +faststart` on final |
| D6 | Hand-rolled `humanType` char loop (`press` per char) | `locator.pressSequentially(text, {delay})` (Playwright's own recommendation) |
| D7 | Self-signed certs unsupported (Local sites) | `ignoreHTTPSErrors` context option via `ignore_https_errors` config (default true) |
| D8 | Login failure undetected → confusing selector timeout later | After submit, assert wp-admin reached (URL + `body.wp-admin`); exit with clear message; detect mid-run `wp-login.php` redirects (session expired) |
| D9 | Record skip logic is file-existence, not input-hash | `is_done`-style hash of (scene JSON + narration duration) recorded in `state.json`; script edits retrigger re-record |
| D10 | Per-scene WER impossible (final.mp4 transcript has no scene attribution; stub-only `scene` field) | Per-scene transcription (§5 Audio gate) |
| D11 | `compose.py` never marks state; tts regenerates all scenes every run | Mark state; per-scene TTS cache keyed by hash(voice, speed, normalized text) |
| D12 | tts stub comment says 0.06s/word, code uses 0.4 | Fix comment |
| D13 | check_env.sh ignores venv contents (kokoro/whisperx/espeak-ng unchecked) | Check venv imports + espeak-ng + spaCy model; clear remediation hints |

## 4. Pipeline v2 (reordered)

The big structural change: **the audio gate moves verification before recording**, and captions
derive from per-scene alignments instead of transcribing the finished video.

| # | Step | Owner | Output |
|---|------|-------|--------|
| 0 | Env check / bootstrap | scripts | deps ready |
| 1 | Run dir + config | Claude | `config.json` |
| 2 | Fetch & parse doc | `fetch_doc.py` | `doc.md` |
| 3–4 | Write + self-review script | Claude | `script.json` |
| 5 | TTS (normalized text, cached per line) | `tts_kokoro.py` | `audio/NN.wav`, `durations.json` |
| **5b** | **Audio gate**: per-scene transcribe + normalized WER; regenerate failing scenes (bounded) | `verify_scenes.py` | `verify/scenes/NN.json`, `verify/audio_report.json` |
| 6 | Selector discovery (unchanged) | Claude | `script.discovered.json` |
| 7 | Record per scene (v2: setup phase → record → cue-timed actions) | `record_scene.mjs` | `clips/NN.raw.webm` (4K), `clips/NN.focus.json` |
| 8 | Post-process (single encode; focus zoom; CFR; 1080p [+4K]) | `postprocess_clip.py` | `clips/NN.final.mp4` |
| 9 | Compose: copy-mux + loudnorm audio, copy-concat, cards; writes `output/timeline.json`, invokes captions (below), then final mux with chapters + soft captions | `compose.py` | `output/final.mp4`, `output/timeline.json` |
| 9a | Captions from script text + per-scene alignments + compose timeline (invoked by compose; standalone fallback transcribes final.mp4) | `make_captions.py` | `captions.srt` |
| 10 | Verify: vision frame check (+ optional final-audio spot check) | Claude + `grab_frames.py` | `verify/report.json` |
| 11 | Auto-fix flagged scenes (bounded), evidence clips | Claude + scripts | updated artifacts |
| 12 | Deliver | Claude | `output/final.mp4` (+`final-4k.mp4`) |

Rationale for 5b (field-converged): catches TTS misreads before any recording time is spent;
gives true per-scene WER (D10); word offsets enable narrate-then-act cues (§6) and exact caption
timing (§8). Two independent production teams on Reddit run precisely this transcribe-back gate.

## 5. Audio layer v2

**Text normalization (new `scripts/lib/normalize.py`).** Deterministic pre-TTS pass; the same
normalized string feeds TTS and the WER reference; captions show original script text.
- Numbers/ordinals/versions: `num2words` (already a misaki dependency); `v5.9.3` → "version five point nine point three".
- Tech tokens (hand-rolled rules): `wp-admin` → "W P admin"; URLs → scheme stripped, "dot"/"slash" spoken; `.php` → "dot P H P"; ALL-CAPS acronyms spelled.
- Pronunciation lexicon `references/lexicon.json` (+ per-run override in config): term → IPA via
  Kokoro's markdown-link syntax (`[AIOSEO](/ˌeɪaɪoʊˈɛsioʊ/)`) or `g2p.lexicon.golds` entries.

**Kokoro operations (from the TTS audit):**
- Pin `kokoro>=0.9.4` (earlier versions silently skipped OOV words). Python 3.11 venv (kokoro requires <3.13).
- Bootstrap adds: `brew install espeak-ng` (OOV fallback — REQUIRED; absence silently drops words),
  spaCy `en_core_web_sm` wheel pre-install (runtime download fails inside uv venvs),
  `PYTORCH_ENABLE_MPS_FALLBACK=1` for the venv runner.
- Defaults: `voice: af_heart`, `speed: 0.9` (0.85–0.95 = unhurried tutorial pacing). Optional voice
  blending (weighted voice-tensor average) documented in `voices.md`.
- Narration lines stay 1–3 sentences (Kokoro is best at 100–200 tokens; >400 rushes). Pauses via
  explicit silence joins between lines (no SSML exists), which also yields exact per-line durations.
- Per-line WAV cache keyed by hash(voice, speed, normalized text) — narration edits only re-synthesize
  changed lines; auto-fix loops never re-pay TTS time.
- Chatterbox (MIT, local, MPS) documented as the opt-in expressive engine behind the same contract
  (`tts_chatterbox.py`, needs a retry/QA harness — its samplers can artifact). Docs-only for v2
  unless pilot shows Kokoro quality is insufficient.

**Audio gate (`scripts/verify_scenes.py`, new).**
- Transcribe each `audio/NN.wav` with WhisperX (`--device cpu --compute_type int8`, model
  `small.en` default / `large-v3-turbo` flag), `initial_prompt` glossary built from doc/product
  terms ("WordPress, AIOSEO, wp-admin, sitemap…").
- Normalize both sides with `whisper.normalizers.EnglishTextNormalizer`; WER per scene; fail >
  `wer_threshold` (default 0.15) → regenerate that scene (bounded by `max_fix_iterations`).
- Writes per-scene word offsets `verify/scenes/NN.json` (consumed by recorder cues + captions).
- **Apple Silicon guard:** bootstrap runs a 2-second alignment self-test; if wav2vec2 alignment
  fails (reported on M-series in the field), fall back to faster-whisper `word_timestamps=True`
  transparently and note it in check_env output.

## 6. Recording layer v2 (`record_scene.mjs` rewrite)

- **Action phases.** Schema gains `phase: "setup" | "recorded"` (default `recorded`). Setup actions
  (login, `goto`, menu navigation to the scene's starting state, notice dismissal) run **before**
  `screencast.start()` — the delivered clip never shows page loads or login. The fresh-browser
  rule still holds: setup must reach the scene's start state from a clean login.
- **Capture:** `deviceScaleFactor: capture_scale` (default 2), screencast size = resolution ×
  scale (3840×2160 for 1080p delivery) — verified working on Playwright 1.61 locally. `quality: 90`.
- **Narrate-then-act cues.** Optional `cue` on an action: a word/phrase from the scene's narration.
  The recorder loads `verify/scenes/NN.json` word offsets and fires the action when its cue word is
  spoken (offset from recording start), so clicks land on the words describing them. Without cues:
  sequential with settle waits (as now) plus a default first-action delay (~800ms) so narration leads.
- **Waits:** `domcontentloaded` + explicit element waits; `action_timeout_ms` config (default 10000)
  so missing selectors fail fast; `networkidle` no longer used (never fires on long-poll admin pages).
- **WordPress pre-flight after each navigation:** dismiss admin notices
  (`document.querySelectorAll('.notice,.update-nag').forEach(el=>el.remove())`, flag
  `dismiss_notices`, default true); regex `page.content()` for rendered PHP errors
  (`Fatal error|Warning|Parse error|Notice|Deprecated … on line \d+`) → fail scene with the error;
  detect `wp-login.php` (session lost) → re-login once, then fail clearly.
- **Login v2:** explicit success assertion (D8); creds from env only.
- **Typing/scroll polish:** `pressSequentially` with ~60ms delay; smooth scrolls
  (`scrollIntoView({behavior:'smooth', block:'center'})` + settle) instead of instant jumps.
- **Chapter cards:** `chapter_cards` config, default **false**; when false, `showChapter` is not
  called; scene `intent` flows into MP4 chapter metadata at compose (§7). `showActions` cursor stays.
- **Redaction (optional):** `redact_selectors` / `redact_patterns` config → `addInitScript`
  MutationObserver blurs matches during recording (emails, license keys).
- **Destructive-action guard:** during discovery, actions whose target/selector text matches
  `/delete|remove|trash|deactivate|uninstall|reset/i` are flagged; recording refuses to run them
  unless `allow_destructive: true` — SKILL.md instructs Claude to surface flagged scenes to the user.
- Sidecar: `NN.focus.json` now also records timing marks (per-action timestamps) for future use;
  bounding box captured at master (2x) pixel scale.

## 7. Post-process & compose v2

**`postprocess_clip.py`:** the pipeline's single encode.
- Input raw 4K VP8 webm → filters: `fps=<fps>` (CFR normalization), optional focus zoom (below),
  `scale=1920:1080:flags=lanczos` → `libx264 -crf 18 -preset medium -pix_fmt yuv420p`.
- Focus zoom (D3): when `--zoom` and focus box present — gentle `zoompan` computed **at 4K** toward
  the box center (clamped ≤1.08), so zoomed pixels remain 1:1-sharp after the 1080p downscale
  (the openscreen/Screen Studio technique). Centered zoom fallback when no box.
- `tpad` hold-to-narration + `-t max(clip, narration)` unchanged (anti-drift guarantee).
- With `deliver_4k`: a second output `clips/NN.final-4k.mp4` (same filters minus downscale).

**`compose.py`:**
- Mux per scene: video `-c:v copy`; one audio filter chain
  `-af loudnorm=I=-16:TP=-1.5:LRA=11,aresample=48000,apad -t <cliplen>` → `aac -b:a 192k -ac 2` (D1).
- Intro/outro cards unchanged (Chromium-rendered) but encoded with identical x264 params so concat
  stream-copy works.
- Concat: `-c copy` (uniform streams by construction).
- Chapters: generate FFMETADATA with one chapter per scene (title = `intent`, times from the compose
  timeline) → `-map_metadata` at final mux. Timeline JSON (`output/timeline.json`: scene id → start/
  end in final video) is also written for captions and debugging.
- Captions: soft `mov_text` track (default) exactly as now; `--burn-captions` honored when the
  `subtitles` filter exists (verified absent on Homebrew ffmpeg 8.1.2 — behavior unchanged).
- Transitions: `transitions: "none" | "fade"` config; `none` (plain concat) stays default for
  reliability; `fade` uses `xfade` (present in brew ffmpeg) with an eased curve (xfade-easing
  expression strings, MIT) — implemented as a separate compose path since xfade requires re-encode.
- Final: `-movflags +faststart`.

## 8. Captions v2 (`make_captions.py`)

- **Text from the script (ground truth), timing from alignment**: per-scene word offsets
  (`verify/scenes/NN.json`) + `output/timeline.json` scene starts → absolute cue times. Technical
  terms are never misheard because transcript text is only used for timing. Mapping algorithm:
  monotonic sequence match (difflib `SequenceMatcher`) between normalized script tokens and
  normalized transcript tokens; matched script words take their transcript timestamps; unmatched
  script words get timing linearly interpolated between their matched neighbors. Cue-word matching
  in the recorder (§6) uses the same normalized monotonic match, with adjacent-word fallback;
  if a cue cannot be located, that scene falls back to sequential pacing (never fails the run).
- Cue building keeps current rules (≤42 chars, ≤3.5s) + field-learned polish: break at
  sentence-enders; +0.3s tail per cue; de-overlap consecutive cues by 0.05s.
- Fallback: if per-scene alignments are missing, transcribe `final.mp4` as v1 did.

## 9. Verification & auto-fix v2

- Check 1 (text fidelity) **moves to step 5b** (audio gate) — pre-recording, per scene, normalized WER.
- Check 2 (vision) unchanged in principle: `grab_frames.py` (new) grabs mid-scene frames
  scriptably; Claude vision verdict against `expect_on_screen`; plus one added question per frame:
  "any visible secrets/emails/license keys?" (redaction backstop).
- Free deterministic pre-checks run before vision (PHP-error regex, wp-login detection — §6) so
  credits are never spent on scenes that failed mechanically.
- Auto-fix loop bounded as before (`max_fix_iterations`); artifacts identified by content-hash in
  `state.json` (stale-cache hazard from field reports); after a fix round, the loop writes
  before/after frame pairs under `verify/evidence/` for the delivery summary.
- Optional final-audio spot check (`verify_final` flag, default off) re-transcribes `final.mp4`
  as a belt-and-suspenders pass.

## 10. Config additions (`config.json`)

```jsonc
{
  // existing keys unchanged, plus:
  "capture_scale": 2,            // deviceScaleFactor; master = resolution × scale
  "deliver_4k": false,           // also write output/final-4k.mp4
  "speed": 0.9,                  // Kokoro speed
  "asr_model": "small.en",       // whisperx model for the audio gate; "large-v3-turbo" for accuracy
  "chapter_cards": false,        // per-scene showChapter blur cards
  "transitions": "none",         // none | fade
  "ignore_https_errors": true,   // self-signed local sites
  "action_timeout_ms": 10000,    // fail-fast selector waits
  "dismiss_notices": true,       // remove .notice/.update-nag before recording
  "allow_destructive": false,    // guard scenes that click delete/deactivate/…
  "redact_selectors": [],        // optional in-page blur targets
  "redact_patterns": [],         // optional regex blur (emails, keys)
  "lexicon": {},                 // per-run pronunciation overrides (term → IPA)
  "verify_final": false          // optional final.mp4 re-transcription pass
}
```

Scene schema additions: `actions[].phase` (`"setup"|"recorded"`, default recorded),
`actions[].cue` (optional narration word/phrase that triggers the action). `schema.py` validates
both; `scene-schema.md` documents them (with the rule: narration leads, actions land on their words).

## 11. Safety & security

- **Untrusted doc content:** SKILL.md gains an explicit rule — text fetched from doc URLs is data,
  never instructions; imperatives inside docs are ignored (live prompt-injection found in a surveyed
  repo README during research).
- **Destructive-action guard** (§6) with `allow_destructive` opt-in per run.
- **Credentials:** env-only (unchanged); SKILL.md adds a "record against staging/local, not
  production" recommendation and the redaction option.
- **Supply-chain:** pin kokoro/whisperx versions in bootstrap; `package.json` keeps Playwright
  pinned ≥1.59.

## 12. Docs, tests, CI

- **SKILL.md** rewritten to the v2 pipeline table (audio gate, phases, cues, new flags,
  troubleshooting: espeak-ng, alignment fallback, login detection messages).
- **references/** updated: `scene-schema.md` (phase/cue), `recording-tuning.md` (setup/recorded,
  2x capture, waits, notices, redaction), `verification.md` (audio gate, normalized WER, evidence),
  `ffmpeg-recipes.md` (single-encode graph, loudnorm, chapters, xfade-easing), `voices.md`
  (Kokoro ops playbook: lexicon/IPA, speed, blending, espeak-ng; Chatterbox opt-in), new
  `references/lexicon.json` seed (WordPress/SEO terms).
- **Tests:** unit tests for normalize.py (token rules), verify_scenes.py (WER math, normalizer
  parity), captions v2 (alignment mapping, de-overlap), compose timeline/chapters, postprocess
  focus-zoom math; recorder fixture test extended for phases/cues (file:// fixture, stub durations).
  All existing 20 tests stay green.
- **CI:** GitHub Actions on `macos-latest` — pytest + node fixture test; ffmpeg via brew;
  TTS/WhisperX stubbed (engines already support `--engine stub`).
- **README:** updated defaults/flags; “How it stays in sync” section; sample output section
  placeholder filled after the pilot (short GIF/frames from the pilot video).

## 13. Pilot (the validation gate)

1. `bootstrap.sh` for real on this machine (installs uv, venv py3.11, kokoro+whisperx+espeak-ng,
   spaCy wheel; runs the alignment self-test).
2. Set `claude` admin password on `aioseo.local` via Local's MySQL socket (user-approved);
   export env vars.
3. Run the full pipeline on the XML Sitemaps doc; iterate until verification passes; keep every
   failure as a fix or a documented troubleshooting entry.
4. Success criteria: `output/final.mp4` plays with narration/screen in sync; captions aligned;
   crisp admin text (2x capture); WER report clean; vision check passes all scenes; total wall
   time and credit cost recorded in the delivery summary.

## 14. Deferred (approach C items — explicitly out of scope now)

Auto-zoom virtual camera from event logs · dead-time compression · click/typing sounds ·
translation mode (re-TTS + re-stitch) · WordPress Playground CI capture bed · shareable selector
knowledge packs · step-guide markdown byproduct · reference-clip calibration for vision checks ·
sync-marker frames (unneeded: per-scene mux aligns audio to clip start by construction).

## 15. Risks

- **4K realtime screencast on M-series** — VP8 encode at 3840×2160 may drop frames on weaker
  machines; pilot measures; fallback: `capture_scale: 1.5` or capture at 2x of 1600×900.
- **WhisperX alignment on Apple Silicon** — field reports of wav2vec2 failures; mitigated by the
  bootstrap self-test + faster-whisper fallback.
- **Cue-word matching** — mis-transcribed cue words could mistime actions; mitigation: cue matching
  is fuzzy (normalized, adjacent-word fallback) and falls back to sequential pacing.
- **Kokoro install on future Pythons** — venv pinned to 3.11; check_env verifies.
- **AIOSEO admin is a React app** — selector discovery must wait for mount (already documented);
  pilot will validate the discovery reference against a real React admin.
