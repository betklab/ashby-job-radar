#!/usr/bin/env python3
"""
Ashby Job Radar — daily digest of recently published Growth / Product roles
in Berlin & Munich, pulled from Ashby's public job-board API.

Ashby has no global search, so this script:
  1. reads company board slugs from companies.txt (jobs.ashbyhq.com/<slug>)
  2. optionally discovers new slugs via a web-search API (Brave), running
     the query  site:jobs.ashbyhq.com ("growth" OR "product") berlin|munich
  3. fetches every board, filters by title + location + recency
  4. skips anything already sent before (seen.json)
  5. writes digest.md / digest.html and emails it if SMTP is configured

Standard library only. Python 3.9+.
"""
from __future__ import annotations

import argparse
import html
import json
import os
import re
import smtplib
import sys
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

ROOT = Path(__file__).resolve().parent
API = "https://api.ashbyhq.com/posting-api/job-board/{slug}?includeCompensation=true"
UA = {"User-Agent": "ashby-job-radar/1.0 (personal job digest)"}


# ---------------------------------------------------------------- config / io
def load_config(path: Path) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def load_slugs(path: Path) -> list[str]:
    """Accepts bare slugs or full jobs.ashbyhq.com URLs, one per line; # comments."""
    slugs = []
    if not path.exists():
        return slugs
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        m = re.search(r"jobs\.ashbyhq\.com/([^/?#\s]+)", line)
        slug = m.group(1) if m else line
        slug = urllib.parse.unquote(slug).strip()
        if slug and slug.lower() not in {s.lower() for s in slugs}:
            slugs.append(slug)
    return slugs


def load_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


# ---------------------------------------------------------------- discovery
def discover_slugs(cfg: dict) -> set[str]:
    """Optional: find new Ashby boards via Brave Search API (BRAVE_API_KEY)."""
    key = os.environ.get("BRAVE_API_KEY")
    if not key:
        return set()
    found = set()
    for q in cfg.get("discovery_queries", []):
        url = "https://api.search.brave.com/res/v1/web/search?" + urllib.parse.urlencode(
            {"q": q, "count": 20, "freshness": "pw"})
        req = urllib.request.Request(url, headers={
            "Accept": "application/json", "X-Subscription-Token": key, **UA})
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                data = json.load(r)
        except Exception as e:  # discovery is best-effort
            print(f"[discover] query failed: {q!r}: {e}", file=sys.stderr)
            continue
        for item in data.get("web", {}).get("results", []):
            m = re.search(r"jobs\.ashbyhq\.com/([^/?#]+)", item.get("url", ""))
            if m:
                found.add(urllib.parse.unquote(m.group(1)))
    return found


# ---------------------------------------------------------------- fetching
def fetch_board(slug: str) -> tuple[str, list[dict] | None, str | None]:
    req = urllib.request.Request(API.format(slug=urllib.parse.quote(slug)), headers=UA)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            data = json.load(r)
        return slug, data.get("jobs", []), None
    except urllib.error.HTTPError as e:
        return slug, None, f"HTTP {e.code}"
    except Exception as e:
        return slug, None, type(e).__name__


# ---------------------------------------------------------------- filtering
def norm(s: str) -> str:
    return (s or "").lower().replace("ü", "u")


def job_locations(job: dict) -> list[str]:
    locs = [job.get("location") or ""]
    for sl in job.get("secondaryLocations") or []:
        locs.append(sl.get("location") or "")
    addr = (job.get("address") or {}).get("postalAddress") or {}
    locs.append(" ".join(str(addr.get(k) or "") for k in
                         ("addressLocality", "addressRegion", "addressCountry")))
    return [l for l in locs if l.strip()]


def match_location(job: dict, cfg: dict) -> str | None:
    """Return the matched label, or None."""
    blob = norm(" | ".join(job_locations(job)))
    for city in cfg["cities"]:
        if any(norm(alias) in blob for alias in city["aliases"]):
            return city["label"]
    if cfg.get("include_remote_germany") and job.get("isRemote"):
        if any(t in blob for t in ("germany", "deutschland", "dach")):
            return "Remote (DE)"
    return None


def word_hit(text: str, words: list[str]) -> list[str]:
    t = norm(text)
    return [w for w in words if re.search(r"\b" + re.escape(norm(w)) + r"\b", t)]


