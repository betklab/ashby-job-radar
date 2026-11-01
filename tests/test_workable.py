"""Workable cursor, identity, catalog and opt-in contracts, without network."""
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import urllib.parse

import ashby_radar as radar
import sources

spec = importlib.util.spec_from_file_location("board", Path(__file__).parents[1] / "job-board/build.py")
board = importlib.util.module_from_spec(spec)
spec.loader.exec_module(board)
COMPANY = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"


def job(identifier="one", **changes):
    return {"id": identifier, "title": "Product Manager", "state": "published", "workplace": "hybrid",
            "created": "2026-10-30T10:00:00Z", "location": {"city": "Berlin", "countryName": "Germany"},
            "description": "<p>Build tools.</p>", "requirementsSection": "<p>Fluent German required.</p>",
            "benefitsSection": "<p>Flexible hours.</p>", "url": "https://jobs.workable.com/view/" + identifier,
            "company": {"id": COMPANY, "title": "Company", "website": "https://example.com/",
                        "image": "https://example.com/logo.png", "description": "<p>About us.</p>"}, **changes}


class Workable(unittest.TestCase):
    def test_default_email_stays_ashby_and_workable_is_catalog_only(self):
        self.assertEqual(sources.enabled({}), ["ashby"])
        self.assertEqual(sources.enabled({"board_sources": ["workable"]}, "board_sources"), ["workable"])
        with self.assertRaisesRegex(ValueError, "board_sources"):
            sources.enabled({"sources": ["workable"]})

    def test_uuid_and_per_board_guard(self):
        self.assertEqual(sources.board_key("workable:" + COMPANY), "workable:" + COMPANY)
        with self.assertRaises(ValueError):
            sources.board_key("workable:acme")
        with patch.object(radar, "download", side_effect=AssertionError("unexpected HTTP")):
            self.assertIsNone(radar.fetch_board("workable:" + COMPANY)[1])

    def test_cursor_keeps_native_city_ids_deduplicates_same_id_and_includes_sections(self):
        pages = [{"jobs": [job()], "totalSize": 2, "nextPageToken": "next&token"},
                 {"jobs": [job(), job("two", location={"city": "Munich", "countryName": "Germany"})], "totalSize": 2}]
        urls = []
        def request(url, parse):
            urls.append(url)
            return parse(json.dumps(pages.pop(0)).encode())
        with patch.object(sources.time, "sleep") as pause:
            jobs = sources.fetch_workable(request)["workable:" + COMPANY]
        self.assertEqual([j["id"] for j in jobs], ["workable:one", "workable:two"])
        self.assertEqual(urllib.parse.parse_qs(urllib.parse.urlsplit(urls[1]).query)["pageToken"], ["next&token"])
        self.assertIn("Flexible hours", jobs[0]["descriptionHtml"])
        self.assertIn("Fluent German", jobs[0]["descriptionHtml"])
        pause.assert_called_once_with(2)

    def test_partial_and_malformed_responses_fail(self):
        for data in [{"jobs": [], "totalSize": 3}, {"jobs": [None]}, {"error": "down"},
                     {"jobs": [], "nextPageToken": "next"}, {"jobs": [], "totalSize": "0"}]:
            with self.subTest(data=data), self.assertRaises(ValueError):
                sources.fetch_workable(lambda *a, **k: data, delay=0)

    def test_loop_and_page_limit_fail(self):
        data = {"jobs": [job()], "nextPageToken": "next"}
        with self.assertRaises(ValueError):
            sources.fetch_workable(lambda *a, **k: data, delay=0)
        with self.assertRaisesRegex(ValueError, "page limit"):
            sources.fetch_workable(lambda *a, **k: data, delay=0, max_pages=1)

    def test_valid_empty_scan(self):
        self.assertEqual(sources.fetch_workable(lambda *a, **k: {"jobs": [], "totalSize": 0}), {})

    def run_board(self, result):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        cfg = radar.load_config(Path(__file__).parents[1] / "config.json")
        (root/"config.json").write_text(json.dumps({**cfg, "board_sources": ["workable"]}))
        with patch.object(radar, "fetch_workable", side_effect=result), \
             patch.object(radar, "fetch_board", side_effect=AssertionError("unexpected per-board fetch")), \
             patch.object(radar, "load_slugs_notion", return_value=[]), \
             contextlib.redirect_stdout(io.StringIO()):
            code = board.main(["--config", str(root/"config.json"), "--out", str(root/"site")])
        return code, root

    def test_search_failure_withholds_site_and_does_not_write_history(self):
        code, root = self.run_board(OSError("offline"))
        self.assertEqual(code, 1)
        self.assertFalse((root/"site").exists())
        self.assertFalse((root/"seen.json").exists())

    def test_catalog_uses_search_company_metadata_and_full_description(self):
        key, posting = sources.workable_job(job())
        code, root = self.run_board(lambda: {key: [posting]})
        self.assertEqual(code, 0)
        data = json.loads(next((root/"site").glob("jobs-*.json")).read_text())
        self.assertEqual(data["companies"][key]["name"], "Company")
        self.assertEqual(data["companies"][key]["logoUrl"], "https://example.com/logo.png")
        self.assertEqual(data["jobs"][0]["source"], "workable")
        self.assertIn("Flexible hours", data["jobs"][0]["description"])
        self.assertFalse((root/"seen.json").exists())

    def test_empty_search_publishes_empty_catalog(self):
        code, root = self.run_board(lambda: {})
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(next((root/"site").glob("jobs-*.json")).read_text())["jobs"], [])


if __name__ == "__main__":
    unittest.main()
