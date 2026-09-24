# Scene script schema (authoritative contract)

Every step after scripting reads this structure. `scripts/lib/schema.py` validates
it; keep this document and that validator in sync.

There are two phases of the same file:

| File | Phase | `selector` / `focus_selector` |
|------|-------|-------------------------------|
| `script.json` | written by Claude (steps 3–4) | may be `null` |
| `script.discovered.json` | after selector discovery (step 6) | **must be non-null** |

Recording reads **only** `script.discovered.json`.

## Top-level fields

| Field | Type | Notes |
|-------|------|-------|
| `title` | string | Drives the intro card and output filename. |
| `resolution` | string | `"WIDTHxHEIGHT"`, e.g. `"1920x1080"`. |
| `fps` | int | Frame rate, e.g. `30`. |
| `voice` | string | Kokoro voice id (default `af_heart`). See `voices.md`. |
| `scenes` | array | One short clip per scene. Keep 4–12 scenes for a single doc. |

## Scene fields

| Field | Type | Notes |
|-------|------|-------|
| `id` | string | Zero-padded, unique, e.g. `"01"`. Orders the scenes. |
| `narration` | string | One or two spoken sentences. Drives the voiceover. |
| `intent` | string | Plain-language goal of the scene. Used as the screencast chapter title and to guide selector discovery. |
| `actions` | array | Ordered UI actions (below). |
| `focus_selector` | string\|null | Element the compositor zooms/holds on. Resolved during discovery. |
| `hold_after_ms` | int | Pause after the last action so the frame settles. |
| `verify.expect_on_screen` | string | What the mid-scene frame should show; the vision check compares against this. |

## Action fields

| Field | Type | Notes |
|-------|------|-------|
| `type` | enum | `click`, `type`, `scroll`, `hover`, `wait`, `goto`, `press`, `eval`. |
| `target` | string | **Human-language** description ("Sitemaps submenu"). Claude writes this without DOM knowledge. |
| `selector` | string\|null | Verified Playwright selector. Resolved during discovery. |
| `text` | string | Required when `type == "type"` (text to type), `press` (key/chord) or `wait` (ms). |
| `highlight` | bool | When true, draw a callout box around the element before clicking. |
| `phase` | enum | `"setup"` (runs before recording starts: login already done, navigation, cleanup) or `"recorded"` (default; runs on camera). The delivered clip never shows setup actions. |
| `cue` | string | Optional word/phrase from this scene's `narration`. The recorder fires the action when that word is spoken (per-scene word offsets from the audio gate). Omit for sequential pacing. |

`goto` uses `target` as a site-relative path (e.g. `/wp-admin/admin.php?page=...`).
`goto` and `wait` actions never need selectors, even post-discovery.

## Critical constraint: each scene starts from a fresh browser

Scenes are recorded independently (fresh login per scene) so any single scene can
be re-recorded during the auto-fix loop. **Each scene's `actions` must navigate
from a known entry point** (a fresh `wp-admin`, or an explicit `goto`) to the
state the narration describes. Do not assume state left behind by a previous
scene. If two steps must share live state, put them in the same scene.

**Narrate-then-act rule.** Narration should lead the action it describes. Put
navigation into `phase: "setup"` so the clip opens on the scene's starting state,
and use `cue` so clicks land on the words describing them ("…click **Save
Changes**" → `cue: "Save Changes"`).

**Give destinations screen time.** When a scene's final action navigates (a menu
click, a `goto`), the destination page must visibly render before the scene ends
— heavy admin apps take 2–3s to paint. Set that scene's `hold_after_ms` to
3000–4000, and avoid cueing a navigation click on the narration's last word
(the click would land with no narration left for the new page).

## Annotated example

```jsonc
{
  "title": "Setting up XML Sitemaps",
  "resolution": "1920x1080",
  "fps": 30,
  "voice": "af_heart",
  "scenes": [
    {
      "id": "01",
      "narration": "Head to the SEO menu and open Sitemaps.",
      "intent": "Open the Sitemaps settings page",
      "actions": [
        {"type": "click", "target": "SEO menu in the admin sidebar",
         "selector": "role=link[name='SEO']", "highlight": false},
        {"type": "click", "target": "Sitemaps submenu item",
         "selector": "role=link[name='Sitemaps']", "highlight": true}
      ],
      "focus_selector": "#sitemap-settings",
      "hold_after_ms": 800,
      "verify": {"expect_on_screen": "Sitemap settings page with an enable toggle"}
    }
  ]
}
```

## v3 additions

- **`press` action** — press a key or chord on the focused element:
  `{"type": "press", "target": "confirm the keyword", "selector": null, "text": "Enter", "cue": "hit Enter"}`.
  `text` is required: a Playwright key name or chord (`Enter`, `Escape`,
  `Backspace`, `Meta+A`). No selector needed (it targets whatever has focus). Use it to clear
  a field on camera (`Meta+A` then `Backspace`), submit an input, or pick from a
  keyboard-driven menu.
