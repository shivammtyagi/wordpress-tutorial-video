"""Deterministic pre-TTS text normalization.

Two views of the same narration string:
  for_tts(): what Kokoro reads — IPA markdown links for lexicon terms, versions/URLs/
             tech tokens expanded to their spoken form.
  for_ref(): the WER reference — identical spoken expansions but lexicon terms stay
             as plain written words (WhisperX is glossary-biased toward them).
Bare cardinal numbers are left alone (misaki/num2words handles them), so the reference
and the transcript agree after EnglishTextNormalizer.
"""
import json
import os
import re

_LEXICON_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "references", "lexicon.json")

_VERSION = re.compile(r"\bv?(\d+)\.(\d+)(?:\.(\d+))?\b")
_URL = re.compile(r"\bhttps?://([^\s/]+)((?:/[^\s]*)?)")
_EXT = re.compile(r"\b(\w+)\.(php|css|js|json|xml|html)\b", re.IGNORECASE)

_ONES = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine",
         "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen",
         "seventeen", "eighteen", "nineteen"]


def _small_num(s):
    n = int(s)
    return _ONES[n] if 0 <= n < 20 else s


def load_lexicon(extra=None):
    with open(os.path.abspath(_LEXICON_PATH), encoding="utf-8") as f:
        lex = json.load(f)
    if extra:
        lex.update(extra)
    return lex


def _spoken_version(m):
    parts = [m.group(1), m.group(2)] + ([m.group(3)] if m.group(3) else [])
    return "version " + " point ".join(_small_num(p) for p in parts)


def _spoken_url(m):
    host = m.group(1).replace(".", " dot ")
    path = (m.group(2) or "").strip("/")
    spoken = host
    if path:
        spoken += " slash " + path.replace("/", " slash ").replace("-", " ")
    return re.sub(r"\s+", " ", spoken).strip()


def _spoken_ext(m):
    return f"{m.group(1)} dot {' '.join(m.group(2).upper())}"


def _expand(text):
    text = _URL.sub(_spoken_url, text)
    text = _VERSION.sub(_spoken_version, text)
    text = _EXT.sub(_spoken_ext, text)
    text = re.sub(r"\bwp-admin\b", "W P admin", text)
    text = re.sub(r"\bwp-login\b", "W P login", text)
    return text


def for_tts(text, lexicon):
    out = _expand(text)
    for term, ipa in lexicon.items():
        out = re.sub(rf"(?<!\[)\b{re.escape(term)}\b", f"[{term}](/{ipa}/)", out)
    return out


def for_ref(text, lexicon):
    return _expand(text)
