"""BIRD, the relay between the agency and its syndicates: the anomaly
vocabulary stays in step with the code, failures are classified, link
faults are retransmitted within their budgets, and every finding lands
on the task's ledger as an RFC 9457 problem."""

import asyncio
import io
import json
import re
import unittest
import urllib.error
from email.message import Message
from pathlib import Path
from unittest import mock

from tests.support import DatastoreCase, REPO

from lib.dispatch import bird
from lib.dispatch.mission import (
    Mission, MissionConstraints, MissionStatus, Task, TaskResult, TaskStatus,
)
from lib.dispatch.provider import OllamaChatProvider, ProviderConfig
from lib.dispatch.verification import Expectation

VOCAB = json.loads((REPO / "data/agency/data/reference/anomaly.json")
                   .read_text())["data"]
SLUGS = {row["slug"] for row in VOCAB}


_OPEN = []


def http_error(code, body=b"", headers=None):
    msg = Message()
    for k, v in (headers or {}).items():
        msg[k] = v
    exc = urllib.error.HTTPError("https://x.invalid", code, "err", msg,
                                 io.BytesIO(body))
    _OPEN.append(exc)
    return exc


def tearDownModule():
    for exc in _OPEN:
        exc.close()


def provider_with(*replies):
    """An Ollama chat provider whose _post plays back replies or raises."""
    provider = OllamaChatProvider(ProviderConfig(
        name="t", base_uri="https://x.invalid", path="/api/chat", model="m"))
    queue = list(replies)

    def post(payload, timeout):
        item = queue.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item
    provider._post = post
    provider.remaining = queue
    return provider


OK = {"message": {"content": "fine"}, "done_reason": "stop",
      "prompt_eval_count": 10}


class TestVocabulary(unittest.TestCase):

    def test_code_and_table_agree(self):
        """Every anomaly the code records is a row; every row is recorded
        somewhere (no dead vocabulary)."""
        slug = r'"((?:link|frame|context|content)_[a-z_]+)"'
        recorded = re.compile(r'record\(\s*' + slug)
        used = set()
        for path in (REPO / "lib").rglob("*.py"):
            text = path.read_text()
            # the classifier and detectors in bird.py name slugs directly;
            # elsewhere only record("...") calls count
            used |= set((re.compile(slug) if path.name == "bird.py"
                         else recorded).findall(text))
        self.assertEqual(sorted(used - SLUGS), [], "recorded but not in the table")
        self.assertEqual(sorted(SLUGS - used), [], "in the table but never recorded")

    def test_layers_prefix_slugs(self):
        for row in VOCAB:
            self.assertTrue(row["slug"].startswith(row["layer"] + "_"), row["slug"])

    def test_problem_is_rfc9457_shaped(self):
        ledger = bird.Ledger(task_id="t9", vocab={r["slug"]: r for r in VOCAB})
        problem = ledger.record("link_rate_limited", "slow down", status=429)
        for member in ("type", "title", "detail", "instance", "status"):
            self.assertIn(member, problem)
        self.assertEqual(problem["type"],
                         "https://nim.agency/bird/anomaly/link_rate_limited")
        # an unknown slug degrades to an unlabeled problem, never raises:
        # recording must not cost the caller its real error or reply
        unknown = ledger.record("link_telepathy", "?")
        self.assertEqual(unknown["status_code"], "UNKNOWN")
        self.assertEqual(unknown["layer"], "link")


