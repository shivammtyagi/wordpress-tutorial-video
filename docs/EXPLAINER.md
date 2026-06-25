# What is this? — A plain-English explainer

**Built by Shivam Tyagi.** This is a tool that turns a **help/documentation page
into a finished tutorial video** — automatically.

## The one-line version

You give it a documentation URL. It produces a narrated, captioned screen-recording
video that walks through the steps in that doc, recorded on a real WordPress site.

## The problem it solves

Making tutorial videos by hand is slow: you write a script, record your screen,
record a voiceover, sync them, add captions, and re-record whenever you fumble a
step. That's an afternoon per video. This tool does the whole thing for you, and it
works for **any** WordPress plugin or feature — not just one product.

## What you give it

1. A **documentation URL** (any WordPress how-to article).
2. A **WordPress site** it can log into (your own local or staging site).

That's it. You don't write a script or touch a video editor.

## What it gives back

A polished **MP4 video** with:
- a clear voiceover reading beginner-friendly narration,
- the actual on-screen steps, with the mouse moving and clicks highlighted,
- captions (subtitles),
- a simple title card at the start and end.

## How it works (the simple version)

Think of it like an assistant that follows six steps:

1. **Reads the doc.** Pulls the real instructions out of the web page.
2. **Writes the script.** Breaks the doc into short scenes, each with one
   spoken sentence or two.
3. **Records the voiceover first.** This is the clever bit — by making the audio
   first, it knows exactly how long each scene's narration is, so the video and
   the voice never drift out of sync.
4. **Figures out the buttons.** It logs into your site and *looks at the live page*
   to find the actual menus and buttons the doc is talking about — which is why it
   works on any plugin, not just one it was pre-programmed for.
5. **Records each step** as its own short clip, paced to match the narration.
6. **Stitches it all together** into one video with captions and title cards.

## The part that makes it trustworthy

After building the video, it **double-checks itself**:
- It listens back to the voiceover and confirms it said the right words.
- It looks at a frame from each scene and confirms the screen actually shows what
  the narration is describing.

If something's off, it **fixes that scene and re-renders** — automatically. So you
don't ship a video where the voice says one thing and the screen shows another.

## Why it's built the way it is

- **Free and open.** No paid software. The voice, the video tools, and the
  checking tools are all free and open-source.
- **Anyone can use it.** It's published on GitHub. The only requirement is that the
  thing you're documenting runs on WordPress.
- **Reliable.** It works in small, saved steps, so if something fails halfway it
  picks up where it left off instead of starting over.

## What it is *not*

- It's not for non-WordPress websites.
- It doesn't set up a WordPress site for you — you point it at one you already have.
- It currently runs on **macOS**.

## The short pitch for the team

> "Give it a help-doc link and a WordPress site, and it hands you a narrated,
> captioned tutorial video — and checks its own work before it's done. It works for
> any plugin, it's free and open-source, and it's on GitHub for anyone to use."

**Repo:** https://github.com/shivammtyagi/wordpress-tutorial-video
