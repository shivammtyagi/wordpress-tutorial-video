# Recording tuning (step 7)

`scripts/record_scene.mjs` records one scene with Playwright's `page.screencast`
API (Playwright ≥ 1.59). It is deterministic, paced, and annotated so the result
reads as a tutorial, not a bot.

## Why screencast (not recordVideo)

Playwright's context `recordVideo` is hardcoded to ~1 Mbit/s VP8 WebM with no
quality control. `page.screencast` records at a real bitrate and adds tutorial
affordances:

| Call | Effect |
|------|--------|
| `screencast.start({path, size, quality})` | Record to WebM; `quality` 0–100 (we use 90); `size` is the **master** resolution (delivery × `capture_scale`). |
| `screencast.showActions({cursor:'pointer', duration})` | Animated mouse pointer that glides between action points + an action title overlay. |
| `screencast.showOverlay(html, {duration})` | Inject a `pointer-events:none` callout (we draw a highlight box around `highlight: true` clicks). |
| `screencast.showChapter(text)` | 2s blurred chapter card. **Off by default** (`chapter_cards: false`) — scene intents become MP4 chapter metadata at compose instead. |

Overlays are pointer-events-none, so they never interfere with the page.

## 2x capture (crisp text)

The context is created with `deviceScaleFactor: capture_scale` (default 2) and
the screencast records at `resolution × capture_scale` — a true 4K master for a
1080p delivery. `postprocess_clip.py` performs the single encode and the lanczos
downscale, so WP admin text stays razor-sharp, and any focus zoom re-samples
original 2x pixels instead of magnifying finished video.

## Action phases: setup vs recorded

Actions with `phase: "setup"` run **before** `screencast.start()` — login,
navigation to the scene's starting screen, and cleanup happen off camera, so
clips never open on a page load. Actions with `phase: "recorded"` (default) run
on camera. The recorder auto-navigates to `/wp-admin/` before setup when the
first setup action isn't a `goto`.

## Cue timing (narrate-then-act)

When an action carries `cue: "<word from the narration>"`, the recorder waits
until that word is spoken (offsets from `verify/scenes/NN.json`, produced by the
audio gate) before firing the action. Cue matching is normalized and monotonic;
an unmatched cue silently falls back to sequential pacing. This is what makes
"now click **Save Changes**" land exactly on those words.

## Waits & pacing

- **Navigation:** `domcontentloaded` + explicit element waits. `networkidle` is
  never used — admin pages with long-poll/heartbeat connections never go idle.
- **Selector waits:** `action_timeout_ms` (default 10000) — missing selectors
  fail fast with a clear error instead of stalling.
- **Human typing:** `pressSequentially(text, {delay: 60})`.
- **Smooth scrolling:** elements scroll into view with
  `behavior:'smooth', block:'center'` + settle, never instant jumps.
- **Pre-click settle:** 350ms after scroll; +250ms after a highlight overlay.
- **Pacing:** measured from `screencast.start()` (not process start), so the
  recorded content fills the narration window; the post-processor pads the exact
  remainder to `max(clip, narration)`.

## WordPress pre-flight (after login and every navigation)

- **Notice dismissal** (`dismiss_notices`, default true): removes
  `.notice, .update-nag` so plugin banners never pollute the recording.
- **PHP error scan:** the rendered HTML is regex-checked for
  `Fatal error|Parse error|Warning|Notice|Deprecated … on line N` → exit 6.
- **Session expiry:** a redirect to `wp-login.php` mid-scene → exit 3 with a
  clear message (re-run the scene; each run logs in fresh).
- **Login assertion:** after submit, the recorder verifies it actually landed in
  `wp-admin` (URL or `body.wp-admin`) and exits 3 otherwise.

## Guards & redaction

- **Destructive-action guard:** on-camera clicks whose target/selector matches
  `delete|remove|trash|deactivate|uninstall|reset` abort the run (exit 5) unless
  `allow_destructive: true`. Surface flagged scenes to the user.
- **Redaction:** `redact_selectors` / `redact_patterns` inject a MutationObserver
  that blurs matching elements live (emails, license keys) for the whole session.

## Focus sidecar

After the actions, the recorder writes `clips/NN.focus.json` with the bounding
box of `focus_selector` (CSS px), the viewport, and the capture `scale`.
`postprocess_clip.py` multiplies by `scale` for master-pixel coordinates and
drives the optional Ken Burns zoom toward the box.

## Exit codes

`2` usage/missing scene · `3` login/session failure · `4` empty recording ·
`5` destructive guard · `6` PHP error on page.

## CDP → FFmpeg fallback (60 fps / very high bitrate)

If a project needs 60 fps or a higher bitrate than screencast provides, replace
the recorder's capture with a CDP screencast that pipes JPEG frames to ffmpeg
(`Page.startScreencast` → `image2pipe`), or use screencast's `onFrame` callback.
The default screencast path is preferred for its built-in cursor/overlay support.
