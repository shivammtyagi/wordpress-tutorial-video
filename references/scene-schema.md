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
| `type` | enum | `click`, `type`, `scroll`, `hover`, `wait`, `goto`, `press`. |
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
