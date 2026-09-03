#!/usr/bin/env python3
"""Turn a user-supplied image (e.g. a Claude Design export) into a full-bleed
intro or outro card template.

Writes <run>/assets/card_intro.html or card_outro.html with the image embedded
as a data URI, full-bleed cover. compose.py picks the template up automatically
on the next run; the {{TITLE}}/{{SUBTITLE}} placeholders are intentionally
absent — the image IS the card.

  python3 image_card.py --run-dir <dir> --image thumbnail.png --card intro
  python3 image_card.py --run-dir <dir> --image endcard.png  --card outro

Also handy: export the same image as the YouTube thumbnail (1280x720) with
  ffmpeg -i thumbnail.png -vf scale=1280:720 thumbnail-yt.png
"""
import argparse
import base64
import mimetypes
import os

TEMPLATE = """<!doctype html>
<html>
<head>
<meta charset="utf-8">
<style>
  html, body {{ margin: 0; height: 100%; }}
  body {{ background: {bg}; }}
  img {{ width: 100vw; height: 100vh; object-fit: cover; display: block; }}
</style>
</head>
<body>
  <img src="data:{mime};base64,{b64}" alt="">
</body>
</html>
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-dir", required=True)
    ap.add_argument("--image", required=True, help="PNG/JPEG to use as the card")
    ap.add_argument("--card", choices=["intro", "outro"], required=True)
    ap.add_argument("--bg", default="#000000", help="letterbox color if aspect differs")
    args = ap.parse_args()

    mime = mimetypes.guess_type(args.image)[0]
    if mime not in ("image/png", "image/jpeg", "image/webp"):
        raise SystemExit(f"image_card: unsupported image type {mime} ({args.image})")
    b64 = base64.b64encode(open(args.image, "rb").read()).decode()

    out_dir = os.path.join(args.run_dir, "assets")
    os.makedirs(out_dir, exist_ok=True)
    out = os.path.join(out_dir, f"card_{args.card}.html")
    with open(out, "w") as f:
        f.write(TEMPLATE.format(mime=mime, b64=b64, bg=args.bg))
    print(f"image_card: wrote {out} ({len(b64) // 1024} KiB embedded) — "
          f"re-run compose.py --force to rebuild the video with it")


if __name__ == "__main__":
    main()
