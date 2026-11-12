"""Platform records (lib/platform): capture from a faked host, render the
unit, detect drift, refuse to act on another host, and keep the tracked
records schema-valid."""

import json
import plistlib
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from tests.support import REPO, DatastoreCase

from lib import platform as platforms
from lib.cli import platform as platform_cli
from lib.platform import launchd, ollama

UNIT = {
    "Label": "homebrew.mxcl.ollama",
    "ProgramArguments": ["/opt/homebrew/opt/ollama/bin/ollama", "serve"],
    "EnvironmentVariables": {"OLLAMA_HOST": "0.0.0.0:11434",
                             "OLLAMA_KEEP_ALIVE": "-1", "HF_TOKEN": "secret"},
    "KeepAlive": True, "RunAtLoad": True,
    "StandardErrorPath": "/opt/homebrew/var/log/ollama.log",
}
SERVE = {"Services": {"svc:mux": {"Web": {"mux.example.ts.net:443": {
    "Handlers": {"/": {"Proxy": "http://127.0.0.1:11434"}}}}}}}
API = {
    "/api/version": {"version": "0.33.2"},
    "/api/tags": {"models": [{"name": "m:1b", "digest": "abc", "size": 10,
                              "details": {"family": "f", "parameter_size": "1B",
                                          "quantization_level": "Q4",
                                          "context_length": 4096},
                              "capabilities": ["completion", "tools"]}]},
    "/api/show": {"parameters": 'temperature                    0.7\n'
                                'stop                           "<|a|>"\n'
                                'stop                           "<|b|>"'},
    "/api/ps": {"models": [{"name": "m:1b", "context_length": 4096,
                            "expires_at": "volatile"}]},
}


def fake_runner(argv, timeout=15.0):
    if argv[:2] == ["tailscale", "status"]:
        return json.dumps({"Self": {"DNSName": "box.example.ts.net."}})
    if argv[:3] == ["tailscale", "serve", "status"]:
        return json.dumps(SERVE)
    if argv[0].endswith("socketfilterfw"):
        return f"Incoming connection to {argv[-1]} is blocked."
    if argv[:2] == ["sysctl", "-n"]:
        return "17179869184" if argv[2] == "hw.memsize" else "Test CPU"
    return None


def fake_fetch(url, payload=None, timeout=10.0):
    return API.get("/" + url.split("/", 3)[3])


class CaptureTest(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="nim-platform-"))
        with open(self.tmp / "homebrew.mxcl.ollama.plist", "wb") as f:
            plistlib.dump(UNIT, f)
        self.record = ollama.capture("box_ollama", host_name="box",
                                     runner=fake_runner, fetch=fake_fetch,
                                     agents_dir=self.tmp)

    def test_settings_are_allowlisted(self):
        env = self.record["service"]["environment"]
        self.assertEqual(env, {"OLLAMA_HOST": "0.0.0.0:11434", "OLLAMA_KEEP_ALIVE": "-1"})
        self.assertNotIn("secret", json.dumps(self.record))

    def test_host_exposure_and_artifacts(self):
        r = self.record
        self.assertEqual(r["host"]["tailnet_name"], "box.example.ts.net")
        self.assertEqual(r["software"]["install"]["method"], "homebrew")
        self.assertEqual(r["exposure"]["bind"], "0.0.0.0:11434")
        self.assertEqual(r["exposure"]["proxies"][0]["url"], "https://mux.example.ts.net")
        self.assertEqual(r["exposure"]["guards"][0]["kind"], "alf_block_incoming")
        model = r["artifacts"][0]
        self.assertEqual((model["digest"], model["context_length"]), ("abc", 4096))
        self.assertEqual(model["parameters"], {"temperature": 0.7, "stop": ["<|a|>", "<|b|>"]})
        self.assertNotIn("expires_at", json.dumps(r["served"]))   # no churn

    def test_bind_normalization(self):
        self.assertEqual(ollama.normalize_bind(None), "127.0.0.1:11434")
        self.assertEqual(ollama.normalize_bind("http://0.0.0.0"), "0.0.0.0:11434")
        self.assertEqual(ollama.api_base("0.0.0.0:11434"), "http://127.0.0.1:11434")


class RenderDriftTest(unittest.TestCase):

    def record(self, **service):
        return {"slug": "box_ollama", "vendor_slug": "ollama",
                "host": {"name": "box", "hostname": "box"},
                "software": {"version": "1", "install": {"method": "binary"}},
                "service": {"manager": "launchd", "label": "org.metasyndicate.nim.box_ollama",
                            "program": ["/bin/ollama", "serve"],
                            "environment": {"OLLAMA_HOST": "0.0.0.0:11434"},
                            "replaces": ["homebrew.mxcl.ollama"], **service},
                "exposure": {"bind": "0.0.0.0:11434",
                             "guards": [{"kind": "alf_block_incoming", "target": "/bin/ollama"}]},
                "artifacts": [{"kind": "model", "name": "m", "digest": "a"}]}

    def test_render_is_the_record(self):
        path, text = launchd.render(self.record(), Path("/agents"))
        self.assertEqual(path, "/agents/org.metasyndicate.nim.box_ollama.plist")
        unit = plistlib.loads(text.encode())
        self.assertEqual(unit["EnvironmentVariables"], {"OLLAMA_HOST": "0.0.0.0:11434"})
        self.assertEqual(unit["ProgramArguments"], ["/bin/ollama", "serve"])
        self.assertTrue(any("homebrew.mxcl.ollama" in s for s in launchd.plan(self.record())))

    def test_drift_names_each_difference(self):
        want = self.record()
        live = json.loads(json.dumps(want))
        self.assertEqual(platforms.drift(want, live), [])
        live["service"]["environment"]["OLLAMA_HOST"] = "127.0.0.1:11434"
        live["artifacts"][0]["digest"] = "b"
        live["exposure"]["guards"] = []
        problems = "\n".join(platforms.drift(want, live))
        for needle in ("settings", "digest a recorded, b on host", "not enforced"):
            self.assertIn(needle, problems)
        self.assertIn("--blockapp /bin/ollama", " ".join(platforms.guard_commands(want)))

    def test_unknown_vendor_is_named(self):
        with self.assertRaises(platforms.PlatformError) as ctx:
            platforms.capture("comfyui", "x")
        self.assertIn("lib/platform/comfyui.py", str(ctx.exception))


class CommandTest(DatastoreCase):

    def test_apply_and_verify_refuse_another_host(self):
        record = RenderDriftTest().record()
        record["host"]["hostname"] = "somewhere-else"
        with mock.patch.object(platform_cli, "_find", return_value=record), \
             mock.patch("sys.stderr"):
            args = SimpleNamespace(slug="box_ollama", yes=True)
            self.assertEqual(platform_cli.cmd_apply(args), 1)
            self.assertEqual(platform_cli.cmd_verify(args), 1)


class TrackedRecordsTest(unittest.TestCase):

    def test_tracked_platforms_conform_and_carry_no_secrets(self):
        from lib.data.schema import get_schema_manager
        sm = get_schema_manager()
        paths = list((REPO / "data/agency/data/platforms").glob("*.json"))
        self.assertTrue(paths)
        for path in paths:
            record = json.loads(path.read_text())
            env = record["service"].get("environment") or {}
            self.assertTrue(all(k.startswith("OLLAMA_") for k in env)
                            or record["vendor_slug"] != "ollama", path.name)
            if sm.can_validate():
                self.assertEqual(sm.validate("platform", record), [], path.name)


if __name__ == "__main__":
    unittest.main()
