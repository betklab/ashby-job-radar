"""Provider isolation, one-artifact layout, bounded fallback and preview state contracts."""
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import build
import collect
import combine
from prepare import fingerprint, write_json
import prepare


class Pipeline(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.now = datetime.now(timezone.utc)
        self.cfg = {"board_sources": ["ashby", "workable"], "title_include": ["product"], "title_exclude": [],
                    "cities": [{"label": "Berlin", "aliases": ["berlin"]}], "country_terms": ["germany"],
                    "focus_cities": ["Berlin"], "german_required_phrases": []}
        self.inputs = self.root/"inputs"
        write_json(self.inputs/"config.json", self.cfg)
        write_json(self.inputs/"source.json", {"commit": "same", "run": "1"})
        write_json(self.inputs/"boards.json", {"keys": ["sample"]})
        write_json(self.inputs/"seen.json", {"1": "2026-10-01"})
        self.provenance = {"commit": "same", "run": "1"}
        self.collections = self.root/"collections"

    def job(self, source, identifier=1):
        return {"id": (source+":" if source != "ashby" else "")+str(identifier), "source": source,
                "title": "Product Manager", "location": "Berlin", "publishedAt": "2026-10-01T00:00:00Z",
                "jobUrl": f"https://example.org/{source}/{identifier}", "descriptionPlain": "Build tools."}

    def collection(self, provider, jobs=None, **changes):
        jobs = [self.job(provider)] if jobs is None else jobs
        key = "sample" if provider == "ashby" else provider+":sample"
        report = {"provider": provider, "complete": True, "generated_at": self.now.isoformat(),
                  "boards_requested": 1, "boards_ok": 1, "boards_failed": 0, "boards_not_found": 0,
                  "roles": len(jobs), "scan_complete": provider == "workable"}
        report.update(changes)
        data = {"version": 1, "provider": provider, "config_hash": fingerprint(self.cfg),
                "provenance": self.provenance, "report": report, "boards": {key: jobs}}
        write_json(self.collections/provider/"collection.json", data)
        return data

    def previous(self, hours=1):
        role = build.open_roles({"workable:sample": [self.job("workable")]}, self.cfg)[0]
        return {"configHash": fingerprint(self.cfg),
                "sourceStatus": {"workable": {"state": "stale", "updatedAt": (self.now-timedelta(hours=hours)).isoformat()}},
                "jobs": [build.card(role)], "companies": {"workable:sample": {"name": "Old Company"}}}

    def merge(self, previous=None):
        return combine.combine(self.collections, self.cfg, self.provenance, previous or {}, self.now, timedelta(hours=48))

    def test_snapshot_includes_notion_once_and_preserves_seen(self):
        with patch.object(build, "ROOT", self.root), \
             patch.object(build.radar, "load_config", return_value=self.cfg), \
             patch.object(build.radar, "load_json", side_effect=lambda path, default: {"1": "2026-10-01"} if path.name == "seen.json" else {"registry": {}}), \
             patch.object(build.radar, "load_slugs", return_value=["file"]), \
             patch.object(build.radar, "load_slugs_notion", return_value=["private-notion"]) as notion, \
             patch.object(prepare.subprocess, "check_output", return_value="fixed-commit\n"):
            self.assertEqual(prepare.main(["--out", str(self.root/"snapshot")]), 0)
        data = json.loads((self.root/"snapshot/boards.json").read_text())
        self.assertIn("private-notion", data["keys"])
        self.assertEqual(json.loads((self.root/"snapshot/seen.json").read_text()), {"1": "2026-10-01"})
        notion.assert_called_once()

    def test_notion_failure_warns_and_keeps_public_inputs(self):
        with patch.object(build, "ROOT", self.root), \
             patch.object(build.radar, "load_config", return_value=self.cfg), \
             patch.object(build.radar, "load_json", return_value={}), \
             patch.object(build.radar, "load_slugs", return_value=["Public"]), \
             patch.object(build.radar, "load_slugs_notion", side_effect=OSError("expired token")), \
             patch.object(prepare.subprocess, "check_output", return_value="fixed-commit\n"):
            self.assertEqual(prepare.main(["--out", str(self.root/"snapshot")]), 0)
        snapshot = self.root/"snapshot"
        self.assertEqual(json.loads((snapshot/"boards.json").read_text())["keys"], ["public"])
        self.assertIn("Notion could not be read", json.loads((snapshot/"source.json").read_text())["warnings"][0])

    def test_writer_retains_owner_footer_and_newsletter_signup(self):
        roles = build.open_roles({"sample": [self.job("ashby")]}, self.cfg)
        with patch.dict("os.environ", {"GITHUB_REPOSITORY": "owner/project", "BUTTONDOWN_USERNAME": "example-news"}):
            build.write_site(self.root/"page", roles, {}, set(), self.now)
        page = (self.root/"page/index.html").read_text()
        self.assertIn("https://github.com/owner/project", page)
        self.assertIn("example-news", page)
        self.assertNotIn("{{", page)

    def test_independent_collections_combine(self):
        self.collection("ashby")
        self.collection("workable")
        roles, retained, reports, statuses = self.merge()
        self.assertEqual(len(roles), 2)
        self.assertEqual(retained, [])
        self.assertEqual(set(reports), {"ashby", "workable"})
        self.assertTrue(all(s["state"] == "fresh" for s in statuses.values()))

    def test_single_artifact_flat_layout_and_missing_provider(self):
        self.collection("ashby")
        (self.collections/"ashby/collection.json").rename(self.collections/"collection.json")
        roles, _, reports, statuses = self.merge()
        self.assertEqual(len(roles), 1)
        self.assertEqual(list(reports), ["ashby"])
        self.assertEqual(statuses["workable"]["state"], "unavailable")

    def test_missing_provider_retains_original_timestamp(self):
        self.collection("ashby")
        previous = self.previous(hours=30)
        roles, retained, _, statuses = self.merge(previous)
        self.assertEqual(len(roles)+len(retained), 2)
        self.assertEqual(statuses["workable"]["updatedAt"], previous["sourceStatus"]["workable"]["updatedAt"])
        self.assertEqual(statuses["workable"]["state"], "stale")

    def test_expired_future_or_changed_preferences_drop_fallback(self):
        self.collection("ashby")
        for hours in (49, -1):
            self.assertEqual(self.merge(self.previous(hours))[1], [])
        previous = self.previous()
        previous["configHash"] = "other"
        self.assertEqual(self.merge(previous)[1], [])

    def test_empty_success_clears_previous_roles(self):
        self.collection("ashby")
        self.collection("workable", [])
        _, retained, _, statuses = self.merge(self.previous())
        self.assertEqual(retained, [])
        self.assertEqual(statuses["workable"]["state"], "fresh")

    def test_partial_scan_and_failure_budget_rejected(self):
        self.collection("ashby")
        self.collection("workable", scan_complete=False)
        self.assertEqual(list(self.merge()[2]), ["ashby"])
        self.collection("workable")
        self.collection("ashby", boards_requested=19, boards_failed=1)
        self.assertEqual(list(self.merge()[2]), ["workable"])
        self.collection("ashby", boards_requested=20, boards_failed=1)
        self.assertEqual(self.merge()[3]["ashby"]["state"], "partial")

    def test_duplicates_foreign_jobs_provenance_and_old_collections_rejected(self):
        self.collection("ashby")
        data = self.collection("workable")
        write_json(self.collections/"duplicate/collection.json", data)
        self.assertEqual(list(self.merge()[2]), ["ashby"])
        (self.collections/"duplicate/collection.json").unlink()
        self.collection("workable", [self.job("greenhouse")])
        self.assertEqual(list(self.merge()[2]), ["ashby"])
        data = self.collection("workable")
        data["provenance"] = {"commit": "wrong"}
        write_json(self.collections/"workable/collection.json", data)
        self.assertEqual(list(self.merge()[2]), ["ashby"])
        self.collection("workable", generated_at=(self.now-timedelta(hours=3)).isoformat())
        self.assertEqual(list(self.merge()[2]), ["ashby"])

    def args(self):
        return ["--inputs", str(self.inputs), "--collections", str(self.collections), "--out", str(self.root/"output")]

    def test_all_fail_withholds_site(self):
        self.assertEqual(combine.main(self.args()), 1)
        self.assertFalse((self.root/"output/site").exists())
        self.assertTrue((self.root/"output/run.json").exists())

    def test_seen_and_stale_remain_in_catalog_only_fresh_unseen_in_preview(self):
        self.collection("ashby", [self.job("ashby", 1), self.job("ashby", 2)])
        write_json(self.root/"previous.json", self.previous())
        before = (self.inputs/"seen.json").read_bytes()
        self.assertEqual(combine.main(self.args()+["--previous", str(self.root/"previous.json")]), 0)
        data = json.loads(next((self.root/"output/site").glob("jobs-*.json")).read_text())
        preview = json.loads((self.root/"output/digest/matches.json").read_text())
        self.assertEqual(len(data["jobs"]), 3)
        self.assertEqual([j["id"] for j in preview], ["2"])
        self.assertTrue(data["partial"])
        self.assertEqual(data["companies"]["workable:sample"]["name"], "Old Company")
        self.assertEqual((self.inputs/"seen.json").read_bytes(), before)
        self.assertIn("Sources not refreshed: workable", (self.root/"output/digest/digest.md").read_text())

    def test_unreadable_previous_can_publish_available_fresh_provider(self):
        self.collection("ashby")
        with patch.object(combine, "load_previous", side_effect=OSError("offline")):
            self.assertEqual(combine.main(self.args()+["--previous-url", "https://example.org/"]), 0)

    def test_workable_collector_failure_has_report_without_collection(self):
        with patch.object(build.radar, "fetch_workable", side_effect=OSError("offline")):
            self.assertEqual(collect.main(["--inputs", str(self.inputs), "--source", "workable", "--out", str(self.root/"out")]), 1)
        self.assertTrue((self.root/"out/run.json").exists())
        self.assertFalse((self.root/"out/collection.json").exists())

    def test_collector_keeps_empty_complete_scan(self):
        with patch.object(build.radar, "fetch_workable", return_value={}):
            self.assertEqual(collect.main(["--inputs", str(self.inputs), "--source", "workable", "--out", str(self.root/"out")]), 0)
        self.assertTrue((self.root/"out/collection.json").exists())

    def test_company_failure_budget_in_collector(self):
        write_json(self.inputs/"boards.json", {"keys": [f"company{i}" for i in range(20)]})
        def fetch(key, cfg):
            return (key, None, "HTTP 503") if key == "company0" else (key, [], None)
        with patch.object(build.radar, "fetch_board", side_effect=fetch):
            self.assertEqual(collect.main(["--inputs", str(self.inputs), "--source", "ashby", "--out", str(self.root/"out")]), 0)
        data = json.loads((self.root/"out/collection.json").read_text())
        self.assertEqual(data["report"]["boards_failed"], 1)


if __name__ == "__main__":
    unittest.main()
