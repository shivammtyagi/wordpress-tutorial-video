#!/usr/bin/env python3
"""Validate a run without recording: config.json, script schema, and (optionally)
the environment. Prints the planned run-directory layout. Exits non-zero if the
run is not ready.

  python3 scripts/dry_run.py --run-dir <d> [--check-env]
"""
import argparse
import json
import os
import shutil
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "lib"))
import run_dir as rd
import schema

REQUIRED_CONFIG = ["doc_url"]
LAYOUT = [
    "config.json", "doc.md", "script.json", "script.discovered.json",
    "audio/NN.wav + durations.json", "clips/NN.raw.webm + NN.final.mp4",
    "captions.srt", "verify/transcript.json + report.json + frames/NN.png",
    "output/final.mp4", "state.json",
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-dir", required=True)
    ap.add_argument("--check-env", action="store_true")
    args = ap.parse_args()
    problems = []

    cfg_path = os.path.join(args.run_dir, "config.json")
    if not os.path.exists(cfg_path):
        problems.append(f"missing {cfg_path}")
    else:
        cfg = json.load(open(cfg_path))
        for key in REQUIRED_CONFIG:
            if not cfg.get(key):
                problems.append(f"config.json: missing '{key}'")

    for name, discovered in (("script.json", False), ("script.discovered.json", True)):
        p = os.path.join(args.run_dir, name)
        if os.path.exists(p):
            errs = schema.validate_script(json.load(open(p)), discovered=discovered)
            problems += [f"{name}: {e}" for e in errs]

    if args.check_env:
        for tool in ("ffmpeg", "ffprobe", "node"):
            if not shutil.which(tool):
                problems.append(f"env: '{tool}' not found (run scripts/bootstrap.sh)")

    print(f"Planned run directory: {args.run_dir}")
    for item in LAYOUT:
        print(f"  - {item}")

    if problems:
        print("\nNOT READY:")
        for p in problems:
            print(f"  ✗ {p}")
        sys.exit(1)
    print("\nREADY: config + script(s) valid.")


if __name__ == "__main__":
    main()
