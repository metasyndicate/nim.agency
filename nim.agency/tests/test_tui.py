"""TUI composition: the mixin split reassembles the full AgentBuilder."""

import unittest

from tests.support import REPO  # noqa: F401  (sys.path side effect)


class TestTuiComposition(unittest.TestCase):

    def test_public_surface_reexported(self):
        from tui.builder import (  # noqa: F401
            AgentBuilder, Colors, MIN_TERMINAL_HEIGHT, MIN_TERMINAL_WIDTH,
        )
        self.assertEqual(MIN_TERMINAL_WIDTH, 80)
        self.assertEqual(MIN_TERMINAL_HEIGHT, 24)

    def test_mixins_compose_every_screen(self):
        from tui.builder import AgentBuilder
        bases = {b.__name__ for b in AgentBuilder.__mro__}
        for mixin in ("ChromeMixin", "RosterMixin", "DossierMixin",
                      "ProgressionMixin", "CreateMixin", "AgentEditMixin",
                      "SetupMixin", "SubstationsMixin", "CruesMixin",
                      "DispatchMixin", "ViewerMixin", "MissionsMixin",
                      "StatsMixin", "FoundryMixin"):
            self.assertIn(mixin, bases)
        for method in ("_draw", "_handle_input", "_draw_roster",
                       "_handle_view_input", "_connect_substation",
                       "_cycle_dispatch_preset", "_open_access_view",
                       "_save_foundry_form", "_save_agent_edit",
                       "_dispatch_worker", "_draw_header", "_safe_addstr"):
            self.assertTrue(callable(getattr(AgentBuilder, method)), method)

    def test_class_config_tables_survive(self):
        from tui.builder import AgentBuilder
        self.assertTrue(AgentBuilder.DISPATCH_FIELDS)
        self.assertTrue(AgentBuilder.AGENT_EDIT_FIELDS)
        self.assertTrue(AgentBuilder.FOUNDRY_TABLES)
        self.assertTrue(AgentBuilder.FOUNDRY_DEFAULTS)

    def test_headless_instantiation(self):
        """__init__ must not touch curses - state only."""
        from tui.builder import AgentBuilder
        builder = AgentBuilder()
        self.assertEqual(builder.mode, "menu")
        self.assertTrue(builder.system_operator)


if __name__ == "__main__":
    unittest.main()
