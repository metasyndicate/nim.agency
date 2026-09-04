"""Attribution convention: the three codified formats + spawn context."""

import unittest

from tests.support import fake_agent

from lib.data.attribution import (
    banner, code_byline, dispatched_by_line, git_author, git_trailers,
    mission_scope_label, nim_stamp, spawn_context,
)
from lib.dispatch.mission import Mission, MissionStatus


def sample_agent():
    return fake_agent(
        21, "synodic.miner",
        identity={"name": "Dr. Julia Pembroke", "codename": "synodic.miner",
                  "role_title": "Field Specialist",
                  "dogtag": "dr.julia.pembroke"},
        economy={"gold": 450, "xp": 1645},
        crypto={"fingerprint": "SHA256:abc", "key_type": "ed25519"})


class TestSpawnContext(unittest.TestCase):

    def test_captures_piggybacked_identity(self):
        ctx = spawn_context()
        for key in ("operator", "host", "fqdn", "cwd", "captured_at"):
            self.assertTrue(ctx.get(key), key)
        self.assertIn("uid", ctx)
        self.assertFalse(ctx["fqdn"].endswith(".arpa"))
        self.assertEqual(dispatched_by_line(ctx),
                         f"{ctx['operator']}@{ctx['fqdn']}")


class TestFormats(unittest.TestCase):

    def setUp(self):
        self.agent = sample_agent()
        self.mission = Mission(id="23e2f097", title="Doc coverage",
                               status=MissionStatus.COMPLETE)
        self.ctx = {"operator": "bradley", "fqdn": "host.example.edu",
                    "uid": 501}

    def test_nim_stamp_shape(self):
        parts = nim_stamp().split(".")
        self.assertEqual(len(parts), 3)
        self.assertTrue(all(p.isdigit() for p in parts))

    def test_git_author(self):
        self.assertEqual(git_author(self.agent),
                         "Biomimetic Dr. Julia Pembroke <@synodic.miner>")

    def test_git_trailers(self):
        trailers = git_trailers(self.agent, mission=self.mission,
                                ctx=self.ctx)
        self.assertEqual(
            trailers[0],
            "Attributed-To: Biomimetic Dr. Julia Pembroke <@synodic.miner>")
        self.assertIn("Mission: 23e2f097 (complete)", trailers)
        self.assertIn("Dispatched-By: bradley@host.example.edu (uid=501)",
                      trailers)
        self.assertTrue(trailers[-1].startswith(
            "Agency: NIM Biomimetic Agency ["))

    def test_banner(self):
        line = banner(self.agent, mission=self.mission, pay_gold=15,
                      ctx=self.ctx, scope=mission_scope_label(True))
        for fragment in ("@synodic.miner", "Dr. Julia Pembroke",
                         "Field Specialist", "💰 450", "⚡ 1645",
                         "[23e2f097] COMPLETE pay:+15g",
                         "dispatched by bradley@host.example.edu",
                         "scope: read-only"):
            self.assertIn(fragment, line)

    def test_code_byline(self):
        byline = code_byline(self.agent, mission=self.mission, ctx=self.ctx)
        self.assertTrue(byline.startswith(
            "Authored by Biomimetic Dr. Julia Pembroke (@synodic.miner)"))
        self.assertIn("mission 23e2f097", byline)
        self.assertIn("Dispatched by bradley@host.example.edu.", byline)


if __name__ == "__main__":
    unittest.main()
