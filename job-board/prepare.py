#!/usr/bin/env python3
"""Snapshot one run's settings, company inputs and sent history before parallel collection."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import subprocess

import build
import sources


def fingerprint(cfg):
    return hashlib.sha256(json.dumps(cfg, sort_keys=True).encode()).hexdigest()


def read_object(path):
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected an object: {path}")
    return value


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args(argv)
    if args.out.exists():
        ap.error("Output directory must be new")
    cfg = build.radar.load_config(build.ROOT / "config.json")
    active = sources.enabled(cfg, "board_sources")
    registry = build.radar.load_json(build.ROOT / "boards.json", {})
    known = set(build.radar.load_slugs(build.ROOT / "companies.txt"))
    known.update(build.radar.load_slugs(build.ROOT / "embedded-boards/boards.txt"))
    known.update(k for k in registry if not k.startswith("_"))
    warnings = []
    try:
        known.update(build.radar.load_slugs_notion(cfg) or [])
    except Exception as e:
        message = f"Notion could not be read; using public company lists: {e}"
        print(f"[notion] {message}", file=sys.stderr)
        warnings.append(message)
    keys = sources.select(sorted(k.lower() for k in known), active)
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=build.ROOT, text=True).strip()
    provenance = {"commit": commit, "run": os.environ.get("GITHUB_RUN_ID", "local"),
                  "attempt": os.environ.get("GITHUB_RUN_ATTEMPT", "1"),
                  "inputsHash": hashlib.sha256(json.dumps(keys).encode()).hexdigest(), "warnings": warnings}
    write_json(args.out / "config.json", cfg)
    write_json(args.out / "boards.json", {"keys": keys})
    write_json(args.out / "seen.json", build.radar.load_json(build.ROOT / "seen.json", {}))
    write_json(args.out / "source.json", provenance)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary and warnings:
        with open(summary, "a", encoding="utf-8") as f:
            f.write("\n".join(warnings) + "\n")
    output = os.environ.get("GITHUB_OUTPUT")
    if output:
        with open(output, "a") as f:
            f.write("sources=" + json.dumps(active) + "\n")
            f.write("commit=" + provenance["commit"] + "\n")
    print(f"Snapshot: {len(keys)} boards; providers: {', '.join(active)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
