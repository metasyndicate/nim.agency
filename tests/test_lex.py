"""The lex/ vocabulary the agent generator composes from. These would have
caught both 2026 defects: lex/ deleted from the working tree, and every
bio template cut at its first comma by a bare-comma split."""

import csv
import re
import unittest

from tests.support import REPO

from lib.data.generator import AgentGenerator, LexLoader

LEX = REPO / "lex"
# Files the generator reads (lib/data/generator.py mapping)
USED = ("bioentity", "surnames", "titles", "root", "system", "descriptor",
        "temperaments", "tones", "academic", "mottos", "constellation",
        "origins", "bio_templates")


class TestLexFiles(unittest.TestCase):

    def test_every_used_file_exists_and_has_values(self):
        for name in USED:
            path = LEX / f"{name}.csv"
            self.assertTrue(path.exists(), f"lex/{name}.csv missing")
            self.assertTrue(LexLoader.load_simple(name, cache=False), name)

    def test_rows_match_their_header_width(self):
        # RFC 4180: a value containing a comma must be quoted, so every
        # non-blank row has exactly as many fields as the header
        for name in USED:
            with open(LEX / f"{name}.csv", encoding="utf-8", newline="") as f:
                rows = [r for r in csv.reader(f) if r]
            width = len(rows[0])
            bad = [i + 1 for i, r in enumerate(rows) if len(r) != width]
            self.assertEqual(bad, [], f"lex/{name}.csv rows with stray commas")

    def test_commas_inside_values_survive(self):
        mottos = LexLoader.load_simple("mottos", cache=False)
        self.assertIn("Measure twice, deploy once.", mottos)
        for template in LexLoader.load_simple("bio_templates", cache=False):
            self.assertTrue(template.endswith("."), template[-40:])
            self.assertIn("{name}", template)


class TestGeneratedProfile(unittest.TestCase):

    def test_bio_is_complete(self):
        LexLoader._cache.clear()
        for _ in range(8):
            bio = AgentGenerator().generate()["profile"]["bio"]
            self.assertTrue(bio.endswith("."), bio)
            self.assertIsNone(re.search(r"\{\w+\}", bio), bio)   # all filled


if __name__ == "__main__":
    unittest.main()
