# FFmpeg recipes (steps 8–9)

These are the command templates `postprocess_clip.py` and `compose.py` build.
Copy/adapt when debugging or extending the compositor.

## Portability note (read this first)

Homebrew's current `ffmpeg` formula (8.x) is built **without** libfreetype,
libass, or fontconfig. That means the `drawtext` and `subtitles` filters are
**not available** on a stock `brew install ffmpeg` (verified on 8.1.2). This
skill therefore:

- renders intro/outro **cards via Chromium** (`render_card.mjs`) instead of
  `drawtext`, and
- ships captions as a **soft `mov_text` subtitle track** by default.

`--burn-captions` is honored only when the `subtitles` filter is detected;
otherwise it transparently falls back to soft captions.

## The single-encode rule

Video is encoded **once**, in `postprocess_clip.py`
(`libx264 -crf 18 -preset medium -pix_fmt yuv420p`). Everything downstream
(`_mux_scene`, concat) uses `-c:v copy`. Intro/outro cards are encoded with the
IDENTICAL parameters so the copy-concat stays valid. This preserves quality
(no generational loss) and makes compose nearly instant.

## Post-process: master → delivery (the one encode)

```bash
# 4K master (resolution × capture_scale) → crisp 1080p delivery
ffmpeg -y -i scene.raw.webm \
  -vf "fps=30,scale=1920:1080:flags=lanczos,\
tpad=stop_mode=clone:stop_duration=2.500" \
  -t 9.000 -an -c:v libx264 -crf 18 -preset medium -pix_fmt yuv420p scene.final.mp4
```

`fps=30` normalizes the VFR screencast to CFR **before** concat (forcing VFR→CFR
later duplicates frames unevenly and stutters). `tpad=stop_mode=clone` freezes
the last frame; `-t` caps to `max(clip, narration)`.

## Focus zoom (Ken Burns toward the focus box, at master resolution)

```bash
# cx,cy = focus box center × capture_scale (master pixels); clamped to frame
-vf "fps=30,zoompan=z='min(zoom+0.0005,1.08)'\
:x='min(max(0,CX-(iw/zoom/2)),iw-iw/zoom)'\
:y='min(max(0,CY-(ih/zoom/2)),ih-ih/zoom)'\
:d=1:s=3840x2160:fps=30,scale=1920:1080:flags=lanczos,..."
```

Zooming happens at 2x/4K and *then* downscales — zoomed pixels re-sample
original capture pixels and stay sharp. (Never zoom the finished 1080p video.)

## Mux a scene clip with its narration (video copied, audio normalized)

```bash
ffmpeg -y -i scene.final.mp4 -i scene.wav \
  -map 0:v:0 -map 1:a:0 -c:v copy \
  -af "loudnorm=I=-16:TP=-1.5:LRA=11,aresample=48000,apad" \
  -c:a aac -b:a 192k -ar 48000 -ac 2 -t <clip_len> seg.mp4
```

- `apad` + `-t <clip_len>`: audio is padded with silence to the video length —
  **never** `-shortest`, which truncates the video when actions outlast narration.
- `loudnorm` at −16 LUFS is deliberate: voice-first normalization. More
  aggressive targets (−14) make TTS voices sound robotic.
- `loudnorm` NaNs out on digitally-silent audio; `compose.py` automatically
  retries without it (matters only for stub/test audio).

## Intro/outro card → 2 s segment (card PNG from Chromium)

```bash
node scripts/render_card.mjs --title "My Tutorial" --subtitle "..." \
  --out card.png --width 1920 --height 1080
ffmpeg -y -loop 1 -i card.png \
  -f lavfi -i anullsrc=channel_layout=stereo:sample_rate=48000 \
  -t 2 -c:v libx264 -crf 18 -preset medium -pix_fmt yuv420p -r 30 \
  -vf scale=1920:1080 -c:a aac -b:a 192k -ar 48000 -ac 2 -shortest intro.mp4
```

## Concatenate segments (stream copy — uniform params by construction)

```bash
# list.txt: one `file '/abs/path/seg.mp4'` per line
ffmpeg -y -f concat -safe 0 -i list.txt -c copy concat.mp4
```

### Optional fade transitions (`transitions: "fade"` — re-encodes)

```bash
ffmpeg -y -i a.mp4 -i b.mp4 -filter_complex \
  "[0:v][1:v]xfade=transition=fade:duration=0.5:offset=<lenA-0.5>[v];\
   [0:a][1:a]acrossfade=d=0.5[a]" \
  -map "[v]" -map "[a]" -c:v libx264 -crf 18 -preset medium -pix_fmt yuv420p \
  -c:a aac -b:a 192k final.mp4
```

For eased (non-linear) wipes on stock ffmpeg, see scriptituk/xfade-easing —
pre-generated `xfade=transition=custom:expr='…'` strings, MIT.

## Chapters (FFMETADATA) + captions + faststart (final mux)

```bash
# chapters.ffmeta:
#   ;FFMETADATA1
#   title=My Tutorial
#   [CHAPTER]
#   TIMEBASE=1/1000
#   START=2000
#   END=11400
#   title=Open the Sitemaps settings
ffmpeg -y -i concat.mp4 -i captions.srt -i chapters.ffmeta \
  -map 0 -map 1 -map_metadata 2 \
  -c copy -c:s mov_text -metadata:s:s:0 language=eng \
  -movflags +faststart final.mp4
```

Burned-in (only if `ffmpeg -filters | grep subtitles` is non-empty):

```bash
ffmpeg -y -i concat.mp4 -i chapters.ffmeta -map_metadata 1 \
  -vf "subtitles='captions.srt'" \
  -c:v libx264 -crf 18 -preset medium -pix_fmt yuv420p -c:a copy \
  -movflags +faststart final.mp4
```

## Probe helpers

```bash
ffprobe -v error -show_entries format=duration -of default=noprint_wrappers=1:nokey=1 f.mp4
ffprobe -v error -select_streams v:0 -show_entries stream=width,height -of csv=p=0 f.mp4
ffprobe -v error -show_chapters -of json f.mp4
# loudness check:
ffmpeg -i final.mp4 -af loudnorm=print_format=summary -f null - 2>&1 | tail -12
```