class TestClassification(unittest.TestCase):

    def setUp(self):
        self.provider = provider_with()

    def slug(self, exc):
        return bird.classify_exception(exc, self.provider)[0]

    def test_link_and_context_classes(self):
        self.assertEqual(self.slug(asyncio.TimeoutError()), "link_timeout")
        self.assertEqual(self.slug(urllib.error.URLError("refused")),
                         "link_unreachable")
        self.assertEqual(self.slug(http_error(429)), "link_rate_limited")
        self.assertEqual(self.slug(http_error(503)), "link_unavailable")
        self.assertEqual(self.slug(http_error(500)), "link_http_error")
        self.assertEqual(self.slug(http_error(
            400, b'{"error":"request (3040 tokens) exceeds the available '
                 b'context size (1024 tokens)"}')), "context_exceeded")
        self.assertEqual(self.slug(json.JSONDecodeError("x", "doc", 0)),
                         "frame_malformed_body")
        self.assertIsNone(bird.classify_exception(RuntimeError("?"), self.provider))

    def test_error_body_survives_classification(self):
        exc = http_error(500, b"model crashed")
        bird.classify_exception(exc, self.provider)
        self.assertIn("model crashed", self.provider.describe_error(exc))

    def test_retry_after(self):
        self.assertEqual(bird.parse_retry_after("7"), 7.0)
        self.assertGreaterEqual(bird.parse_retry_after(
            "Wed, 21 Oct 2099 07:28:00 GMT"), 0)
        self.assertIsNone(bird.parse_retry_after("soon"))
        exc = http_error(429, headers={"Retry-After": "3"})
        self.assertEqual(bird.classify_exception(exc, self.provider)[2], 3.0)

    def test_full_jitter_is_bounded(self):
        cfg = dict(bird.RELAY_DEFAULTS)
        for attempt in range(8):
            delay = bird._delay(attempt, cfg, None)
            self.assertLessEqual(delay, cfg["max_delay_seconds"])
            self.assertGreaterEqual(delay, 0)
        self.assertEqual(bird._delay(0, cfg, 5.0), 5.0)
        self.assertEqual(bird._delay(0, cfg, 999.0), cfg["max_delay_seconds"])


class TestTransmit(unittest.TestCase):

    def setUp(self):
        patcher = mock.patch("lib.dispatch.bird._delay", return_value=0.0)
        patcher.start()
        self.addCleanup(patcher.stop)

    def send(self, provider, payload=None, timeout=30):
        async def go():
            token = bird.begin("t1")
            try:
                try:
                    reply = await bird.transmit(provider, payload or {"messages": []},
                                                timeout)
                except Exception as exc:          # noqa: BLE001
                    reply = exc
            finally:
                problems = bird.end(token)
            return reply, problems
        return asyncio.run(go())

    def test_unreachable_then_reply(self):
        reply, problems = self.send(provider_with(
            urllib.error.URLError("refused"), OK))
        self.assertEqual(reply, OK)
        self.assertEqual([p["anomaly"] for p in problems], ["link_unreachable"])
        self.assertIn("retransmitting", problems[0]["detail"])

    def test_attempts_are_bounded_by_the_row(self):
        provider = provider_with(*[urllib.error.URLError("refused")] * 5)
        reply, problems = self.send(provider)
        self.assertIsInstance(reply, urllib.error.URLError)
        self.assertEqual(len(problems), 3)            # 2 retransmits + final
        self.assertEqual(len(provider.remaining), 2)  # never sent

    def test_task_budget_caps_retransmits(self):
        with mock.patch("lib.dispatch.bird.relay_config", return_value={
                **bird.RELAY_DEFAULTS, "max_retransmits_per_task": 1}):
            provider = provider_with(*[http_error(503)] * 4)
            reply, problems = self.send(provider)
        self.assertIsInstance(reply, urllib.error.HTTPError)
        self.assertEqual(len(problems), 2)

    def test_deadline_stops_retransmission(self):
        with mock.patch("lib.dispatch.bird._delay", return_value=60.0):
            provider = provider_with(urllib.error.URLError("refused"), OK)
            reply, problems = self.send(provider, timeout=5)
        self.assertIsInstance(reply, urllib.error.URLError)
        self.assertEqual(len(problems), 1)
        self.assertNotIn("retransmitting", problems[0]["detail"])

    def test_empty_reply_is_resent_once(self):
        empty = {"message": {"content": ""}, "done_reason": "stop"}
        reply, problems = self.send(provider_with(empty, OK))
        self.assertEqual(reply, OK)
        self.assertEqual([p["anomaly"] for p in problems], ["frame_empty_message"])

    def test_truncated_output_is_recorded_not_resent(self):
        cut = {"message": {"content": "1, 2,"}, "done_reason": "length"}
        provider = provider_with(cut, OK)
        reply, problems = self.send(provider)
        self.assertEqual(reply, cut)
        self.assertEqual([p["anomaly"] for p in problems], ["frame_output_truncated"])
        self.assertEqual(len(provider.remaining), 1)

    def test_context_refusal_is_recorded_and_raised(self):
        exc = http_error(400, b"exceeds the available context size")
        reply, problems = self.send(provider_with(exc))
        self.assertIs(reply, exc)
        self.assertEqual(problems[0]["anomaly"], "context_exceeded")
        self.assertEqual(problems[0]["status"], 400)

    def test_silent_truncation_is_suspected(self):
        payload = {"messages": [{"role": "user", "content": "x " * 3000}]}
        short = {"message": {"content": "?"}, "done_reason": "stop",
                 "prompt_eval_count": 26}
        reply, problems = self.send(provider_with(short), payload)
        self.assertEqual([p["anomaly"] for p in problems],
                         ["context_truncation_suspected"])

    def test_ledgers_are_per_task(self):
        async def task(name, slug):
            token = bird.begin(name)
            await asyncio.sleep(0)
            bird.record(slug, name)
            await asyncio.sleep(0)
            return bird.end(token)

        async def both():
            return await asyncio.gather(task("a", "link_timeout"),
                                        task("b", "frame_empty_message"))
        a, b = asyncio.run(both())
        self.assertEqual([p["anomaly"] for p in a], ["link_timeout"])
        self.assertEqual([p["anomaly"] for p in b], ["frame_empty_message"])


