"""Syndicate inventory: address resolution, capability derivation and
closure, channel validation, the dispatch gate, and its truth table.

No live endpoints - the HTTP providers are exercised against a stubbed
transport so payload shape and error surfacing are asserted offline.
"""

import unittest
from unittest import mock
import urllib.error

from tests.support import DatastoreCase

from lib.dispatch.mission import (
    Mission, MissionConstraints, MissionStatus, Task, TaskStatus,
)
from lib.dispatch.provider import (
    OllamaChatProvider, OllamaProvider, OpenAICompatProvider, ProviderConfig, get_provider,
    provider_config,
)
from lib.dispatch.syndicate import (
    SyndicateError, address_of, capability_violation, channel_capabilities,
    expand_dependencies, required_capabilities, resolve, transport_of,
    validate_channel,
)


def constraints(**kwargs) -> MissionConstraints:
    """MissionConstraints with an explicit toolset (defaults carry tools)."""
    kwargs.setdefault("allowed_tools", [])
    return MissionConstraints(**kwargs)


# A bare completion endpoint: the text-only reference case for the gate.
# The shipped inventory no longer has one (mux_nova runs behind the agency
# harness), so the tests carry their own.
TEXT_ONLY = {
    "slug": "text_fixture",
    "identity": {"label": "Text-only fixture", "vendor": "self"},
    "transport_slug": "ollama_native",
    "endpoint": {"base_uri": "https://text.invalid", "egress": "private"},
    "channels": [{
        "slug": "plain", "model": "tiny:1b", "default": True,
        "capabilities": ["text_generation", "system_prompt"],
        "limits": {"max_concurrent": 1},
    }],
    "enabled": True,
}


