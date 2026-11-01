# Job board

One web page with **every open role that fits `config.json`**, from the same companies the email checks, plus the Greenhouse companies in `boards.json` (set by `board_sources`). Unlike the email, it keeps roles you were already sent and older ones too, so nothing you missed is lost. Search covers titles, companies and full job descriptions; filter by city, work style, company, date and salary. When roles come from both Ashby and Greenhouse, a *Listed on* filter appears too.

## When it updates

*Publish job board* runs on its own:

- after every *Ashby job radar* run (each morning, after your email) and every *Find new companies* run
- when you change `config.json`, `companies.txt`, `embedded-boards/boards.txt` or anything in this folder
- whenever you click *Actions → Publish job board → Run workflow*

It never sends email or changes `seen.json`. If a few companies fail to load, the page is published without them for the day; if many fail (an Ashby or Greenhouse outage), yesterday's page stays up and the run turns red.

## Turn it on

*Settings → Pages → Source: GitHub Actions*, then run it once from Actions. The page appears at `https://<your-username>.github.io/<repo-name>/` and is public. Pages needs a public repo, or a paid GitHub plan for a private one. Until it's on, the workflow just reminds you and stops.

## Company names, logos and stars

- Names, logos, websites and intros come from each company's Ashby page. They're fetched once and reused for 30 days, kept by GitHub Actions (not in the repo).
- For Greenhouse companies, the name and intro come from Greenhouse's board API, and the logo only when it's uploaded to the company's Greenhouse page (a company that sends you to its own careers site gets its initial instead of that site's banner). Greenhouse has no website field.
- Companies in [`featured.txt`](featured.txt) get a ★ and a *Featured companies* filter. It starts with your original `companies.txt` list.

## Files

- `build.py`: fetches every board, keeps the matching roles, writes the page into `site/`
- `../sources.py`: reads Greenhouse boards in the same shape as Ashby's
- `index.html`, `styles.css`, `app.js`, `favicon.svg`: the page itself; no framework, nothing to install

On your laptop:

```bash
python3 job-board/build.py --out site
python3 -m http.server --directory site    # then open http://localhost:8000
```

## Included Greenhouse companies

`boards.json` already includes 4,639 verified Greenhouse board names. You do not
need API keys or an import step. The normal build fetches their current
jobs, and daily email still checks Ashby unless you change `sources`. Greenhouse
roles on the board also reach the weekly newsletter, which reads the board JSON.

The list combines Google discovery and three Common Crawl indexes validated
on 1 November 2026. It is additive: no existing Ashby entries, crawl metadata,
company choices or delivery history are replaced. Known mirror boards were
omitted to avoid reposts (Doctolib, think-cell, Artefact LinkedIn, HelloFresh
alumni and Sony Music DE); this can omit a few roles unique to the mirrors.
The validation and runtime evidence is available in
[the fork's discovery reports](https://github.com/p1va/ashby-job-radar/tree/06daa56/discovery/results).

Greenhouse rarely states remote/hybrid explicitly and has no standard work-style
field here. “Not stated” is expected; we infer a style only from an explicit
location label. We keep first-published dates when supplied, without treating
a refresh date as a new posting.
