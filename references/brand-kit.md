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
- Questions must be phrased to carry rising intonation. If the TTS reads your
  question like a statement, rephrase it ("Want to see every tag?" works
  better than "Prefer to see everything that's available?").
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

## 5. Thumbnail & end card via Claude Design (optional, after delivery)

After delivering the MP4, ask:

> "Would you like a matching YouTube thumbnail and end card for this video?
> If you have a design system in Claude Design, I'll write you a prompt to
> paste there — bring back the exported images and I'll build them into the
> video."

If yes, generate a paste-ready prompt from the run (fill the bracketed
parts from the script title and brand facts):

    Using our design system, create two 1920x1080 images for a tutorial
    video, in our YouTube thumbnail style:

    1. TITLE CARD — headline "[VIDEO TITLE]", with our logo, a TUTORIAL
       badge, and an illustration that represents [one-line concept, e.g.
       "a sitemap list with one entry excluded"]. Left-aligned text
       column, illustration on the right.
    2. END CARD — "Thanks for watching" with our logo and [site domain],
       plus space for a subscribe prompt.

    Flat export, no rounded page corners; text must stay inside a 5%
    margin from every edge (video-safe area).

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
