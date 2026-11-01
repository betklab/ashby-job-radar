"""
Job board providers: Ashby (the default) and Greenhouse.

A company is written as its board name. A bare name like `acme` is an Ashby board
(jobs.ashbyhq.com/acme); `greenhouse:acme` is a Greenhouse board
(job-boards.greenhouse.io/acme). Board addresses work too. Greenhouse jobs are turned
into the same fields as Ashby's, so the filters, the email and the job board treat both alike.

Standard library only. Python 3.9+.
"""
from __future__ import annotations

import html
import json
import re
import urllib.error
import urllib.request
from html.parser import HTMLParser
from urllib.parse import parse_qs, quote, unquote, urlsplit

SUPPORTED = ("ashby", "greenhouse")
GREENHOUSE_API = "https://boards-api.greenhouse.io/v1/boards/"
GREENHOUSE_URL = re.compile(r"(?:job-boards(?:\.eu)?|boards)\.greenhouse\.io/([^\s#]*)", re.I)
GREENHOUSE_TOKEN = re.compile(r"[a-z0-9_-]+")
# Greenhouse's own page serves EU-hosted boards too; the EU address is only a fallback.
GREENHOUSE_PAGES = ("https://job-boards.greenhouse.io/", "https://job-boards.eu.greenhouse.io/")
GREENHOUSE_LOGO = re.compile(r"(?:s\d+-)?recruiting\.cdn\.greenhouse\.io")
GREENHOUSE_LOGO_PATH = "/external_greenhouse_job_boards/logos/"


# ---------------------------------------------------------------- board names
def provider(key: str) -> str:
    """'greenhouse' for greenhouse:acme, otherwise 'ashby'."""
    prefix = key.split(":", 1)[0].lower() if ":" in key else ""
    return prefix if prefix in SUPPORTED else "ashby"


def board_key(value: str) -> str:
    """A line from companies.txt as a board name. Ashby names and addresses are read as
    before; a Greenhouse address or greenhouse:<token> becomes greenhouse:<token>."""
    value = value.strip()
    m = re.search(r"jobs\.ashbyhq\.com/([^/?#\s]+)", value)
    if m:
        return unquote(m.group(1)).strip()
    m = GREENHOUSE_URL.search(value)
    if m:
        path = urlsplit("https://x/" + m.group(1))
        parts = path.path.strip("/").split("/")
        token = (parse_qs(path.query).get("for") or [""])[0] if parts[0] == "embed" else parts[0]
        return greenhouse_key(token)
    if provider(value) == "greenhouse":
        return greenhouse_key(value.split(":", 1)[1])
    return unquote(value).strip()


def greenhouse_key(token: str) -> str:
    token = unquote(token).strip().lower()
    if not GREENHOUSE_TOKEN.fullmatch(token):
        raise ValueError(f"not a Greenhouse board name: {token!r}")
    return "greenhouse:" + token


def enabled(cfg: dict, key: str = "sources") -> list[str]:
    """Providers named in config.json. "sources" is for the email (Ashby if unset);
    "board_sources" is for the job board (the email's if unset)."""
    values = cfg.get(key, cfg.get("sources", ["ashby"]))
    if not isinstance(values, list) or not values or any(v not in SUPPORTED for v in values):
        raise ValueError(f"config.json {key} must list one or more of: {', '.join(SUPPORTED)}")
    return list(dict.fromkeys(values))


def select(keys, providers) -> list[str]:
    """The board names from these providers, in order, each once."""
    out, seen = [], set()
    for key in keys:
        if provider(key) in providers and key.lower() not in seen:
            seen.add(key.lower())
            out.append(key)
    return out


# ---------------------------------------------------------------- Greenhouse jobs
def jobs_list(body: bytes) -> list[dict]:
    data = json.loads(body)
    if not isinstance(data, dict) or not isinstance(data.get("jobs"), list):
        raise ValueError("Greenhouse returned no jobs list")
    if not all(isinstance(j, dict) for j in data["jobs"]):
        raise ValueError("Greenhouse returned an invalid job")
    total = (data.get("meta") or {}).get("total")
    if total is not None and total != len(data["jobs"]):
        raise ValueError("Greenhouse returned an incomplete jobs list")
    return data["jobs"]


def greenhouse_job(job: dict) -> dict:
    """A Greenhouse job with the field names of an Ashby one."""
    if not job.get("id") or not isinstance(job.get("title"), str):
        raise ValueError("Greenhouse job has no ID or title")
    first = job.get("first_published")
    return {"id": f"greenhouse:{job['id']}", "source": "greenhouse", "title": job["title"],
            "companyName": str(job.get("company_name") or "").strip(),
            "location": (job.get("location") or {}).get("name") or "", "secondaryLocations": [],
            "workplaceType": "", "isListed": True,
            "publishedAt": first if isinstance(first, str) else None,
            # the job list sends the description's HTML escaped once more
            "descriptionHtml": html.unescape(job.get("content") or ""),
            "jobUrl": job.get("absolute_url") or "",
            "department": " / ".join(d.get("name") or "" for d in job.get("departments") or [])}


def fetch_greenhouse(key: str, request, candidate=None) -> list[dict]:
    """Every open job on a Greenhouse board. Descriptions are fetched only when a job
    passes candidate(job), which saves a large download for boards with nothing relevant."""
    url = GREENHOUSE_API + quote(key.split(":", 1)[1], safe="") + "/jobs"
    jobs = [greenhouse_job(j) for j in request(url, parse=jobs_list)]
    if not any(candidate(j) if candidate else True for j in jobs):
        return jobs
    return [greenhouse_job(j) for j in request(url + "?content=true", parse=jobs_list)]


# ---------------------------------------------------------------- Greenhouse company profile
class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None          # the redirect is raised as an HTTPError instead


NO_REDIRECT = urllib.request.build_opener(_NoRedirect)


class _OpenGraphImage(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.image = ""

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag == "meta" and values.get("property") == "og:image" and not self.image:
            self.image = values.get("content") or ""


def board_logo(page: str) -> str:
    """Only a logo uploaded to the company's Greenhouse page, never another site's picture."""
    parser = _OpenGraphImage()
    parser.feed(page)
    url = html.unescape(parser.image.strip())
    parts = urlsplit(url)
    if (parts.scheme == "https" and GREENHOUSE_LOGO.fullmatch(parts.hostname or "")
            and parts.path.startswith(GREENHOUSE_LOGO_PATH)):
        return url
    return ""


def greenhouse_logo(token: str, request) -> str:
    """The logo on the board's Greenhouse page. A redirect means the company uses its own
    careers site, whose picture is usually a banner, so it's left out."""
    for base in GREENHOUSE_PAGES:
        try:
            page = request(base + quote(token, safe=""), opener=NO_REDIRECT.open,
                           parse=lambda body: body.decode("utf-8", "replace"))
            return board_logo(page)
        except urllib.error.HTTPError as e:
            if e.code == 404:            # not on this host: try the EU one
                continue
            if 300 <= e.code < 400:
                return ""
            raise
    return ""


def fetch_greenhouse_profile(key: str, request) -> dict:
    """Name, logo and the intro's HTML for a Greenhouse board (Greenhouse has no website field)."""
    token = key.split(":", 1)[1]
    board = request(GREENHOUSE_API + quote(token, safe=""))
    name = board.get("name") if isinstance(board, dict) else None
    if not isinstance(name, str) or not name.strip():
        raise ValueError("Greenhouse returned no company")
    return {"name": name.strip(), "logo": greenhouse_logo(token, request),
            "intro_html": board.get("content") or ""}
