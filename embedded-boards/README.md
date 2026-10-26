# Embedded Ashby boards

Some companies show their Ashby jobs on their own careers page instead of linking to
`jobs.ashbyhq.com`. The radar's weekly Common Crawl harvest only looks for
`jobs.ashbyhq.com/...` links, so it misses many of them. This folder finds them with a monthly
manual step and lists them in `boards.txt`, which the radar reads on every run.

**How it works.** When a visitor opens a job on an embedded board, the page address gets
`?ashby_jid=<job id>` (see Ashby's
[embedding guide](https://docs.ashbyhq.com/embedding-ashby-job-boards-in-an-external-careers-page)).
Common Crawl's [columnar URL index](https://commoncrawl.org/blog/index-to-warc-files-and-urls-in-columnar-format)
can be searched by that part of the address with Amazon Athena. The steps:

1. **Find the sites.** Athena lists every site in a crawl where such an address was captured, and where the captured page is stored in Common Crawl's archive.
2. **Find the slug.** `update_boards.py` downloads just that archived page and reads the board slug from the embed code, for example `<script src="https://jobs.ashbyhq.com/acme/embed">`.
3. **Confirm and add.** It checks each new slug with Ashby's
   [posting API](https://developers.ashbyhq.com/docs/public-job-posting-api) and adds live boards to `boards.txt`.

| File | What it is |
| --- | --- |
| [`boards.txt`](boards.txt) | The companies found so far, one per line like `companies.txt` |
| [`query.sql`](query.sql) | The Athena queries |
| [`update_boards.py`](update_boards.py) | Reads the Athena CSV and adds new companies to `boards.txt` (standard library only) |
| `results/` | One Athena CSV per crawl, plus `report.csv` from the last run |

## One-time setup

Following Common Crawl's
[Athena guide](https://commoncrawl.org/blog/index-to-warc-files-and-urls-in-columnar-format):

1. **Open Athena.** Create an AWS account and open the
   [Athena console](https://docs.aws.amazon.com/athena/latest/ug/getting-started.html) in
   **us-east-1**, where Common Crawl's data lives. On first use, Athena asks for an S3 bucket
   for query results.
2. **Create the database:** `CREATE DATABASE ccindex;`
3. **Create the table.** In that database, run Common Crawl's
   [table definition](https://github.com/commoncrawl/cc-index-table/blob/main/src/sql/athena/cc-index-create-table-flat.sql).
   It's a table over Common Crawl's public Parquet files, so nothing is copied into your account.
4. **Load the crawls.** Either `MSCK REPAIR TABLE ccindex.ccindex;` (all crawls, slow), or the
   `ALTER TABLE … ADD PARTITION` line at the top of `query.sql` (one crawl, instant).

## Each new crawl (about every 4–5 weeks)

1. **Find the new crawl ID.** The newest crawl is first in
   [collinfo.json](https://index.commoncrawl.org/collinfo.json), for example `CC-MAIN-2026-43`.
   The runs table below lists the crawls already done.
2. **Load it into Athena** with the `ALTER TABLE … ADD PARTITION` line from `query.sql`.
3. **Run the export query.** Put the crawl ID in `crawl IN (...)` and run the last query in
   `query.sql`. Several crawls can go in one run.
4. **Save the CSV.** Download the result and save it as `results/<crawl id>.csv`.
5. **Update `boards.txt`** from the repository root:
   ```sh
   python embedded-boards/update_boards.py embedded-boards/results/CC-MAIN-2026-47.csv
   ```
   Add `--dry-run` to see the report without changing `boards.txt`. A run takes about 3 seconds
   per site, because it waits between requests to stay polite.
6. **Commit** `boards.txt`, the CSV and `results/report.csv`. Add a row to the runs table below.

New boards go in as never checked. The next radar run checks each one once. After that,
boards with jobs in Germany are checked daily and the rest weekly.

## Cost

Athena bills by data scanned ([pricing](https://aws.amazon.com/athena/pricing/), $5 per TB).
The index is Parquet, so a query only reads the columns it uses.

| Query | Reads per crawl | Cost per crawl |
| --- | --- | --- |
| Count (optional) | about 4 GB | about $0.02 |
| Export | at most about 50 GB | about $0.25 |

These figures were estimated from the October 2026 index files. Athena shows the actual "Data
scanned" for each query. Downloading archived pages from `data.commoncrawl.org` is free and needs
no AWS account.

## What the report says

`results/report.csv` has one row per slug found:

| Column | Meaning |
| --- | --- |
| `source` | How the slug was found: `embed` (the embed script or settings), `link` (another `jobs.ashbyhq.com` link on the page) or `domain guess` (for example `acme.io` → `acme` or `acme-io`) |
| `status: new` | A live board that wasn't in `boards.txt`; added unless `--dry-run` |
| `status: known` | Already in `boards.txt` |
| `status: not found` | On the page, but Ashby no longer has that board. Usually a leftover tag after a company left Ashby or renamed its board. Example: leapsome.com still loads `jobs.ashbyhq.com/Leapsome/embed`, which Ashby answers with 400 |

## Limits

- **It only finds shared job links.** A site appears only if Common Crawl captured one of its
  job links with `ashby_jid`, usually because someone shared it. Each crawl sees a different
  slice: only 19 of the first 230 sites appeared in all three crawls.
- **Some pages hide the slug.** On some pages JavaScript inserts the embed, so the archived HTML
  doesn't contain it. The domain guess catches some of those.
- **A guessed slug may belong to another company.** That doesn't matter for the radar: it's still
  a live Ashby board, and the digest names companies after the board.

## Runs

| Date | Crawls | Sites | Live boards | Added to `boards.txt` |
| --- | --- | --- | --- | --- |
| 26 Oct 2026 | CC-MAIN-2026-43, -39, -34 | 230 | 203 | 203 (about 100 the crawl misses; 10 of those with jobs in Germany) |
