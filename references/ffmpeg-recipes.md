# FFmpeg recipes (steps 8–9)

These are the command templates `postprocess_clip.py` and `compose.py` build.
Copy/adapt when debugging or extending the compositor.

## Portability note (read this first)

Homebrew's current `ffmpeg` formula (8.x) is built **without** libfreetype,
libass, or fontconfig. That means the `drawtext` and `subtitles` filters are
**not available** on a stock `brew install ffmpeg`. This skill therefore:

- renders intro/outro **cards via Chromium** (`render_card.mjs`) instead of
  `drawtext`, and
- ships captions as a **soft `mov_text` subtitle track** by default instead of
  burning them in.

`--burn-captions` is honored only when the `subtitles` filter is detected;
otherwise it transparently falls back to soft captions.

## Pad / hold last frame to a target duration (anti-drift)

```bash
ffmpeg -y -i scene.webm \
  -vf "scale=1920:1080:force_original_aspect_ratio=decrease,\
pad=1920:1080:(ow-iw)/2:(oh-ih)/2:black,fps=30,\
tpad=stop_mode=clone:stop_duration=2.5" \
  -t 9.0 -an -c:v libx264 -pix_fmt yuv420p scene.final.mp4
```

`tpad=stop_mode=clone` freezes the last frame; `-t` caps to the target
(`max(clip, narration)`).

## Gentle Ken Burns zoom toward the focus element

```bash
-vf "...,zoompan=z='min(zoom+0.0005,1.08)':d=1:s=1920x1080:fps=30"
```

## Mux a scene clip with its narration

```bash
ffmpeg -y -i scene.final.mp4 -i scene.wav \
  -map 0:v:0 -map 1:a:0 -c:v libx264 -pix_fmt yuv420p -r 30 \
  -c:a aac -ar 44100 -shortest seg.mp4
```

## Intro/outro card → 2 s segment (card PNG from Chromium)

```bash
node scripts/render_card.mjs --title "My Tutorial" --subtitle "..." \
  --out card.png --width 1920 --height 1080
ffmpeg -y -loop 1 -i card.png \
  -f lavfi -i anullsrc=channel_layout=stereo:sample_rate=44100 \
  -t 2 -c:v libx264 -pix_fmt yuv420p -r 30 -vf scale=1920:1080 \
  -c:a aac -shortest intro.mp4
```

## Concatenate segments (re-encode for uniform params)

```bash
# list.txt: one `file '/abs/path/seg.mp4'` per line
ffmpeg -y -f concat -safe 0 -i list.txt \
  -c:v libx264 -pix_fmt yuv420p -r 30 -c:a aac -ar 44100 concat.mp4
```

For crossfades between scenes use the `xfade` filter (requires a filter_complex
graph with offsets); plain concat is the default for reliability.

## Captions

Soft track (default, portable):

```bash
ffmpeg -y -i concat.mp4 -i captions.srt \
  -map 0 -map 1 -c copy -c:s mov_text -metadata:s:s:0 language=eng final.mp4
```

Burned-in (only if `ffmpeg -filters | grep subtitles` is non-empty):

```bash
ffmpeg -y -i concat.mp4 -vf "subtitles='captions.srt'" \
  -c:v libx264 -pix_fmt yuv420p -c:a copy final.mp4
```

## Probe a duration

```bash
ffprobe -v error -show_entries format=duration \
  -of default=noprint_wrappers=1:nokey=1 file.mp4
```
