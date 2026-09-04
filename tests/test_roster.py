"""Roster matching: competency scoring and availability partition."""

import unittest

from tests.support import DatastoreCase, fake_agent

from lib.data.project import assign_agent, create_project
from lib.data.roster import match_agents


class TestAvailabilityMatching(DatastoreCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.domain = cls.ds.load_reference("skill_domain")[0]
        cls.free = fake_agent(
            11, "agent.free",
            skills=[{"domain_id": cls.domain["id"], "points": 5}])
        cls.busy = fake_agent(
            12, "agent.busy",
            skills=[{"domain_id": cls.domain["id"], "points": 9}])
        for agent in (cls.free, cls.busy):
            cls.ds.create("agent", agent, validate=False)
        project = create_project(cls.ds, "roster-booking", "Booking")
        assign_agent(cls.ds, project, cls.busy)

    def test_plain_ranking_ignores_bookings(self):
        matches = match_agents([self.domain["slug"]], count=1, ds=self.ds)
        self.assertEqual(matches[0].codename, "agent.busy")

    def test_available_only_partitions_and_discloses(self):
        matches = match_agents([self.domain["slug"]], count=1, ds=self.ds,
                               available_only=True)
        selected = [m.codename for m in matches if m.available]
        passed_over = [m.codename for m in matches if not m.available]
        self.assertEqual(selected, ["agent.free"])
        self.assertEqual(passed_over, ["agent.busy"])
        booked = next(m for m in matches if not m.available)
        self.assertIn("roster-booking", booked.booked_on)


if __name__ == "__main__":
    unittest.main()