class FixedProvider:
    def __init__(self, text):
        self.text = text

    def is_available(self):
        return True

    async def execute(self, system_prompt, task, constraints):
        return TaskResult(task_id=task.id, agent_id=task.agent_id,
                          status=TaskStatus.COMPLETE, output=self.text)


class TestDispatcherLedger(DatastoreCase):

    def test_known_answer_miss_is_a_content_anomaly(self):
        from lib.data.generator import AgentGenerator
        from lib.dispatch.dispatcher import Dispatcher
        from lib.dispatch.ops_log import DispatchLogger
        agent = self.ds.create("agent", AgentGenerator(self.ds).generate(),
                               validate=False)
        mission = Mission(title="kat", crew_ids=[agent["id"]],
                          constraints=MissionConstraints(allowed_tools=[]))
        mission.add_task(Task(id="t1", agent_id=agent["id"], prompt="count",
                              role="r", expect=Expectation(
                                  "json_subset", {"n": 26}).to_dict()))
        events = []
        log = self.tmp / "b.json"
        result = Dispatcher(
            datastore=self.ds, provider=FixedProvider('{"n": 32}'),
            logger=DispatchLogger(log_path=self.tmp / "b.log",
                                  json_log_path=log),
        ).execute_sync(mission, on_event=events.append)
        self.assertEqual(result.status, MissionStatus.FAILED)
        anomalies = result.tasks[0].result.anomalies
        self.assertEqual([p["anomaly"] for p in anomalies],
                         ["content_known_answer_mismatch"])
        self.assertTrue(any(e.event_type == "bird" for e in events))
        self.assertIn("Relay Anomalies (BIRD)", result.final_report)
        logged = json.loads(log.read_text())
        entries = logged if isinstance(logged, list) else logged.get("entries", [])
        self.assertTrue(any(e.get("log_type") == "anomaly" for e in entries))


if __name__ == "__main__":
    unittest.main()
