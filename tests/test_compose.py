"""The composition kernel: forms from schemas, drafts edited through them,
and the generic CLI verbs. Field lists, choices, and rules all come from
the schemas - these tests pin the behaviour, not a field list."""

import argparse
import contextlib
import io
import unittest
from unittest import mock

from tests.support import DatastoreCase

from lib.cli import compose as cli
from lib.data import forms, refs
from lib.data.drafts import Draft, EditError, inbound
from lib.data.schema import SchemaManager

SM = SchemaManager().load_all()


class TestPointer(unittest.TestCase):

    def test_rfc6901_get_set_unset(self):
        doc = {"a/b": {"m~n": [1, 2]}}
        self.assertEqual(forms.pointer_get(doc, "/a~1b/m~0n/1"), 2)
        forms.pointer_set(doc, "/a~1b/m~0n/-", 3)          # JSON Patch append
        self.assertEqual(doc["a/b"]["m~n"], [1, 2, 3])
        forms.pointer_set(doc, "/x/y", True)               # creates parents
        self.assertEqual(doc["x"], {"y": True})
        self.assertTrue(forms.pointer_unset(doc, "/x/y"))
        self.assertFalse(forms.pointer_unset(doc, "/x/y"))
        with self.assertRaises(ValueError):
            forms.parse_pointer("no/leading/slash")


class TestForm(DatastoreCase):

    def setUp(self):
        self.root = forms.form("syndicate", self.ds, SM)

    def test_kinds_are_scored_from_constraints(self):
        kind = {f.pointer: f.kind for f in self.root.walk()}
        self.assertEqual(kind["/transport_slug"], "select")        # x-nim-ref
        self.assertEqual(kind["/endpoint/egress"], "select")       # enum
        self.assertEqual(kind["/endpoint/verify_tls"], "toggle")
        self.assertEqual(kind["/channels"], "list")
        self.assertEqual(kind["/channels/*/capabilities"], "multiselect")
        self.assertEqual(kind["/channels/*/limits/context_window"], "integer")
        self.assertEqual(kind["/identity/tags"], "tags")
        # external $ref chains resolve in their own document
        self.assertEqual(kind["/audit/created_at"], "text")

    def test_system_fields_are_read_only(self):
        for name in ("id", "status", "audit"):
            self.assertTrue(self.root.find((name,)).read_only, name)
        self.assertFalse(self.root.find(("endpoint",)).read_only)

    def test_nullable_reference(self):
        f = self.root.find(("endpoint", "substation_id"))
        self.assertTrue(f.nullable)
        self.assertEqual(f.ref["entity"], "substation")

    def test_capabilities_are_bounded_by_the_transport_ceiling(self):
        record = self.ds.query("syndicate",
                               lambda s: s["slug"] == "mux_nova").first()
        f = self.root.find(("channels", 0, "capabilities"))
        bounded = {o.value for o in forms.options(f, self.ds, record, self.root)}
        ceiling = set(self.ds.get_reference_by_slug(
            "transport", record["transport_slug"])["capability_ceiling"])
        self.assertEqual(bounded, ceiling)
        unbounded = {o.value for o in forms.options(f, self.ds)}
        self.assertIn("shell_exec", unbounded - bounded)

    def test_every_entity_has_a_form(self):
        for entity, config in self.ds.ENTITY_CONFIG.items():
            if config["type"] in ("reference", "collection"):
                root = forms.form(entity, self.ds, SM)
                self.assertTrue(root.children, entity)


