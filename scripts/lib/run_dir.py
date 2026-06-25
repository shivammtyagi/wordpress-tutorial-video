"""Shared run-directory + state helpers for the wordpress-tutorial-video pipeline.

Every pipeline step communicates only through a per-run working directory and
records completion (keyed by an input content-hash) in ``state.json`` so the
pipeline is resumable and each step is idempotent.
"""
import hashlib
import json
import os
import re
import tempfile


def _hash_inputs(inputs):
    """Hash both the paths and the contents of the given input files."""
    h = hashlib.sha256()
    for p in sorted(inputs):
        h.update(p.encode())
        if os.path.exists(p):
            with open(p, "rb") as f:
                h.update(f.read())
    return h.hexdigest()


def _state_path(run_dir):
    return os.path.join(run_dir, "state.json")


def _load_state(run_dir):
    p = _state_path(run_dir)
    return json.load(open(p)) if os.path.exists(p) else {}


def atomic_write(path, data):
    """Write ``data`` (str or bytes) to ``path`` atomically via a temp file."""
    parent = os.path.dirname(path) or "."
    os.makedirs(parent, exist_ok=True)
    is_bytes = isinstance(data, (bytes, bytearray))
    fd, tmp = tempfile.mkstemp(dir=parent)
    try:
        with os.fdopen(fd, "wb" if is_bytes else "w") as f:
            f.write(data)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def write_json(path, obj):
    atomic_write(path, json.dumps(obj, indent=2, ensure_ascii=False))


def mark_done(run_dir, step, inputs):
    """Record that ``step`` completed for the current hash of ``inputs``."""
    st = _load_state(run_dir)
    st[step] = {"input_hash": _hash_inputs(inputs)}
    atomic_write(_state_path(run_dir), json.dumps(st, indent=2))


def is_done(run_dir, step, inputs):
    """True only if ``step`` was completed and its inputs are unchanged."""
    st = _load_state(run_dir)
    return step in st and st[step].get("input_hash") == _hash_inputs(inputs)


def load_config(run_dir):
    return json.load(open(os.path.join(run_dir, "config.json")))


def slug_for(url):
    """Derive a filesystem-safe slug from the last path segment of a URL."""
    from urllib.parse import urlparse

    path = urlparse(url).path.strip("/")
    tail = path.split("/")[-1] if path else ""
    slug = re.sub(r"[^a-z0-9]+", "-", tail.lower()).strip("-")
    return slug or "video"
