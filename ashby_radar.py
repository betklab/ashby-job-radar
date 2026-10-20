#!/usr/bin/env python3
"""
Ashby Job Radar — daily digest of recently published growth, product, marketing,
operations, GTM and BD roles anywhere in Germany, pulled from Ashby's public job-board API.

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


def _notion_text(prop: dict) -> str:
    t = prop.get("type")
    if t in ("title", "rich_text"):
        return "".join(x.get("plain_text", "") for x in prop.get(t, []))
    if t == "url":
        return prop.get("url") or ""
    if t == "select":
        return (prop.get("select") or {}).get("name", "")
    if t == "formula":
        return str((prop.get("formula") or {}).get("string") or "")
    return ""


def load_slugs_notion(cfg: dict) -> list[str] | None:
    """Read Ashby IDs from a Notion database (NOTION_TOKEN + NOTION_DATABASE_ID).
    Column name from config 'notion.ashby_id_property' (text, title or URL column).
    If 'notion.active_property' names a checkbox column, only ticked rows are used."""
    token, db = os.environ.get("NOTION_TOKEN"), os.environ.get("NOTION_DATABASE_ID")
    if not (token and db):
        return None
    ncfg = cfg.get("notion", {})
    id_prop = ncfg.get("ashby_id_property", "Ashby ID")
    active = ncfg.get("active_property")
    db = re.sub(r"[^0-9a-fA-F]", "", db.split("?")[0])[-32:]   # accept a full Notion URL
    body: dict = {"page_size": 100}
    if active:
        body["filter"] = {"property": active, "checkbox": {"equals": True}}
    slugs, cursor = [], None
    while True:
        if cursor:
            body["start_cursor"] = cursor
        req = urllib.request.Request(
            f"https://api.notion.com/v1/databases/{db}/query",
            data=json.dumps(body).encode(), method="POST", headers={
                "Authorization": f"Bearer {token}", "Notion-Version": "2022-06-28",
                "Content-Type": "application/json", **UA})
        with urllib.request.urlopen(req, timeout=30) as r:
            data = json.load(r)
        for page in data.get("results", []):
            prop = page.get("properties", {}).get(id_prop)
            if not prop:
                continue
            raw = _notion_text(prop).strip()
            m = re.search(r"jobs\.ashbyhq\.com/([^/?#\s]+)", raw)
            slug = urllib.parse.unquote(m.group(1) if m else raw).strip()
            if slug and slug.lower() not in {s.lower() for s in slugs}:
                slugs.append(slug)
        if not data.get("has_more"):
            break
        cursor = data.get("next_cursor")
    return slugs


def load_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


# ---------------------------------------------------------------- discovery
SLUG_RE = re.compile(r"jobs\.ashbyhq\.com/([^/?#\s\"']+)")


def discover_tavily(cfg: dict, key: str) -> set[str]:
    """Tavily Search API — free tier, 1,000 searches/month, no card."""
    found = set()
    for q in cfg.get("discovery_queries", []):
        q = q.replace("site:jobs.ashbyhq.com", "").strip()   # domain filter does this
        body = json.dumps({"query": q, "include_domains": ["jobs.ashbyhq.com"],
                           "max_results": 20, "time_range": "week"}).encode()
        req = urllib.request.Request("https://api.tavily.com/search", data=body, headers={
            "Content-Type": "application/json", "Authorization": f"Bearer {key}", **UA})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                data = json.load(r)
        except Exception as e:
            print(f"[discover] tavily query failed: {q!r}: {e}", file=sys.stderr)
            continue
        for item in data.get("results", []):
            m = SLUG_RE.search(item.get("url", ""))
            if m:
                found.add(urllib.parse.unquote(m.group(1)))
    return found


def discover_slugs(cfg: dict) -> set[str]:
    """Optional: find new Ashby boards via Tavily (TAVILY_API_KEY) or Brave (BRAVE_API_KEY)."""
    if os.environ.get("TAVILY_API_KEY"):
        return discover_tavily(cfg, os.environ["TAVILY_API_KEY"])
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
    s = (s or "").lower()
    for a, b in (("ü", "u"), ("ö", "o"), ("ä", "a"), ("ß", "ss"), ("’", ""), ("'", ""), ("–", "-")):
        s = s.replace(a, b)
    return s


def job_locations(job: dict) -> list[str]:
    locs = [job.get("location") or ""]
    for sl in job.get("secondaryLocations") or []:
        locs.append(sl.get("location") or "")
    addr = (job.get("address") or {}).get("postalAddress") or {}
    locs.append(" ".join(str(addr.get(k) or "") for k in
                         ("addressLocality", "addressRegion", "addressCountry")))
    return [l for l in locs if l.strip()]


def match_location(job: dict, cfg: dict) -> str | None:
    """Return a display label like 'Berlin', 'Berlin / Munich' or 'Germany', or None."""
    blob = norm(" | ".join(job_locations(job)))
    cities = []
    for city in cfg["cities"]:
        if any(re.search(r"\b" + re.escape(norm(a)) + r"\b", blob) for a in city["aliases"]):
            cities.append(city["label"])
    if cities:
        return " / ".join(cities)
    country = norm(str(((job.get("address") or {}).get("postalAddress") or {})
                       .get("addressCountry") or ""))
    if country in ("de", "deu", "germany", "deutschland") or any(
            re.search(r"\b" + re.escape(norm(t)) + r"\b", blob) for t in cfg["country_terms"]):
        return "Germany"
    if cfg.get("include_remote_europe") and any(
            re.search(r"\b" + t + r"\b", blob) for t in ("europe", "emea", "eu")):
        return "Remote Europe"
    return None


def work_style(job: dict) -> str:
    wt = norm(job.get("workplaceType") or "")
    if wt == "remote":
        return "Remote"
    if wt == "hybrid":
        return "Hybrid"
    if wt in ("onsite", "on-site", "on site"):
        return "Office"
    locs = norm(" ".join(job_locations(job)))
    if "hybrid" in locs:
        return "Hybrid"
    if job.get("isRemote") or "remote" in locs:
        return "Remote"
    return "Not stated"


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


GERMAN_STOPWORDS = {"und", "die", "der", "das", "wir", "mit", "fur", "bei", "ist", "sie",
                    "ein", "eine", "auf", "zu", "von", "den", "dich", "du", "uns", "ihre"}


def german_required(job: dict, cfg: dict) -> str | None:
    desc = norm(job.get("descriptionPlain") or "")
    if not desc:
        return None
    words = re.findall(r"[a-z]+", desc)
    if words and sum(w in GERMAN_STOPWORDS for w in words) / len(words) > 0.08:
        return "German required (ad is in German)"
    if any(norm(p) in desc for p in cfg["german_required_phrases"]):
        return "German required"
    strong = r"(fluent|fluency|native|c1|c2|business|proficien|required|must|excellent|professional|verhandlungssicher)"
    soft = r"(plus|nice to have|nice-to-have|bonus|advantage|beneficial|preferred|helpful|not required)"
    for m in re.finditer(r"\b(german|deutsch)\b", desc):
        window = desc[max(0, m.start() - 60): m.end() + 60]
        if re.search(strong, window) and not re.search(soft, window):
            return "German required"
    return None


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
            job["_style"] = work_style(job)
            job["_german"] = german_required(job, cfg)
            job["_comp"] = compensation(job)
            picks.append(job)
    picks.sort(key=lambda j: j["_pub"], reverse=True)       # newest release first
    return picks[: cfg["max_results"]]


# ---------------------------------------------------------------- output
def company_name(slug: str) -> str:
    return re.sub(r"[-_]+", " ", slug).strip().title()


def job_meta(j: dict) -> list[str]:
    return [f"📍 {j['_loc']}", j["_style"], f"💶 {j['_comp'] or 'Salary not listed'}",
            f"posted {j['_pub']:%d %b}"]


def render_md(picks, now, stats) -> str:
    lines = [f"# Ashby Job Radar — {now:%a %d %b %Y}", "",
             f"{len(picks)} new roles in Germany · newest first · {stats['boards_ok']} "
             f"companies checked", ""]
    if not picks:
        lines.append("_Nothing new today._")
    for j in picks:
        lines.append(f"### [{j['title']}]({j['jobUrl']}) — {company_name(j['_slug'])}")
        lines.append(" · ".join(job_meta(j)))
        if j["_german"]:
            lines.append(f"🇩🇪 {j['_german']}")
        lines.append("")
    if stats["bad_slugs"]:
        lines += ["---", "Boards that failed (fix or remove the Ashby ID): "
                  + ", ".join(f"`{s}` ({e})" for s, e in stats["bad_slugs"])]
    if stats["new_slugs"]:
        lines += ["", "New boards discovered and added: "
                  + ", ".join(f"`{s}`" for s in sorted(stats["new_slugs"]))]
    return "\n".join(lines) + "\n"


STYLE_COLOR = {"Remote": "#047857", "Hybrid": "#1d4ed8", "Office": "#6b7280", "Not stated": "#9ca3af"}


def render_html(picks, now, stats) -> str:
    e = html.escape
    rows = []
    for j in picks:
        pill = (f'<span style="display:inline-block;padding:1px 8px;border-radius:10px;'
                f'border:1px solid {STYLE_COLOR[j["_style"]]};color:{STYLE_COLOR[j["_style"]]};'
                f'font-size:12px">{e(j["_style"])}</span>')
        german = (f'<div style="color:#b45309;font-size:13px;margin-top:2px">🇩🇪 {e(j["_german"])}</div>'
                  if j["_german"] else "")
        rows.append(
            f'<tr><td style="padding:12px 0;border-bottom:1px solid #e5e7eb">'
            f'<a href="{e(j["jobUrl"])}" style="font-weight:600;color:#1d4ed8;'
            f'text-decoration:none">{e(j["title"])}</a>'
            f'<div style="color:#111">{e(company_name(j["_slug"]))}</div>'
            f'<div style="color:#374151;font-size:13px;margin-top:2px">📍 {e(j["_loc"])} &nbsp;{pill}'
            f' &nbsp;💶 {e(j["_comp"] or "Salary not listed")}</div>'
            f'<div style="color:#9ca3af;font-size:12px">posted {j["_pub"]:%a %d %b}</div>'
            f'{german}</td></tr>')
    body = "".join(rows) or '<tr><td style="color:#6b7280">Nothing new today.</td></tr>'
    foot = ""
    if stats["bad_slugs"]:
        foot = ('<p style="color:#9ca3af;font-size:12px">Boards that failed: '
                + e(", ".join(s for s, _ in stats["bad_slugs"])) + "</p>")
    return (f'<div style="font-family:-apple-system,Segoe UI,sans-serif;max-width:640px">'
            f'<h2 style="margin:0">Ashby Job Radar</h2>'
            f'<p style="color:#6b7280;margin:4px 0 12px">{now:%A %d %B %Y} · '
            f'{len(picks)} new roles in Germany, newest first</p>'
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
    if not args.fixtures:
        try:
            notion = load_slugs_notion(cfg)
        except Exception as e:
            print(f"[notion] could not read database: {e}", file=sys.stderr)
            notion = None
        if notion is not None:
            print(f"[notion] {len(notion)} companies from Notion", file=sys.stderr)
            known = {s.lower() for s in slugs}
            slugs += [s for s in notion if s.lower() not in known]

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
          "published": j["_pub"].isoformat(), "url": j["jobUrl"], "work_style": j["_style"],
          "german_required": j["_german"], "compensation": j["_comp"]} for j in picks],
        indent=2, ensure_ascii=False), encoding="utf-8")

    if not args.dry_run:
        for j in picks:
            seen[j.get("id") or j["jobUrl"]] = now.date().isoformat()
        cutoff = (now - timedelta(days=cfg.get("forget_after_days", 60))).date().isoformat()
        seen = {k: v for k, v in seen.items() if v >= cutoff}
        args.seen.write_text(json.dumps(seen, indent=1, sort_keys=True), encoding="utf-8")

    print(md)
    if not args.no_email and (picks or cfg.get("email_when_empty")):
        subj = f"Ashby radar: {len(picks)} new roles in Germany ({now:%d %b})"
        if send_email(subj, page, md):
            print("Email sent.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
