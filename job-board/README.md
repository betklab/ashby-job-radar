# Job board

One web page with **every open role that fits `config.json`**, from the same companies the email checks. Unlike the email, it keeps roles you were already sent and older ones too, so nothing you missed is lost. Search covers titles, companies and full job descriptions; filter by city, work style, company, date and salary.

## When it updates

*Publish job board* runs on its own:

- after every *Ashby job radar* run (each morning, after your email) and every *Find new companies* run
- when you change `config.json`, `companies.txt`, `embedded-boards/boards.txt` or anything in this folder
- whenever you click *Actions → Publish job board → Run workflow*

It never sends email or changes `seen.json`. If a few companies fail to load, the page is published without them for the day; if many fail (an Ashby outage), yesterday's page stays up and the run turns red.

## Turn it on

*Settings → Pages → Source: GitHub Actions*, then run it once from Actions. The page appears at `https://<your-username>.github.io/<repo-name>/` and is public. Pages needs a public repo, or a paid GitHub plan for a private one. Until it's on, the workflow just reminds you and stops.

## Company names, logos and stars

- Names, logos, websites and intros come from each company's Ashby page. They're fetched once and reused for 30 days, kept by GitHub Actions (not in the repo).
- Companies in [`featured.txt`](featured.txt) get a ★ and a *Featured companies* filter. It starts with your original `companies.txt` list.

## Files

- `build.py`: fetches every board, keeps the matching roles, writes the page into `site/`
- `index.html`, `styles.css`, `app.js`, `favicon.svg`: the page itself; no framework, nothing to install

On your laptop:

```bash
python3 job-board/build.py --out site
python3 -m http.server --directory site    # then open http://localhost:8000
```
