"""Word timings and deliberate pauses from a character-level alignment.

Engine-agnostic helpers for TTS services that voice a whole scene in one
request and return per-character timestamps (ElevenLabs "with-timestamps").
Voicing a scene in one piece keeps the prosody natural across sentences; the
pauses are then shaped afterwards, on the audio, instead of by splitting the
text:

  build_spoken()     narration -> the text that is sent, with pronunciation
                     overrides applied, plus a char map back to the narration
  word_timings()     alignment + char map -> [{word, start, end}] in the
                     narration's own spelling (exact cue and caption timing)
  find_boundaries()  sentence / paragraph boundaries with their timestamps
  shape_pauses()     lengthen (or cap) the silence at each boundary, trim the
                     edges, pad a lead and a tail; returns the new samples
                     and the list of time edits
  shift_words()      apply those edits to the word timings

Pure standard library (array-based PCM), so the premium voice path needs no
local ML stack and the tests run anywhere.
"""
import re
from array import array

_BOUNDARY = re.compile(r"[.!?][\"')\]]*(\s+)")


# --------------------------------------------------------------------------
# text: pronunciation overrides + char map
# --------------------------------------------------------------------------

def build_spoken(ref_text, overrides):
    """Apply pronunciation overrides to ``ref_text``.

    overrides: {term: say}. ``say`` is sent in place of the term — a plain
    respelling ("True SEO") or an IPA transcription wrapped in slashes
    ("/tɹuˌɛsˌiˈoʊ/"). Longer terms win over shorter ones.

    Returns (spoken_text, cmap) where cmap[i] describes spoken char i:
      ("p", ref_index)                    a char copied from the narration
      ("t", ref_start, ref_end, k, n)     char k of n inside a replaced term
                                          that covered ref_text[ref_start:ref_end]
    """
    spans = []
    for term in sorted(overrides or {}, key=len, reverse=True):
        say = overrides[term]
        if not term or not say:
            continue
        for m in re.finditer(rf"(?<![\w/]){re.escape(term)}(?![\w/])", ref_text):
            if any(m.start() < e and s < m.end() for s, e, _ in spans):
                continue
            spans.append((m.start(), m.end(), say))
    spans.sort()
    out, cmap, pos = [], [], 0
    for s, e, say in spans:
        for i in range(pos, s):
            out.append(ref_text[i])
            cmap.append(("p", i))
        for k, ch in enumerate(say):
            out.append(ch)
            cmap.append(("t", s, e, k, len(say)))
        pos = e
    for i in range(pos, len(ref_text)):
        out.append(ref_text[i])
        cmap.append(("p", i))
    return "".join(out), cmap


def word_timings(ref_text, cmap, starts, ends):
    """Per-word timings for ``ref_text`` from a per-spoken-char alignment.

    Chars copied from the narration take their own timestamps; chars of a
    replaced term share that term's time span in proportion to position, so
    a multi-word term ("All in One SEO") still yields one timing per word.
    """
    if not (len(cmap) == len(starts) == len(ends)):
        raise ValueError("alignment length does not match the spoken text")
    n = len(ref_text)
    cs, ce = [None] * n, [None] * n
    term_time = {}
    for i, m in enumerate(cmap):
        if m[0] == "p":
            cs[m[1]], ce[m[1]] = starts[i], ends[i]
        else:
            key = (m[1], m[2])
            t0, t1 = term_time.get(key, (starts[i], ends[i]))
            term_time[key] = (min(t0, starts[i]), max(t1, ends[i]))
    for (s, e), (t0, t1) in term_time.items():
        span = max(1, e - s)
        for j in range(s, e):
            cs[j] = t0 + (t1 - t0) * (j - s) / span
            ce[j] = t0 + (t1 - t0) * (j - s + 1) / span
    words = []
    for m in re.finditer(r"\S+", ref_text):
        ts = [cs[j] for j in range(m.start(), m.end()) if cs[j] is not None]
        te = [ce[j] for j in range(m.start(), m.end()) if ce[j] is not None]
        if not ts:
            continue
        words.append({"word": m.group(0), "start": round(min(ts), 3), "end": round(max(te), 3)})
    return words


