"""Agency harness: executor enforcement, the offer join, the tool loop,
chat transports, and known-answer verification.

No live endpoints - providers run against scripted chat turns, and the
executor runs against a temp directory tree.
"""

import asyncio
import os
import tempfile
import unittest
from pathlib import Path

from tests.support import DatastoreCase

from lib.dispatch.harness import (
    ChatTurn, ExecutionPolicy, Executor, ToolCall, glob_regex,
    offered_functions, parse_arguments, run, tool_schema,
)
from lib.dispatch.mission import (
    Mission, MissionConstraints, MissionStatus, Task, TaskStatus,
)
from lib.dispatch.provider import (
    OllamaChatProvider, OllamaProvider, OpenAICompatProvider, ProviderConfig,
    truncation_error,
)
from lib.dispatch.verification import (
    Expectation, extract_json_object, json_mismatches, verify,
)


READ_ROWS = None   # filled from reference data in setUpClass


def make_tree(root: Path) -> None:
    """A small project: 3 files at top, 2 nested, a .git dir, a binary."""
    (root / "src" / "pkg").mkdir(parents=True)
    (root / ".git").mkdir()
    (root / "README.md").write_text("hello\nworld\n")
    (root / "a.py").write_text("import os\nTODO = 1\n")
    (root / "b.py").write_text("print('b')\n")
    (root / "src" / "c.py").write_text("# TODO later\n")
    (root / "src" / "pkg" / "d.py").write_text("x = 1\n")
    (root / ".git" / "config.py").write_text("TODO hidden\n")
    (root / "blob.bin").write_bytes(b"\x00\x01TODO")


