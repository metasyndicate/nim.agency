"""Declared references (x-nim-ref): schema declarations, data resolution,
and the integrity check that turns prose "Reference to X" into a rule."""

import json
from pathlib import Path
import unittest

from tests.support import DatastoreCase, REPO

from lib.data import refs
from lib.data.crud import DataStore
from lib.data.schema import SchemaManager


SM = SchemaManager().load_all()
SPEC = SM.get_defs()["x-nim-ref"]


def top_level_fields(schema_name: str) -> set:
    """Field names an instance of the schema may carry at its root,
    including those inherited through allOf/$ref (e.g. the concept base)."""
    names = set()
    for path, sub in refs.walk(SM.get_schema(schema_name), SM):
        if len(path) == 1:
            names.add(path[0])
    return names


class TestDeclarations(unittest.TestCase):

    def test_every_annotation_is_well_formed_and_targets_a_real_key(self):
        ds = DataStore()
        seen = 0
        for name in SM.list_schemas():
            for decl in refs.declarations(name, SM):
                seen += 1
                where = f"{name}{decl.pointer}"
                self.assertIn(decl.entity, ds.ENTITY_CONFIG, where)
                target = refs.schema_name(ds, decl.entity)
                self.assertIn(decl.key, top_level_fields(target), where)
        self.assertGreater(seen, 30)

    def test_annotation_keys_match_the_metaschema_definition(self):
        allowed = set(SPEC["properties"])
        for name in SM.list_schemas():
            for _, sub in refs.walk(SM.get_schema(name), SM):
                spec = sub.get(refs.KEYWORD)
                if spec is not None:
                    self.assertTrue(set(spec) <= allowed, (name, spec))
                    for key in SPEC["required"]:
                        self.assertIn(key, spec, name)

    def test_syndicate_declares_its_references(self):
        found = {d.pointer: (d.entity, d.key)
                 for d in refs.declarations("syndicate", SM)}
        self.assertEqual(found["/transport_slug"], ("transport", "slug"))
        self.assertEqual(found["/endpoint/substation_id"], ("substation", "id"))
        self.assertEqual(found["/channels/*/capabilities/*"],
                         ("channel_capability", "slug"))

    def test_inherited_fields_are_walked_through_refs(self):
        # focus rows inherit id/slug/label from the metaschema concept
        self.assertTrue({"id", "slug", "label", "ops_domain_id"}
                        <= top_level_fields("focus"))


class TestValuesAt(unittest.TestCase):

    def test_patterns(self):
        doc = {"a": [{"b": 1}, {"b": None}, {}], "m": {"x": 2, "y": 3},
               "n": None}
        self.assertEqual(list(refs.values_at(doc, ("a", "*", "b"))),
                         [("/a/0/b", 1)])
        self.assertEqual(sorted(refs.values_at(doc, ("m", "~value"))),
                         [("/m/x", 2), ("/m/y", 3)])
        self.assertEqual(sorted(refs.values_at(doc, ("m", "~key"))),
                         [("/m/x", "x"), ("/m/y", "y")])
        self.assertEqual(list(refs.values_at(doc, ("n",))), [])
        self.assertEqual(list(refs.values_at(doc, ("missing", "*"))), [])


class TestCheck(DatastoreCase):

    OBJECTIVE_PATH = ["syndicate", "transport", "channel_capability",
                      "harness_function", "focus", "specialty", "class",
                      "tool", "instruction"]

    def test_objective_path_resolves(self):
        report = refs.check(self.ds, SM, self.OBJECTIVE_PATH)
        self.assertGreater(report.checked, 0)
        self.assertEqual([str(f) for f in report.dangling], [])

    def test_dangling_and_unbound_are_reported(self):
        record = json.loads(json.dumps(self.ds.query("syndicate").first()))
        record["id"] = "00000000-0000-4000-8000-000000000001"
        record["slug"] = "ref_probe"
        record["channels"][0]["capabilities"].append("telepathy")
        record["endpoint"]["substation_id"] = \
            "00000000-0000-4000-8000-0000000000ff"
        self.ds.create("syndicate", record, validate=False)
        try:
            report = refs.check(self.ds, SM, ["syndicate"])
            dangling = [f for f in report.dangling if f.record == "ref_probe"]
            unbound = [f for f in report.unbound if f.record == "ref_probe"]
            self.assertEqual([f.value for f in dangling], ["telepathy"])
            self.assertEqual(dangling[0].target, "channel_capability.slug")
            self.assertEqual(len(unbound), 1)
            self.assertIn("/endpoint/substation_id", unbound[0].pointer)
        finally:
            self.ds.delete("syndicate", record["id"], hard=True)


class TestInstructionBlockSelectors(unittest.TestCase):
    """Blocks select agents by concept slug. With integer ids, renumbering
    the focus table sent firewall doctrine to power agents."""

    @classmethod
    def setUpClass(cls):
        from lib.data.composer import AgentComposer
        cls.ds = DataStore()
        cls.composer = AgentComposer(cls.ds)
        cls.focus = {f["slug"]: f["id"] for f in cls.ds.load_reference("focus")}

    def focus_blocks(self, focus_slug):
        agent = {"id": "t", "identity": {"name": "T", "codename": "t"},
                 "classification": {"rank_id": 3,
                                    "focus_id": self.focus[focus_slug]},
                 "economy": {"xp": 0}}
        profile = self.composer.compose(agent)
        return sorted(Path(s).stem for s in profile.sources
                      if "/instruction/focus/" in str(s))

    def test_focus_doctrine_reaches_its_own_focus_only(self):
        for slug in ("database", "firewall", "kubernetes"):
            self.assertEqual(self.focus_blocks(slug), [slug])
        for slug in ("power", "cooling", "cabling", "loadbalancer"):
            self.assertEqual(self.focus_blocks(slug), [], slug)

    def test_block_selectors_resolve(self):
        report = refs.check(self.ds, SM, ["instruction_block"])
        self.assertGreater(report.checked, 0)
        self.assertEqual([str(f) for f in report.dangling], [])


if __name__ == "__main__":
    unittest.main()
