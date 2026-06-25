# Selector discovery (step 6)

This is the step that lets one skill work against **any** WordPress plugin. After
`script.json` exists (human-language `target`s, `selector: null`), Claude logs
into the user's live site and resolves every `target` into a verified Playwright
selector, producing `script.discovered.json`.

Use the Playwright MCP tools (`browser_navigate`, `browser_snapshot`,
`browser_click`, etc.) to explore — do **not** guess selectors from the doc alone.

## Procedure

1. **Log in.** Navigate to `<site>/wp-login.php`, fill `#user_login` / `#user_pass`
   from the configured env vars, submit, confirm you land in `wp-admin`.
2. **For each scene, walk the intent.** Navigate toward what `intent` describes.
   After each navigation, take an accessibility snapshot (`browser_snapshot`) and
   read the real structure — menu labels, button names, headings.
3. **Resolve each action `target` to a selector.** Prefer, in order:
   - `role=<role>[name='<accessible name>']` (most stable)
   - `text=<visible text>` for unique link/menu labels
   - `[aria-label='...']` / `[name='...']` for inputs
   - a stable `#id` if present
   - a CSS path only as a last resort (brittle).
4. **Verify before accepting.** A selector must resolve to **exactly one visible
   element**. If it matches zero or many, refine it. Confirm the action actually
   advances the flow (e.g. the click reveals the expected panel).
5. **Resolve `focus_selector`.** Pick the container that best frames what the
   narration is about (the settings panel, the toggle row). This is what the
   compositor zooms/holds on.
6. **Honor the fresh-browser rule.** Because each scene records from a fresh
   login (see `scene-schema.md`), make sure each scene's action list, run from a
   clean `wp-admin`, reaches the described state. Add a leading `goto` or the
   intermediate menu clicks if needed.
7. **Flag, don't guess.** If a target cannot be resolved to a unique visible
   element, do **not** invent a selector. Mark the scene and revise the script
   (split the scene, change the approach, or ask the user about access/config).

## Output

Write `script.discovered.json` — identical to `script.json` but with every
`selector` and `focus_selector` filled with a verified value. Validate it with
`schema.py` (`discovered=True`) before recording.

## WordPress-specific tips

- The admin sidebar uses `role=link` items; submenus appear on hover/expand.
- Settings frameworks (and many SEO/marketing plugins) render React apps — wait
  for the panel to mount before snapshotting; selectors on text/role are far more
  stable than generated CSS class names.
- Toggles are often `<button role="switch">` or a styled checkbox; target the
  accessible name, not the visual element.
- Save buttons are commonly `role=button[name='Save Changes']`.
