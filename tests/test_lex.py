"""The lex/ vocabulary the agent generator composes from. These would have
caught both 2026 defects: lex/ deleted from the working tree, and every
bio template cut at its first comma by a bare-comma split."""

import csv
import random
import re
import shutil
import tempfile
import unittest
from pathlib import Path

from tests.support import REPO

from lib.data import lex
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


class TestLexicon(unittest.TestCase):
    """lib/data/lex.py: the files against lex/csv-metadata.json (CSVW), and
    the taxonomy.sh port."""

    def setUp(self):
        self.lex = lex.Lexicon(LEX)

    def test_every_file_conforms_and_is_described(self):
        self.assertEqual(self.lex.check(), [])
        self.assertTrue(set(USED) <= set(self.lex.groups()))

    def test_first_rows_are_data_once_headers_exist(self):
        # group.csv and haiku.csv had no header: their first row was eaten
        self.assertIn("Metasyndicate", self.lex.table("group").values())
        self.assertIn("Nature is simple.", self.lex.table("haiku").values())

    def test_columns_are_typed_and_lists_split(self):
        conscript = next(r for r in self.lex.table("rank").rows()
                         if r["title"] == "Conscript")
        self.assertEqual(conscript["classifiers"], ["Field", "Platform", "System", "Data"])
        nebula = self.lex.table("star").rows()[0]
        self.assertEqual(nebula["equilibrium"], 0)
        with self.assertRaisesRegex(lex.LexError, "no column"):
            self.lex.table("star").values("colour")

    def test_seeded_draws_repeat(self):
        draw = lambda: [self.lex.salad(3, random.Random(9)),
                        self.lex.phrase(["descriptor", "group"], random.Random(9)),
                        self.lex.name(random.Random(9))]
        self.assertEqual(draw(), draw())

    def test_salad_uses_distinct_word_tables(self):
        words = self.lex.groups(kind="word")
        with self.assertRaises(lex.LexError):
            self.lex.salad(len(words) + 1, random.Random(1))
        with self.assertRaises(lex.LexError):
            self.lex.salad(0, random.Random(1))
        self.assertTrue(self.lex.salad(len(words), random.Random(1)))

    def test_name_has_an_honorific_or_letters_never_both(self):
        titles = set(self.lex.table("titles").values())
        letters = set(self.lex.table("postnominals").values())
        rng = random.Random(5)
        for _ in range(200):
            name = self.lex.name(rng)
            has_title = name.split(" ")[0] in titles
            has_letters = ", " in name and name.rsplit(", ", 1)[1] in letters
            self.assertFalse(has_title and has_letters, name)
        self.assertIn(self.lex.name(rng, title=True).split(" ")[0], titles)
        self.assertNotIn(self.lex.name(rng, title=False).split(" ")[0], titles)

    def test_check_reports_drift(self):
        tmp = Path(tempfile.mkdtemp(prefix="nim-lex-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        shutil.copy(LEX / lex.METADATA, tmp)
        for path in LEX.glob("*.csv"):
            shutil.copy(path, tmp)
        with open(tmp / "tones.csv", "a", encoding="utf-8") as f:
            f.write("Formal\n")
        (tmp / "mottos.csv").write_text("Mottos\nOne.\n", encoding="utf-8")
        (tmp / "extra.csv").write_text("X\ny\n", encoding="utf-8")
        found = "\n".join(lex.Lexicon(tmp).check())
        self.assertIn("tones.csv:12: duplicate of line 2", found)
        self.assertIn("mottos.csv: header", found)
        self.assertIn("extra.csv: not described", found)


class TestGeneratedProfile(unittest.TestCase):

    def test_bio_is_complete(self):
        LexLoader._cache.clear()
        for _ in range(8):
            bio = AgentGenerator().generate()["profile"]["bio"]
            self.assertTrue(bio.endswith("."), bio)
            self.assertIsNone(re.search(r"\{\w+\}", bio), bio)   # all filled


if __name__ == "__main__":
    unittest.main()
