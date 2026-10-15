"""Regression tests for the 2026-10-14 independent review of the
composition kernel and the BIRD relay. Each test names the finding."""

import asyncio
import json
import shutil
import unittest
import urllib.error
from unittest import mock

from tests.support import DatastoreCase, REPO

from lib.data import refs
from lib.data.drafts import Draft, EditError
from lib.data.schema import SchemaManager
from lib.dispatch import bird
from lib.dispatch.provider import OllamaChatProvider, ProviderConfig

SM = SchemaManager().load_all()


class BlockStoreCase(DatastoreCase):
    """A datastore that also carries the instruction blocks."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        shutil.copytree(REPO / "data/agency/data/instruction",
                        cls.tmp / "instruction")


class TestLayoutStorage(BlockStoreCase):

    def test_create_never_overwrites_another_record(self):        # finding 1
        before = (self.tmp / "instruction/scope/read_only.json").read_text()
        draft = Draft.new(self.ds, SM, "instruction_block", {
            "id": "dup", "slug": "read_only", "category": "scope",
            "label": "dup", "priority": 1, "applies_when": {},
            "instruction_blocks": {}})
        with self.assertRaisesRegex(ValueError, "already holds"):
            draft.save()
        self.assertEqual(
            (self.tmp / "instruction/scope/read_only.json").read_text(), before)

    def test_update_moves_rather_than_duplicates(self):           # finding 2
        self.ds.update("instruction_block", "focus_kubernetes",
                       {"slug": "k8s"})
        self.assertFalse((self.tmp / "instruction/focus/kubernetes.json").exists())
        self.assertTrue((self.tmp / "instruction/focus/k8s.json").exists())
        ids = [b["id"] for b in self.ds.query("instruction_block")]
        self.assertEqual(ids.count("focus_kubernetes"), 1)
        self.ds.update("instruction_block", "focus_kubernetes",
                       {"slug": "kubernetes"})

    def test_move_onto_another_record_is_refused(self):
        with self.assertRaisesRegex(ValueError, "already holds"):
            self.ds.update("instruction_block", "focus_kubernetes",
                           {"slug": "database"})
        self.assertTrue((self.tmp / "instruction/focus/kubernetes.json").exists())


class TestDraftEdges(DatastoreCase):

    def test_reference_keys_are_immutable_once_saved(self):       # finding 3
        draft = Draft.load(self.ds, SM, "transport", "ollama_chat")
        with self.assertRaisesRegex(EditError, "immutable"):
            draft.set("/slug", "zzz")
        self.assertEqual(draft.diff(), [])

    def test_bad_index_is_refused_and_rolled_back(self):          # finding 4
        draft = Draft.load(self.ds, SM, "syndicate", "mux_nova")
        snapshot = json.dumps(draft.record, sort_keys=True)
        with self.assertRaisesRegex(EditError, "no such location"):
            draft.set("/channels/5/slug", "x")
        self.assertEqual(json.dumps(draft.record, sort_keys=True), snapshot)

    def test_save_keeps_fresh_system_fields(self):                # finding 10
        draft = Draft.load(self.ds, SM, "syndicate", "mux_nova")
        record = self.ds.read("syndicate", draft.original["id"])
        record["status"] = {"state": "offline", "last_check": "2099-01-01T00:00:00Z"}
        self.ds.replace("syndicate", record["id"], record)      # a probe ran
        draft.add("/identity/tags", "probe-race")
        saved = draft.save()
        self.assertEqual(saved["status"]["state"], "offline")

    def test_operator_local_reference_may_be_unbound(self):       # finding 11
        draft = Draft.load(self.ds, SM, "syndicate", "mux_nova")
        draft.record["endpoint"]["substation_id"] = \
            "00000000-0000-4000-8000-0000000000aa"
        self.assertEqual(draft.problems(), [])
        with self.assertRaises(EditError):                      # typed here: checked
            draft.set("/endpoint/substation_id",
                      "00000000-0000-4000-8000-0000000000bb")

    def test_retired_records_are_not_live(self):                  # finding 9
        draft = Draft.new(self.ds, SM, "syndicate")
        for pointer, value in (("/slug", "retiree"), ("/identity/label", "R"),
                               ("/transport_slug", "ollama_chat"),
                               ("/channels", [{"slug": "main", "model": "m"}])):
            draft.set(pointer, value)
        saved = draft.save()
        Draft.load(self.ds, SM, "syndicate", "retiree").delete()   # soft
        self.assertNotIn("retiree", {r["slug"] for r in
                                     refs.records(self.ds, "syndicate")})
        self.assertIn("retiree", {r["slug"] for r in refs.records(
            self.ds, "syndicate", include_retired=True)})
        with self.assertRaises(ValueError):
            Draft.load(self.ds, SM, "syndicate", "retiree")
        self.ds.delete("syndicate", saved["id"], hard=True)


class TestProbeStatus(DatastoreCase):

    def test_failed_probe_does_not_inherit_the_last_one(self):    # finding 8
        from lib.cli.syndicate import _record_status
        record = self.ds.query("syndicate",
                               lambda s: s["slug"] == "mux_nova").first()
        _record_status(self.ds, record, "online", latency=12.3, models=["m"])
        record = self.ds.read("syndicate", record["id"])
        record["status"]["conformance"] = {"x": {"suite": "kat-1", "passed": 1,
                                                 "total": 1, "at": "2026-10-14T00:00:00Z"}}
        self.ds.replace("syndicate", record["id"], record)
        _record_status(self.ds, record, "unreachable", error="refused")
        status = self.ds.read("syndicate", record["id"])["status"]
        self.assertEqual(status["state"], "unreachable")
        self.assertNotIn("latency_ms", status)
        self.assertNotIn("models_reported", status)
        self.assertIn("conformance", status)


class TestRelayEdges(unittest.TestCase):

    def setUp(self):
        patcher = mock.patch("lib.dispatch.bird._delay", return_value=0.0)
        patcher.start()
        self.addCleanup(patcher.stop)

    def provider(self, replies):
        provider = OllamaChatProvider(ProviderConfig(
            name="t", base_uri="https://x.invalid", path="/api/chat", model="m"))
        queue = list(replies)

        def post(payload, timeout):
            item = queue.pop(0)
            if isinstance(item, BaseException):
                raise item
            return item
        provider._post = post
        return provider

    def test_no_ledger_outside_a_task_accumulates(self):          # finding 5
        ok = {"message": {"content": "ok"}, "done_reason": "stop"}

        async def three_calls():
            counts = []
            for _ in range(3):
                provider = self.provider([urllib.error.URLError("refused"),
                                          urllib.error.URLError("refused"), ok])
                await bird.transmit(provider, {"messages": []}, 30)
                counts.append(bird.current().retransmits)
            return counts
        self.assertEqual(asyncio.run(three_calls()), [0, 0, 0])

    def test_missing_vocabulary_never_costs_the_reply(self):      # finding 6
        empty = {"message": {"content": ""}, "done_reason": "stop"}

        async def go():
            token = bird._CURRENT.set(bird.Ledger(task_id="t", vocab={}))
            try:
                reply = await bird.transmit(self.provider([empty]),
                                            {"messages": []}, 30)
                return reply, bird._CURRENT.get().problems
            finally:
                bird._CURRENT.reset(token)
        reply, problems = asyncio.run(go())
        self.assertEqual(reply, empty)
        self.assertEqual(problems[0]["status_code"], "UNKNOWN")

    def test_retry_after_rejects_non_ascii_digits(self):          # finding 7
        self.assertIsNone(bird.parse_retry_after("²"))
        self.assertEqual(bird.parse_retry_after("12"), 12.0)

    def test_timeouts_are_not_resent(self):                       # finding 12
        provider = self.provider([asyncio.TimeoutError(), {"message": {}}])

        async def go():
            token = bird.begin("t")
            try:
                with self.assertRaises(asyncio.TimeoutError):
                    await bird.transmit(provider, {"messages": []}, 30)
            finally:
                return bird.end(token)
        problems = asyncio.run(go())
        self.assertEqual([p["anomaly"] for p in problems], ["link_timeout"])


if __name__ == "__main__":
    unittest.main()
