#!/usr/bin/env python3
"""
Job board — one web page with every open role that fits config.json, for GitHub Pages.

Same companies and filters as the daily email, but every open role, not only new ones:
no date cutoff, no max_results, and seen.json is never read or changed. Company names,
logos and intros come from each company's Ashby page and are kept for 30 days (--profiles).

Standard library only. Python 3.9+.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))
import ashby_radar as radar  # noqa: E402

GRAPHQL = "https://jobs.ashbyhq.com/api/non-user-graphql"
PROFILE = """query CompanyProfile($slug: String!) {
  organizationFromHostedJobsPageName(organizationHostedJobsPageName: $slug, searchContext: JobBoard) {
    name publicWebsite theme { logoSquareImageUrl logoWordmarkImageUrl jobBoardTopDescriptionHtml }
  }
}"""


# ---------------------------------------------------------------- text and links
class PlainText(HTMLParser):
    """Keeps paragraph breaks; the company's HTML itself never goes on the page."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts, self.hidden = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.hidden += 1
        elif not self.hidden and tag in ("p", "div", "br", "li", "h1", "h2", "h3", "h4"):
            self.parts.append("\n" if tag == "br" else "\n\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self.hidden = max(0, self.hidden - 1)
        elif not self.hidden and tag in ("p", "div", "li", "h1", "h2", "h3", "h4"):
            self.parts.append("\n\n")

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def plain_text(html_text: str | None) -> str:
    parser = PlainText()
    parser.feed(html_text or "")
    return re.sub(r"\n[ \t]*\n(?:[ \t]*\n)+", "\n\n", "".join(parser.parts)).strip()


def web_link(value) -> str:
    """Only http(s) links go on the page."""
    try:
        parts = urlsplit(value.strip())
        return value.strip() if parts.scheme in ("http", "https") and parts.hostname else ""
    except (AttributeError, ValueError):
        return ""


# ---------------------------------------------------------------- roles
def open_roles(boards: dict[str, list[dict]], cfg: dict, now: datetime) -> list[dict]:
    """The email's own filters, without its date cutoff, so every open role counts."""
    picks = radar.curate(boards, {**cfg, "digest_mode": "week", "lookback_days": 36500}, {}, now)
    roles, keys = [], set()
    for job in picks:
        key = job.get("id") or job.get("jobUrl")
        if key not in keys and web_link(job.get("jobUrl")):   # a company can have two board names
            keys.add(key)
            roles.append(job)
    return roles


def card(job: dict) -> dict:
    locations = []
    for location in [job] + (job.get("secondaryLocations") or []):
        addr = radar.postal(location)
        label = location.get("location") or ", ".join(dict.fromkeys(
            str(addr[k]) for k in ("addressLocality", "addressRegion", "addressCountry") if addr.get(k)))
        if label and label not in locations:
            locations.append(label)
    return {
        "id": str(job.get("id") or job["jobUrl"]), "title": job["title"].strip(),
        "company": job["_slug"], "location": job["_loc"], "locations": locations,
        "style": job["_style"], "salary": job["_comp"],
        "employment": job.get("employmentType") or "",
        "department": job.get("department") or "", "team": job.get("team") or "",
        "published": job["_pub"].isoformat(),
        "description": (job.get("descriptionPlain") or "").strip() or plain_text(job.get("descriptionHtml")),
        "url": job["jobUrl"], "applyUrl": web_link(job.get("applyUrl")) or job["jobUrl"],
    }


# ---------------------------------------------------------------- company profiles
def fetch_profile(slug: str) -> dict:
    """Name, website, logo and intro, from the API behind the company's Ashby page."""
    body = json.dumps({"operationName": "CompanyProfile", "query": PROFILE,
                       "variables": {"slug": slug}}).encode()
    req = urllib.request.Request(GRAPHQL, data=body,
                                 headers={**radar.UA, "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        data = json.load(r)
    if data.get("errors"):
        raise RuntimeError(f"Ashby's page API changed: {data['errors']}")
    org = (data.get("data") or {}).get("organizationFromHostedJobsPageName") or {}
    theme = org.get("theme") or {}
    return {"name": (org.get("name") or "").strip(), "website": web_link(org.get("publicWebsite")),
            "logo": web_link(theme.get("logoSquareImageUrl") or theme.get("logoWordmarkImageUrl")),
            "intro": plain_text(theme.get("jobBoardTopDescriptionHtml"))}


def update_profiles(slugs: list[str], profiles: dict, today: date, max_age: int = 30) -> tuple[int, int]:
    """Fetch the profiles that are missing or older than max_age days, one a second.
    Stops early if Ashby asks us to slow down or answers oddly; the rest wait for the next run."""
    def fresh(p):
        try:
            return date.fromisoformat(p["fetched"]) > today - timedelta(days=max_age)
        except (KeyError, TypeError, ValueError):
            return False

    due = [s for s in slugs if not fresh(profiles.get(s))]
    fetched = failed = 0
    for i, slug in enumerate(due):
        if i:
            time.sleep(1)
        try:
            profiles[slug] = {**fetch_profile(slug), "fetched": today.isoformat()}
            fetched += 1
        except Exception as e:
            failed += 1
            print(f"[profiles] {slug}: {e}", file=sys.stderr)
            if isinstance(e, RuntimeError) or (isinstance(e, urllib.error.HTTPError)
                                               and (e.code == 429 or e.code >= 500)):
                print(f"[profiles] stopping; {len(due) - i - 1} left for the next run", file=sys.stderr)
                break
    return fetched, failed


# ---------------------------------------------------------------- page
def write_site(out: Path, roles: list[dict], profiles: dict, featured: set[str], now: datetime):
    """index.html plus one data file; names include a hash, so browsers never mix old and new."""
    out.mkdir(parents=True, exist_ok=True)
    companies = {}
    for job in roles:
        slug = job["_slug"]
        if slug not in companies:
            p = profiles.get(slug, {})
            companies[slug] = {"name": p.get("name") or radar.company_name(slug),
                               "website": p.get("website", ""), "logoUrl": p.get("logo", ""),
                               "introduction": p.get("intro", ""), "featured": slug in featured,
                               "roles": 0}
        companies[slug]["roles"] += 1
    jobs = [{**card(j), "companyName": companies[j["_slug"]]["name"]} for j in roles]
    data = json.dumps({"generatedAt": now.isoformat(), "jobs": jobs, "companies": companies},
                      ensure_ascii=False, separators=(",", ":")).encode()
    names = {"DATA_FILE": f"jobs-{hashlib.sha256(data).hexdigest()[:12]}.json",
             "ROLE_COUNT": f"{len(jobs):,}", "COMPANY_COUNT": str(len(companies)),
             "REPO": os.environ.get("GITHUB_REPOSITORY", "betklab/ashby-job-radar")}
    (out / names["DATA_FILE"]).write_bytes(data)
    for name in ("app.js", "styles.css"):
        content = (HERE / name).read_bytes()
        hashed = name.replace(".", f"-{hashlib.sha256(content).hexdigest()[:12]}.", 1)
        (out / hashed).write_bytes(content)
        names[name.upper().replace(".", "_")] = hashed
    page = (HERE / "index.html").read_text(encoding="utf-8")
    for key, value in names.items():
        page = page.replace("{{" + key + "}}", value)
    (out / "index.html").write_text(page, encoding="utf-8")
    (out / "favicon.svg").write_bytes((HERE / "favicon.svg").read_bytes())


# ---------------------------------------------------------------- main
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--config", default=ROOT / "config.json", type=Path)
    ap.add_argument("--companies", default=ROOT / "companies.txt", type=Path)
    ap.add_argument("--registry", default=ROOT / "boards.json", type=Path)
    ap.add_argument("--embedded", default=ROOT / "embedded-boards" / "boards.txt", type=Path)
    ap.add_argument("--featured", default=HERE / "featured.txt", type=Path,
                    help="companies shown with a star")
    ap.add_argument("--profiles", type=Path, help="company names and logos, kept between runs")
    ap.add_argument("--fixtures", type=Path, help="dir of <slug>.json files (offline test)")
    ap.add_argument("--out", default=ROOT / "site", type=Path)
    args = ap.parse_args(argv)

    cfg = radar.load_config(args.config)
    now = datetime.now(timezone.utc)
    boards, failed, gone = {}, {}, 0
    if args.fixtures:
        for p in sorted(args.fixtures.glob("*.json")):
            boards[p.stem] = json.loads(p.read_text())["jobs"]
        slugs = sorted(boards)
    else:
        # The same companies the radar knows: your list, Notion, embedded boards and the crawl
        slugs = {s.lower() for s in radar.load_slugs(args.companies) + radar.load_slugs(args.embedded)}
        slugs |= {k.lower() for k in radar.load_json(args.registry, {}) if k != "_meta"}
        try:
            slugs |= {s.lower() for s in radar.load_slugs_notion(cfg) or []}
        except Exception as e:
            print(f"[notion] could not read database: {e}", file=sys.stderr)
        slugs = sorted(slugs)
        with ThreadPoolExecutor(max_workers=cfg.get("workers", 16)) as pool:
            for slug, jobs, err in pool.map(radar.fetch_board, slugs):
                if jobs is not None:
                    boards[slug] = jobs
                elif err == "HTTP 404":           # no longer on Ashby
                    gone += 1
                else:
                    failed[slug] = err

    report = [f"Job board: {len(boards)} of {len(slugs)} companies checked, "
              f"{gone} no longer on Ashby, {len(failed)} failed"
              + (f" ({', '.join(sorted(failed)[:20])}{', …' if len(failed) > 20 else ''})" if failed else "")]
    ok = bool(boards) and len(failed) <= len(slugs) // 20     # a few hiccups are fine; an outage isn't
    if ok:
        roles = open_roles(boards, cfg, now)
        on_board = sorted({j["_slug"] for j in roles})
        profiles = radar.load_json(args.profiles, {}) if args.profiles else {}
        if args.profiles:
            fetched, bad = update_profiles(on_board, profiles, now.date())
            radar.write_file(args.profiles, json.dumps(profiles, indent=1, sort_keys=True) + "\n")
            report.append(f"Company profiles: {fetched} fetched, {bad} failed, the rest from earlier runs")
        write_site(args.out, roles, profiles, {s.lower() for s in radar.load_slugs(args.featured)}, now)
        report.insert(0, f"**{len(roles)} open roles at {len(on_board)} companies**")
    else:
        report.append("Too many companies failed, so the page wasn't updated (yesterday's stays up).")
    print("\n".join(report) + (f"\nPage written to {args.out}" if ok else ""))
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as f:
            f.write("\n\n".join(report) + "\n")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
