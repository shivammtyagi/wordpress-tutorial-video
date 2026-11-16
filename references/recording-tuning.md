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
  that blurs matching elements live (emails, license keys) for the whole session,
  on every origin — including third-party sign-in screens in a connection flow.
  Patterns are tested against `input`, `td`, `code`, `span`, and leaf `div`/`p`/`a`/
  `li`/`strong`/`b` elements (leaves only, so a match never blurs a container).
  Add `img[src*="googleusercontent.com"]` to `redact_selectors` to blur Google avatars.
  The scan is incremental (one full pass, then only changed subtrees, once per
  animation frame), so it stays cheap on big list screens.
- **Read-only recording:** `block_writes` (below) stops anything on camera from
  being saved, trashed or deactivated on the site.

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

## Recording a live site with a signed-in profile

For live sites, and for flows that go through a third-party account (Google
Search Console, Bing Webmaster Tools), credentials must never pass through the
skill. Instead the user signs in themselves once, in a persistent browser
profile, and every scene reuses that session:

```bash
node scripts/sign_in.mjs --profile ~/.wtv-profiles/live \
     --url https://example.com/wp-admin/ \
     --url https://search.google.com/search-console
```

Sign in on each tab (on WordPress, tick **Remember Me** — otherwise the login
cookies are session-only and vanish when the window closes), then quit that
browser. Point the run at the profile in `config.json`:

| Key | Default | Meaning |
|-----|---------|---------|
| `browser_profile` | unset | Profile dir (`~/…`, absolute, or relative to the run dir). When set, the recorder skips the credential login and only checks the session. |
| `profile_channel` | `chrome` | Browser that opens the profile. Real Chrome is less likely to be refused by Google sign-in. |
| `record_channel` | `chromium` | Where scenes are recorded. `chromium` copies the profile's session (cookies + storage, in memory only) into the normal recording browser; `profile` records in the profile's own browser — only for flows that need real Chrome, e.g. Google account screens. |
| `browser_channel` | unset | The recording browser, exactly as without a profile: unset = bundled Chromium, `"chrome"` = installed Chrome. |
| `headless` | `true` | Record without a visible window. Set `false` for third-party screens that serve headless browsers a different page (Google's OAuth consent). |
| `navigation_timeout_ms` | 90000 with a profile, else 30000 | Page-load timeout; live sites are slower than local installs. |
| `settle_max_ms` | 60000 | Longest wait for the page to go idle before capture starts. |

`goto` targets may be absolute URLs (a third-party page mid-flow); WordPress
preflight checks (notice removal, PHP-error scan, session check) run only on
the site's own origin. Mark an action `"optional": true` when its screen may
not appear (Google's account chooser): if the element is not there within
`optional_timeout_ms` (default 8000) the action is skipped and the scene
carries on. Profiles hold live session cookies: keep them outside the repo and
private (`sign_in.mjs` creates them `0700`).

A profile makes the recorder able to change a real site. Turn `block_writes`
on unless the video has to show a save actually happening; the recorder prints
a reminder when a profile is used without it.

### Why scenes record in bundled Chromium, not real Chrome

Measured on a heavy plugin list screen (same site, same click): bundled
Chromium 0.02s per click; real Chrome under Playwright 2.5–11s, with or without
the anti-automation flags, headed or headless, with or without screencast or
request interception. Some plugin scripts also stall ~10s each in real Chrome.
So the profile is opened in Chrome only to read its signed-in session, and the
scene records in the normal recording browser. (On a machine where the bundled
headless shell is unstable — see SKILL.md troubleshooting — `browser_channel:
"chrome"` still applies and that trade-off is yours to make.)

Before capture the recorder also waits until the page is idle (three
consecutive `requestAnimationFrame` round trips under 250ms), and actions can
still land ~1.5–2s after their cue on heavy live screens — cue a word or two
*before* the phrase the action should land on. Set `WTV_DEBUG=1` to print
per-step timings for each action.

## Read-only recording: `block_writes`

`"block_writes": true` aborts every POST/PUT/PATCH/DELETE to the site's origin
while recording, so a stray Save can never reach the site. Some screens *read*
data over POST — allowlist those with regexes in `allow_write_patterns` (for
example a posts list that loads one of its columns through
`/wp-json/<plugin>/v1/posts-list/load-details-column/`). The recorder prints
how many writes it blocked per scene.

WordPress runs destructive admin actions (Trash, Delete Permanently, Restore,
Deactivate, Spam…) through plain **GET** links, so `block_writes` also blocks
any URL on the site carrying one of those `action=` values, on any method; the
allowlist never overrides that. A mis-click on a row action cannot trash a
post: the scene fails with "block_writes stopped this click" instead. Blocked
background calls (heartbeat, admin-ajax) are harmless for a recording.

What the viewer sees: a blocked background save simply fails in the page (the
plugin may show its own "could not save" message — check the frame). A blocked
classic form Save (a full-page POST, like Settings → General) leaves the
browser on the form with the typed values still showing and no "Settings
saved." notice, so do not narrate a confirmation that will not appear.

The guard is armed after login, so it also works without a profile (the
recorder's own login POST goes through first). Requests to other origins are
never blocked. The rules live in `scripts/lib/guards.mjs`.

## Demo content on a real site: display rewrites

To record a real site as neutral demo content without changing its data,
rewrite what the recording browser shows:

```jsonc
"display_rewrites": [
  { "find": "How to Grow Tomatoes", "replace": "How to Write Better SEO Titles" },
  { "find": "example-garden.com",   "replace": "example.com" },
  { "find": "Jane Smith",           "replace": "Demo User" }   // admin-bar name, Author column
],
"hide_selectors": ["#wp-admin-bar-my-account img.avatar"]
```

Rewrites are literal, case-sensitive, and apply to text nodes plus the
`title` / `alt` / `placeholder` / `aria-label` attributes — never to hrefs,
form values or the text inside a `<textarea>`, so links still work and no
rewritten text can be saved. A `find` string must sit inside **one** text node:
WordPress splits "Howdy, **Name**" across elements, so target the name alone.
Pick replacements of similar length so pixel/character counters shown beside
them stay plausible. Images (featured images, thumbnails) are not rewritten —
hide them with `hide_selectors` (the element keeps its space, so the layout
does not jump).

Rewrites, hides, dismissals and redaction are re-applied to frames whose
document is replaced in place (some page builders open their preview iframe as
`about:blank` and then write the page into it).

## Text selections in a background window

The recording window is usually unfocused, so text selections draw in pale grey.

- **`extra_css`** (alias of `inject_css`; both are applied): CSS injected on
  every page, any origin — set
  `"::selection{background:#B4D5FE !important;color:inherit !important}"` when
  a scene selects text.
- **`emulate_focus: true`**: the page behaves as if its window were focused (CDP
  focus emulation), so selections draw in the normal blue. Preferred on sites
  whose CSP blocks injected styles (Bing).