class TestDraft(DatastoreCase):

    def draft(self):
        return Draft.load(self.ds, SM, "syndicate", "mux_nova")

    def test_refusals(self):
        draft = self.draft()
        with self.assertRaisesRegex(EditError, "capability_ceiling"):
            draft.add("/channels/0/capabilities", "shell_exec")
        with self.assertRaisesRegex(EditError, "read-only"):
            draft.set("/status/state", "online")
        with self.assertRaisesRegex(EditError, "choose from"):
            draft.set("/endpoint/egress", "everywhere")
        with self.assertRaisesRegex(EditError, "no field"):
            draft.set("/endpoint/colour", "teal")
        with self.assertRaisesRegex(EditError, "required"):
            draft.unset("/transport_slug")
        self.assertEqual(draft.diff(), [])

    def test_edit_diff_save_and_unset(self):
        draft = self.draft()
        draft.set("/channels/0/limits/max_concurrent", "2")
        draft.add("/identity/tags", "probe")
        draft.unset("/identity/vendor")
        changed = {p for p, _, _ in draft.diff()}
        self.assertIn("/channels/0/limits/max_concurrent", changed)
        self.assertEqual(draft.problems(), [])
        saved = draft.save()
        stored = self.ds.read("syndicate", saved["id"])
        self.assertEqual(stored["channels"][0]["limits"]["max_concurrent"], 2)
        self.assertIn("probe", stored["identity"]["tags"])
        self.assertNotIn("vendor", stored["identity"])      # replace, not merge
        self.assertEqual(stored["audit"]["created_at"],
                         draft.original["audit"]["created_at"])

    def test_new_record_lists_what_is_missing_then_saves(self):
        draft = Draft.new(self.ds, SM, "syndicate")
        missing = draft.problems()
        self.assertTrue(any("/transport_slug" in p for p in missing), missing)
        draft.set("/slug", "compose_probe")
        draft.set("/identity/label", "Compose probe")
        draft.set("/transport_slug", "ollama_chat")
        draft.set("/channels", '[{"slug": "m", "model": "m:1"}]')
        self.assertTrue(any("/channels/0/slug: shorter" in p
                            for p in draft.problems()))
        draft.set("/channels/0/slug", "main")
        draft.add("/channels/0/capabilities", "text_generation")
        self.assertEqual(draft.problems(), [])
        saved = draft.save()
        self.assertTrue(saved["id"])
        Draft.load(self.ds, SM, "syndicate", "compose_probe").delete(hard=True)
        self.assertIsNone(self.ds.read("syndicate", saved["id"]))

    def test_delete_is_refused_while_referenced(self):
        transport = Draft.load(self.ds, SM, "transport", "ollama_chat")
        self.assertTrue(inbound(self.ds, SM, "transport", transport.original))
        with self.assertRaisesRegex(EditError, "cannot be deleted"):
            transport.delete()

    def test_reference_rows_compose_with_immutable_keys(self):
        draft = Draft.new(self.ds, SM, "role_title")
        draft.set("/label", "Relay Warden")
        saved = draft.save()
        self.assertEqual(saved["slug"], "relay_warden")
        edit = Draft.load(self.ds, SM, "role_title", "relay_warden")
        edit.set("/description", "Keeps the relay honest")
        edit.save()
        row = self.ds.get_reference_by_slug("role_title", "relay_warden")
        self.assertEqual(row["id"], saved["id"])
        Draft.load(self.ds, SM, "role_title", "relay_warden").delete()
        self.assertIsNone(self.ds.get_reference_by_slug("role_title",
                                                        "relay_warden"))


class TestSubstationHostModel(unittest.TestCase):
    """Substations are composed without touching the operator's ~/.nim:
    the drafts below are never saved."""

    def setUp(self):
        from lib.data.crud import DataStore
        self.ds = DataStore()

    def test_username_is_required_for_ssh_only(self):
        draft = Draft.new(self.ds, SM, "substation")
        draft.set("/identity/name", "bonsai")
        draft.set("/identity/codename", "sub.bonsai")
        draft.set("/connection/hostname", "bonsai.example")
        self.assertIn("/connection/username: required", draft.problems())
        draft.set("/connection/kind", "https")
        self.assertNotIn("/connection/username: required", draft.problems())
        with self.assertRaisesRegex(EditError, "choose from"):
            draft.set("/connection/kind", "telnet")

    def test_observed_fields_are_not_composed(self):
        draft = Draft.new(self.ds, SM, "substation")
        for pointer in ("/status/state", "/deployments", "/id"):
            with self.assertRaisesRegex(EditError, "read-only"):
                draft.set(pointer, "x")


