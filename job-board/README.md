# Job board

One web page with **every open role that fits `config.json`**, from the job sites in `board_sources`. Unlike the email, it keeps roles you were already sent and older ones too, so nothing you missed is lost. Search covers titles, companies and full job descriptions; filter by city, work style, company, date and salary. With more than one job site, a *Listed on* filter appears too.

## When it updates

*Publish job board* runs on its own:

- after every *Ashby job radar* run (each morning, after your email) and every *Find new companies* run
- when you change `config.json`, `companies.txt`, `embedded-boards/boards.txt` or anything in this folder
- whenever you click *Actions → Publish job board → Run workflow*

It never sends email or changes `seen.json`. Each provider is collected in its own parallel step. A provider failure uses its previous matching results for at most 48 hours, with their original collection time shown. Healthy providers still update. If every provider fails, the published page stays as it was.

## Turn it on

*Settings → Pages → Source: GitHub Actions*, then run it once from Actions. The page appears at `https://<your-username>.github.io/<repo-name>/` and is public. Pages needs a public repo, or a paid GitHub plan for a private one. Until it's on, the workflow just reminds you and stops.

## Company names, logos and stars

- Names, logos, websites and intros come from each company's job board page. They're fetched once and reused for 30 days, kept by GitHub Actions (not in the repo).
- Companies in [`featured.txt`](featured.txt) get a ★ and a *Featured companies* filter. It starts with your original `companies.txt` list.

## Files

- `build.py`: fetches every board, keeps the matching roles, writes the page into `site/`
- `../sources.py`: reads each job site's jobs in the same shape
- `index.html`, `styles.css`, `app.js`, `favicon.svg`: the page itself; no framework, nothing to install

On your laptop:

```bash
python3 job-board/build.py --out site
python3 -m http.server --directory site    # then open http://localhost:8000
```

## Independent collection and catch-up preview

Each provider runs in parallel using one snapshot of your settings and company lists, including Notion. If Notion cannot be read, the run reports it and continues with the public lists.
If a provider fails, healthy providers still update; its old results stay for at most 48 hours when the filters haven't changed, with the original collection time shown. Without compatible older results it is labelled unavailable. If all fail, the published page stays up.
The final Actions artifact includes a catch-up preview grouped by company (HTML, Markdown, CSV and JSON): fresh matching jobs absent from `seen.json`, without a date cutoff or result cap. Saved history has finite retention, so some older roles may have been sent before.
This preview sends nothing and never changes sent history. Daily email and the weekly newsletter keep their own workflows; company enrichment keeps the existing Actions cache.
Run the offline pipeline checks with `python -B -m unittest discover -s job-board -p 'test_*.py'`.