def find_boundaries(spoken, starts, ends, min_words=0):
    """Sentence / paragraph boundaries of ``spoken`` with their timestamps.

    Returns [{"kind": "sentence"|"paragraph", "t_prev": end of the last char
    before the gap, "t_next": start of the first char after it}]. A boundary
    is sentence-ending punctuation followed by whitespace; it is a paragraph
    boundary when that whitespace contains a newline. A bare newline is a
    paragraph boundary too. Within a paragraph, a boundary next to a sentence
    shorter than ``min_words`` words is skipped (its natural pause is kept),
    mirroring the Kokoro natural-pacing merge rule.
    """
    cuts = []
    for m in _BOUNDARY.finditer(spoken):
        nxt = m.end()
        if nxt >= len(spoken):
            continue
        kind = "paragraph" if "\n" in m.group(1) else "sentence"
        if kind == "sentence" and spoken[nxt].islower():
            continue  # "e.g. this" — an abbreviation, not a sentence end
        cuts.append((m.start(1), nxt, kind))
    for m in re.finditer(r"[^\s.!?\"')\]]([ \t]*\n\s*)", spoken):
        if m.end() < len(spoken):
            cuts.append((m.start(1), m.end(), "paragraph"))
    cuts.sort()
    out, seg_start = [], 0
    for idx, (a, b, kind) in enumerate(cuts):
        nxt_end = cuts[idx + 1][0] if idx + 1 < len(cuts) else len(spoken)
        if kind == "sentence" and min_words:
            before = len(spoken[seg_start:a].split())
            after = len(spoken[b:nxt_end].split())
            if before < min_words or after < min_words:
                continue  # merged: seg_start stays, like split_units()
        seg_start = b
        out.append({"kind": kind, "t_prev": ends[a - 1], "t_next": starts[b]})
    return out


# --------------------------------------------------------------------------
# audio: envelope, quiet runs, pause edits
# --------------------------------------------------------------------------

def _envelope(samples, win):
    n = len(samples) // win
    env = [0] * n
    for i in range(n):
        seg = samples[i * win:(i + 1) * win]
        env[i] = max(max(seg), -min(seg))
    return env


def _longest_quiet(quiet, lo, hi):
    """Longest run of True in quiet[lo:hi] -> (start, end) or None."""
    best, run_start = None, None
    for i in range(max(0, lo), min(len(quiet), hi)):
        if quiet[i]:
            if run_start is None:
                run_start = i
        elif run_start is not None:
            if best is None or i - run_start > best[1] - best[0]:
                best = (run_start, i)
            run_start = None
    end = min(len(quiet), hi)
    if run_start is not None and (best is None or end - run_start > best[1] - best[0]):
        best = (run_start, end)
    return best


def _fade(samples, a, b, fade_in):
    n = b - a
    for i in range(n):
        g = (i + 1) / (n + 1) if fade_in else 1 - (i + 1) / (n + 1)
        samples[a + i] = int(samples[a + i] * g)


