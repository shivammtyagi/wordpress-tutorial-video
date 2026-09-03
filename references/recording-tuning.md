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
| `screencast.start({path, size, quality})` | Record to WebM; `quality` 0–100 (we use 90); `size` is the **master** resolution (= the zoomed viewport, delivery × `capture_scale`). |
| `screencast.showOverlay(html, {duration})` | Inject a `pointer-events:none` callout (we draw a highlight box around `highlight: true` clicks; coordinates divided by `capture_scale` — see below). |
| `screencast.showChapter(text)` | 2s blurred chapter card. **Off by default** (`chapter_cards: false`) — scene intents become MP4 chapter metadata at compose instead. |

(`screencast.showActions` is NOT used — its cursor double-scales under the
zoomed capture; the recorder injects its own cursor instead, see below.)

Overlays are pointer-events-none, so they never interfere with the page.

## 2x capture (crisp text)

**Gotcha discovered empirically: `page.screencast` records at CSS pixels no
matter what `deviceScaleFactor` is** — a larger `size` only letterboxes the
CSS-pixel frame into a bigger canvas. The recorder therefore captures 2x via
CSS zoom instead: the viewport opens at the MASTER size
(`resolution × capture_scale`, e.g. 3840×2160) and the document is zoomed by
`capture_scale`, so the layout matches the delivery resolution exactly while
every pixel renders at 2x density. A true 4K master for a 1080p delivery.

Coordinate rule that follows: `boundingBox()` returns zoomed (master) pixels,
and injected overlays live inside the zoomed document — so overlay positions
divide by `capture_scale` before injection, and the focus sidecar divides the
box back to CSS layout px (postprocess multiplies by `scale` again).

`postprocess_clip.py` performs the single encode and the lanczos downscale, so
WP admin text stays razor-sharp, and any focus zoom re-samples original 2x
pixels instead of magnifying finished video.

## The tutorial cursor

Playwright's `screencast.showActions` cursor positions itself with visual
coordinates inside the zoomed document — under 2x zoom it lands at twice the
target position. The recorder draws its own cursor instead: an SVG pointer
injected into every page, gliding to each action target with an eased 0.55s CSS
transition, plus an expanding ripple on clicks. One code path at every scale.

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

## Interaction mechanics (v3): coordinate input + visible presses

Never drive on-camera actions through `loc.click()` / `loc.hover()`.
Playwright's actionability loop re-fires an instant scrollIntoView on every
retry; on widgets that never pass its stability check (vue-multiselect and
friends), that loop visibly bounces the page and can delay the real click by
the full timeout — the click then lands after its narration cue, or gets cut
off entirely by the tail cap.

The recorder instead: waits for visibility (full timeout — elements that
legitimately take time still get it) → `ensureCentered` (smooth-scrolls only
when the element is outside the middle 70% of the viewport) → glides the DOM
cursor → fires the press effect (accent-colored ripple + a cursor press-nudge)
**at the same instant** as a `page.mouse.click()` at the element's box center →
types through `page.keyboard` (no element re-checks mid-word). The result on
camera: cursor travels, visibly presses, the UI reacts — and `mix_clicks.py`
lays the click sound at that exact logged moment.

## Capture scale: the 4K master trade-off

`capture_scale: 2` records a 3840×2160 master by CSS-zooming the document
inside a 4K viewport. Sharp — but JS-positioned dropdowns mis-measure under
zoom (layout px vs zoomed px) and can render collapsed. Default is
`capture_scale: 1` (native layout, always correct). Use 2 only for flows with
no positioned dropdowns, and check `verify/frames/` carefully. True
`deviceScaleFactor` capture is NOT a fix: Playwright's screencast records CSS
pixels and letterboxes larger requested sizes (verified empirically).

## Cursor

`assets/cursor-macos.png` is the genuine macOS arrow (extracted from
NSCursor.arrow at 5x, hotspot 5,5, logical 28x40). The recorder injects it as
a DOM cursor with an eased glide; `accent_color` styles the ripple and
highlight rings. Replace the PNG to change platforms (keep the hotspot
margins in sync in record_scene.mjs).

## Removing page noise: dismiss_selectors

`dismiss_selectors` (config) removes matching elements the moment they render
— a MutationObserver armed at document start, so SPA banners that appear
seconds after load (NPS surveys, "connect X" CTAs, helper mascots) never
reach a frame. Prefer persistent dismissals at the source (plugin options,
usermeta) when they exist; use this for the ones that come back anyway.