class ExecutorCase(DatastoreCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.rows = {r["function_name"]: r
                    for r in cls.ds.load_reference("harness_function")}
        cls.work = Path(tempfile.mkdtemp(prefix="nim-harness-")).resolve()
        cls.root = cls.work / "project"
        cls.root.mkdir()
        make_tree(cls.root)
        (cls.work / "secret.txt").write_text("outside\n")

    def executor(self, names=("Read", "LS", "Glob", "Grep"), **policy):
        policy.setdefault("root", self.root)
        policy.setdefault("offered", {n: self.rows[n] for n in names})
        return Executor(ExecutionPolicy(**policy))


class TestExecutorEnforcement(ExecutorCase):
    """The safety rules from the plan: refusals are results."""

    def test_traversal_refused(self):
        outcome = self.executor().run("Read", {"path": "../secret.txt"})
        self.assertEqual(outcome.outcome, "refused")
        self.assertIn("outside the working directory", outcome.content)

    def test_absolute_path_outside_refused(self):
        outcome = self.executor().run(
            "Read", {"path": str(self.work / "secret.txt")})
        self.assertEqual(outcome.outcome, "refused")

    def test_absolute_path_inside_allowed(self):
        outcome = self.executor().run(
            "Read", {"path": str(self.root / "README.md")})
        self.assertTrue(outcome.ok, outcome.content)

    def test_symlink_escape_refused(self):
        link = self.root / "escape.txt"
        try:
            os.symlink(self.work / "secret.txt", link)
        except OSError:
            self.skipTest("symlinks unavailable")
        try:
            outcome = self.executor().run("Read", {"path": "escape.txt"})
            self.assertEqual(outcome.outcome, "refused")
            # and the walk does not surface it either
            listing = self.executor().run("Grep", {"pattern": "outside"})
            self.assertIn("0 match(es)", listing.content)
        finally:
            link.unlink()

    def test_unoffered_function_refused(self):
        outcome = self.executor(names=("LS",)).run("Read",
                                                   {"path": "a.py"})
        self.assertEqual(outcome.outcome, "refused")
        self.assertIn("not available", outcome.content)
        outcome = self.executor().run("Bash", {"command": "ls"})
        self.assertEqual(outcome.outcome, "refused")

    def test_read_only_refuses_mutation(self):
        write_row = {"function_name": "Write", "handler": "write",
                     "parameters": {"type": "object", "properties": {
                         "path": {"type": "string"}}}}
        executor = Executor(ExecutionPolicy(
            root=self.root, read_only=True, offered={"Write": write_row},
            hazards={"Write": "mutation"}))
        outcome = executor.run("Write", {"path": "a.py"})
        self.assertEqual(outcome.outcome, "refused")
        self.assertIn("read-only", outcome.content)

    def test_arguments_checked_against_schema(self):
        ex = self.executor()
        self.assertEqual(ex.run("Read", {}).outcome, "refused")
        self.assertEqual(ex.run("Read", {"path": 3}).outcome, "refused")
        self.assertEqual(ex.run("Read", {"path": "a.py", "limit": 0}).outcome,
                         "refused")
        self.assertEqual(ex.run("Read", {"path": "a.py", "limit": True}).outcome,
                         "refused")
        bogus = ex.run("Read", {"path": "a.py", "mode": "w"})
        self.assertEqual(bogus.outcome, "refused")
        self.assertIn("unknown argument", bogus.content)
        self.assertEqual(ex.run("LS", "not-an-object").outcome, "refused")

    def test_deadline_is_enforced(self):
        outcome = self.executor(call_timeout=-1).run("Glob",
                                                     {"pattern": "**/*"})
        self.assertEqual(outcome.outcome, "error")
        self.assertIn("time budget", outcome.content)

    def test_catastrophic_regex_is_stopped(self):
        """Review finding: re does not release the GIL, so a backtracking
        pattern must run where it can be killed."""
        import time
        victim = self.root / "redos.txt"
        victim.write_text("a" * 40 + "!\n")
        try:
            started = time.monotonic()
            outcome = self.executor(call_timeout=1.5).run(
                "Grep", {"pattern": "(a+)+$", "path": "redos.txt"})
            elapsed = time.monotonic() - started
        finally:
            victim.unlink()
        self.assertEqual(outcome.outcome, "error")
        self.assertIn("time budget", outcome.content)
        self.assertLess(elapsed, 5)

    def test_malformed_patterns_are_results_not_crashes(self):
        ex = self.executor()
        for name, args in (("Glob", {"pattern": "[]a]"}),
                           ("Glob", {"pattern": "[z-a]"}),
                           ("Grep", {"pattern": "a{99999999999}"})):
            with self.subTest(args=args):
                self.assertIn(ex.run(name, args).outcome, ("ok", "error"))
        self.assertEqual(ex.run(["Read"], {"path": "a.py"}).outcome,
                         "refused")
        self.assertEqual(ex.run("Glob", {"pattern": "*" * 600}).outcome,
                         "refused")

    def test_special_files_are_not_opened(self):
        fifo = self.root / "pipe"
        try:
            os.mkfifo(fifo)
        except (OSError, AttributeError):
            self.skipTest("mkfifo unavailable")
        try:
            self.assertEqual(self.executor().run("Read", {"path": "pipe"})
                             .outcome, "error")
            grep = self.executor(call_timeout=5).run("Grep",
                                                     {"pattern": "TODO"})
            self.assertTrue(grep.ok)    # walked past the FIFO, no hang
        finally:
            fifo.unlink()

    def test_result_is_clipped(self):
        outcome = self.executor(max_result_chars=20).run("LS", {})
        self.assertIn("[truncated", outcome.content)


class TestExecutorHandlers(ExecutorCase):

    def test_ls_reports_exact_counts(self):
        outcome = self.executor().run("LS", {})
        self.assertTrue(outcome.ok)
        # README.md a.py b.py blob.bin + src/ .git/
        self.assertIn("6 entries (4 files, 2 directories)", outcome.content)
        self.assertIn("src/", outcome.content)

    def test_read_numbers_and_pages(self):
        outcome = self.executor().run("Read", {"path": "README.md",
                                               "offset": 2, "limit": 5})
        self.assertIn("lines 2-2 of 2", outcome.content)
        self.assertIn("2\tworld", outcome.content)
        self.assertEqual(self.executor().run("Read", {"path": "src"}).outcome,
                         "error")
        self.assertEqual(
            self.executor().run("Read", {"path": "blob.bin"}).outcome,
            "error")
        self.assertEqual(
            self.executor().run("Read", {"path": "nope.txt"}).outcome,
            "error")

    def test_glob_follows_pathlib_semantics(self):
        top = self.executor().run("Glob", {"pattern": "*.py"})
        self.assertIn("2 file(s)", top.content)
        deep = self.executor().run("Glob", {"pattern": "**/*.py"})
        # a, b, src/c, src/pkg/d - never .git/config.py
        self.assertIn("4 file(s)", deep.content)
        self.assertNotIn(".git", deep.content.split("\n", 1)[1])
        scoped = self.executor().run("Glob", {"pattern": "*.py",
                                              "path": "src"})
        self.assertIn("src/c.py", scoped.content)

    def test_grep_skips_git_and_binaries(self):
        outcome = self.executor().run("Grep", {"pattern": "TODO"})
        self.assertIn("2 match(es)", outcome.content)
        self.assertIn("a.py:2:TODO = 1", outcome.content)
        self.assertNotIn("hidden", outcome.content)
        filtered = self.executor().run("Grep", {"pattern": "todo",
                                                "ignore_case": True,
                                                "glob": "c.py"})
        self.assertIn("1 match(es)", filtered.content)
        bad = self.executor().run("Grep", {"pattern": "("})
        self.assertEqual(bad.outcome, "error")
        literal = self.executor().run("Grep", {"pattern": "x",
                                               "path": "src/*.py"})
        self.assertEqual(literal.outcome, "error")
        none = self.executor().run("Grep", {"pattern": "^zzz"})
        self.assertIn("use Glob or LS", none.content)

    def test_glob_regex(self):
        cases = [("*.py", "a.py", True), ("*.py", "src/a.py", False),
                 ("**/*.py", "a.py", True), ("**/*.py", "x/y/a.py", True),
                 ("src/**", "src/x/y", True), ("[ab].py", "b.py", True),
                 ("[!ab].py", "c.py", True), ("?.py", "ab.py", False)]
        for pattern, path, expected in cases:
            with self.subTest(pattern=pattern, path=path):
                self.assertEqual(bool(glob_regex(pattern).fullmatch(path)),
                                 expected)


def harness_channel(*caps):
    return {"slug": "h", "model": "m",
            "capabilities": ["text_generation", "system_prompt", *caps]}


class TestHarnessProfiles(DatastoreCase):
    """A channel's harness profile narrows the offer and sets the limits."""

    def test_profile_narrows_the_offer(self):
        from lib.dispatch.harness import offered_functions
        channel = harness_channel("tool_use", "filesystem_read")
        mc = MissionConstraints(allowed_tools=["Read", "LS", "Glob", "Grep"])
        full = {r["function_name"] for r in
                offered_functions(mc, channel, ["grep"], self.ds)}
        self.ds.add_reference_entry("harness_profile", {
            "slug": "listing_only", "label": "Listing only",
            "functions": ["LS", "Glob"]})
        channel["harness_profile"] = "listing_only"
        narrowed = {r["function_name"] for r in
                    offered_functions(mc, channel, ["grep"], self.ds)}
        self.assertEqual(narrowed, {"LS", "Glob"})
        self.assertTrue(narrowed < full)

    def test_profile_limits_override_config(self):
        from lib.dispatch.harness import harness_config
        channel = harness_channel("tool_use", "filesystem_read")
        base = harness_config(channel, self.ds)
        channel["harness_profile"] = "readonly_fs_compact"
        tuned = harness_config(channel, self.ds)
        self.assertEqual(tuned["max_calls_per_turn"], 4)
        self.assertEqual(tuned["max_result_chars"], 8000)
        self.assertEqual(tuned["max_iterations"], base["max_iterations"])

    def test_missing_profile_fails_closed(self):
        from lib.dispatch.harness import offered_functions
        channel = harness_channel("tool_use", "filesystem_read")
        channel["harness_profile"] = "no_such_profile"
        mc = MissionConstraints(allowed_tools=["Read", "LS"])
        self.assertEqual(offered_functions(mc, channel, [], self.ds), [])


class TestOfferJoin(DatastoreCase):
    """agent grants ∩ mission allowed_tools ∩ channel capabilities."""

    def test_intersection(self):
        channel = harness_channel("tool_use", "filesystem_read")
        mc = MissionConstraints(allowed_tools=["Read", "Grep", "Bash"])
        names = {r["function_name"] for r in
                 offered_functions(mc, channel, ["grep"], self.ds)}
        self.assertEqual(names, {"Read", "Grep"})   # Bash: not implemented

    def test_depot_grant_required(self):
        channel = harness_channel("tool_use", "filesystem_read")
        mc = MissionConstraints(allowed_tools=["Read", "Grep"])
        names = {r["function_name"] for r in
                 offered_functions(mc, channel, ["git"], self.ds)}
        self.assertEqual(names, {"Read"})
        # an unstamped task holds no depot tools
        names = {r["function_name"] for r in
                 offered_functions(mc, channel, None, self.ds)}
        self.assertEqual(names, {"Read"})

    def test_channel_without_harness_offers_nothing(self):
        mc = MissionConstraints(allowed_tools=["Read"])
        self.assertEqual(offered_functions(mc, harness_channel(), ["grep"],
                                           self.ds), [])
        self.assertEqual(offered_functions(mc, None, ["grep"], self.ds), [])

    def test_every_function_is_named_by_a_capability(self):
        """Invariant: the capability table, not this one, maps names to
        capabilities - every row must be reachable through it."""
        from lib.dispatch.harness.executor import Executor as Ex
        from lib.dispatch.syndicate import capability_of_tool
        for row in self.ds.load_reference("harness_function"):
            with self.subTest(function=row["function_name"]):
                self.assertIsNotNone(
                    capability_of_tool(row["function_name"], self.ds))
                self.assertTrue(hasattr(Ex, f"_fn_{row['handler']}"))
                self.assertEqual(row["parameters"]["type"], "object")

    def test_tool_schema_shape(self):
        row = self.ds.get_reference_by_slug("harness_function", "read")
        schema = tool_schema(row)
        self.assertEqual(schema["type"], "function")
        self.assertEqual(schema["function"]["name"], "Read")
        self.assertIn("path", schema["function"]["parameters"]["properties"])

    def test_ollama_chat_ceiling_matches_executor(self):
        """The transport ceiling must not promise what the executor lacks."""
        transport = self.ds.get_reference_by_slug("transport", "ollama_chat")
        ceiling = set(transport["capability_ceiling"])
        self.assertIn("filesystem_read", ceiling)
        for absent in ("filesystem_write", "shell_exec", "network_egress"):
            self.assertNotIn(absent, ceiling)


class ScriptedChat:
    """Plays back ChatTurns and records each request's messages."""

    def __init__(self, turns):
        self.turns = list(turns)
        self.requests = []

    async def __call__(self, messages, tools, constraints, timeout):
        self.requests.append({"messages": [dict(m) for m in messages],
                              "tools": tools})
        if not self.turns:
            raise AssertionError("loop asked for more turns than scripted")
        return self.turns.pop(0)


def call_turn(*calls, prompt_tokens=100):
    tool_calls = [ToolCall(id=f"c{i}", name=n, arguments=a)
                  for i, (n, a) in enumerate(calls)]
    return ChatTurn(content="", tool_calls=tool_calls,
                    message={"role": "assistant", "content": "",
                             "tool_calls": ["..."]},
                    prompt_tokens=prompt_tokens, completion_tokens=10)


def final_turn(text, prompt_tokens=200):
    return ChatTurn(content=text, tool_calls=[],
                    message={"role": "assistant", "content": text},
                    prompt_tokens=prompt_tokens, completion_tokens=20)


class TestLoop(ExecutorCase):

    def provider(self, turns, context_window=32768):
        provider = OllamaChatProvider(ProviderConfig(
            name="test:chat", transport="ollama_chat",
            base_uri="https://example.invalid", path="/api/chat", model="m"))
        provider.channel = harness_channel("tool_use", "filesystem_read")
        provider.channel["limits"] = {"context_window": context_window}
        provider.ds = self.ds
        provider.chat = ScriptedChat(turns)
        return provider

    def constraints(self, **kwargs):
        kwargs.setdefault("allowed_tools", ["Read", "LS", "Glob", "Grep"])
        return MissionConstraints(working_directory=str(self.root), **kwargs)

    def go(self, provider, mc=None, grants=("grep",)):
        mc = mc or self.constraints()
        task = Task(id="t1", agent_id="a1", prompt="count the files",
                    role="Spec", tool_grants=list(grants))
        offered = provider.offered_functions(task, mc)
        return asyncio.run(run(provider, "PERSONA", task, mc, offered,
                               self.ds)), provider

    def test_call_then_answer(self):
        result, provider = self.go(self.provider([
            call_turn(("LS", {})),
            final_turn("There are 4 files."),
        ]))
        self.assertEqual(result.status, TaskStatus.COMPLETE)
        self.assertEqual(result.output, "There are 4 files.")
        self.assertEqual(result.tokens_used, 110 + 220)
        self.assertEqual(len(result.tool_calls), 1)
        self.assertEqual(result.tool_calls[0]["function"], "LS")
        self.assertEqual(result.tool_calls[0]["outcome"], "ok")

        first = provider.chat.requests[0]
        self.assertEqual(first["messages"][0],
                         {"role": "system", "content": "PERSONA"})
        self.assertIn("**TOOLS:**", first["messages"][1]["content"])
        self.assertEqual({t["function"]["name"] for t in first["tools"]},
                         {"Read", "LS", "Glob", "Grep"})
        # the real listing reached the model
        tool_msg = provider.chat.requests[1]["messages"][-1]
        self.assertEqual(tool_msg["role"], "tool")
        self.assertIn("6 entries", tool_msg["content"])

    def test_refusal_returns_to_model_and_mission_continues(self):
        result, provider = self.go(self.provider([
            call_turn(("Read", {"path": "../../etc/passwd"})),
            final_turn("I could not read that."),
        ]))
        self.assertEqual(result.status, TaskStatus.COMPLETE)
        self.assertEqual(result.tool_calls[0]["outcome"], "refused")
        self.assertIn("REFUSED", provider.chat.requests[1]["messages"][-1]
                      ["content"])

    def test_iteration_cap(self):
        from lib.config import get_config
        harness = get_config().setdefault("harness", {})
        original = harness.get("max_iterations")
        harness["max_iterations"] = 3
        try:
            result, _ = self.go(self.provider(
                [call_turn(("LS", {})) for _ in range(3)]))
        finally:
            harness["max_iterations"] = original or 12
        self.assertEqual(result.status, TaskStatus.FAILED)
        self.assertIn("harness exhausted", result.error)
        self.assertEqual(len(result.tool_calls), 3)

    def test_budget_never_discards_a_final_answer(self):
        from lib.config import get_config
        harness = get_config().setdefault("harness", {})
        original = harness.get("token_budget")
        harness["token_budget"] = 50
        try:
            result, _ = self.go(self.provider([final_turn("the answer")]))
            self.assertEqual(result.status, TaskStatus.COMPLETE)
            self.assertEqual(result.output, "the answer")
            # ...but it does stop further work
            result, _ = self.go(self.provider([call_turn(("LS", {}))]))
            self.assertIn("token budget", result.error)
        finally:
            harness["token_budget"] = original or 200000

    def test_context_exhaustion_fails(self):
        result, _ = self.go(self.provider(
            [call_turn(("LS", {}), prompt_tokens=5000)], context_window=4096))
        self.assertEqual(result.status, TaskStatus.FAILED)
        self.assertIn("context exhausted", result.error)

    def test_calls_per_turn_are_capped(self):
        from lib.config import get_config
        harness = get_config().setdefault("harness", {})
        original = harness.get("max_calls_per_turn")
        harness["max_calls_per_turn"] = 2
        try:
            result, _ = self.go(self.provider([
                call_turn(("LS", {}), ("LS", {}), ("LS", {})),
                final_turn("done"),
            ]))
        finally:
            harness["max_calls_per_turn"] = original or 8
        outcomes = [c["outcome"] for c in result.tool_calls]
        self.assertEqual(outcomes, ["ok", "ok", "refused"])
        self.assertIn("calls-per-turn", result.tool_calls[2]["error"])

    def test_results_are_clipped_to_context_room(self):
        """A turn of large reads must not overflow the window - the endpoint
        would silently drop the task from the front of the conversation."""
        big = self.root / "big.txt"
        big.write_text("line\n" * 800)
        try:
            provider = self.provider([
                call_turn(("Read", {"path": "big.txt"}),
                          ("Read", {"path": "big.txt"}), prompt_tokens=900),
                final_turn("ok", prompt_tokens=2000),
            ], context_window=2600)
            result, provider = self.go(
                provider, self.constraints(max_tokens_per_task=200))
        finally:
            big.unlink()
        self.assertEqual(result.status, TaskStatus.COMPLETE)
        results = [m for m in provider.chat.requests[1]["messages"]
                   if m["role"] == "tool"]
        self.assertEqual(len(results), 2)
        for message in results:
            self.assertIn("[clipped", message["content"])

    def test_no_room_left_asks_for_the_answer(self):
        """Out of room: nothing runs, the model is told to answer, and an
        answer on the next turn completes the task."""
        result, provider = self.go(self.provider(
            [call_turn(("LS", {}), prompt_tokens=3000),
             final_turn("best effort", prompt_tokens=3100)],
            context_window=4096),
            self.constraints(max_tokens_per_task=512))
        self.assertEqual(result.status, TaskStatus.COMPLETE)
        self.assertEqual(result.output, "best effort")
        self.assertEqual(result.tool_calls[0]["outcome"], "refused")
        self.assertIn("final answer now",
                      provider.chat.requests[1]["messages"][-1]["content"])

    def test_calling_on_after_wrap_up_fails(self):
        result, _ = self.go(self.provider(
            [call_turn(("LS", {}), prompt_tokens=3000),
             call_turn(("LS", {}), prompt_tokens=3100)],
            context_window=4096),
            self.constraints(max_tokens_per_task=512))
        self.assertEqual(result.status, TaskStatus.FAILED)
        self.assertIn("context exhausted", result.error)

    def test_low_water_warns_once(self):
        """Results that leave little room carry the wrap-up note; the
        calls themselves still ran."""
        big = self.root / "mid.txt"
        big.write_text("line\n" * 600)
        try:
            result, provider = self.go(self.provider(
                [call_turn(("Read", {"path": "mid.txt"}), prompt_tokens=900),
                 final_turn("ok", prompt_tokens=2000)], context_window=2600),
                self.constraints(max_tokens_per_task=200))
        finally:
            big.unlink()
        self.assertEqual(result.tool_calls[0]["outcome"], "ok")
        last = provider.chat.requests[1]["messages"][-1]["content"]
        self.assertIn("final answer now", last)

    def test_malformed_arguments_are_refused_not_fatal(self):
        bad = ChatTurn(content="", message={"role": "assistant"},
                       tool_calls=[ToolCall(id="x", name="Read",
                                            arguments=None,
                                            parse_error="arguments are not "
                                                        "valid JSON")])
        result, _ = self.go(self.provider([bad, final_turn("ok")]))
        self.assertEqual(result.status, TaskStatus.COMPLETE)
        self.assertEqual(result.tool_calls[0]["outcome"], "refused")

    def test_text_only_mission_takes_single_shot(self):
        provider = self.provider([])
        sent = {}

        def fake_post(payload, timeout):
            sent.update(payload)
            return {"message": {"content": "plain answer"},
                    "prompt_eval_count": 5, "eval_count": 2}

        provider._post = fake_post
        mc = self.constraints(allowed_tools=[])
        task = Task(id="t1", agent_id="a1", prompt="p", role="r",
                    tool_grants=["grep"])
        result = provider.execute_sync("PERSONA", task, mc)
        self.assertEqual(result.output, "plain answer")
        self.assertNotIn("tools", sent)
        self.assertIn("NO TOOLS AVAILABLE", sent["messages"][1]["content"])
        self.assertEqual(provider.chat.requests, [])

    def test_single_shot_answer_cut_at_output_limit_fails(self):
        # Observed on bonsai: HTTP 200, done_reason "length", partial text
        provider = self.provider([])
        provider._post = lambda payload, timeout: {
            "message": {"content": "1, 2, 3,"}, "done_reason": "length",
            "prompt_eval_count": 17, "eval_count": 8}
        task = Task(id="t1", agent_id="a1", prompt="p", role="r",
                    tool_grants=[])
        result = provider.execute_sync(
            "PERSONA", task, self.constraints(allowed_tools=[],
                                              max_tokens_per_task=8))
        self.assertEqual(result.status, TaskStatus.FAILED)
        self.assertIn("output truncated", result.error)
        self.assertIn("8-token", result.error)
        self.assertEqual(result.output, "1, 2, 3,")

    def test_final_answer_cut_at_output_limit_fails(self):
        cut = final_turn('{"count": 1')
        cut.finish_reason = "length"
        result, _ = self.go(self.provider([cut]))
        self.assertEqual(result.status, TaskStatus.FAILED)
        self.assertIn("output truncated", result.error)

    def test_cut_turn_with_tool_calls_continues(self):
        turn = call_turn(("Glob", {"pattern": "*.py"}))
        turn.finish_reason = "length"
        result, _ = self.go(self.provider([turn, final_turn("2")]))
        self.assertEqual(result.status, TaskStatus.COMPLETE)
        self.assertEqual(result.output, "2")

    def test_execute_routes_harnessed_tasks_to_the_loop(self):
        provider = self.provider([call_turn(("Glob", {"pattern": "*.py"})),
                                  final_turn("2")])
        task = Task(id="t1", agent_id="a1", prompt="p", role="r",
                    tool_grants=[])
        result = provider.execute_sync("PERSONA", task, self.constraints())
        self.assertEqual(result.output, "2")
        self.assertEqual(result.tool_calls[0]["summary"],
                         "2 file(s) match '*.py' under .")


class TestChatTransports(unittest.TestCase):

    def test_ollama_chat_round_trip(self):
        provider = OllamaChatProvider(ProviderConfig(
            name="t", base_uri="https://x.invalid", path="/api/chat",
            model="m", parameters={"temperature": 0.2}))
        payload = provider._chat_payload(
            [{"role": "user", "content": "hi"}], [{"type": "function"}],
            MissionConstraints())
        self.assertEqual(payload["tools"], [{"type": "function"}])
        self.assertEqual(payload["options"]["temperature"], 0.2)
        self.assertFalse(payload["stream"])

        turn = provider._parse_chat({
            "message": {"role": "assistant", "content": "", "tool_calls": [
                {"id": "call_1", "function": {
                    "name": "LS", "arguments": {"path": "."}}}]},
            "prompt_eval_count": 50, "eval_count": 5})
        self.assertEqual(turn.tool_calls[0].name, "LS")
        self.assertEqual(turn.tool_calls[0].arguments, {"path": "."})
        self.assertEqual(turn.tokens, 55)
        self.assertIn("tool_calls", turn.message)
        reply = provider.tool_result_message(turn.tool_calls[0], "listing")
        self.assertEqual(reply, {"role": "tool", "content": "listing",
                                 "tool_name": "LS", "tool_call_id": "call_1"})

    def test_openai_arguments_arrive_as_json_text(self):
        provider = OpenAICompatProvider(ProviderConfig(
            name="t", base_uri="https://x.invalid", path="/v1", model="m"))
        turn = provider._parse_chat({"choices": [{"message": {
            "content": None, "tool_calls": [
                {"id": "a", "type": "function", "function": {
                    "name": "Read", "arguments": "{\"path\": \"x\"}"}},
                {"id": "b", "type": "function", "function": {
                    "name": "Read", "arguments": "{not json"}}]}}],
            "usage": {"prompt_tokens": 9, "completion_tokens": 1}})
        self.assertEqual(turn.tool_calls[0].arguments, {"path": "x"})
        self.assertIsNotNone(turn.tool_calls[1].parse_error)
        self.assertEqual(turn.content, "")
        self.assertEqual(provider.tool_result_message(turn.tool_calls[0], "r"),
                         {"role": "tool", "tool_call_id": "a",
                          "content": "r"})

    def test_ollama_requests_refuse_rather_than_truncate(self):
        # Ollama's default truncate=true drops the oldest messages and still
        # answers 200; observed on bonsai: 3040 tokens sent, 26 evaluated
        config = ProviderConfig(name="t", base_uri="https://x.invalid",
                                path="/api/chat", model="m")
        chat = OllamaChatProvider(config)
        for payload in (chat._payload("s", "p", MissionConstraints()),
                        chat._chat_payload([], [], MissionConstraints()),
                        OllamaProvider(config)._payload(
                            "s", "p", MissionConstraints())):
            self.assertIs(payload["truncate"], False)
            self.assertIs(payload["shift"], False)

    def test_context_refusal_is_named(self):
        import io
        import urllib.error
        body = (b'{"error":"request (3040 tokens) exceeds the available '
                b'context size (1024 tokens)","type":"exceed_context_size_error"}')
        exc = urllib.error.HTTPError("https://x.invalid", 400, "Bad Request",
                                     {}, io.BytesIO(body))
        provider = OllamaChatProvider(ProviderConfig(
            name="t", base_uri="https://x.invalid", path="/api/chat", model="m"))
        self.assertTrue(provider.describe_error(exc).startswith(
            "context exceeded"))

    def test_finish_reasons_are_read_as_reported(self):
        ollama = OllamaChatProvider(ProviderConfig(
            name="t", base_uri="https://x.invalid", path="/api/chat", model="m"))
        turn = ollama._parse_chat({"message": {"content": "x"},
                                   "done_reason": "length"})
        self.assertEqual(turn.finish_reason, "length")
        self.assertEqual(ollama._finish_reason({"done_reason": "stop"}), "stop")
        openai = OpenAICompatProvider(ProviderConfig(
            name="t", base_uri="https://x.invalid", path="/v1", model="m"))
        body = {"choices": [{"message": {"content": "x"},
                             "finish_reason": "length"}]}
        self.assertEqual(openai._parse_chat(body).finish_reason, "length")
        self.assertEqual(openai._finish_reason(body), "length")
        self.assertIsNone(truncation_error("stop", 10))
        self.assertIsNone(truncation_error(None, 10))
        self.assertIsNotNone(truncation_error("max_tokens", 10))

    def test_parse_arguments(self):
        self.assertEqual(parse_arguments({"a": 1}), ({"a": 1}, None))
        self.assertEqual(parse_arguments(""), ({}, None))
        self.assertEqual(parse_arguments('{"a": 1}'), ({"a": 1}, None))
        self.assertIsNotNone(parse_arguments("[1]")[1])


class TestVerification(unittest.TestCase):

    def test_contains(self):
        exp = Expectation("contains", "26")
        self.assertTrue(verify(exp, "there are 26 files").passed)
        self.assertFalse(verify(exp, "there are 32 files").passed)

    def test_json_subset_from_fenced_prose(self):
        exp = Expectation("json_subset", {"json_files": 26,
                                          "names": ["b", "a"]})
        text = 'Sure:\n```json\n{"json_files": 26, "names": ["a", "b"], ' \
               '"extra": true}\n```'
        self.assertTrue(verify(exp, text).passed)
        wrong = verify(exp, '{"json_files": 32, "names": ["a"]}')
        self.assertFalse(wrong.passed)
        self.assertIn("$.json_files: expected 26, got 32", wrong.detail)
        self.assertIn("missing \"b\"", wrong.detail)
        self.assertFalse(verify(exp, "no json here").passed)

    def test_mismatch_paths(self):
        self.assertEqual(json_mismatches({"a": {"b": 1}}, {"a": {"b": 1}}), [])
        self.assertEqual(json_mismatches({"a": 1}, {}), ["$.a: missing"])
        self.assertIsNone(extract_json_object("[1, 2]"))

    def test_kind_is_validated(self):
        with self.assertRaises(ValueError):
            Expectation("telepathy", 1)
        with self.assertRaises(ValueError):
            Expectation("json_subset", [1])


class FixedProvider:
    """Answers every task with the same text."""

    def __init__(self, text):
        self.text = text

    def is_available(self):
        return True

    async def execute(self, system_prompt, task, constraints):
        from lib.dispatch.mission import TaskResult
        return TaskResult(task_id=task.id, agent_id=task.agent_id,
                          status=TaskStatus.COMPLETE, output=self.text)


class TestVerificationBeforePayroll(DatastoreCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        from lib.data.generator import AgentGenerator
        cls.agent = cls.ds.create("agent", AgentGenerator(cls.ds).generate(),
                                  validate=False)

    def dispatch(self, answer):
        from lib.dispatch.dispatcher import Dispatcher
        from lib.dispatch.ops_log import DispatchLogger
        mission = Mission(title="kat", crew_ids=[self.agent["id"]],
                          constraints=MissionConstraints(allowed_tools=[]))
        mission.add_task(Task(
            id="t1", agent_id=self.agent["id"], prompt="count", role="r",
            expect=Expectation("json_subset", {"n": 26}).to_dict()))
        events = []
        result = Dispatcher(
            datastore=self.ds, provider=FixedProvider(answer),
            logger=DispatchLogger(log_path=self.tmp / "v.log",
                                  json_log_path=self.tmp / "v.json"),
        ).execute_sync(mission, on_event=events.append)
        return result, events

    def test_wrong_answer_fails_and_is_not_paid(self):
        before = self.ds.read("agent", self.agent["id"])["economy"]
        result, events = self.dispatch('{"n": 32}')
        self.assertEqual(result.status, MissionStatus.FAILED)
        task = result.tasks[0]
        self.assertTrue(task.result.error.startswith("verification_failed"))
        self.assertFalse(task.result.verdict["passed"])
        self.assertEqual(task.result.output, '{"n": 32}')
        after = self.ds.read("agent", self.agent["id"])["economy"]
        self.assertEqual(before.get("xp"), after.get("xp"))
        self.assertFalse(any(e.event_type == "payroll" for e in events))
        # an honest miss is not misconduct
        conduct = self.ds.read("agent", self.agent["id"]).get("conduct") or {}
        self.assertEqual(conduct.get("violations", 0), 0)
        self.assertIn("Known-answer check:** FAIL", result.final_report)

    def test_right_answer_is_paid(self):
        result, events = self.dispatch('answer: {"n": 26}')
        self.assertEqual(result.status, MissionStatus.COMPLETE)
        self.assertTrue(result.tasks[0].result.verdict["passed"])
        self.assertTrue(any(e.event_type == "payroll" for e in events))
        self.assertTrue(any(e.event_type == "verify" for e in events))

    def test_expectation_round_trips_through_saved_mission(self):
        result, _ = self.dispatch('{"n": 26}')
        result.save(self.tmp / "kat.json")
        loaded = Mission.load(self.tmp / "kat.json")
        self.assertEqual(loaded.tasks[0].expect["kind"], "json_subset")
        self.assertTrue(loaded.tasks[0].result.verdict["passed"])


class TestConformanceSuite(DatastoreCase):

    def test_answers_derive_from_the_fixture(self):
        from lib.dispatch.conformance import cases
        expected = {c.slug: c.expected for c in cases()}
        self.assertEqual(expected["count_top"], {"entries": 7})
        self.assertEqual(expected["count_recursive"], {"py_files": 5})
        self.assertEqual(expected["names_prefix"],
                         {"files": ["limits.json", "logging.json"]})
        self.assertEqual(expected["locate_content"], {"path": "docs/notes.md"})
        self.assertEqual(expected["text_count"]["json_files"], 11)

    def test_text_only_channel_runs_text_cases(self):
        from lib.dispatch.conformance import cases, run_suite
        answers = {c.prompt: c.expected for c in cases()}

        class Oracle:
            ds = self.ds

            async def execute(self, system_prompt, task, constraints):
                import json as _json
                from lib.dispatch.mission import TaskResult
                return TaskResult(task_id=task.id, agent_id=task.agent_id,
                                  status=TaskStatus.COMPLETE,
                                  output=_json.dumps(answers[task.prompt]))

        card = run_suite(Oracle(), runs=2)
        self.assertEqual(card["mode"], "text")
        self.assertEqual((card["passed"], card["total"]), (2, 2))
        self.assertEqual(card["rate"], 1.0)


if __name__ == "__main__":
    unittest.main()
