"""Project aggregate: CRUD, engagements, availability, lifecycle."""

import unittest

from tests.support import DatastoreCase, fake_agent

from lib.data.project import (
    ENGAGEMENT_COMPLETED, ENGAGEMENT_QUEUED, ENGAGEMENT_REVIEW,
    ENGAGEMENT_SMOKED, ENGAGEMENT_SWARMING, PROJECT_COMPLETE, PROJECT_DRAFT,
    active_engagements, agent_bookings, append_journal, apply_mission_outcome,
    assign_agent, availability_map, binding_violation, complete_project,
    create_project, cycle_in_flight, find_project, next_cycle,
    next_objective_id, open_objectives, primary_worktree, release_agent,
    transition_engagements,
)


class TestProjectCrud(DatastoreCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.project = create_project(
            cls.ds, "crud-test", "CRUD Test",
            description="test aggregate", worktree="/tmp", writable=False)

    def test_create_defaults(self):
        p = self.project
        self.assertEqual(p["status_id"], PROJECT_DRAFT)
        self.assertTrue(p["audit"]["created_at"])
        self.assertIsNone(p["audit"]["retired_at"])
        self.assertEqual(p["host_binding"]["worktrees"][0],
                         {"path": "/tmp", "writable": False, "primary": True})
        self.assertTrue(p["policy"]["read_only"])
        self.assertEqual(p["policy"]["max_concurrent_engagements"], 1)

    def test_duplicate_slug_refused(self):
        with self.assertRaises(ValueError):
            create_project(self.ds, "crud-test", "Again")

    def test_resolution(self):
        p = self.project
        self.assertEqual(find_project(self.ds, "crud-test")["id"], p["id"])
        self.assertEqual(find_project(self.ds, p["id"])["id"], p["id"])
        self.assertEqual(find_project(self.ds, p["id"][:8])["id"], p["id"])
        self.assertIsNone(find_project(self.ds, "nope"))

    def test_objective_ids(self):
        self.assertEqual(next_objective_id({"objectives": []}), 1)
        self.assertEqual(next_objective_id({"objectives": [{"id": 3}]}), 4)


class TestEngagements(DatastoreCase):

    _serial = 0

    def setUp(self):
        # Unique project AND agents per test: the datastore is shared
        # class-wide and engagements persist - a leftover active row
        # correctly books the agent everywhere else (cap 1)
        type(self)._serial += 1
        n = self._serial
        slug = f"eng-{self._testMethodName}"[:48].replace("_", "-")
        self.project = create_project(self.ds, slug, "Engagements")
        self.alpha = fake_agent(100 + n, f"agent.alpha{n}")
        self.beta = fake_agent(200 + n, f"agent.beta{n}")

    def test_assign_release_rehire(self):
        row = assign_agent(self.ds, self.project, self.alpha, role="lead",
                           responsibilities=["own the schema"])
        self.assertEqual(row["status_id"], ENGAGEMENT_QUEUED)
        self.assertIsNone(row["released_at"])
        with self.assertRaises(ValueError):
            assign_agent(self.ds, self.project, self.alpha)

        released = release_agent(self.ds, self.project, self.alpha)
        self.assertIsNotNone(released["released_at"])
        self.assertEqual(len(active_engagements(self.project)), 0)
        self.assertEqual(len(self.project["engagements"]), 1)
        with self.assertRaises(ValueError):
            release_agent(self.ds, self.project, self.alpha)

        rehired = assign_agent(self.ds, self.project, self.alpha)
        self.assertIsNone(rehired["released_at"])

    def test_booking_cap_across_projects(self):
        assign_agent(self.ds, self.project, self.alpha)
        second = create_project(self.ds, self.project["slug"] + "-2", "Two")
        with self.assertRaises(ValueError):
            assign_agent(self.ds, second, self.alpha)

        self.ds.update("project", second["id"],
                       {"policy": {"max_concurrent_engagements": 2}})
        second = self.ds.read("project", second["id"])
        assign_agent(self.ds, second, self.alpha)
        self.assertEqual(len(agent_bookings(self.ds, self.alpha["id"])), 2)
        release_agent(self.ds, second, self.alpha)

    def test_availability_derivation(self):
        assign_agent(self.ds, self.project, self.alpha)
        booked = availability_map(self.ds)
        self.assertIn(self.project["slug"], booked[self.alpha["id"]])
        release_agent(self.ds, self.project, self.alpha)
        self.assertNotIn(
            self.project["slug"],
            availability_map(self.ds).get(self.alpha["id"], []))

    def test_transitions_and_outcomes(self):
        assign_agent(self.ds, self.project, self.alpha)
        moved = transition_engagements(
            self.ds, self.project, ENGAGEMENT_SWARMING,
            agent_ids=[self.alpha["id"]])
        self.assertEqual(moved, 1)
        self.assertTrue(cycle_in_flight(self.project))

        moved, status_id = apply_mission_outcome(
            self.ds, self.project, "complete")
        self.assertEqual(status_id, ENGAGEMENT_REVIEW)
        self.assertFalse(cycle_in_flight(self.project))

        _, status_id = apply_mission_outcome(self.ds, self.project, "failed")
        self.assertEqual(status_id, ENGAGEMENT_SMOKED)


class TestCycleLifecycle(DatastoreCase):

    def setUp(self):
        slug = f"cyc-{self._testMethodName}"[:48].replace("_", "-")
        self.project = create_project(self.ds, slug, "Cycle",
                                      worktree="/tmp")
        self.ds.update("project", self.project["id"], {
            "objectives": [
                {"id": 1, "text": "open one", "done": False},
                {"id": 2, "text": "closed one", "done": True},
            ]})
        self.project = self.ds.read("project", self.project["id"])

    def test_open_objectives_and_worktree(self):
        self.assertEqual([o["id"] for o in open_objectives(self.project)],
                         [1])
        self.assertEqual(primary_worktree(self.project)["path"], "/tmp")

    def test_journal_drives_cycle_numbering(self):
        self.assertEqual(next_cycle(self.project), 1)
        append_journal(self.ds, self.project, 1, "abcd1234", "complete",
                       "1/1 tasks")
        self.assertEqual(next_cycle(self.project), 2)
        entry = self.ds.read("project",
                             self.project["id"])["journal"][-1]
        self.assertEqual(entry["mission_id"], "abcd1234")

    def test_complete_releases_crew(self):
        agent = fake_agent(9, "agent.omega")
        assign_agent(self.ds, self.project, agent)
        released = complete_project(self.ds, self.project)
        self.assertEqual(released, 1)
        self.assertEqual(self.project["status_id"], PROJECT_COMPLETE)
        for row in self.project["engagements"]:
            self.assertEqual(row["status_id"], ENGAGEMENT_COMPLETED)
            self.assertIsNotNone(row["released_at"])


class TestBindingGuardrail(DatastoreCase):
    """Permission-mismatch: fail the briefing, not the mission."""

    def _project(self, slug, *, read_only, writable, permissions):
        project = create_project(self.ds, slug, "Guardrail",
                                 worktree="/tmp", writable=writable)
        self.ds.update("project", project["id"], {
            "policy": {"read_only": read_only},
            "host_binding": {
                "substation_id": None,
                "worktrees": [{"path": "/tmp", "writable": writable,
                               "primary": True}],
                "required_permissions": permissions,
            }})
        return self.ds.read("project", project["id"])

    def test_read_only_policy_always_passes(self):
        project = self._project("guard-ro", read_only=True, writable=False,
                                permissions=["read"])
        self.assertIsNone(binding_violation(project))

    def test_write_policy_needs_writable_worktree(self):
        project = self._project("guard-wt", read_only=False, writable=False,
                                permissions=["read", "write"])
        self.assertIn("read-only", binding_violation(project))

    def test_write_policy_needs_write_permission(self):
        project = self._project("guard-perm", read_only=False, writable=True,
                                permissions=["read"])
        self.assertIn("required_permissions", binding_violation(project))

    def test_coherent_write_binding_passes(self):
        project = self._project("guard-ok", read_only=False, writable=True,
                                permissions=["read", "write"])
        self.assertIsNone(binding_violation(project))


if __name__ == "__main__":
    unittest.main()