class TestGroupClassification(DatastoreCase):
    """O5: a group is classified by a crue_type row (org:classification),
    chosen from the stored table rather than typed."""

    def test_type_is_a_reference_to_crue_type(self):
        f = forms.form("group", self.ds, SM).find(("crue_type_slug",))
        self.assertEqual(f.kind, "select")
        offered = {o.value for o in forms.options(f, self.ds, {}, {})}
        stored = {r["slug"] for r in self.ds.load_reference("crue_type")}
        self.assertEqual(offered, stored)

    def test_new_group_saves_only_with_a_known_type(self):
        draft = Draft.new(self.ds, SM, "group")
        draft.set("/name", "Probe Crue")
        self.assertTrue(any(p.startswith("/crue_type_slug:")
                            for p in draft.problems()))
        with self.assertRaisesRegex(EditError, "choose from"):
            draft.set("/crue_type_slug", "Ground Crüe")       # a label, not a key
        draft.set("/crue_type_slug", "ground_crue")
        draft.set("/slug", "ground_crue_probe")
        self.assertEqual(draft.problems(), [])
        saved = draft.save()
        self.assertEqual(self.ds.read("group", saved["id"])["crue_type_slug"],
                         "ground_crue")
        draft = Draft.load(self.ds, SM, "group", saved["id"])
        with self.assertRaisesRegex(EditError, "crue_type"):
            Draft.load(self.ds, SM, "crue_type", "ground_crue").delete()
        draft.delete(hard=True)


class TestFormCoversTheData(DatastoreCase):
    """Every stored record can be rebuilt through a Draft and comes out
    identical, with no problems, and every value sits on a field the form
    knows. If not, schema and data have drifted (plan P4)."""

    ENTITIES = ("syndicate", "transport", "channel_capability",
                "harness_function", "task_preset", "role_title",
                "tool", "command_policy", "harness_profile")

    def known(self, root, pointer):
        tokens = forms.parse_pointer(pointer)
        for depth in range(len(tokens), 0, -1):
            f = root.find(tuple(tokens[:depth]))
            if f is not None:
                # a leaf inside a free-form or list value is covered by it
                return depth == len(tokens) or f.kind in (
                    "json", "tags", "multiselect", "group")
        return False

    def test_rebuild_every_record(self):
        from lib.data.drafts import _leaves
        for entity in self.ENTITIES:
            for record in refs.records(self.ds, entity):
                draft = Draft.new(self.ds, SM, entity)
                for f in draft.root.children:
                    if f.name in record and not f.read_only:
                        draft.set(f"/{f.name}", record[f.name])
                    elif f.name not in record:
                        draft.record.pop(f.name, None)
                label = f"{entity}[{record.get('slug') or record.get('id')}]"
                self.assertEqual(draft.problems(), [], label)
                composed = {k: v for k, v in record.items()
                            if not draft.root.find((k,)) or
                            not draft.root.find((k,)).read_only}
                self.assertEqual(draft.record, composed, label)
                unknown = [p for p in _leaves(record)
                           if not self.known(draft.root, p)]
                self.assertEqual(unknown, [], label)


class TestCli(DatastoreCase):

    def run_cli(self, func, **kwargs):
        defaults = dict(set=None, add=None, remove=None, unset=None,
                        dry_run=False, from_file=None, record=None,
                        pointer=None, hard=False)
        args = argparse.Namespace(**{**defaults, **kwargs})
        out = io.StringIO()
        with mock.patch.object(cli, "_store", return_value=(SM, self.ds)), \
                contextlib.redirect_stdout(out):
            code = func(args)
        return code, out.getvalue()

    def test_form_and_options(self):
        code, out = self.run_cli(cli.cmd_form, entity="syndicate")
        self.assertEqual(code, 0)
        self.assertIn("/channels/*/capabilities", out)
        code, out = self.run_cli(cli.cmd_options, entity="syndicate",
                                 pointer="/channels/0/capabilities",
                                 record="mux_nova")
        self.assertIn("tool_use", out)
        self.assertNotIn("shell_exec", out)

    def test_edit_dry_run_writes_nothing(self):
        code, out = self.run_cli(cli.cmd_edit, entity="syndicate",
                                 ref="mux_nova", dry_run=True,
                                 set=["/channels/0/limits/max_concurrent=3"])
        self.assertEqual(code, 0)
        self.assertIn("-> 3", out)
        stored = self.ds.query("syndicate",
                               lambda s: s["slug"] == "mux_nova").first()
        self.assertNotEqual(stored["channels"][0]["limits"]["max_concurrent"], 3)

    def test_refusal_exits_nonzero(self):
        code, out = self.run_cli(cli.cmd_edit, entity="syndicate",
                                 ref="mux_nova",
                                 add=["/channels/0/capabilities=shell_exec"])
        self.assertEqual(code, 1)
        self.assertIn("refused", out)


if __name__ == "__main__":
    unittest.main()
