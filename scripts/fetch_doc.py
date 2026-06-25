#!/usr/bin/env python3
"""Step 2: fetch a documentation URL and write cleaned text to doc.md.

Strips script/style/nav/header/footer chrome, keeps headings, paragraphs, list
items, and collects images into an `## Images` section (url + alt text).

Network is used by default; `--html-file` bypasses it for tests.
Honors the resumable-step contract: skips when inputs unchanged unless --force.
"""
import argparse
import os
import re
import sys
from html.parser import HTMLParser

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "lib"))
import run_dir as rd

SKIP_TAGS = {"script", "style", "nav", "header", "footer", "noscript", "svg", "form"}
BLOCK_TAGS = {"p", "div", "section", "article", "li", "br", "tr"}
HEADING_TAGS = {"h1", "h2", "h3", "h4", "h5", "h6"}


class DocParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.skip_depth = 0
        self.parts = []
        self.images = []
        self._heading = None
        self._buf = []

    def handle_starttag(self, tag, attrs):
        if tag in SKIP_TAGS:
            self.skip_depth += 1
            return
        if self.skip_depth:
            return
        if tag == "img":
            a = dict(attrs)
            src = a.get("src") or a.get("data-src")
            if src:
                self.images.append((src, a.get("alt", "").strip()))
        if tag in HEADING_TAGS:
            self._flush()
            self._heading = tag
        elif tag in BLOCK_TAGS:
            self._flush()

    def handle_endtag(self, tag):
        if tag in SKIP_TAGS and self.skip_depth:
            self.skip_depth -= 1
            return
        if self.skip_depth:
            return
        if tag in HEADING_TAGS:
            self._flush()

    def handle_data(self, data):
        if self.skip_depth:
            return
        text = data.strip()
        if text:
            self._buf.append(text)

    def _flush(self):
        if not self._buf:
            return
        text = " ".join(self._buf).strip()
        self._buf = []
        if not text:
            self._heading = None
            return
        if self._heading:
            level = int(self._heading[1])
            self.parts.append("#" * level + " " + text)
            self._heading = None
        else:
            self.parts.append(text)

    def result(self):
        self._flush()
        return self.parts, self.images


def clean_html(html):
    parser = DocParser()
    parser.feed(html)
    parts, images = parser.result()
    # collapse repeated blank lines, dedupe consecutive identical lines
    out, prev = [], None
    for line in parts:
        line = re.sub(r"\s+", " ", line).strip()
        if line and line != prev:
            out.append(line)
            prev = line
    body = "\n\n".join(out)
    if images:
        body += "\n\n## Images\n\n"
        body += "\n".join(f"- {alt or '(no alt)'} — {src}" for src, alt in images)
    return body + "\n"


def fetch(url):
    from urllib.request import Request, urlopen
    req = Request(url, headers={"User-Agent": "wordpress-tutorial-video/1.0"})
    with urlopen(req, timeout=30) as resp:
        charset = resp.headers.get_content_charset() or "utf-8"
        return resp.read().decode(charset, errors="replace")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-dir", required=True)
    ap.add_argument("--html-file", help="read local HTML instead of fetching (tests)")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    out_path = os.path.join(args.run_dir, "doc.md")
    if args.html_file:
        html = open(args.html_file, encoding="utf-8").read()
        url = "file://" + os.path.abspath(args.html_file)
        inputs = [args.html_file]
    else:
        cfg = rd.load_config(args.run_dir)
        url = cfg["doc_url"]
        inputs = [os.path.join(args.run_dir, "config.json")]
        if rd.is_done(args.run_dir, "fetch_doc", inputs) and not args.force:
            print(f"fetch_doc: up to date ({out_path})")
            return
        html = fetch(url)

    rd.atomic_write(out_path, f"# Source: {url}\n\n" + clean_html(html))
    if not args.html_file:
        rd.mark_done(args.run_dir, "fetch_doc", inputs)
    print(f"fetch_doc: wrote {out_path}")


if __name__ == "__main__":
    main()
