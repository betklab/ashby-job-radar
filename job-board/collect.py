#!/usr/bin/env python3
"""Collect one provider independently. A failed scan never produces collection.json."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
import sys
from urllib.parse import unquote

import build
import sources
from prepare import fingerprint, read_object, write_json


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--inputs", type=Path, required=True)
    ap.add_argument("--source", choices=sources.SUPPORTED, required=True)
    ap.add_argument("--fixtures", type=Path, help="saved normalized board JSON, for offline checks")
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args(argv)
    if args.out.exists():
        ap.error("Output directory must be new")
    cfg = read_object(args.inputs / "config.json")
    if args.source not in sources.enabled(cfg, "board_sources"):
        ap.error("Provider is not enabled in board_sources")
    provenance = read_object(args.inputs / "source.json")
    keys = sources.select(read_object(args.inputs / "boards.json")["keys"], [args.source])
    keys = [k for k in keys if sources.provider(k) != "workable"]
    boards, failures, missing = {}, {}, []
    started = datetime.now(timezone.utc).isoformat()
    scan_ok = False
    if args.fixtures:
        for path in sorted(args.fixtures.glob("*.json")):
            key = unquote(path.stem)
            if sources.provider(key) == args.source:
                jobs = read_object(path)["jobs"]
                if not isinstance(jobs, list) or not all(isinstance(j, dict) for j in jobs):
                    raise ValueError(f"Invalid fixture {path}")
                boards[key] = jobs
        keys = list(boards)
        scan_ok = args.source == "workable"
    elif args.source == "workable":
        try:
            boards = build.radar.fetch_workable()
            scan_ok = True
        except Exception as e:
            failures["workable search"] = f"{type(e).__name__}: {e}"
    else:
        with ThreadPoolExecutor(max_workers=cfg.get("workers", 16)) as pool:
            for key, jobs, error in pool.map(lambda k: build.radar.fetch_board(k, cfg), keys):
                if jobs is not None:
                    boards[key] = jobs
                elif error == "HTTP 404":
                    missing.append(key)
                else:
                    failures[key] = error
    roles = build.open_roles(boards, cfg)
    ok = (scan_ok if args.source == "workable" else bool(boards) and len(failures) <= len(keys) // 20)
    report = {"provider": args.source, "generated_at": started, "boards_requested": len(keys),
              "boards_ok": len(boards), "boards_failed": len(failures), "boards_not_found": len(missing),
              "jobs_read": sum(map(len, boards.values())), "roles": len(roles),
              "failures": failures, "complete": ok, "scan_complete": scan_ok}
    write_json(args.out / "run.json", report)
    if not ok:
        print(f"[{args.source}] Incomplete collection: {len(failures)} failures", file=sys.stderr)
        return 1
    selected = {}
    for role in roles:
        selected.setdefault(role["_slug"], []).append({k: v for k, v in role.items() if not k.startswith("_")})
    write_json(args.out / "collection.json", {"version": 1, "provider": args.source,
               "config_hash": fingerprint(cfg), "provenance": provenance, "report": report, "boards": selected})
    print(f"[{args.source}] {len(roles)} matching roles; {len(boards)} boards read")
    return 0


if __name__ == "__main__":
    sys.exit(main())
