"""Profiles model (plan.101526.profiles.md C0): the nine reference tables
resolve and hold their invariants, and documents / substrates / findings
live in the instance root, filed per target tag, never in the checkout."""

import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests.support import REPO, DatastoreCase

from lib.data import refs
from lib.data.crud import DataStore
from lib.data.schema import get_schema_manager
from lib.install.root import RootError

REFERENCE = ["substrate_kind", "stratum", "source_role", "doc_kind", "doc_form",
             "requirement_level", "finding_kind", "substrate_status", "render_target"]
SHA = "0" * 64


def document(tag="pilot", path="AGENTS.md", **kw) -> dict:
    return {"tag": tag, "path": path, "doc_kind_slug": "agents_md",
            "doc_form_slug": None, "source_role_slug": "candidate",
            "git_state": "tracked", "sha256": SHA, "bytes": 10, "lines": 1,
            "frontmatter": None, "refs": [],
            "dated": {"modified": "2026-10-15T00:00:00+00:00", "source": "git"}, **kw}


class TestReferenceTables(DatastoreCase):

    def rows(self, entity) -> dict:
        return {r["slug"]: r for r in self.ds.load_reference(entity)}

    def test_every_reference_resolves(self):
        report = refs.check(self.ds, get_schema_manager(), REFERENCE)
        self.assertGreater(report.checked, 0)
        self.assertEqual([str(f) for f in report.findings], [])

    def test_strata_are_strictly_ordered(self):
        ranks = [r["rank"] for r in self.ds.load_reference("stratum")]
        self.assertEqual(ranks, sorted(set(ranks)))

    def test_requirement_opposition_is_symmetric(self):
        levels = self.rows("requirement_level")
        for slug, level in levels.items():
            for other in level["opposes"]:
                self.assertIn(slug, levels[other]["opposes"], f"{slug} / {other}")
        self.assertEqual(levels["may"]["opposes"], [])

    def test_doc_kinds_match_in_a_total_order_ending_with_markdown(self):
        kinds = self.ds.load_reference("doc_kind")
        orders = [k["sort_order"] for k in kinds]
        self.assertEqual(len(orders), len(set(orders)))
        self.assertEqual(max(kinds, key=lambda k: k["sort_order"])["patterns"], ["**/*.md"])

    def test_hubs_are_read_by_a_harness(self):
        for slug, kind in self.rows("doc_kind").items():
            if kind["hub"]:
                self.assertTrue(kind["harness_slugs"], slug)

    def test_every_render_target_names_a_hub_or_contracted_kind(self):
        kinds = self.rows("doc_kind")
        for slug, target in self.rows("render_target").items():
            kind = kinds[target["doc_kind_slug"]]
            self.assertTrue(set(target["harness_slugs"]) <= set(kind["harness_slugs"])
                            or not kind["harness_slugs"], slug)

    def test_finding_kinds_are_problem_types(self):
        for slug, kind in self.rows("finding_kind").items():
            self.assertTrue(kind["problem_type"].startswith("https://nim.agency/problems/"), slug)


class TestInstanceStorage(DatastoreCase):

    def test_records_are_filed_per_tag_in_the_instance_root(self):
        doc = self.ds.create("document", document(tag="filed"))
        self.ds.create("finding", {"tag": "filed", "kind_slug": "dead_ref", "severity": "error",
                                   "title": "x", "subjects": [{"entity": "document", "id": doc["id"]}],
                                   "status": "open"})
        on_disk = self.tmp / "instance" / "profiles" / "filed" / "documents" / f"{doc['id']}.json"
        self.assertTrue(on_disk.is_file())
        mine = [d["id"] for d in self.ds.query("document").data if d["tag"] == "filed"]
        self.assertEqual(mine, [doc["id"]])
        self.assertEqual([f["tag"] for f in self.ds.query("finding").data], ["filed"])
        self.assertFalse((REPO / "data" / "agency" / "data" / "profiles").exists())

    def test_tags_are_kept_apart(self):
        self.ds.create("document", document(tag="alpha"))
        self.ds.create("document", document(tag="beta", path="CLAUDE.md"))
        tags = sorted(d["tag"] for d in self.ds.query("document").data
                      if d["tag"] in ("alpha", "beta"))
        self.assertEqual(tags, ["alpha", "beta"])
        self.assertTrue((self.tmp / "instance" / "profiles" / "alpha" / "documents").is_dir())

    def test_substrate_sources_resolve_to_documents(self):
        doc = self.ds.create("document", document())
        self.ds.create("substrate", {"tag": "pilot", "kind_slug": "directive",
                                     "stratum_slug": "project", "status_slug": "proposed",
                                     "subject": "interpreter",
                                     "body": {"requirement_level_slug": "must_not",
                                              "text": "Never run bare python3"},
                                     "sources": [{"document_id": doc["id"], "line_start": 1,
                                                  "line_end": 1}]})
        report = refs.check(self.ds, get_schema_manager(), ["substrate"])
        self.assertEqual([str(f) for f in report.findings], [])


class TestUncoupled(unittest.TestCase):
    """No coupled root: reads find nothing, writes refuse."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="nim-uncoupled-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        patch = mock.patch("lib.install.root.coupled_root", return_value=None)
        patch.start()
        self.addCleanup(patch.stop)
        shutil.copytree(REPO / "data/agency/data/reference", self.tmp / "reference")
        self.ds = DataStore(base_path=str(self.tmp))

    def test_reads_find_nothing(self):
        self.assertEqual(self.ds.query("document").data, [])

    def test_writes_refuse_and_name_the_remedy(self):
        with self.assertRaisesRegex(RootError, "install couple"):
            self.ds.create("document", document(), validate=False)


if __name__ == "__main__":
    unittest.main()
