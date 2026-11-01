"""Description rendering regressions; no network or dependencies."""
import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location("board", Path(__file__).with_name("build.py"))
board = importlib.util.module_from_spec(spec)
spec.loader.exec_module(board)


class Descriptions(unittest.TestCase):
    def test_plain_spacer_lines_and_paragraphs(self):
        self.assertEqual(board.normalize_text("  Intro\r\n\xa0\r\n \t\r\n\r\nNext\n\n  • One\n\n  • Two  "),
                         "Intro\n\nNext\n\n  • One\n  • Two")

    def test_indented_html_list_with_paragraph_wrappers(self):
        html = "<p>About us</p><ul>\n  <li><p>One <b>point</b></p></li>\n  <li>Two</li>\n</ul><p>Apply now</p>"
        self.assertEqual(board.plain_text(html), "About us\n\n• One point\n• Two\n\nApply now")

    def test_empty_bullets_and_hidden_content(self):
        self.assertEqual(board.plain_text("<style>hidden</style><ul><li> &nbsp; </li><li>Visible</li></ul><script>alert(1)</script>"), "• Visible")

    def test_br_entities_and_literal_text(self):
        self.assertEqual(board.plain_text("<h2>R&amp;D</h2><p>One<br>Two</p>"), "R&D\n\nOne\nTwo")
        self.assertEqual(board.normalize_text("Use <tools> & skills"), "Use <tools> & skills")

    def test_public_card_normalizes_plain_before_html_fallback(self):
        role = {"id": "id", "title": "Product manager", "_slug": "test", "_loc": "Berlin",
                "_style": "Remote", "_comp": "", "_pub": None, "jobUrl": "https://example.org/job",
                "descriptionPlain": "Intro\n\xa0\n\n\nDone", "descriptionHtml": "<p>Fallback</p>"}
        self.assertEqual(board.card(role)["description"], "Intro\n\nDone")
        role["descriptionPlain"] = " \xa0 "
        self.assertEqual(board.card(role)["description"], "Fallback")

    def test_missing_description(self):
        self.assertEqual(board.plain_text(None), "")


if __name__ == "__main__":
    unittest.main()