class SyndicateCase(DatastoreCase):
    """Isolated datastore plus the text-only fixture syndicate."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.ds.create("syndicate", dict(TEXT_ONLY), validate=False)


class TestResolution(SyndicateCase):

    def test_address_forms(self):
        syndicate, channel = resolve("mux_nova", self.ds)
        self.assertEqual(syndicate["slug"], "mux_nova")
        self.assertEqual(channel["slug"], "qwen3_coder")
        self.assertEqual(address_of(syndicate, channel),
                         "mux_nova:qwen3_coder")

        _, explicit = resolve("mux_nova:qwen3_coder", self.ds)
        self.assertEqual(explicit["slug"], "qwen3_coder")

    def test_unknown_address_names_the_inventory(self):
        with self.assertRaises(SyndicateError) as ctx:
            resolve("does_not_exist", self.ds)
        message = str(ctx.exception)
        self.assertIn("does_not_exist", message)
        self.assertIn("mux_nova", message)

        with self.assertRaises(SyndicateError) as ctx:
            resolve("mux_nova:nope", self.ds)
        self.assertIn("qwen3_coder", str(ctx.exception))

    def test_transport_selects_handler(self):
        syndicate, _ = resolve("mux_nova", self.ds)
        self.assertEqual(transport_of(syndicate, self.ds)["handler"],
                         "OllamaChatProvider")
        provider = get_provider("mux_nova", ds=self.ds)
        self.assertIsInstance(provider, OllamaChatProvider)
        self.assertEqual(provider.config.model, "qwen3-coder:30b")
        self.assertEqual(provider.config.url,
                         "https://mux-nova.tailef068f.ts.net/api/chat")
        # Inventory records ride along for the gate and the briefing
        self.assertEqual(provider.syndicate["slug"], "mux_nova")
        self.assertTrue(provider.has_harness)

        plain = get_provider("text_fixture", ds=self.ds)
        self.assertIsInstance(plain, OllamaProvider)
        self.assertEqual(plain.config.url, "https://text.invalid/api/generate")
        self.assertFalse(plain.has_harness)

    def test_disabled_syndicate_refuses(self):
        syndicate, _ = resolve("mux_nova", self.ds)
        self.ds.update("syndicate", syndicate["id"], {"enabled": False})
        try:
            with self.assertRaises(SyndicateError) as ctx:
                resolve("mux_nova", self.ds)
            self.assertIn("disabled", str(ctx.exception))
        finally:
            self.ds.update("syndicate", syndicate["id"], {"enabled": True})


class TestCapabilityDerivation(SyndicateCase):

    def test_dependency_closure(self):
        # filesystem_write pulls in its whole chain
        self.assertEqual(
            expand_dependencies(["filesystem_write"], self.ds),
            {"filesystem_write", "filesystem_read", "tool_use"})
        self.assertEqual(expand_dependencies(["text_generation"], self.ds),
                         {"text_generation"})

    def test_requirements_derive_from_tools(self):
        """Truth table: mission constraints -> required capabilities."""
        cases = [
            # (allowed_tools, read_only, expected additions beyond baseline)
            ([], True, set()),
            (["Read"], True, {"filesystem_read", "tool_use"}),
            (["Glob", "Grep"], True, {"filesystem_read", "tool_use"}),
            (["Bash"], True, {"shell_exec", "tool_use"}),
            (["Read", "Bash"], True,
             {"filesystem_read", "shell_exec", "tool_use"}),
            (["Edit"], True,
             {"filesystem_write", "filesystem_read", "tool_use"}),
            (["WebFetch"], True, {"network_egress", "tool_use"}),
            # A write-capable mission requires write even with no edit tool
            ([], False, {"filesystem_write", "filesystem_read", "tool_use"}),
        ]
        for tools, read_only, extra in cases:
            with self.subTest(tools=tools, read_only=read_only):
                required = required_capabilities(
                    constraints(allowed_tools=tools, read_only=read_only),
                    self.ds)
                self.assertEqual(required, {"text_generation"} | extra)

    def test_channel_grants_are_closed(self):
        _, channel = resolve("claude_cli", self.ds)
        granted = channel_capabilities(channel, self.ds)
        self.assertIn("tool_use", granted)
        self.assertIn("filesystem_write", granted)


class TestChannelValidation(SyndicateCase):

    def test_dependency_gap_is_inconsistent(self):
        syndicate, _ = resolve("mux_nova", self.ds)
        broken = {"slug": "broken", "model": "x",
                  "capabilities": ["filesystem_read"]}  # no tool_use
        problems = validate_channel(syndicate, broken, self.ds)
        self.assertTrue(any("requires tool_use" in p for p in problems))

    def test_transport_ceiling_is_enforced(self):
        syndicate, _ = resolve("text_fixture", self.ds)
        overreach = {"slug": "over", "model": "x",
                     "capabilities": ["text_generation", "tool_use",
                                      "filesystem_read"]}
        problems = validate_channel(syndicate, overreach, self.ds)
        self.assertTrue(
            any("cannot carry" in p for p in problems),
            f"ollama_native must refuse filesystem_read: {problems}")

    def test_unknown_slug_is_reported(self):
        syndicate, _ = resolve("mux_nova", self.ds)
        bogus = {"slug": "b", "model": "x", "capabilities": ["telepathy"]}
        problems = validate_channel(syndicate, bogus, self.ds)
        self.assertTrue(any("unknown capability" in p for p in problems))

    def test_shipped_inventory_is_consistent(self):
        """Every channel we ship validates - the seed data is the example."""
        for slug in ("claude_cli", "mux_nova", "text_fixture"):
            syndicate, _ = resolve(slug, self.ds)
            for channel in syndicate["channels"]:
                with self.subTest(channel=address_of(syndicate, channel)):
                    self.assertEqual(
                        validate_channel(syndicate, channel, self.ds), [])


class TestCapabilityGate(SyndicateCase):
    """The refusal truth table: which missions which channels may serve."""

    def test_text_only_channel_refuses_tool_missions(self):
        syndicate, channel = resolve("text_fixture", self.ds)
        refusal = capability_violation(
            constraints(allowed_tools=["Read", "Bash"]),
            syndicate, channel, self.ds)
        self.assertIsNotNone(refusal)
        self.assertIn("filesystem_read", refusal)
        # The message must name the knob that changes the answer
        self.assertIn("claude_cli", refusal)

    def test_text_only_channel_serves_text_missions(self):
        syndicate, channel = resolve("text_fixture", self.ds)
        self.assertIsNone(capability_violation(
            constraints(allowed_tools=[]), syndicate, channel, self.ds))

    def test_harness_channel_serves_both(self):
        syndicate, channel = resolve("claude_cli", self.ds)
        for tools in ([], ["Read", "Bash"], ["Edit", "Write"]):
            with self.subTest(tools=tools):
                self.assertIsNone(capability_violation(
                    constraints(allowed_tools=tools, read_only=not tools),
                    syndicate, channel, self.ds))

    def test_write_mission_refused_by_text_channel(self):
        syndicate, channel = resolve("text_fixture", self.ds)
        refusal = capability_violation(
            constraints(allowed_tools=[], read_only=False),
            syndicate, channel, self.ds)
        self.assertIn("filesystem_write", refusal)

    def test_agency_harness_serves_reads_not_shell(self):
        """mux_nova runs behind the agency harness: read functions pass,
        a shell or write mission is refused with the narrowing knob."""
        syndicate, channel = resolve("mux_nova", self.ds)
        self.assertIsNone(capability_violation(
            constraints(allowed_tools=["Read", "Glob", "Grep", "LS"]),
            syndicate, channel, self.ds))
        refusal = capability_violation(
            constraints(allowed_tools=["Read", "Bash"]),
            syndicate, channel, self.ds)
        self.assertIn("shell_exec", refusal)
        self.assertIn("--tools Glob,Grep,LS,Read", refusal)
        self.assertNotIn("runs no tools", refusal)
        self.assertIn("filesystem_write", capability_violation(
            constraints(allowed_tools=["Edit"], read_only=False),
            syndicate, channel, self.ds))

    def test_egress_policy_refuses_distant_syndicates(self):
        from lib.config import get_config
        cfg = get_config()
        original = cfg["dispatch"].get("egress_policy", "public")
        syndicate, channel = resolve("text_fixture", self.ds)   # private
        try:
            cfg["dispatch"]["egress_policy"] = "local"
            refusal = capability_violation(
                constraints(), syndicate, channel, self.ds)
            self.assertIn("egress", refusal)

            cfg["dispatch"]["egress_policy"] = "private"
            self.assertIsNone(capability_violation(
                constraints(), syndicate, channel, self.ds))
        finally:
            cfg["dispatch"]["egress_policy"] = original


class TestDispatchBackstop(SyndicateCase):
    """The dispatcher refuses past the briefing, from any entry point."""

    def test_dispatcher_refuses_incapable_channel(self):
        from lib.dispatch.dispatcher import Dispatcher
        from lib.dispatch.ops_log import DispatchLogger

        provider = get_provider("text_fixture", ds=self.ds)
        mission = Mission(
            title="needs tools",
            constraints=MissionConstraints(allowed_tools=["Read"]))
        mission.add_task(Task(id="t1", agent_id="a1", prompt="p", role="r"))

        events = []
        dispatcher = Dispatcher(
            datastore=self.ds, provider=provider,
            logger=DispatchLogger(log_path=self.tmp / "s.log",
                                  json_log_path=self.tmp / "s.json"))
        result = dispatcher.execute_sync(mission, on_event=events.append)

        self.assertEqual(result.status, MissionStatus.FAILED)
        self.assertTrue(any("cannot serve" in e.message for e in events
                            if e.event_type == "error"))
        # Refused before any agent work: no spawn context stamped
        self.assertIsNone(mission.dispatched_by)


class StubResponse:
    """Captures the payload a provider would have sent."""

    def __init__(self, provider, reply: dict):
        self.provider = provider
        self.reply = reply
        self.sent = None

    def __call__(self, payload, timeout):
        self.sent = payload
        return self.reply


class TestHttpProviders(SyndicateCase):

    def setUp(self):
        # The relay retransmits link failures with backoff; tests do not wait
        patcher = mock.patch("lib.dispatch.bird._delay", return_value=0.0)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _task(self):
        return Task(id="t1", agent_id="a1", prompt="do the thing", role="Spec")

    def _provider(self, cls, reply, **overrides):
        config = ProviderConfig(
            name="test:channel", base_uri="https://example.invalid",
            path="/x", model="m", **overrides)
        provider = cls(config)
        provider._post = StubResponse(provider, reply)
        return provider

    def test_ollama_payload_and_extraction(self):
        provider = self._provider(
            OllamaProvider,
            {"response": "hello", "eval_count": 7, "prompt_eval_count": 3},
            parameters={"temperature": 0.2})
        result = provider.execute_sync("PERSONA", self._task(),
                                       constraints())
        self.assertEqual(result.status, TaskStatus.COMPLETE)
        self.assertEqual(result.output, "hello")
        self.assertEqual(result.tokens_used, 10)

        sent = provider._post.sent
        self.assertEqual(sent["model"], "m")
        self.assertEqual(sent["system"], "PERSONA")
        self.assertFalse(sent["stream"])
        self.assertEqual(sent["options"]["temperature"], 0.2)
        self.assertIn("do the thing", sent["prompt"])

    def test_openai_payload_and_extraction(self):
        provider = self._provider(
            OpenAICompatProvider,
            {"choices": [{"message": {"content": "hi"}}],
             "usage": {"total_tokens": 12}})
        result = provider.execute_sync("PERSONA", self._task(), constraints())
        self.assertEqual(result.output, "hi")
        self.assertEqual(result.tokens_used, 12)

        messages = provider._post.sent["messages"]
        self.assertEqual(messages[0], {"role": "system", "content": "PERSONA"})
        self.assertIn("do the thing", messages[1]["content"])

    def test_no_tools_notice_for_text_only_channel(self):
        """A channel without a harness must tell the model it has no hands."""
        provider = get_provider("text_fixture", ds=self.ds)
        provider._post = StubResponse(provider, {"response": "ok"})
        provider.execute_sync("PERSONA", self._task(), constraints())
        prompt = provider._post.sent["prompt"]
        self.assertIn("NO TOOLS AVAILABLE", prompt)
        self.assertIn("DO NOT", prompt)

    def test_transport_errors_surface(self):
        provider = self._provider(OllamaProvider, {})

        def boom(payload, timeout):
            raise urllib.error.URLError("connection refused")

        provider._post = boom
        result = provider.execute_sync("P", self._task(), constraints())
        self.assertEqual(result.status, TaskStatus.FAILED)
        self.assertIn("unreachable", result.error)

    def test_empty_response_is_a_failure_not_a_success(self):
        provider = self._provider(OllamaProvider, {"response": "   "})
        result = provider.execute_sync("P", self._task(), constraints())
        self.assertEqual(result.status, TaskStatus.FAILED)
        self.assertIn("empty response", result.error)

    def test_policy_refusal_is_classified(self):
        provider = self._provider(
            OllamaProvider,
            {"response": "I cannot assist with that request."})
        result = provider.execute_sync("P", self._task(), constraints())
        self.assertEqual(result.status, TaskStatus.FAILED)
        self.assertTrue(result.error.startswith("policy_violation"))


class TestProviderConfigMapping(SyndicateCase):

    def test_inventory_flattens_into_config(self):
        syndicate, channel = resolve("mux_nova", self.ds)
        config = provider_config(syndicate, channel,
                                 transport_of(syndicate, self.ds))
        self.assertEqual(config.name, "mux_nova:qwen3_coder")
        self.assertEqual(config.max_concurrent, 1)
        self.assertEqual(config.egress, "private")
        self.assertEqual(config.parameters["temperature"], 0.2)
        self.assertIsNone(config.credential_id)


if __name__ == "__main__":
    unittest.main()
