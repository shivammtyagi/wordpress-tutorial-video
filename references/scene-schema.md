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
| `type` | enum | `click`, `type`, `scroll`, `hover`, `wait`, `goto`. |
| `target` | string | **Human-language** description ("Sitemaps submenu"). Claude writes this without DOM knowledge. |
| `selector` | string\|null | Verified Playwright selector. Resolved during discovery. |
| `text` | string | Required when `type == "type"` (text to type) or `wait` (ms). |
| `highlight` | bool | When true, draw a callout box around the element before clicking. |

`goto` uses `target` as a site-relative path (e.g. `/wp-admin/admin.php?page=...`).

## Critical constraint: each scene starts from a fresh browser

Scenes are recorded independently (fresh login per scene) so any single scene can
be re-recorded during the auto-fix loop. **Each scene's `actions` must navigate
from a known entry point** (a fresh `wp-admin`, or an explicit `goto`) to the
state the narration describes. Do not assume state left behind by a previous
scene. If two steps must share live state, put them in the same scene.

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
