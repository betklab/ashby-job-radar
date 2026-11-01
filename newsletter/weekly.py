#!/usr/bin/env python3
"""
Weekly newsletter: the roles published on bet's board in the last 7 days, sent to every
subscriber through Buttondown.

It reads the job board's own published data (no extra crawling), so the email always matches
the board. Standard library only.

  python newsletter/weekly.py --dry-run            # print the email, send nothing
  BUTTONDOWN_API_KEY=... python newsletter/weekly.py --mode draft   # save as a draft in Buttondown
  BUTTONDOWN_API_KEY=... python newsletter/weekly.py --mode send    # send to all subscribers
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from collections import OrderedDict
from datetime import datetime, timedelta, timezone

BOARD_URL = os.environ.get("BOARD_URL", "https://betklab.github.io/ashby-job-radar/")
API = "https://api.buttondown.com/v1/emails"
UA = {"User-Agent": "bets-board-newsletter/1.0"}
MONTHS = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()


def get(url: str) -> bytes:
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=60) as r:
        return r.read()


def load_board(board_url: str) -> dict:
    page = get(board_url).decode("utf-8", "replace")
    m = re.search(r'data-source="\./(jobs-[0-9a-f]+\.json)"', page)
    if not m:
        raise SystemExit("Couldn't find the job data on the board page. Is the board published?")
    return json.loads(get(board_url.rstrip("/") + "/" + m.group(1)))


def parse(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        d = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def day(d: datetime) -> str:
    return f"{d.day} {MONTHS[d.month - 1]}"


def md_escape(s: str) -> str:
    return re.sub(r"([\[\]\*_`])", r"\\\1", s or "")


def pick(data: dict, now: datetime, days: int) -> list[dict]:
    since = now - timedelta(days=days)
    jobs = []
    for j in data.get("jobs", []):
        pub = parse(j.get("published"))
        if pub and since < pub <= now + timedelta(hours=1):
            jobs.append({**j, "_pub": pub})
    jobs.sort(key=lambda j: j["_pub"], reverse=True)
    return jobs


def compose(jobs: list[dict], now: datetime, days: int, board_url: str, max_roles: int) -> tuple[str, str]:
    start = now - timedelta(days=days)
    span = f"{day(start)} – {day(now)}"
    n = len(jobs)
    subject = f"{n} new role{'s' if n != 1 else ''} on bet’s board ({span})"
    shown = jobs[:max_roles]
    by_company: "OrderedDict[str, list[dict]]" = OrderedDict()
    for j in shown:
        by_company.setdefault(j.get("companyName") or j.get("company") or "Other", []).append(j)

    lines = [
        f"**{n} new role{'s' if n != 1 else ''}** in product, growth, operations and more, "
        f"published {span} in Berlin, Munich or remote in Germany.",
        "",
        f"Browse everything, with search and filters, on [bet’s board]({board_url}).",
        "",
    ]
    for company, roles in by_company.items():
        lines.append(f"### {md_escape(company)}")
        lines.append("")
        for j in roles:
            meta = [j.get("location"), j.get("style") if j.get("style") and j.get("style") != "Not stated" else None,
                    j.get("salary") or None, f"posted {day(j['_pub'])}"]
            lines.append(f"- [{md_escape(j['title'])}]({j['url']}) · " + " · ".join(m for m in meta if m))
        lines.append("")
    if n > len(shown):
        lines += [f"…and {n - len(shown)} more on [the board]({board_url}).", ""]
    lines += ["---", "",
              "You’re getting this because you subscribed on bet’s board. "
              "Roles and details come from each company’s own job page; always check the posting before applying."]
    return subject, "\n".join(lines)


def send(api_key: str, subject: str, body: str, mode: str) -> dict:
    payload = {"subject": subject, "body": body, "status": "about_to_send" if mode == "send" else "draft"}
    req = urllib.request.Request(API, data=json.dumps(payload).encode(), method="POST",
                                 headers={**UA, "Authorization": f"Token {api_key}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        raise SystemExit(f"Buttondown refused the email (HTTP {e.code}): {e.read().decode('utf-8', 'replace')[:500]}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Send the weekly bet's board newsletter via Buttondown.")
    ap.add_argument("--mode", choices=["draft", "send"], default=os.environ.get("NEWSLETTER_MODE") or "draft")
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--max-roles", type=int, default=80)
    ap.add_argument("--board", default=BOARD_URL)
    ap.add_argument("--data-file", help="read a saved jobs-*.json instead of the live board (testing)")
    ap.add_argument("--dry-run", action="store_true", help="print the email and send nothing")
    args = ap.parse_args(argv)

    now = datetime.now(timezone.utc)
    data = json.load(open(args.data_file, encoding="utf-8")) if args.data_file else load_board(args.board)
    jobs = pick(data, now, args.days)
    if not jobs:
        print("No new roles this week, so no email.")
        return 0
    subject, body = compose(jobs, now, args.days, args.board, args.max_roles)
    if args.dry_run:
        print(subject + "\n\n" + body)
        return 0
    key = os.environ.get("BUTTONDOWN_API_KEY")
    if not key:
        raise SystemExit("BUTTONDOWN_API_KEY is not set.")
    result = send(key, subject, body, args.mode)
    print(f"{'Sent' if args.mode == 'send' else 'Saved as a draft'}: {subject} (id {result.get('id', '?')})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
