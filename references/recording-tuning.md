# Recording tuning (step 7)

`scripts/record_scene.mjs` records one scene with Playwright's `page.screencast`
API (Playwright ≥ 1.59). It is deterministic, paced, and annotated so the result
reads as a tutorial, not a bot.

## Why screencast (not recordVideo)

Playwright's context `recordVideo` is hardcoded to ~1 Mbit/s VP8 WebM with no
quality control, so it looks mediocre. `page.screencast` records at a real
bitrate and adds tutorial affordances:

| Call | Effect |
|------|--------|
| `screencast.start({path, size, quality})` | Record to WebM; `quality` 0–100 (we use 90). |
| `screencast.showActions({cursor:'pointer', duration})` | Animated mouse pointer that glides between action points + an action title overlay. This is the cursor spotlight — no custom asset needed. |
| `screencast.showOverlay(html, {duration})` | Inject a `pointer-events:none` callout (we draw a highlight box around `highlight: true` clicks). |
| `screencast.showChapter(text)` | Chapter marker; we pass the scene `intent`. |

Overlays are pointer-events-none, so they never interfere with the page.

## Pacing constants (in `record_scene.mjs`)

- **Human typing:** ~55 ms per character via `locator.press`. Reads naturally,
  not instant paste.
- **Pre-click settle:** 250 ms after scroll/highlight before the click lands.
- **Network idle:** after each action, wait up to 8 s for `networkidle`
  (best-effort; admin pages with long-poll connections fall through the timeout).
- **`hold_after_ms`:** per-scene pause after the last action so the final state
  is visible.
- **Narration pacing:** if the actions finish faster than the scene's narration
  (`audio/durations.json`), the recorder idles to roughly match it. The
  post-processor then pads to the exact `max(clip, narration)`.

## Focus sidecar

After the actions, the recorder writes `clips/NN.focus.json` with the bounding box
of `focus_selector` (and the viewport). `postprocess_clip.py` uses it for the
optional Ken Burns zoom toward the focused element.

## CDP → FFmpeg fallback (60 fps / very high bitrate)

If a project needs 60 fps or a higher bitrate than screencast provides, replace
the recorder's capture with a CDP screencast that pipes JPEG frames to ffmpeg:

```js
const client = await context.newCDPSession(page);
await client.send('Page.startScreencast', { format: 'jpeg', quality: 90, everyNthFrame: 1 });
client.on('Page.screencastFrame', async ({ data, sessionId }) => {
  ffmpegStdin.write(Buffer.from(data, 'base64'));
  await client.send('Page.screencastFrameAck', { sessionId });
});
// ... run actions ...
await client.send('Page.stopScreencast');
// ffmpeg -f image2pipe -framerate 60 -i - -c:v libx264 -pix_fmt yuv420p out.mp4
```

The default screencast path is preferred for its built-in cursor/overlay support.
