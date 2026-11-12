"""Local inference ports: the Agency harness function's allowlist, toolset
resolution from the catalog, and loopback ports bound to their host."""

import json
import socket
import tempfile
import unittest
from pathlib import Path

from tests.support import DatastoreCase

from lib.dispatch.harness import (ExecutionPolicy, Executor, offered_functions,
                                  resolve_toolset)
from lib.dispatch.mission import MissionConstraints
from lib.dispatch.provider import HttpProvider, ProviderConfig, bound_host


class AgencyFunctionTest(DatastoreCase):

    def executor(self):
        row = next(r for r in self.ds.load_reference("harness_function")
                   if r["function_name"] == "Agency")
        return Executor(ExecutionPolicy(root=Path(self.tmp), offered={"Agency": row},
                                        hazards={"Agency": "disclosure"},
                                        call_timeout=60))

    def test_refusals_are_results(self):
        ex = self.executor()
        cases = {
            "agent generate --format json": "--dry-run",
            "data new agent --set /x=1": "--dry-run",
            "agent credit someone 5": "not an allowed",
            "rm -rf /": "not an allowed",
            "data show credential abc": "operator-local",
            "data new agent --dry-run --from /etc/passwd": "--from",
            "data edit agent x --dry-run --yes": "--yes",
            'agent show "unterminated': "cannot parse",
        }
        for command, needle in cases.items():
            outcome = ex.run("Agency", {"command": command})
            self.assertEqual(outcome.outcome, "refused", command)
            self.assertIn(needle, outcome.content, command)

    def test_inspection_runs_the_cli(self):
        outcome = self.executor().run("Agency", {"command": "nim.agency data schemas"})
        self.assertEqual(outcome.outcome, "ok", outcome.content[:300])
        self.assertIn("exit 0", outcome.summary)
        self.assertIn("platform", outcome.content)
        self.assertNotIn("\x1b[", outcome.content)

    def test_offered_only_when_the_mission_names_it(self):
        channel = {"slug": "c", "harness_profile": "agency_introspect_compact",
                   "capabilities": ["text_generation", "tool_use",
                                    "filesystem_read", "agency_cli"]}
        read_only = MissionConstraints(allowed_tools=["Read", "LS"])
        names = {r["function_name"] for r in offered_functions(read_only, channel, [], self.ds)}
        self.assertEqual(names, {"Read", "LS"})
        with_cli = MissionConstraints(allowed_tools=resolve_toolset(
            "agency_introspect_compact", self.ds))
        names = {r["function_name"] for r in offered_functions(with_cli, channel, ["grep"], self.ds)}
        self.assertEqual(names, {"Read", "LS", "Glob", "Grep", "Agency"})


class ToolsetTest(DatastoreCase):

    def test_slug_id_and_file(self):
        self.assertEqual(resolve_toolset("readonly_fs", self.ds),
                         ["Read", "LS", "Glob", "Grep"])
        self.assertEqual(resolve_toolset("4", self.ds)[-1], "Agency")
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump({"functions": ["Read", "LS"]}, f)
        self.addCleanup(Path(f.name).unlink)
        self.assertEqual(resolve_toolset(f.name, self.ds), ["Read", "LS"])

    def test_unknown_names_the_catalog(self):
        with self.assertRaises(ValueError) as ctx:
            resolve_toolset("nope", self.ds)
        self.assertIn("readonly_fs", str(ctx.exception))


class LoopbackPortTest(DatastoreCase):

    def platform(self, hostname):
        return self.ds.create("platform", {
            "slug": "x_ollama", "vendor_slug": "ollama",
            "host": {"name": "x", "hostname": hostname},
            "software": {"version": "1", "install": {"method": "binary"}},
            "service": {"manager": "manual", "label": "", "program": ["ollama", "serve"]},
            "exposure": {"bind": "0.0.0.0:11434"}}, validate=False)

    def test_loopback_port_is_bound_to_its_platform_host(self):
        here = self.platform(socket.gethostname())
        away = self.platform("some-other-host")
        loop = {"endpoint": {"base_uri": "http://127.0.0.1:11434", "platform_id": away["id"]}}
        self.assertEqual(bound_host(loop, self.ds), "some-other-host")
        provider = HttpProvider(ProviderConfig(name="x:c", base_uri="http://127.0.0.1:11434",
                                               bound_host="some-other-host"))
        self.assertFalse(provider.is_available())
        self.assertIn("some-other-host", provider.unavailable_reason)
        loop["endpoint"]["platform_id"] = here["id"]
        provider.config.bound_host = bound_host(loop, self.ds)
        self.assertTrue(provider.is_available())

    def test_network_ports_are_not_bound(self):
        away = self.platform("some-other-host")
        tailnet = {"endpoint": {"base_uri": "https://mux.example.ts.net",
                                "platform_id": away["id"]}}
        self.assertIsNone(bound_host(tailnet, self.ds))


if __name__ == "__main__":
    unittest.main()