def shape_pauses(pcm, sr, boundaries, gaps, lead_s=0.3, tail_s=0.45,
                 max_pause_s=None, quiet_ratio=0.02, win_s=0.005, edge_keep_s=0.04):
    """Shape the silences of 16-bit mono PCM.

    pcm: bytes or array('h'). boundaries: from find_boundaries(). gaps:
    {"sentence": seconds, "paragraph": seconds} — MINIMUM pause at each kind
    of boundary (a missing or zero entry leaves that kind alone). A pause the
    engine made longer than the minimum is kept, unless it exceeds
    ``max_pause_s``. The edges are trimmed to the voice and padded with
    ``lead_s`` / ``tail_s`` of silence.

    Returns (array('h') samples, edits). edits is [(t, delta, quiet_from)] in
    original time: every timestamp >= t moves by delta seconds, and
    quiet_from (or None) is where the silence around t begins — the moment
    the preceding word really ends (see shift_words()).
    """
    x = pcm if isinstance(pcm, array) else array("h", pcm)
    if not len(x):
        return x, []
    win = max(1, int(win_s * sr))
    env = _envelope(x, win)
    peak = max(env) if env else 0
    thresh = max(40, peak * quiet_ratio)
    quiet = [e < thresh for e in env]
    fade = max(1, int(0.006 * sr))

    plan = []  # (sample_index, delta_samples)
    for b in boundaries:
        target = float(gaps.get(b["kind"]) or 0)
        lo = int((b["t_prev"] - 0.12) / win_s)
        hi = int((b["t_next"] + 0.12) / win_s) + 1
        run = _longest_quiet(quiet, lo, hi)
        if run:
            r0, r1 = run  # the window may clip the silence: grow it to its real edges
            while r0 > 0 and quiet[r0 - 1]:
                r0 -= 1
            while r1 < len(quiet) and quiet[r1]:
                r1 += 1
            run = (r0, r1)
            have = (run[1] - run[0]) * win / sr
            cut = (run[0] + run[1]) // 2 * win
            quiet_from = run[0] * win / sr
        else:
            have = 0.0
            quiet_from = None
            a = max(0, int(b["t_prev"] / win_s))
            z = min(len(env), max(a + 1, int(b["t_next"] / win_s) + 1))
            cut = min(range(a, z), key=lambda i: env[i]) * win if a < len(env) else len(x)
        if target and have < target:
            plan.append((cut, int(round((target - have) * sr)), quiet_from))
        elif max_pause_s and have > max_pause_s:
            plan.append((cut, -int(round((have - max_pause_s) * sr)), quiet_from))
        elif quiet_from is not None:
            plan.append((cut, 0, quiet_from))  # untouched pause: still marks where speech ends

    out, edits, pos = array("h"), [], 0
    for cut, delta, quiet_from in sorted(plan, key=lambda p: p[:2]):
        if delta == 0:
            edits.append((cut / sr, 0.0, quiet_from))
            continue
        cut = min(max(cut, pos), len(x))
        if delta > 0:
            out.extend(x[pos:cut])
            f = min(fade, cut - pos)
            if f:
                _fade(out, len(out) - f, len(out), fade_in=False)
            out.extend(array("h", bytes(2 * delta)))
            mark = len(out)
            pos = cut
            out.extend(x[pos:min(len(x), pos + fade)])
            _fade(out, mark, len(out), fade_in=True)
            pos = min(len(x), pos + fade)
        else:
            half = (-delta) // 2
            a, z = max(pos, cut - half), min(len(x), cut + (-delta - half))
            out.extend(x[pos:a])
            pos = z
            delta = -(z - a)
        edits.append((cut / sr, delta / sr, quiet_from))
    out.extend(x[pos:])

    # edges: trim to the voice, then pad the lead and the tail
    env2 = _envelope(out, win)
    loud = [i for i, e in enumerate(env2) if e >= thresh]
    if loud:
        keep = int(edge_keep_s * sr)
        a = max(0, loud[0] * win - keep)
        z = min(len(out), (loud[-1] + 1) * win + keep)
        out = out[a:z]
        lead = int(lead_s * sr)
        out = array("h", bytes(2 * lead)) + out + array("h", bytes(2 * int(tail_s * sr)))
        edits.append((0.0, (lead - a) / sr, None))
    return out, edits


def shift_words(words, edits, total_s=None):
    """Apply shape_pauses() edits to word timings (returns a new list).

    edits: [(t, delta)] or [(t, delta, quiet_from)]. A service's timestamp
    for sentence-final punctuation usually runs into the pause that follows;
    such a word ends where that silence begins, so no word spans a pause and
    captions clear on time.
    """
    edits = sorted((e[0], e[1], e[2] if len(e) > 2 else None) for e in edits)

    def shift_at(t):
        return sum(d for at, d, _ in edits if at <= t + 1e-9)

    out = []
    for w in words:
        s, e = w["start"], w["end"]
        base = shift_at(s)
        ne = None
        for at, d, quiet_from in edits:
            if quiet_from is not None and s + 1e-9 < quiet_from <= e + 1e-9:
                ne = quiet_from + base          # speech stopped here
                break
            if d > 0 and s + 1e-9 < at <= e + 1e-9:
                ne = at + base                  # no silence info: stop at the cut
                break
        ns = s + base
        if ne is None:
            ne = e + shift_at(e)
        ns, ne = max(0.0, ns), max(0.0, ne)
        if total_s is not None:
            ns, ne = min(ns, total_s), min(ne, total_s)
        out.append({"word": w["word"], "start": round(ns, 3), "end": round(max(ns, ne), 3)})
    return out


def scale_times(values, factor):
    """Divide timestamps by a tempo factor (after an atempo stretch)."""
    return [v / factor for v in values]