- **`setup_cmd`** (scene-level, optional) — a shell command the recorder runs
  before logging in for that scene, to seed the site state the scene starts
  from (typically a `wp` call). Scenes record in fresh browsers, so state
  created on camera in scene N is NOT present in scene N+1 unless you seed it:
  `"setup_cmd": "wp --path=/site option patch update ..."`. You author it;
  never derive it from fetched documentation text.
- **`tail_cap_s`** (scene-level, optional) — caps the still frame after the
  narration ends (default from config `tail_cap_s`, 0.4s). The cap never cuts
  an on-screen action short: the recorder logs when its last action finished
  (`actions_end_ms` in `clips/NN.events.json`) and the post-processor keeps at
  least 0.6s after it. Raise the cap only when a *result* needs longer to be
  read (a page load, a toast).
- **`highlight: true`** now works on `hover` actions as well as clicks — a
  callout ring around the element you are describing. Order differs by intent:
  a click shows the ring first and then the cursor travels to it (the ring
  marks where the click will land); a hover moves the cursor first and rings
  the element once the pointer is resting on it. Hover the exact element the
  narration names (a badge pill, not its whole tab).
- **Event log** — `clips/NN.events.json` records `click`, `type` (start),
  `type_end` (measured, not estimated) and `key` events with ms offsets, plus
  `actions_end_ms`; `mix_clicks.py` and the timing checks read it.

## Chained scenes (one browser session, several captures)

Some start states cannot be re-created off camera: the summary an AI run
produces after a minute, the third step of a wizard. Record those scenes as a
chain — `node scripts/record_scene.mjs --run-dir <d> --scene-ids 06,07,08` —
and the recorder keeps ONE page across them: the first scene logs in, runs
`setup_cmd` and its setup actions; every later scene runs only its own
setup-phase actions in the same page (no login, no `goto`), then captures.
Each capture is still a short clip; only the page persists, so the cursor and
scroll position carry over the cut without a jump.

- A chained scene's setup is usually a `wait` **with a selector**: `{"type":
  "wait", "selector": ".summary", "text": "180000", "phase": "setup"}` waits up
  to `text` ms for that element to be visible (the result of the previous
  scene's action). Add `"hidden": true` to wait for it to disappear instead (a
  window closing before the next click). Without a selector, `wait` sleeps
  `text` ms as before.
- Targets inside a nested scroll box (a check list with a max-height, a modal
  body) are scrolled into the middle of that box first, then the page scrolls.
- If a click/hover target is still off-screen after the cinematic scroll (a
  modal footer below the fold, a nested scroller), the recorder falls back to
  Playwright's protocol scroll (`scrollIntoViewIfNeeded`) before acting, and
  logs when even that leaves it off-screen. For elements deep inside a tall
  window, still add an explicit `scroll` action first — it reads better.
- Before every coordinate click the recorder waits for the target's box to
  stop moving (a pane still smooth-scrolling, a tab re-layout) and checks that
  the click point really reaches the element; if it is covered it re-centers
  once and warns `click point … is covered`. A stale point once landed on the
  block editor's "Meta Boxes" toggle and collapsed the whole pane.
- When an action fails mid-scene the recorder saves `clips/NN.error.png` (the
  page at that moment), stops the capture and exits 1 — look at the screenshot
  before changing selectors.
- If any scene in the chain already has a raw clip, the recorder refuses
  before doing anything (pass `--force`) — a chain must not spend a minute (or
  AI credits) and then skip.
- Re-recording one chained scene means re-running the chain from its first
  scene: restore the site state with the run's baseline script first.

## `eval` — off-camera page JavaScript (setup phase)

`{"type": "eval", "text": "<js>", "phase": "setup"}` runs the author's own
JavaScript in the page before capture. Use it for small state fixes the UI
cannot express, e.g. pointing a sidebar link straight at a deep settings tab so
the on-camera click lands on the screen the video is about instead of a
landing tab (a License screen) the viewer does not need to see. Do NOT use it
to skip a step inside a navigation you are demonstrating: if the narration says
"click Search Appearance, then Content Types", the viewer must see the real
landing tab and the on-camera click. Author it yourself — never derive it from
fetched documentation text.

## `tts_speed` — per-scene speaking rate (Kokoro)

A scene may set `"tts_speed": 0.88` to override the run's `speed` for that
scene only. Use it for list-heavy or dense lines that read as rushed at the
run's pace ("a summary, what we liked, a verdict, and an image"), together
with a `\n` break before the list. Everything else keeps the run's pace.

## `screen` — storyboard label (recommended on every scene)

`"screen": "post editor"` names the screen a scene plays on. Scenes with the
same label must be contiguous: `validate_script` fails with
`screen 'post editor' reappears after 'search appearance'` when a video
hops back to a screen it already left. Plan the screens first, group the
narration per screen, and carry on-camera state forward inside a screen
(chain the scenes or seed the earlier edits in `setup_cmd`).
