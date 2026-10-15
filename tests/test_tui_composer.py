"""The TUI Composer, driven headlessly through its input handlers (no
curses screen): the same Draft rules as the CLI, plus the multi-select
picker over a choice field's options."""

import curses
import unittest

from tests.support import DatastoreCase

from lib.data import forms
from lib.data.schema import SchemaManager
from tui.builder import AgentBuilder
from tui.views.composer import form_rows

SM = SchemaManager().load_all()
ENTER = 10


class TestFormRows(DatastoreCase):

    def test_rows_follow_the_record(self):
        record = self.ds.query("syndicate",
                               lambda s: s["slug"] == "mux_nova").first()
        rows = form_rows(forms.form("syndicate", self.ds, SM), record)
        by_pointer = {(r.pointer, r.kind): r for r in rows}
        self.assertIn(("/channels/0", "item"), by_pointer)
        self.assertIn(("/channels", "add"), by_pointer)
        caps = by_pointer[("/channels/0/capabilities", "value")]
        self.assertIn("tool_use", caps.value)
        self.assertTrue(by_pointer[("/status/state", "value")].locked)
        self.assertFalse(caps.locked)


class TestComposerScreens(DatastoreCase):

    def setUp(self):
        self.tui = AgentBuilder(datastore=self.ds)
        self.tui._enter_compose_mode()

    def press(self, *keys):
        for key in keys:
            self.tui._handle_input(key)

    def open_mux_nova(self):
        names = [n for _, n in self.tui.compose_entity_rows]
        self.tui.compose_entity_index = names.index("syndicate")
        self.press(ENTER)
        self.assertEqual(self.tui.mode, "compose_records")
        slugs = [r.get("slug") for r in self.tui.compose_records]
        self.tui.compose_record_index = slugs.index("mux_nova") + 1
        self.press(ENTER)
        self.assertEqual(self.tui.mode, "compose_form")

    def go_to(self, pointer, kind="value"):
        rows = self.tui._compose_rows()
        self.tui.compose_row_index = next(
            i for i, r in enumerate(rows)
            if r.pointer == pointer and r.kind == kind)

    def test_multiselect_picker_offers_only_the_ceiling(self):
        self.open_mux_nova()
        self.go_to("/channels/0/capabilities")
        self.press(ENTER)
        self.assertEqual(self.tui.mode, "compose_pick")
        offered = [o.value for o in self.tui.compose_pick["options"]]
        self.assertIn("vision", offered)
        self.assertNotIn("shell_exec", offered)
        self.tui.compose_pick["index"] = offered.index("vision")
        self.press(ord(" "), ENTER)
        self.assertEqual(self.tui.mode, "compose_form")
        self.assertIn("vision", self.tui.compose_draft.record["channels"][0]
                      ["capabilities"])
        self.press(ord("s"))
        self.assertIn("Saved", self.tui.message)
        stored = self.ds.query("syndicate",
                               lambda s: s["slug"] == "mux_nova").first()
        self.assertIn("vision", stored["channels"][0]["capabilities"])

    def test_read_only_rows_refuse(self):
        self.open_mux_nova()
        self.go_to("/status/state")
        self.press(ENTER)
        self.assertEqual(self.tui.mode, "compose_form")
        self.assertIn("read-only", self.tui.message)

    def test_text_edit_and_discard_guard(self):
        self.open_mux_nova()
        self.go_to("/channels/0/limits/max_concurrent")
        self.press(ENTER)
        self.assertTrue(self.tui.editing)
        self.tui.edit_buffer = ""
        self.press(ord("3"), ENTER)
        self.assertEqual(self.tui.compose_draft.record["channels"][0]
                         ["limits"]["max_concurrent"], 3)
        self.press(ord("q"))
        self.assertEqual(self.tui.mode, "compose_form")   # unsaved: warned
        self.assertIn("Unsaved", self.tui.message)
        self.press(ord("q"))
        self.assertEqual(self.tui.mode, "compose_records")

    def test_list_items_add_and_remove(self):
        self.open_mux_nova()
        before = len(self.tui.compose_draft.record["channels"])
        self.go_to("/channels", "add")
        self.press(ENTER)
        self.assertEqual(len(self.tui.compose_draft.record["channels"]),
                         before + 1)
        self.go_to(f"/channels/{before}", "item")
        self.press(ord("d"))
        self.assertEqual(len(self.tui.compose_draft.record["channels"]), before)

    def test_substation_screen_composes_new_hosts(self):
        self.tui.mode = "substations"
        self.tui.substations = []
        self.tui._handle_substations_input(ord("n"))
        self.assertEqual(self.tui.mode, "compose_form")
        self.assertEqual(self.tui.compose_entity, "substation")
        self.assertTrue(self.tui.compose_draft.is_new)


class TestMenu(unittest.TestCase):

    def test_composer_is_on_the_menu(self):
        tui = AgentBuilder()
        tui._handle_menu_input(ord("p"))
        self.assertEqual(tui.mode, "compose_entities")
        self.assertTrue(tui.compose_entity_rows)


if __name__ == "__main__":
    unittest.main()
