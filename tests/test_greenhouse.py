"""Greenhouse boards next to Ashby ones: names, fetching, the email's and the board's choice of
providers, and company profiles. Offline: every request is faked.

    python3 -m unittest discover -s tests
"""
import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
import urllib.error
from datetime import date
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "job-board"))
import ashby_radar as radar  # noqa: E402
import build  # noqa: E402
import sources  # noqa: E402

CFG = radar.load_config(ROOT / "config.json")


def gh_job(id, title="Growth Manager", location="Berlin, Germany", content=None):
    job = {"id": id, "title": title, "location": {"name": location}, "company_name": "Acme GmbH",
           "absolute_url": f"https://job-boards.greenhouse.io/acme/jobs/{id}",
           "first_published": "2026-10-30T09:00:00-04:00", "departments": [{"name": "Growth"}]}
    if content is not None:
        job["content"] = content
    return job


def ashby_job(id, title="Growth Manager", location="Berlin"):
    return {"id": id, "title": title, "location": location, "isListed": True,
            "publishedAt": "2026-10-30T09:00:00Z", "jobUrl": f"https://jobs.ashbyhq.com/a/{id}"}


def http_error(code):
    return urllib.error.HTTPError("https://example.test", code, "error", {}, None)


class BoardNames(unittest.TestCase):
    def test_ashby_lines_read_as_before(self):
        self.assertEqual(sources.board_key("Lovable"), "Lovable")
        self.assertEqual(sources.board_key("https://jobs.ashbyhq.com/n8n/abc?x=1"), "n8n")
        self.assertEqual(sources.board_key("jobs.ashbyhq.com/Acme%20Inc"), "Acme Inc")
        # an unknown address is kept as typed, so the email still lists it as a broken ID
        self.assertEqual(sources.board_key("https://jobs.lever.co/acme"), "https://jobs.lever.co/acme")
        self.assertEqual(sources.provider("https://jobs.lever.co/acme"), "ashby")

    def test_greenhouse_addresses(self):
        for line in ("greenhouse:Acme", "https://job-boards.greenhouse.io/acme",
                     "job-boards.eu.greenhouse.io/acme/jobs/123", "https://boards.greenhouse.io/acme",
                     "https://boards.greenhouse.io/embed/job_board?for=acme&b=x"):
            self.assertEqual(sources.board_key(line), "greenhouse:acme", line)
        self.assertEqual(sources.provider("greenhouse:acme"), "greenhouse")
        self.assertEqual(sources.provider("acme"), "ashby")

    def test_invalid_greenhouse_names(self):
        for line in ("greenhouse:", "greenhouse:a/b", "https://job-boards.greenhouse.io/", "greenhouse:a.b"):
            with self.assertRaises(ValueError, msg=line):
                sources.board_key(line)

    def test_companies_file_skips_and_reports_invalid_lines(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "companies.txt"
            path.write_text("n8n\n# a comment\ngreenhouse:\nhttps://job-boards.greenhouse.io/Acme\n"
                            "N8N\ngreenhouse:acme  # same board\n", encoding="utf-8")
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                slugs = radar.load_slugs(path)
        self.assertEqual(slugs, ["n8n", "greenhouse:acme"])
        self.assertIn("companies.txt line 3 skipped", err.getvalue())

    def test_company_name_without_provider(self):
        self.assertEqual(radar.company_name("greenhouse:acme-labs"), "Acme Labs")
        self.assertEqual(radar.company_name("acme_labs"), "Acme Labs")


class Providers(unittest.TestCase):
    def test_email_is_ashby_unless_configured(self):
        self.assertEqual(sources.enabled({}), ["ashby"])
        self.assertEqual(sources.enabled({}, "board_sources"), ["ashby"])
        self.assertEqual(sources.enabled({"sources": ["ashby", "greenhouse"]}, "board_sources"),
                         ["ashby", "greenhouse"])
        self.assertEqual(sources.enabled(CFG), ["ashby"])
        self.assertEqual(sources.enabled(CFG, "board_sources"), ["ashby", "greenhouse"])

    def test_bad_provider_settings_stop_the_run(self):
        for value in ([], ["lever"], "ashby"):
            with self.assertRaises(ValueError):
                sources.enabled({"sources": value})

    def test_select_keeps_order_and_drops_repeats(self):
        keys = ["b", "greenhouse:x", "A", "a", "greenhouse:X"]
        self.assertEqual(sources.select(keys, ["ashby"]), ["b", "A"])
        self.assertEqual(sources.select(keys, ["greenhouse"]), ["greenhouse:x"])


class FetchGreenhouse(unittest.TestCase):
    def fake(self, light, detailed):
        calls = []

        def request(url, parse=json.loads, **kwargs):
            calls.append(url)
            jobs = detailed if url.endswith("?content=true") else light
            return parse(json.dumps({"jobs": jobs, "meta": {"total": len(jobs)}}).encode())
        return request, calls

    def test_jobs_get_ashby_fields_and_descriptions_only_when_relevant(self):
        content = "&lt;p&gt;Fluent German required&lt;/p&gt;"
        request, calls = self.fake([gh_job(1), gh_job(2, "Engineer")],
                                   [gh_job(1, content=content), gh_job(2, "Engineer", content="")])
        with mock.patch.object(radar, "request", request):
            slug, jobs, err = radar.fetch_board("greenhouse:acme", CFG)
        self.assertIsNone(err)
        self.assertEqual(len(calls), 2)
        self.assertTrue(calls[1].endswith("/v1/boards/acme/jobs?content=true"))
        job = jobs[0]
        self.assertEqual(job["id"], "greenhouse:1")
        self.assertEqual(job["source"], "greenhouse")
        self.assertEqual(job["companyName"], "Acme GmbH")
        self.assertEqual(job["descriptionHtml"], "<p>Fluent German required</p>")
        self.assertEqual(job["department"], "Growth")
        self.assertEqual(radar.match_location(job, CFG), "Berlin")
        self.assertEqual(radar.german_required(job, CFG), "German required")

    def test_no_description_download_without_a_candidate(self):
        request, calls = self.fake([gh_job(1, "Engineer"), gh_job(2, location="Paris, France")], [])
        with mock.patch.object(radar, "request", request):
            _, jobs, err = radar.fetch_board("greenhouse:acme", CFG)
        self.assertEqual((len(jobs), err, len(calls)), (2, None, 1))

    def test_a_matching_job_without_its_description_fails_the_board(self):
        # the email then neither sends it nor marks it seen; the board retries next run
        for content in (None, 123):
            detailed = [gh_job(1), gh_job(2, "Engineer", content="")]
            if content is not None:
                detailed[0]["content"] = content
            request, _ = self.fake([gh_job(1), gh_job(2, "Engineer")], detailed)
            with mock.patch.object(radar, "request", request):
                self.assertEqual(radar.fetch_board("greenhouse:acme", CFG)[1:], (None, "ValueError"))
        # a job that can't match may come without one
        request, _ = self.fake([gh_job(1), gh_job(2, "Engineer")], [gh_job(1, content="<p>x</p>"), gh_job(2, "Engineer")])
        with mock.patch.object(radar, "request", request):
            self.assertIsNone(radar.fetch_board("greenhouse:acme", CFG)[2])

    def test_incomplete_list_and_errors_become_failures(self):
        def short(url, parse=json.loads, **kwargs):
            return parse(json.dumps({"jobs": [gh_job(1)], "meta": {"total": 2}}).encode())
        with mock.patch.object(radar, "request", short):
            self.assertEqual(radar.fetch_board("greenhouse:acme", CFG)[1:], (None, "ValueError"))

        def gone(url, **kwargs):
            raise http_error(404)
        with mock.patch.object(radar, "request", gone):
            self.assertEqual(radar.fetch_board("greenhouse:acme", CFG)[1:], (None, "HTTP 404"))


class EmailStaysAshby(unittest.TestCase):
    """With the default settings the daily email never asks Ashby for a Greenhouse board."""

    def test_greenhouse_registry_and_list_entries_are_left_to_the_board(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            (tmp / "companies.txt").write_text("listed\ngreenhouse:mine\n", encoding="utf-8")
            (tmp / "boards.json").write_text(json.dumps({
                "_meta": {}, "german": {"de": True}, "greenhouse:acme": {"de": True},
                "greenhouse:new": {"de": None}}), encoding="utf-8")
            (tmp / "embedded.txt").write_text("", encoding="utf-8")
            asked = []

            def fetch(slug, cfg=None):
                asked.append(slug)
                return slug, [], None
            with mock.patch.object(radar, "fetch_board", fetch), \
                    mock.patch.object(radar, "update_registry", lambda *a: 0), \
                    mock.patch.object(radar, "discover_slugs", lambda cfg: set()), \
                    mock.patch.dict(os.environ, {"NOTION_TOKEN": ""}), \
                    contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                radar.main(["--config", str(ROOT / "config.json"), "--companies", str(tmp / "companies.txt"),
                            "--registry", str(tmp / "boards.json"), "--embedded", str(tmp / "embedded.txt"),
                            "--seen", str(tmp / "seen.json"), "--out", str(tmp / "out"), "--no-email"])
            saved = json.loads((tmp / "boards.json").read_text())
        self.assertEqual(sorted(asked), ["german", "listed"])
        self.assertEqual(saved["greenhouse:acme"], {"de": True})       # kept for the board
        self.assertEqual(saved["greenhouse:new"], {"de": None})


class JobBoard(unittest.TestCase):
    def run_build(self, fetch, cfg_extra=None):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            cfg = {**CFG, **(cfg_extra or {})}
            (tmp / "config.json").write_text(json.dumps(cfg), encoding="utf-8")
            (tmp / "companies.txt").write_text("", encoding="utf-8")
            (tmp / "featured.txt").write_text("greenhouse:acme\n", encoding="utf-8")
            (tmp / "embedded.txt").write_text("", encoding="utf-8")
            reg = {"_meta": {}, **{f"a{i}": {"de": False} for i in range(40)},
                   **{f"greenhouse:g{i}": {"de": False} for i in range(40)}, "greenhouse:acme": {"de": True}}
            (tmp / "boards.json").write_text(json.dumps(reg), encoding="utf-8")
            asked = []

            def tracked(slug, cfg=None):
                asked.append(slug)
                return fetch(slug)
            with mock.patch.object(radar, "fetch_board", tracked), \
                    mock.patch.dict(os.environ, {"NOTION_TOKEN": "", "GITHUB_STEP_SUMMARY": ""}), \
                    contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                code = build.main(["--config", str(tmp / "config.json"), "--companies", str(tmp / "companies.txt"),
                                   "--registry", str(tmp / "boards.json"), "--embedded", str(tmp / "embedded.txt"),
                                   "--featured", str(tmp / "featured.txt"), "--out", str(tmp / "site")])
            data = None
            for path in (tmp / "site").glob("jobs-*.json"):
                data = json.loads(path.read_text())
            return code, data, asked

    @staticmethod
    def boards(slug):
        if slug == "greenhouse:acme":
            return slug, [sources.greenhouse_job(gh_job(7, content="&lt;p&gt;Hi&lt;/p&gt;"))], None
        return slug, [ashby_job("a-" + slug)] if slug == "a0" else [], None

    def test_greenhouse_roles_on_the_board(self):
        code, data, asked = self.run_build(self.boards)
        self.assertEqual(code, 0)
        self.assertEqual(len(asked), 81)
        by_source = {job["source"]: job for job in data["jobs"]}
        self.assertEqual(sorted(by_source), ["ashby", "greenhouse"])
        gh = by_source["greenhouse"]
        self.assertEqual((gh["id"], gh["company"], gh["companyName"]), ("greenhouse:7", "greenhouse:acme", "Acme GmbH"))
        self.assertEqual(gh["description"], "Hi")
        self.assertTrue(data["companies"]["greenhouse:acme"]["featured"])

    def test_board_sources_ashby_only(self):
        code, data, asked = self.run_build(self.boards, {"board_sources": ["ashby"]})
        self.assertEqual(code, 0)
        self.assertFalse(any(slug.startswith("greenhouse:") for slug in asked))
        self.assertEqual({job["source"] for job in data["jobs"]}, {"ashby"})

    def test_failures_are_allowed_per_provider(self):
        def greenhouse_down(slug):
            if slug.startswith("greenhouse:"):
                return slug, None, "HTTP 503"
            return self.boards(slug)
        code, data, _ = self.run_build(greenhouse_down)
        self.assertEqual((code, data), (1, None))       # yesterday's page stays up

        def two_hiccups(slug):        # 2 of 41 Greenhouse boards: within 5%
            if slug in ("greenhouse:g1", "greenhouse:g2"):
                return slug, None, "URLError"
            return self.boards(slug)
        self.assertEqual(self.run_build(two_hiccups)[0], 0)

        def three_hiccups(slug):      # 3 of 41: over 5%, though under 5% of all 81 boards
            if slug in ("greenhouse:g1", "greenhouse:g2", "greenhouse:g3"):
                return slug, None, "URLError"
            return self.boards(slug)
        self.assertEqual(self.run_build(three_hiccups)[0], 1)


class GreenhouseProfiles(unittest.TestCase):
    LOGO = "https://s4-recruiting.cdn.greenhouse.io/external_greenhouse_job_boards/logos/400/1/original/a.png"

    def test_only_greenhouse_hosted_logos(self):
        page = '<meta content="{}" property="og:image"><meta property="og:image" content="https://x/y.png">'
        self.assertEqual(sources.board_logo(page.format(self.LOGO.replace("/a.png", "/a.png?x=1&amp;y=2"))),
                         self.LOGO + "?x=1&y=2")
        for url in ("http://s4-recruiting.cdn.greenhouse.io/external_greenhouse_job_boards/logos/a.png",
                    "https://recruiting.cdn.greenhouse.io.evil.test/external_greenhouse_job_boards/logos/a.png",
                    "https://s4-recruiting.cdn.greenhouse.io/external_greenhouse_job_boards/covers/a.png", ""):
            self.assertEqual(sources.board_logo(page.format(url)), "", url)
        self.assertEqual(sources.board_logo(f'<meta name="twitter:image" content="{self.LOGO}">'), "")

    def test_eu_fallback_redirects_and_errors(self):
        def pages(answers):
            asked = []

            def request(url, parse=json.loads, opener=None, **kwargs):
                asked.append(url)
                self.assertIs(opener.__self__, sources.NO_REDIRECT)      # redirects aren't followed
                answer = answers[len(asked) - 1]
                if isinstance(answer, int):
                    raise http_error(answer)
                return parse(answer.encode())
            return request, asked

        request, asked = pages([404, f'<meta property="og:image" content="{self.LOGO}">'])
        self.assertEqual(sources.greenhouse_logo("acme", request), self.LOGO)
        self.assertEqual(asked, ["https://job-boards.greenhouse.io/acme", "https://job-boards.eu.greenhouse.io/acme"])
        request, asked = pages([302])
        self.assertEqual((sources.greenhouse_logo("acme", request), len(asked)), ("", 1))
        request, _ = pages([503])
        with self.assertRaises(urllib.error.HTTPError):
            sources.greenhouse_logo("acme", request)

    def test_profile_in_her_cache_format(self):
        def request(url, parse=json.loads, opener=None, **kwargs):
            if url.startswith(sources.GREENHOUSE_API):
                return parse(b'{"name": " Acme ", "content": "<p>We build <b>things</b>.</p>"}')
            return parse(f'<meta property="og:image" content="{self.LOGO}">'.encode())
        with mock.patch.object(radar, "request", request):
            profile = build.fetch_profile("greenhouse:acme")
        self.assertEqual(profile, {"name": "Acme", "website": "", "logo": self.LOGO, "intro": "We build things."})

        def nameless(url, parse=json.loads, **kwargs):
            return parse(b'{"name": "", "content": ""}')
        with mock.patch.object(radar, "request", nameless), self.assertRaises(ValueError):
            build.fetch_profile("greenhouse:acme")

    def test_a_slow_down_stops_only_that_provider(self):
        def fetch(slug):
            if slug == "greenhouse:a":
                raise http_error(429)
            return {"name": slug, "website": "", "logo": "", "intro": ""}
        profiles = {}
        with mock.patch.object(build, "fetch_profile", fetch), mock.patch.object(build.time, "sleep"), \
                contextlib.redirect_stderr(io.StringIO()):
            fetched, failed = build.update_profiles(["greenhouse:a", "greenhouse:b", "ashby1", "ashby2"],
                                                    profiles, date(2026, 11, 1))
        self.assertEqual((fetched, failed), (2, 1))
        self.assertEqual(sorted(profiles), ["ashby1", "ashby2"])


if __name__ == "__main__":
    unittest.main()
