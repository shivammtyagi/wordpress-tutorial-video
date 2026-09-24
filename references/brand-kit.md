# Brand kit & house-style narration

The difference between "a screen recording with TTS" and "a video that looks
like the customer's channel" is three inputs you must ask for BEFORE the run:

> "Do you have any brand assets you'd like this video to use — a logo, brand
> colors, fonts? And is there an existing video or YouTube channel whose
> narration style I should match?"

## 1. Branded intro/outro cards

Copy the brand files into `<run>/brand/` (logo SVG/PNG, font files — woff2
works in Chromium). Generate `<run>/assets/card_intro.html` and
`card_outro.html` from the default template, embedding EVERY asset as a data
URI (`data:font/woff2;base64,…`, `data:image/svg+xml;base64,…`) — the card
renderer uses `page.setContent`, so external file references will not load.
Keep the `{{TITLE}}` / `{{SUBTITLE}}` placeholders; `compose.py` picks the
templates up automatically and fills them.

Design guidance that has tested well: light background with a soft brand-color
radial glow, logo above the title, title in the brand's heading font, a short
accent rule in the brand color, and a thin brand-color bar along the bottom
edge. Outro: "Thanks for watching" + logo + the site's domain.

Set `accent_color` in config.json to the brand color — it drives the highlight
rings and click ripples in the recording itself.

## 2. Matching the channel's narration style

Pull transcripts from 1–2 of the customer's existing videos:

    uvx yt-dlp --skip-download --write-auto-subs --sub-lang en \
        --sub-format vtt -o "yt_%(id)s" "<video url>"

Strip the VTT to plain text and study: how they open ("Welcome to X. In this
video I'm going to show you how to…"), how they narrate actions (first-person
play-by-play: "I'm going to click on this one", "let's head on over",
"if we scroll on down"), their connectors ("so", "and then", "one thing to
keep in mind"), reaction beats ("so it's doing exactly what I wanted"), and
the sign-off (docs link + support invitation). Also measure their pace:
`words ÷ video seconds × 60` — write and tune the TTS to land near it
(`tts_target_wpm`).

Without a reference channel, use that generic house style — it is how good
American-English tutorial narration sounds.

## 3. Script-writing rules that TTS rewards

- Contractions everywhere; short spoken clauses over written prose.
- No pause-forcing commas: "a short, inviting summary" → "a short inviting
  summary" (keep the comma in captions if you like — the SRT comes from the
  script text, the TTS reads the narration field).
- **Homophones that change the meaning.** "Too often looks spammy" was heard
  as "two"; write "Using it too often can look spammy" so the context carries
  it. Check too/two, for/four, there/their at clause starts.
- **Breathe after the sign-off opener.** Write the wrap-up as
  `"That's it!\nYou now know…"` so the paragraph gap gives the exclamation room.
- **No heteronyms.** Kokoro guesses "live", "read", "lead", "close" from
  context and often guesses wrong. Use an unambiguous word ("as you type",
  "in real time") or a run-level IPA lexicon entry.
- **Pauses go where the comma is.** Long sentences with a list and a trailing
  clause make the engine breathe in the wrong place. Keep sentences short and
  put a comma exactly where the pause belongs; check the gate's word timings.
- **No questions in the narration.** Kokoro reads a question mark flat, so
  "Want to revert?" lands as a statement with an odd stop. Write the
  conditional instead: "If you want to keep the original wording, click
  Revert." / "If you don't want the changes, then…". This also reads more
  like a tutorial than a pitch. (Only Chatterbox could carry rising
  intonation; don't rely on it.)
- Brand names: verify pronunciation with the audio gate. Kokoro accepts IPA
  via `lexicon`; Chatterbox reads plain text well but test multi-word brand
  names ("All in One SEO" should flow as one unit).
- Spell out anything the engine might spell letter-by-letter or mis-read
  (URLs, versions — the normalizer handles common cases).

## 4. Site polish before recording

- Set the admin display name to the brand ("Howdy, BRAND" is visible in every
  frame): `wp user update <user> --display_name="Brand"`.
- Dismiss NPS surveys / promo banners persistently where the plugin stores
  the dismissal (usermeta, notifications table); for banners that re-appear,
  add selectors to `dismiss_selectors`.
- Plan **continuity baselines**: each scene records in a fresh browser, so
  state created on camera in scene N must be pre-seeded in the DB for scene
  N+1 (e.g. via `wp eval` on the plugin's options). A viewer notices when a
  chip added in one scene vanishes in the next.

## 4b. Skipping the end card

An end card is optional. Set `outro_seconds: 0` (or `"outro": false`) in the
run's `config.json` and delete `assets/card_outro.html`; the video then ends on
the last scene's final frame. Do this whenever the brand has no approved
end-card design — a broken or off-brand end card is worse than none.
(AIOSEO production videos: **no end card**, per the channel owner, 2026-09-24.)

## 5. Thumbnail & end card via Claude Design (optional, after delivery)

After delivering the MP4, ask:

> "Would you like a matching YouTube thumbnail and end card for this video?
> If you have a design system in Claude Design, I'll write you a prompt to
> paste there — bring back the exported images and I'll build them into the
> video."

Better than image exports: if the user can download their design system
as a ZIP (Claude Design supports this), ask for that instead — it often
contains a reusable "YouTube cards" pattern, full-res HTML sources you can
render yourself at exactly 1920x1080 with Playwright (element-screenshot
each card frame after `document.fonts.ready`), and the brand tokens/fonts.
Rendering the HTML beats using preview PNG exports, which are frequently
downscaled. Verify the render at full size — fixed-position layouts
authored small can wrap or collide at 1920px; fix such an issue with a
minimal style override at render time, never by editing their system.

Otherwise generate a paste-ready prompt from the run (fill the bracketed
parts from the script title and brand facts):

    Using our design system's YouTube cards pattern (or thumbnail style),
    create two images for a tutorial video, EXPORTED AT 1920x1080:

    1. TITLE CARD — headline "[VIDEO TITLE]", a short benefit subtitle
       ("[one line: what the viewer gets]"), our logo, a TUTORIAL badge,
       and an illustration that represents [one-line concept, e.g. "a
       sitemap URL list with one entry marked excluded"]. Left-aligned
       text column, illustration on the right.
    2. END CARD — "Thanks for watching" with our logo and [site domain],
       a subscribe prompt, and reserved zones for YouTube end-screen
       elements (next video + subscribe) marked as empty space.

    Flat export, no rounded page corners; text inside a 5% safe margin.

When integrating an end card that reserves YouTube end-screen zones, set
`outro_seconds` to at least 6 — YouTube end-screen elements need 5-20s of
runway at the end of the video.

When the user brings the exports back (any folder path):

1. `python3 scripts/image_card.py --run-dir <run> --image <title-card.png> --card intro`
2. `python3 scripts/image_card.py --run-dir <run> --image <end-card.png> --card outro`
3. Re-run `compose.py --force`, `mix_clicks.py`, and the captions mux —
   the video now opens and closes on the supplied designs.
4. Export the YouTube thumbnail from the same title card:
   `ffmpeg -i <title-card.png> -vf scale=1280:720 <slug>-thumbnail.png`
   and deliver it next to the MP4 (YouTube does NOT take the first frame
   automatically — the thumbnail is uploaded separately; matching first
   frame + thumbnail just makes the play transition seamless).

Note: the intro dissolve (`transitions: "intro"`) works unchanged — the
title card dissolves into the first screen exactly like the text card did.
