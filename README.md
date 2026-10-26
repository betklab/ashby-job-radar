# 📡 Ashby Job Radar

**Every morning, an email with the jobs that just went live in Germany. Only the new ones, and only the roles you actually care about.**

## Why this exists

Lots of startups and scale-ups hire through [Ashby](https://www.ashbyhq.com), and their job boards all live at `jobs.ashbyhq.com/<company>`. The catch is that Ashby has no search across companies. So you either open dozens of career pages every day, or you find out about a great role two weeks late, when it already has 400 applicants.

This radar does the boring part for you. It finds every company in Germany that hires through Ashby, checks all their boards every morning, and emails you only the roles that are fresh and relevant. You open one email and you're up to date.

## What lands in your inbox

A short list of roles **posted since yesterday's email**, newest first, with everything you need to decide in five seconds:

- **The role and the company**, with a link straight to the posting
- 📍 **City**, like Berlin, Munich or Hamburg ("Germany" if no city is given)
- **Work style**: Remote, Hybrid or Office
- 💶 **Salary**, when the company shares it
- 🇩🇪 **German required**, flagged when the ad asks for fluent or native German, or is written in German

You only ever see a job **once**, and never anything older than the previous email. If nothing new came out, you get no email.

## The job board

The same roles also go on one web page, with search and filters: every open role that fits, including ones you were already sent. It updates after each morning's email. To turn it on: *Settings → Pages → Source: GitHub Actions*. More in [`job-board/`](job-board/README.md).

## Which jobs count

Titles with things like **growth, product (manager, analyst, owner, marketing), marketing, operations / BizOps / RevOps, strategy, founder's associate, chief of staff, GTM, business development, partnerships, e-commerce**. Each one has to be **in Berlin or Munich** (or remote within Germany) and **published since the last email**.

Engineering, design, internships and working-student roles are filtered out.

All of this is editable in `config.json`.

## How it finds the companies

There are three sources, and they all feed the same daily check:

1. **The crawl (automatic).** Once a week the radar pulls every `jobs.ashbyhq.com/...` page from [Common Crawl](https://commoncrawl.org)'s free public index. That's thousands of companies, no API key needed. Each company gets checked once. The ones with jobs in Germany are saved in `boards.json` and checked **every day**. The rest are re-checked weekly in case they open a German role.
2. **Your Notion database (optional).** It needs a column called `Ashby ID` with values like `n8n` or `lovable`. Add a row and the company is checked from the next morning.
3. **`companies.txt` (optional).** The same idea as Notion, as a plain list. Paste an ID or a full Ashby link, one per line.

Jobs always come straight from **Ashby's official public job API**. No page scraping.

## Setup (≈10 min, free)

1. **Make a private GitHub repo** and upload these files. Keep `.github/workflows/daily.yml`.
2. **Add your secrets** in *Settings → Secrets and variables → Actions*:

   | Secret | What to put in |
   |---|---|
   | `SMTP_USER` | your Gmail address |
   | `SMTP_PASS` | a Gmail [app password](https://myaccount.google.com/apppasswords) (needs 2-step verification) |
   | `EMAIL_TO` | where the email should go (comma-separate several) |
   | `NOTION_TOKEN` | *optional*: your Notion connection's secret (API type, read-only) |
   | `NOTION_DATABASE_ID` | *optional*: the link to your Notion database |
   | `SERPER_API_KEY` | *optional*: a [Serper](https://serper.dev) key, for *Find new companies* (see Good to know) |

   If you use Notion, also open the database → **••• → Connections** and add your connection, so it can read the list.
3. **Kick it off** from *Actions → Ashby job radar → Run workflow*.

That's it. It runs every morning at around 07:40 Berlin time.

> The first run takes longer (10–30 min) because it's meeting thousands of companies for the first time. It also catches up on this week's roles, so expect a bigger first email.

## Tweaking it

Everything lives in `config.json`:

| Want to… | Change |
|---|---|
| add or remove role keywords | `title_include` / `title_exclude` |
| add cities to the email (e.g. Hamburg) | `focus_cities` (empty list = all of Germany) |
| drop remote-in-Germany roles | `include_remote_germany: false` |
| include "Remote – Europe" roles | `include_remote_europe: true` |
| get the full 7-day list every day instead of only new ones | `digest_mode: "week"` (range set by `lookback_days`) |
| only check ticked companies in Notion | add a checkbox column and set `notion.active_property` |
| turn off the weekly crawl | `harvest.enabled: false` |

## Good to know

- **Companies it can't see:** some companies show their Ashby jobs on their own site (`careers.company.com`) instead of `jobs.ashbyhq.com`. The crawl can't find those. [`embedded-boards/`](embedded-boards/README.md) finds many of them with a monthly manual step; add any others to Notion by hand.
- **Finding new companies with Google:** *Actions → Find new companies → Run workflow* searches Google (through Serper) with `discovery_queries` from `config.json` and adds the live companies it finds to `companies.txt`. It only runs when you click, using up to 20 Serper credits each time.
- **The German flag is a best guess:** it reads the job text. "German is a plus" won't trigger it.
- **Broken company IDs:** IDs from your own list that don't work are listed at the bottom of the email, so you can fix or delete them.
- **Where things are stored:** `seen.json` remembers what you've already been sent, and `boards.json` is the crawled company list. The workflow saves both back to the repo automatically.

## Run it on your laptop

```bash
python3 ashby_radar.py --no-email    # print today's list, don't send anything
python3 ashby_radar.py --dry-run     # same, and save nothing (only output/)
python3 ashby_radar.py --discover-only   # only look for new companies (needs SERPER_API_KEY)
python3 job-board/build.py --out site    # build the job board page into site/
```

Only needs Python 3.9+. No packages to install.
