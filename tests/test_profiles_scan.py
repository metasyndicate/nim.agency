"""The mechanical scan (plan.101526.profiles.md C1): a fixture target with one
planted instance of every mechanical finding kind, scanned without and with
git, plus recording, re-scanning, and the operator's decisions surviving."""

import importlib.util
import os
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

from tests.support import DatastoreCase

from lib.data.profiles import Profiles, glob_regex

LONG = "\n".join(f"- rule {n}: keep the registry and the engine in agreement, always" for n in range(12))
FIXTURE = {
    "AGENTS.md": "# Agents\n\nRead [the guide](docs/guide.md) and [gone](docs/missing.md).\n"
                 "Run `fitness.py` before committing. See `.github/instructions/spec.instructions.md`.\n"
                 "Layout: `helper.py` and `scripts/run.sh` (illustrative). Engine: `tools/a/fitness`,"
                 " `a/fitness.py`, `old/notes.md`.\n",
    "docs/guide.md": "# Guide\n\nThe engine lives in `src/code.py`.\n",
    "docs/long.md": "# Long\n\n" + LONG + "\n",
    "notes/copy.md": "# Guide\n\nThe engine lives in `src/code.py`.\n",
    "notes/near.md": "# Near\n\n" + LONG.replace("rule 11", "rule eleven") + "\n",
    ".github/instructions/engine.instructions.md": "# Engine\n\nNo frontmatter here.\n",
    ".github/instructions/spec.instructions.md": "---\napplyTo: '**/*.py'\n---\n# Spec\n",
    "tools/a/fitness.py": "print('a')\n",
    "tools/b/fitness.py": "print('b')\n",
    "tools/x.instructions.md": "---\napplyTo: '**'\n---\n# Nobody loads me\n",
    ".claude/agents/bad.md": "---\nname: a:b\n---\nYou review.\n",
    "broken.md": "---\nfoo: [unclosed\n---\n# Broken\n",
    "src/code.py": "x = 1\n",
    "node_modules/pkg/README.md": "# vendored\n",
    "sub/AGENTS.md": "# Sub\n\n" + ("x" * 20000) + "\n",
    "arc/old/notes.md": "# Old\n\nSee [nothing](nowhere/at/all.md).\n",
    "arc/guide-copy.md": "# Guide\n\nThe engine lives in `src/code.py`.\n",
    "arc/tools/a/fitness.py": "print('archived a')\n",
}
FIXTURE["AGENTS.md"] += "\n" + ("y" * 14000) + "\n"


def needs_yaml(cls):
    return unittest.skipUnless(importlib.util.find_spec("yaml"), "PyYAML not installed")(cls)


def build(root: Path) -> None:
    for rel, text in FIXTURE.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    old = time.time() - 90 * 86400
    os.utime(root / "docs" / "guide.md", (old, old))


class TestGlobs(unittest.TestCase):

    def test_scheme_less_urls_are_not_paths(self):
        from lib.data.profiles import Profiles
        self.assertFalse(Profiles.Refs.pathlike("github.com/AUEASME/easme"))
        self.assertTrue(Profiles.Refs.pathlike("data/doc/README.md"))
        self.assertEqual(Profiles.Refs.candidates("[site](evolab.science)"), [])

    def test_double_star_spans_directories_single_does_not(self):
        self.assertTrue(glob_regex("**/*.md").match("a/b/c.md"))
        self.assertTrue(glob_regex("**/*.md").match("c.md"))
        self.assertFalse(glob_regex("*.md").match("a/c.md"))
        self.assertTrue(glob_regex(".github/agents/**/*.md").match(".github/agents/x/y.md"))


