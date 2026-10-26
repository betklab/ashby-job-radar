-- Embedded Ashby boards in Common Crawl's columnar URL index (Athena, region us-east-1).
-- Setup and steps: README.md in this folder.
--
-- The embed opens a job as ?ashby_jid=<job id> on the company's own page, and its filters add
-- ashby_department_id, ashby_location_id and ashby_employment_type. url_query is stored without
-- the leading "?".

-- Load a newly published crawl (only once per crawl). Check the newest IDs at
-- https://index.commoncrawl.org/collinfo.json
ALTER TABLE ccindex.ccindex ADD IF NOT EXISTS
  PARTITION (crawl = 'CC-MAIN-2026-43', subset = 'warc')
  LOCATION 's3://commoncrawl/cc-index/table/cc-main/warc/crawl=CC-MAIN-2026-43/subset=warc/';

-- Optional: how many sites? Reads about 4 GB per crawl (about $0.02).
SELECT COUNT(*) AS captures, COUNT(DISTINCT url_host_name) AS sites
FROM ccindex.ccindex
WHERE crawl IN ('CC-MAIN-2026-43')
  AND subset = 'warc'
  AND url_host_registered_domain <> 'ashbyhq.com'
  AND regexp_like(url_query, '(^|&)ashby_(jid|department_id|location_id|employment_type)=[^&]+');

-- One row per site with where its newest capture is archived. Reads at most about 50 GB per
-- crawl (about $0.25). List one or more crawls; download the result as CSV into results/.
WITH candidates AS (
  SELECT
    crawl,
    url_host_registered_domain AS domain,
    url_host_name              AS host,
    url,
    fetch_time,
    warc_filename,
    warc_record_offset,
    warc_record_length,
    COUNT(*) OVER (PARTITION BY url_host_name) AS captures,
    ROW_NUMBER() OVER (PARTITION BY url_host_name ORDER BY fetch_time DESC, url) AS newest
  FROM ccindex.ccindex
  WHERE crawl IN ('CC-MAIN-2026-43')
    AND subset = 'warc'
    AND url_host_registered_domain <> 'ashbyhq.com'
    AND regexp_like(url_query, '(^|&)ashby_(jid|department_id|location_id|employment_type)=[^&]+')
)
SELECT crawl, domain, host, captures, url, fetch_time, warc_filename, warc_record_offset, warc_record_length
FROM candidates
WHERE newest = 1
ORDER BY captures DESC, host;
