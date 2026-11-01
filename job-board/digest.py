"""Read-only, grouped catch-up preview from fresh matching collections."""
from __future__ import annotations

import csv
import html
from pathlib import Path

import build
from prepare import write_json


def write_preview(out: Path, roles, seen, statuses):
    groups, rows = {}, []
    for job in roles:
        if str(job.get("id") or job["jobUrl"]) in seen or job["jobUrl"] in seen:
            continue
        groups.setdefault(job["_slug"], []).append(job)
    count = sum(map(len, groups.values()))
    warning = ", ".join(p for p, info in statuses.items() if info["state"] not in ("fresh", "partial"))
    intro = f"{count} unseen matching open roles. Fresh collections only; no date cutoff or result cap."
    if warning:
        intro += f" Sources not refreshed: {warning}."
    intro += " Saved history has finite retention; older jobs absent from it may have been sent before."
    md = ["# Catch-up preview", "", intro, ""]
    sections = ["<!doctype html><html lang=en><meta charset=utf-8><meta name=viewport content='width=device-width, initial-scale=1'>",
                "<title>Catch-up preview</title><style>body{font:16px/1.6 system-ui;max-width:900px;margin:32px auto;padding:0 20px;color:#172033}a{color:#3949ab}li{margin:1em 0}</style>",
                "<h1>Catch-up preview</h1><p>" + html.escape(intro) + "</p>"]
    def name(slug):
        return groups[slug][0].get("companyName") or build.radar.company_name(slug)
    for slug in sorted(groups, key=lambda s: (name(s).casefold(), s)):
        jobs = sorted(groups[slug], key=lambda j: j.get("publishedAt") or "", reverse=True)
        md += [f"## {name(slug)} — {len(jobs)} roles", ""]
        sections.append("<h2>" + html.escape(name(slug)) + "</h2><ul>")
        for job in jobs:
            card = build.card(job)
            rows.append(card)
            meta = " · ".join(filter(None, [card["location"], card["style"], card["salary"]]))
            md += [f"- [{card['title']}]({card['url']}) · {meta}"]
            sections.append('<li><a href="' + html.escape(card["url"], quote=True) + '">' + html.escape(card["title"]) + "</a><br>" + html.escape(meta) + "</li>")
        md.append("")
        sections.append("</ul>")
    out.mkdir(parents=True, exist_ok=True)
    (out/"digest.md").write_text("\n".join(md)+"\n", encoding="utf-8")
    (out/"digest.html").write_text("\n".join(sections)+"</html>\n", encoding="utf-8")
    write_json(out/"matches.json", rows)
    with (out/"matches.csv").open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["id", "title", "company", "location", "style", "salary", "published", "url"], extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    return count
