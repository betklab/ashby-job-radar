"""Opt-in remote scope and role-background matching using real missed-role shapes."""
import unittest
from datetime import datetime, timezone
import importlib.util
from pathlib import Path

import ashby_radar as radar


CONFIG = {"title_include": ["product", "operations"], "title_exclude": ["engineer", "internship"],
          "cities": [{"label": "Berlin", "aliases": ["berlin"]}], "focus_cities": ["Berlin"],
          "country_terms": ["germany"], "include_remote_europe": True, "include_remote_global": True,
          "ignore_background_qualifiers": True}


class MatchingTests(unittest.TestCase):
    def test_remote_europe_and_worldwide_pass_city_focus_only_when_enabled(self):
        for location, expected in [("Europe", "Remote Europe"), ("EMEA", "Remote Europe"),
                                   ("Remote, Global", "Remote Worldwide"), ("Worldwide", "Remote Worldwide")]:
            with self.subTest(location=location):
                job = {"location": location, "workplaceType": "Remote"}
                self.assertEqual(radar.match_location(job, CONFIG), expected)
                self.assertTrue(radar.in_focus(expected, "Remote", CONFIG))
                strict = {**CONFIG, "include_remote_europe": False, "include_remote_global": False}
                self.assertIsNone(radar.match_location(job, strict))
                self.assertFalse(radar.in_focus(expected, "Remote", strict))

    def test_structured_eu_evidence_recovers_posthog_without_company_exceptions(self):
        title = "Product Manager, Observability (ex-founder or ex-product engineer)"
        job = {"title": title, "location": "Remote", "workplaceType": "Remote",
               "address": {"postalAddress": {"addressCountry": "US | EU"}}}
        self.assertEqual(radar.match_location(job, CONFIG), "Remote Europe")
        self.assertIsNone(radar.title_rejection(title, CONFIG))
        self.assertEqual(radar.title_rejection(title, {**CONFIG, "ignore_background_qualifiers": False}), "title_excluded")

    def test_remote_only_us_only_and_onsite_europe_are_not_inferred_eligible(self):
        for job in [{"location": "Remote", "workplaceType": "Remote"},
                    {"location": "Remote (US only)", "workplaceType": "Remote", "descriptionPlain": "A global team working anywhere."},
                    {"location": "Europe", "workplaceType": "OnSite"}]:
            with self.subTest(job=job):
                self.assertIsNone(radar.match_location(job, CONFIG))

    def test_anywhere_does_not_override_a_country_scope(self):
        for location in ["Anywhere in the United States", "anywhere in the US- remote", "Anywhere in California",
                         "Remote - Anywhere", "Anywhere | Singapore", "Fully Remote - can be based anywhere in the U.S."]:
            with self.subTest(location=location):
                self.assertIsNone(radar.match_location({"location": location, "workplaceType": "Remote"}, CONFIG))
        self.assertEqual(radar.match_location({"location": "Remote anywhere in the world", "workplaceType": "Remote"}, CONFIG),
                         "Remote Worldwide")

    def test_europe_wins_over_a_worldwide_tag_and_secondary_global_does_not_widen_us(self):
        self.assertEqual(radar.match_location({"location": "Europe - Anywhere", "workplaceType": "Remote",
            "secondaryLocations": [{"location": "Global"}]}, CONFIG), "Remote Europe")
        job = {"title": "Account Director (US, Fully-Remote, EST Hours)", "location": "US", "workplaceType": "Remote",
               "address": {"postalAddress": {"addressCountry": "United States"}},
               "secondaryLocations": [{"location": "New York"}, {"location": "Global"}]}
        self.assertIsNone(radar.match_location(job, CONFIG))

    def test_global_is_a_region_label_not_a_place_name_or_narrow_region(self):
        for location in ["Bonifacio Global City", "Remote, Global (APAC)", "Global - US only"]:
            with self.subTest(location=location):
                self.assertIsNone(radar.match_location({"location": location, "workplaceType": "Remote"}, CONFIG))
        for location in ["Global (Remote)", "Remote - Global Anywhere", "Remote, Global", "Worldwide", "global - remote work"]:
            with self.subTest(location=location):
                self.assertEqual(radar.match_location({"location": location, "workplaceType": "Remote"}, CONFIG), "Remote Worldwide")

    def test_role_exclusions_stay_effective_and_background_does_not_create_a_match(self):
        for title in ["Product Engineer (ex-founder)", "Product Manager (Internship)",
                      "Product Manager (Engineer)", "Product Manager (former engineer) - Internship"]:
            with self.subTest(title=title):
                self.assertEqual(radar.title_rejection(title, CONFIG), "title_excluded")
        self.assertEqual(radar.title_rejection("Finance Manager (ex-product engineer)", CONFIG), "title_not_included")
        self.assertIsNone(radar.title_rejection("Product Lead (former engineer)", CONFIG))

    def test_flat_address_and_both_consumers_use_the_same_preferences(self):
        spec = importlib.util.spec_from_file_location("board", Path(__file__).parents[1] / "job-board/build.py")
        board = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(board)
        role = {"id": "job", "title": "Product Manager (ex-product engineer)", "location": "Remote",
                "workplaceType": "Remote", "address": {"addressCountry": "US | EU"},
                "jobUrl": "https://example.org/job", "publishedAt": "2026-11-01T10:00:00Z"}
        cfg = {**CONFIG, "forget_after_days": 60, "german_required_phrases": []}
        now = datetime(2026, 11, 1, 12, tzinfo=timezone.utc)
        self.assertEqual(len(radar.curate({"sample": [role]}, cfg, {}, now)), 1)
        cards = board.open_roles({"sample": [role]}, cfg)
        self.assertEqual(board.card(cards[0])["locations"], ["Remote", "US | EU"])
        self.assertIn("permitted countries", board.card(cards[0])["locationNote"])
        strict = {**cfg, "ignore_background_qualifiers": False}
        self.assertEqual(radar.curate({"sample": [role]}, strict, {}, now), [])
        self.assertEqual(board.open_roles({"sample": [role]}, strict), [])

    def test_explicit_germany_and_focus_cities_keep_their_existing_labels(self):
        for location in ["Germany", "Berlin"]:
            self.assertEqual(radar.match_location({"location": location, "workplaceType": "Remote"}, CONFIG), location)


if __name__ == "__main__":
    unittest.main()
