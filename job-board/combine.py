#!/usr/bin/env python3
"""Publish independent collections, with bounded stale fallback and a fresh-only digest preview."""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import re
import sys

import build
import sources
from digest import write_preview
from prepare import fingerprint, read_object, write_json


def parse_time(value):
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("Timestamp must include a timezone")
    return result


def validate(data, provider, cfg, provenance, now):
    report = data["report"]
    if (data.get("version") != 1 or data["provider"] != provider or not report["complete"]
            or data["config_hash"] != fingerprint(cfg) or data["provenance"] != provenance):
        raise ValueError("Incompatible provider, preferences or run")
    age = now - parse_time(report["generated_at"])
    if not -timedelta(minutes=5) <= age <= timedelta(hours=2):
        raise ValueError("Collection is outside this run's freshness window")
    if provider == "workable":
        if not report["scan_complete"]:
            raise ValueError("Workable search did not complete")
    elif report["boards_failed"] > report["boards_requested"] // 20:
        raise ValueError("Provider failure allowance exceeded")
    boards = data["boards"]
    if not isinstance(boards, dict):
        raise ValueError("No board mapping")
    for key, jobs in boards.items():
        if sources.provider(key) != provider or not isinstance(jobs, list):
            raise ValueError("Foreign provider or invalid board")
        for job in jobs:
            if not isinstance(job, dict) or job.get("source", provider) != provider:
                raise ValueError("Foreign or invalid job")
            if provider != "ashby" and not str(job.get("id", "")).startswith(provider + ":"):
                raise ValueError("Missing native job namespace")
    roles = build.open_roles(boards, cfg)
    if len(roles) != report["roles"] or sum(map(len, boards.values())) != len(roles):
        raise ValueError("Matching jobs disagree with report")
    return boards, report


def stale_jobs(previous, provider, cfg, now, max_age):
    if previous.get("configHash") != fingerprint(cfg):
        return [], None
    status = (previous.get("sourceStatus") or {}).get(provider, {})
    try:
        updated = status["updatedAt"]
        age = now - parse_time(updated)
        if status["state"] not in ("fresh", "partial", "stale") or not -timedelta(minutes=5) <= age <= max_age:
            return [], None
        jobs = [j for j in previous["jobs"] if j["source"] == provider]
        if any(sources.provider(j["company"]) != provider or not build.web_link(j["url"]) for j in jobs):
            return [], None
        return jobs, updated
    except (KeyError, TypeError, ValueError, AttributeError):
        return [], None


def combine(path, cfg, provenance, previous, now, max_age):
    available, boards, reports, statuses, retained = {}, {}, {}, {}, []
    # A single artifact can be downloaded flat; multiple ones normally get subfolders.
    for file in sorted(path.rglob("collection.json")):
        try:
            data = read_object(file)
            available.setdefault(data.get("provider"), []).append(data)
        except (OSError, ValueError, TypeError) as e:
            print(f"[combine] Ignoring {file}: {e}", file=sys.stderr)
    for provider in sources.enabled(cfg, "board_sources"):
        try:
            entries = available.get(provider, [])
            if len(entries) != 1:
                raise ValueError(f"Expected one collection; found {len(entries)}")
            jobs, report = validate(entries[0], provider, cfg, provenance, now)
            boards.update(jobs)
            reports[provider] = report
            statuses[provider] = {"state": "partial" if report["boards_failed"] else "fresh", "updatedAt": report["generated_at"]}
        except (KeyError, TypeError, ValueError, AttributeError) as e:
            jobs, updated = stale_jobs(previous, provider, cfg, now, max_age)
            retained.extend(jobs)
            statuses[provider] = {"state": "stale" if updated else "unavailable", "updatedAt": updated, "error": str(e)}
    return build.open_roles(boards, cfg), retained, reports, statuses


def load_previous(url):
    page = build.radar.request(url, parse=lambda b: b.decode("utf-8"))
    match = re.search(r'data-source="\./(jobs-[a-f0-9]+\.json)"', page)
    if not match:
        raise ValueError("Published page has no catalog data link")
    return build.radar.request(url.rstrip("/") + "/" + match[1])


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--inputs", type=Path, required=True)
    ap.add_argument("--collections", type=Path, required=True)
    previous = ap.add_mutually_exclusive_group()
    previous.add_argument("--previous", type=Path)
    previous.add_argument("--previous-url")
    ap.add_argument("--max-stale-hours", type=float, default=48)
    ap.add_argument("--profiles", type=Path)
    ap.add_argument("--featured", type=Path, default=build.HERE / "featured.txt")
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args(argv)
    if args.out.exists() or not 0 <= args.max_stale_hours <= 168:
        ap.error("Output must be new; stale limit must be between 0 and 168 hours")
    cfg = read_object(args.inputs / "config.json")
    seen = read_object(args.inputs / "seen.json")
    provenance = read_object(args.inputs / "source.json")
    baseline = read_object(args.previous) if args.previous else {}
    if args.previous_url:
        try:
            baseline = load_previous(args.previous_url)
        except Exception as e:
            print(f"[combine] Previous board unavailable: {e}", file=sys.stderr)
    now = datetime.now(timezone.utc)
    roles, retained, reports, statuses = combine(args.collections, cfg, provenance, baseline, now, timedelta(hours=args.max_stale_hours))
    report = {"generated_at": now.isoformat(), "sources": statuses, "provider_reports": reports,
              "fresh_sources": list(reports), "roles": len(roles) + len(retained), "retained_roles": len(retained),
              "config_hash": fingerprint(cfg), "provenance": provenance}
    write_json(args.out / "run.json", report)
    lines = [f"**{report['roles']} matching open roles**", "", "| Source | Status | Last collected |", "|---|---|---|"]
    lines += [f"| {p} | {s['state']} | {s['updatedAt'] or '—'} |" for p, s in statuses.items()]
    lines += ["", *provenance.get("warnings", []),
              "Digest preview uses fresh collections only; no email or sent-history writes."]
    (args.out/"summary.md").write_text("\n".join(lines)+"\n", encoding="utf-8")
    if not reports:
        print("No provider refreshed; keeping the previous publication.", file=sys.stderr)
        return 1
    profiles = build.radar.load_json(args.profiles, {}) if args.profiles else {}
    if args.profiles:
        build.update_profiles(sorted({j["_slug"] for j in roles}), profiles, now.date())
        build.radar.write_file(args.profiles, json.dumps(profiles, ensure_ascii=False, indent=1, sort_keys=True)+"\n")
    featured = {s.lower() for s in build.radar.load_slugs(args.featured)}
    build.write_site(args.out/"site", roles, profiles, featured, now, retained=retained,
                     previous_companies=baseline.get("companies"),
                     status={"configHash": fingerprint(cfg), "sourceStatus": statuses,
                             "partial": any(s["state"] != "fresh" for s in statuses.values())})
    report["unseen_preview_roles"] = write_preview(args.out/"digest", roles, seen, statuses)
    write_json(args.out/"run.json", report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