def parse_date(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def score(job: dict, cfg: dict, published: datetime, now: datetime) -> tuple[int, list[str]]:
    title = job.get("title", "")
    s, why = 0, []
    for w, pts in cfg["title_boost"].items():
        if word_hit(title, [w]):
            s += pts
            why.append(w)
    age_days = (now - published).total_seconds() / 86400
    s += max(0, 10 - int(age_days * 2))          # fresher = higher
    if job.get("_loc") in ("Berlin", "Munich"):
        s += 3
    return s, why


def flags(job: dict, cfg: dict) -> list[str]:
    out = []
    desc = norm(job.get("descriptionPlain") or "")
    if any(norm(p) in desc for p in cfg["german_required_phrases"]):
        out.append("German required")
    if any(word_hit(job.get("title", ""), [w]) for w in cfg.get("seniority_flags", [])):
        out.append("very senior")
    return out


def compensation(job: dict) -> str:
    comp = job.get("compensation") or {}
    return (comp.get("compensationTierSummary")
            or comp.get("scrapeableCompensationSalarySummary") or "")


def curate(boards: dict[str, list[dict]], cfg: dict, seen: dict, now: datetime):
    cutoff = now - timedelta(days=cfg["lookback_days"])
    picks = []
    for slug, jobs in boards.items():
        for job in jobs:
            if job.get("isListed") is False:
                continue
            title = job.get("title", "")
            if not word_hit(title, cfg["title_include"]):
                continue
            if word_hit(title, cfg["title_exclude"]):
                continue
            loc = match_location(job, cfg)
            if not loc:
                continue
            pub = parse_date(job.get("publishedAt"))
            if not pub or pub < cutoff:
                continue
            key = job.get("id") or job.get("jobUrl")
            if key in seen:
                continue
            job["_slug"], job["_loc"], job["_pub"] = slug, loc, pub
            job["_score"], job["_why"] = score(job, cfg, pub, now)
            job["_flags"] = flags(job, cfg)
            if "German required" in job["_flags"]:
                job["_score"] -= cfg.get("german_required_penalty", 6)
            job["_comp"] = compensation(job)
            picks.append(job)
    picks.sort(key=lambda j: (-j["_score"], -j["_pub"].timestamp()))
    return picks[: cfg["max_results"]]


# ---------------------------------------------------------------- output
def company_name(slug: str) -> str:
    return re.sub(r"[-_]+", " ", slug).strip().title()


def render_md(picks, now, stats) -> str:
    lines = [f"# Ashby Job Radar — {now:%a %d %b %Y}", "",
             f"{len(picks)} new matching roles · {stats['boards_ok']} boards scanned"
             f" · {stats['jobs_total']} postings checked", ""]
    if not picks:
        lines.append("_Nothing new today._")
    for j in picks:
        meta = [j["_loc"], f"posted {j['_pub']:%d %b}",
                j.get("workplaceType") or "", j.get("employmentType") or "", j["_comp"]]
        lines.append(f"### [{j['title']}]({j['jobUrl']}) — {company_name(j['_slug'])}")
        lines.append(" · ".join(m for m in meta if m))
        if j["_flags"]:
            lines.append("⚠️ " + ", ".join(j["_flags"]))
        lines.append("")
    if stats["bad_slugs"]:
        lines += ["---", "Boards that failed (fix or remove from companies.txt): "
                  + ", ".join(f"`{s}` ({e})" for s, e in stats["bad_slugs"])]
    if stats["new_slugs"]:
        lines += ["", "New boards discovered and added: "
                  + ", ".join(f"`{s}`" for s in sorted(stats["new_slugs"]))]
    return "\n".join(lines) + "\n"


def render_html(picks, now, stats) -> str:
    e = html.escape
    rows = []
    for j in picks:
        meta = " · ".join(m for m in [j["_loc"], f"posted {j['_pub']:%d %b}",
                                      j.get("workplaceType") or "", j["_comp"]] if m)
        flag = (f'<div style="color:#b45309;font-size:13px">⚠ {e(", ".join(j["_flags"]))}</div>'
                if j["_flags"] else "")
        rows.append(
            f'<tr><td style="padding:12px 0;border-bottom:1px solid #e5e7eb">'
            f'<a href="{e(j["jobUrl"])}" style="font-weight:600;color:#1d4ed8;'
            f'text-decoration:none">{e(j["title"])}</a>'
            f'<div style="color:#111">{e(company_name(j["_slug"]))}</div>'
            f'<div style="color:#6b7280;font-size:13px">{e(meta)}</div>{flag}</td></tr>')
    body = "".join(rows) or '<tr><td style="color:#6b7280">Nothing new today.</td></tr>'
    foot = ""
    if stats["bad_slugs"]:
        foot = ('<p style="color:#9ca3af;font-size:12px">Failed boards: '
                + e(", ".join(s for s, _ in stats["bad_slugs"])) + "</p>")
    return (f'<div style="font-family:-apple-system,Segoe UI,sans-serif;max-width:640px">'
            f'<h2 style="margin:0">Ashby Job Radar</h2>'
            f'<p style="color:#6b7280;margin:4px 0 12px">{now:%A %d %B %Y} · '
            f'{len(picks)} new roles from {stats["boards_ok"]} boards</p>'
            f'<table style="width:100%;border-collapse:collapse">{body}</table>{foot}</div>')


def send_email(subject: str, html_body: str, text_body: str) -> bool:
    user, pw, to = (os.environ.get(k) for k in ("SMTP_USER", "SMTP_PASS", "EMAIL_TO"))
    if not (user and pw and to):
        return False
    msg = MIMEMultipart("alternative")
    msg["Subject"], msg["From"], msg["To"] = subject, user, to
    msg.attach(MIMEText(text_body, "plain", "utf-8"))
    msg.attach(MIMEText(html_body, "html", "utf-8"))
    host = os.environ.get("SMTP_HOST", "smtp.gmail.com")
    port = int(os.environ.get("SMTP_PORT", "465"))
    with smtplib.SMTP_SSL(host, port, timeout=30) as s:
        s.login(user, pw)
        s.sendmail(user, [a.strip() for a in to.split(",")], msg.as_string())
    return True


# ---------------------------------------------------------------- main
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--config", default=ROOT / "config.json", type=Path)
    ap.add_argument("--companies", default=ROOT / "companies.txt", type=Path)
    ap.add_argument("--seen", default=ROOT / "seen.json", type=Path)
    ap.add_argument("--out", default=ROOT / "output", type=Path)
    ap.add_argument("--fixtures", type=Path, help="dir of <slug>.json files (offline test)")
    ap.add_argument("--no-email", action="store_true")
    ap.add_argument("--dry-run", action="store_true", help="don't update seen.json")
    args = ap.parse_args(argv)

    cfg = load_config(args.config)
    now = datetime.now(timezone.utc)
    slugs = load_slugs(args.companies)

    new_slugs = set()
    if not args.fixtures:
        known = {s.lower() for s in slugs}
        new_slugs = {s for s in discover_slugs(cfg) if s.lower() not in known}
        if new_slugs:
            with open(args.companies, "a", encoding="utf-8") as f:
                f.write(f"\n# discovered {now:%Y-%m-%d}\n" + "\n".join(sorted(new_slugs)) + "\n")
            slugs += sorted(new_slugs)

    boards, bad = {}, []
    if args.fixtures:
        for p in sorted(args.fixtures.glob("*.json")):
            boards[p.stem] = json.loads(p.read_text())["jobs"]
    else:
        with ThreadPoolExecutor(max_workers=8) as pool:
            for slug, jobs, err in pool.map(fetch_board, slugs):
                if jobs is None:
                    bad.append((slug, err))
                else:
                    boards[slug] = jobs

    seen = load_json(args.seen, {})
    picks = curate(boards, cfg, seen, now)
    stats = {"boards_ok": len(boards), "bad_slugs": bad, "new_slugs": new_slugs,
             "jobs_total": sum(len(j) for j in boards.values())}

    args.out.mkdir(parents=True, exist_ok=True)
    md, page = render_md(picks, now, stats), render_html(picks, now, stats)
    (args.out / "digest.md").write_text(md, encoding="utf-8")
    (args.out / "digest.html").write_text(page, encoding="utf-8")
    (args.out / "digest.json").write_text(json.dumps(
        [{"title": j["title"], "company": j["_slug"], "location": j["_loc"],
          "published": j["_pub"].isoformat(), "url": j["jobUrl"], "flags": j["_flags"],
          "compensation": j["_comp"], "score": j["_score"]} for j in picks],
        indent=2, ensure_ascii=False), encoding="utf-8")

    if not args.dry_run:
        for j in picks:
            seen[j.get("id") or j["jobUrl"]] = now.date().isoformat()
        cutoff = (now - timedelta(days=cfg.get("forget_after_days", 60))).date().isoformat()
        seen = {k: v for k, v in seen.items() if v >= cutoff}
        args.seen.write_text(json.dumps(seen, indent=1, sort_keys=True), encoding="utf-8")

    print(md)
    if not args.no_email and (picks or cfg.get("email_when_empty")):
        subj = f"Ashby radar: {len(picks)} new Berlin/Munich roles ({now:%d %b})"
        if send_email(subj, page, md):
            print("Email sent.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