@needs_yaml
class TestScan(DatastoreCase):

    def setUp(self):
        self.target = Path(tempfile.mkdtemp(prefix="nim-target-"))
        self.addCleanup(shutil.rmtree, self.target, True)
        build(self.target)
        outside = Path(tempfile.mkdtemp(prefix="nim-outside-"))
        self.addCleanup(shutil.rmtree, outside, True)
        (outside / "secret.md").write_text("# instance data\n")
        (self.target / "data").mkdir()
        (self.target / "data" / "local").symlink_to(outside)

    def scan(self, **kw) -> dict:
        return Profiles(self.ds, self.target, "fixture", **kw).run()

    def kinds(self, result) -> dict:
        found = {}
        for f in result["findings"]:
            found.setdefault(f["kind_slug"], []).append(f)
        return found

    def test_classification(self):
        result = self.scan()
        kinds = {d.path: d.kind["slug"] for d in result["docs"]}
        self.assertEqual(kinds["AGENTS.md"], "agents_md")
        self.assertEqual(kinds[".github/instructions/spec.instructions.md"], "scoped_instructions")
        self.assertEqual(kinds["tools/x.instructions.md"], "stray")
        self.assertEqual(kinds[".claude/agents/bad.md"], "claude_agent")
        self.assertEqual(kinds["docs/guide.md"], "markdown")
        self.assertNotIn("node_modules/pkg/README.md", kinds)
        self.assertFalse(any(p.startswith("data/local") for p in kinds), "followed a symlink")

    def test_every_mechanical_kind_is_found(self):
        found = self.kinds(self.scan())
        self.assertEqual(sorted(found), sorted(["dead_ref", "ambiguous_ref", "duplicate", "stale",
                                                "orphan", "noncompliant", "unreadable"]))

    def test_dead_and_ambiguous_references(self):
        found = self.kinds(self.scan())
        dead = found["dead_ref"][0]["evidence"]["refs"]
        self.assertEqual([r["target"] for r in dead], ["docs/missing.md", "scripts/run.sh"])
        vague = found["ambiguous_ref"][0]["evidence"]["refs"][0]
        self.assertEqual(vague["matches"], ["tools/a/fitness.py", "tools/b/fitness.py"])

    def test_resolution_extensions_suffixes_and_archives(self):
        doc = [d for d in self.scan()["docs"] if d.path == "AGENTS.md"][0]
        resolved = {r["target"]: r["resolved"] for r in doc.refs}
        self.assertEqual(resolved["tools/a/fitness"], "tools/a/fitness.py")
        self.assertEqual(resolved["a/fitness.py"], "tools/a/fitness.py")   # live beats arc copy
        self.assertEqual(resolved["old/notes.md"], "arc/old/notes.md")     # only an archived match
        self.assertTrue([r for r in doc.refs if r["target"] == "helper.py"][0]["bare"])

    def test_archives_are_indexed_not_swept(self):
        result = self.scan()
        roles = {d.path: d.role for d in result["docs"]}
        self.assertEqual(roles["arc/old/notes.md"], "reference")
        flagged = {s["path"] for f in result["findings"] for s in f["subjects"]}
        self.assertFalse(any(p.startswith("arc/") for p in flagged))
        exact = [f for f in self.kinds(result)["duplicate"] if f["evidence"]["match"] == "exact"][0]
        self.assertEqual(exact["evidence"]["reference"], ["arc/guide-copy.md"])

    def test_duplicates_exact_and_near(self):
        matches = {f["evidence"]["match"]: f["evidence"]["paths"]
                   for f in self.kinds(self.scan())["duplicate"]}
        self.assertEqual(sorted(matches["exact"]), ["docs/guide.md", "notes/copy.md"])
        self.assertEqual(matches["near"], ["docs/long.md", "notes/near.md"])

    def test_stale_against_what_it_references(self):
        stale = self.kinds(self.scan())["stale"]
        self.assertEqual([f["subjects"][0]["path"] for f in stale], ["docs/guide.md"])
        self.assertEqual(stale[0]["evidence"]["newest_reference"], "src/code.py")

    def test_orphans_are_agent_facing_and_unreached(self):
        orphans = [f["subjects"][0]["path"] for f in self.kinds(self.scan())["orphan"]]
        self.assertEqual(orphans, ["tools/x.instructions.md"])

    def test_noncompliance(self):
        problems = {f["subjects"][0]["path"]: f["evidence"]
                    for f in self.kinds(self.scan())["noncompliant"]}
        self.assertIn("no frontmatter", problems[".github/instructions/engine.instructions.md"]["problems"])
        agent = problems[".claude/agents/bad.md"]["problems"]
        self.assertTrue(any("missing description" in p for p in agent))
        self.assertTrue(any("does not match" in p for p in agent))
        self.assertEqual(problems["sub/AGENTS.md"]["chain"], ["AGENTS.md", "sub/AGENTS.md"])

    def test_unreadable_frontmatter(self):
        bad = self.kinds(self.scan())["unreadable"]
        self.assertEqual([f["subjects"][0]["path"] for f in bad], ["broken.md"])

    def test_reference_library_matches_duplicates_and_is_never_swept(self):
        library = Path(tempfile.mkdtemp(prefix="nim-library-"))
        self.addCleanup(shutil.rmtree, library, True)
        (library / "guide.md").write_text(FIXTURE["docs/guide.md"])
        (library / "x.instructions.md").write_text("# unloaded, but only a reference\n")
        found = self.kinds(self.scan(reference=[library]))
        exact = [f for f in found["duplicate"] if f["evidence"]["match"] == "exact"][0]
        self.assertIn(f"{library.name}/guide.md", exact["evidence"]["reference"])
        self.assertFalse(any(library.name in s.get("path", "") for f in found["orphan"]
                             for s in f["subjects"]))

    def test_scan_never_writes_the_target(self):
        before = sorted(str(p) for p in self.target.rglob("*"))
        self.scan()
        self.assertEqual(before, sorted(str(p) for p in self.target.rglob("*")))

    @unittest.skipUnless(shutil.which("git"), "git not installed")
    def test_git_state_dates_and_untracked_substrates(self):
        run = lambda *a: subprocess.run(["git", "-C", str(self.target), *a], check=True,
                                        capture_output=True)
        run("init", "-q")
        (self.target / ".gitignore").write_text(".claude/\ndata/local\n")
        run("add", "-A")
        run("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "fixture")
        result = self.scan()
        states = {d.path: d.git_state for d in result["docs"]}
        self.assertEqual(states["AGENTS.md"], "tracked")
        self.assertEqual(states[".claude/agents/bad.md"], "ignored")
        dated = {d.path: d.dated_by for d in result["docs"]}
        self.assertEqual((dated["AGENTS.md"], dated[".claude/agents/bad.md"]), ("git", "mtime"))
        untracked = self.kinds(result)["untracked_substrate"]
        self.assertEqual(untracked[0]["evidence"]["paths"], [".claude/agents/bad.md"])

    def test_record_rescan_and_decisions_survive(self):
        profiles = Profiles(self.ds, self.target, "fixture")
        first = profiles.save(profiles.run())
        self.assertGreater(first["findings"], 0)
        docs = {d["path"]: d["id"] for d in self.ds.query("document",
                                                          lambda r: r["tag"] == "fixture").data}
        orphan = [f for f in self.ds.query("finding", lambda r: r["tag"] == "fixture").data
                  if f["kind_slug"] == "orphan"][0]
        self.ds.update("finding", orphan["id"], {"status": "wontfix", "resolution": "kept on purpose"})
        again = Profiles(self.ds, self.target, "fixture")
        second = again.save(again.run())
        self.assertEqual(second["decided_kept"], 1)
        self.assertEqual(second["findings"], first["findings"] - 1)
        redo = {d["path"]: d["id"] for d in self.ds.query("document",
                                                          lambda r: r["tag"] == "fixture").data}
        self.assertEqual(docs, redo, "document ids change between scans")
        statuses = [f["status"] for f in self.ds.query("finding", lambda r: r["tag"] == "fixture").data
                    if f["kind_slug"] == "orphan"]
        self.assertEqual(statuses, ["wontfix"])


if __name__ == "__main__":
    unittest.main()
