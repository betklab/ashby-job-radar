# Ashby Job Radar

Daily email of **newly published Growth / Product / GTM roles in Berlin & Munich** from companies that hire through Ashby (`jobs.ashbyhq.com`).

It calls Ashby's public posting API for each board in `companies.txt`, keeps roles whose title matches and whose location is Berlin, Munich (incl. München / secondary locations) or remote-Germany, published in the last 7 days, and never sends the same job twice (`seen.json`). Each role is ranked by title fit and freshness and flagged when the description requires German or the title is VP/Director level.

## Setup on GitHub (≈5 minutes, free)

1. Create a **private** repo and upload everything in this folder (keep `.github/workflows/daily.yml`).
2. Repo → Settings → Secrets and variables → Actions → add:
   | Secret | Value |
   |---|---|
   | `SMTP_USER` | your Gmail address |
   | `SMTP_PASS` | a Gmail **app password** (Google Account → Security → App passwords; needs 2-step verification) |
   | `EMAIL_TO` | where the digest goes (comma-separate several) |
   | `BRAVE_API_KEY` | *optional* — enables auto-discovery of new Ashby boards with the `site:jobs.ashbyhq.com` queries in `config.json` |
3. Actions tab → **Ashby job radar** → **Run workflow** for a first run. After that it runs every morning around 07:40 Berlin time.

The first run will list every matching role from the past week; later runs only show what's new.

## Run locally

```bash
python3 ashby_radar.py --no-email      # prints digest, writes output/digest.{md,html,json}
python3 ashby_radar.py --dry-run       # same, without marking jobs as seen
```

## Tuning

- **More companies** — the coverage is only as good as `companies.txt`. Paste any `jobs.ashbyhq.com/...` URL you come across. Boards that fail (wrong slug) are listed at the bottom of each digest; delete those lines.
- **What counts as a match** — `config.json`: `title_include`, `title_exclude`, `title_boost` (ranking), `cities`, `lookback_days`, `german_required_phrases`.
- **Email even on empty days** — set `email_when_empty: true`.
