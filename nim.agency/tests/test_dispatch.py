"""Dispatch pipeline: mission tags, cycle composition, engagement hook,
modality backstop, briefing disclosure. Uses a stub provider - no live
claude runs."""

import unittest

from tests.support import DatastoreCase

from lib.data.generator import AgentGenerator
from lib.data.project import (
    ENGAGEMENT_REVIEW, ENGAGEMENT_SMOKED, active_engagements, assign_agent,
    create_project, transition_engagements,
)
from lib.dispatch.briefing import build_briefing, render_briefing
from lib.dispatch.builder import project_cycle_mission
from lib.dispatch.dispatcher import Dispatcher
from lib.dispatch.mission import (
    Mission, MissionConstraints, MissionStatus, Task, TaskResult, TaskStatus,
)
from lib.dispatch.ops_log import DispatchLogger


class StubProvider:
    def __init__(self, fail=False):
        self.fail = fail

    def is_available(self):
        return True

    async def execute(self, system_prompt, task, constraints):
        status = TaskStatus.FAILED if self.fail else TaskStatus.COMPLETE
        return TaskResult(task_id=task.id, agent_id=task.agent_id,
                          status=status, output="ok",
                          error="boom" if self.fail else None)


class TestMissionTags(DatastoreCase):

    def test_project_tags_and_spawn_context_roundtrip(self):
        mission = Mission(title="tagged", project_id="pid", cycle=3,
                          dispatched_by={"operator": "op", "fqdn": "host"})
        mission.save(self.tmp / "m.json")
        loaded = Mission.load(self.tmp / "m.json")
        self.assertEqual(loaded.project_id, "pid")
        self.assertEqual(loaded.cycle, 3)
        self.assertEqual(loaded.dispatched_by["operator"], "op")
        self.assertIsNone(Mission(title="plain").project_id)


class TestCyclePipeline(DatastoreCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        gen = AgentGenerator(cls.ds)
        cls.crew = [cls.ds.create("agent", gen.generate(), validate=False)
                    for _ in range(2)]
        cls.project = create_project(cls.ds, "pipeline", "Pipeline",
                                     worktree=str(cls.tmp))
        cls.ds.update("project", cls.project["id"], {
            "policy": {"max_concurrent_engagements": 2, "read_only": True},
            "description": "pipeline program",
            "objectives": [{"id": 1, "text": "first objective",
                            "acceptance": "it lands", "done": False},
                           {"id": 2, "text": "already done", "done": True}],
            "directives": ["stay sharp"],
            "specifications": [{"label": "spec-a", "path_or_url": "SPEC.md"}],
        })
        cls.project = cls.ds.read("project", cls.project["id"])
        for member in cls.crew:
            assign_agent(cls.ds, cls.project, member)
        cls.rows = active_engagements(cls.project)
        cls.rows[0]["role"] = "lead"
        cls.rows[0]["responsibilities"] = ["own the schema"]
        cls.agents = {r["agent_id"]: cls.ds.read("agent", r["agent_id"])
                      for r in cls.rows}

    def _dispatcher(self, fail=False):
        return Dispatcher(
            datastore=self.ds, provider=StubProvider(fail=fail),
            logger=DispatchLogger(log_path=self.tmp / "d.log",
                                  json_log_path=self.tmp / "d.json"))

    def _mission(self):
        return project_cycle_mission(self.project, self.rows,
                                     self.agents, 1)

    def test_composition_from_project_state(self):
        mission = self._mission()
        self.assertEqual(len(mission.tasks), 2)
        self.assertEqual(mission.project_id, self.project["id"])
        self.assertEqual(mission.cycle, 1)
        self.assertTrue(mission.constraints.read_only)
        self.assertEqual(mission.constraints.working_directory,
                         str(self.tmp))
        prompt = mission.tasks[0].prompt
        for fragment in ("YOUR ROLE: lead", "own the schema",
                         "1. first objective", "(acceptance: it lands)",
                         "stay sharp", "spec-a: SPEC.md"):
            self.assertIn(fragment, prompt)
        self.assertNotIn("already done", prompt)
        self.assertEqual(mission.tasks[0].role, "Lead")

    def test_briefing_discloses_project_and_posture(self):
        rendered = render_briefing(build_briefing(
            self._mission(), ds=self.ds, report_path=self.tmp / "r.md"))
        self.assertIn("PROJECT:   pipeline  cycle 1", rendered)
        self.assertIn("MODALITY:  readonly", rendered)
        self.assertIn("OPERATOR:  ", rendered)

    def test_outcomes_drive_engagements(self):
        events = []
        result = self._dispatcher().execute_sync(self._mission(),
                                                 on_event=events.append)
        self.assertEqual(result.status, MissionStatus.COMPLETE)
        self.assertIsNotNone(result.dispatched_by)
        project = self.ds.read("project", self.project["id"])
        for row in active_engagements(project):
            self.assertEqual(row["status_id"], ENGAGEMENT_REVIEW)
        self.assertTrue(any(e.event_type == "engagement" for e in events))
        self.assertIn("## Attribution", result.final_report)
        self.assertIn("dispatched by", result.final_report)

        transition_engagements(self.ds, project, 2)  # re-queue
        self.project["engagements"] = project["engagements"]
        result = self._dispatcher(fail=True).execute_sync(self._mission())
        self.assertEqual(result.status, MissionStatus.FAILED)
        project = self.ds.read("project", self.project["id"])
        for row in active_engagements(project):
            self.assertEqual(row["status_id"], ENGAGEMENT_SMOKED)

    def test_modality_backstop_refuses_write(self):
        mission = Mission(title="write attempt",
                          constraints=MissionConstraints(read_only=False))
        events = []
        result = self._dispatcher().execute_sync(mission,
                                                 on_event=events.append)
        self.assertEqual(result.status, MissionStatus.FAILED)
        self.assertTrue(any("modality" in e.message for e in events
                            if e.event_type == "error"))
        self.assertIsNone(mission.dispatched_by)


if __name__ == "__main__":
    unittest.main()
