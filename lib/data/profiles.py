"""
Profiles - inventory a target project's agent-facing documents and sweep
them for drift (plan.101526.profiles.md, C1: the mechanical scan).

    Scan     walk the target, classify every markdown file by doc_kind (first
             match by sort_order), read its frontmatter, hash it, record its
             git state and its date (git commit date when tracked, else mtime)
    Refs     extract links and path-like code spans, resolve them against the
             document's directory, then the target root, then a basename index
    Detect   the mechanical finding kinds: dead_ref, ambiguous_ref, duplicate
             (exact and near), stale, orphan, noncompliant, untracked_substrate,
             unreadable. Semantic kinds (conflict, mixed_form) are C3's
    Report   counts, exclusions, findings; markdown + JSON

Everything a detector compares against is data: doc_kind globs, frontmatter
contracts, size limits, finding severities. Thresholds and exclusions are
config (etc/agency.json `profiles`). A scan never edits the target.
"""

import hashlib
import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Optional

from ..config import get_config

AGENT_FACING = {"instruction", "directive", "skill", "prompt", "access", "persona"}
CODE_SPAN = re.compile(r"`([^`\s]+)`")
LINK = re.compile(r"\[[^\]]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
PATHLIKE = re.compile(r"^[A-Za-z0-9_.@+-][A-Za-z0-9_.@+/-]*$")
DOMAIN = re.compile(r"^(?:www\.)?[a-z0-9-]+(?:\.[a-z0-9-]+)*\.(?:com|org|net|io|dev|ai|app|edu|gov|science|info|co|us|uk)(?:/|$)", re.I)
EXTENSIONS = {".md", ".py", ".json", ".sh", ".yml", ".yaml", ".toml", ".txt",
              ".sql", ".js", ".ts", ".html", ".css", ".php", ".swift", ".csv"}


class ProfilesError(RuntimeError):
    """A scan that cannot run, with the remedy."""


def glob_regex(pattern: str) -> re.Pattern:
    """A path glob as a regex: ** spans directories, * and ? do not."""
    out, i = "", 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out, i = out + "(?:.*/)?", i + 3
        elif pattern.startswith("**", i):
            out, i = out + ".*", i + 2
        elif pattern[i] == "*":
            out, i = out + "[^/]*", i + 1
        elif pattern[i] == "?":
            out, i = out + "[^/]", i + 1
        else:
            out, i = out + re.escape(pattern[i]), i + 1
    return re.compile(f"^{out}$")


def utc(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, timezone.utc).isoformat()


@dataclass
class Doc:
    """One scanned document, before it becomes a record."""
    path: str
    kind: dict
    role: str
    data: bytes
    git_state: str = "none"
    modified: str = ""
    dated_by: str = "mtime"
    frontmatter: Optional[dict] = None
    frontmatter_error: Optional[str] = None
    text: str = ""
    refs: list = field(default_factory=list)
    id: Optional[str] = None

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.data).hexdigest()

    @property
    def agent_facing(self) -> bool:
        return bool(AGENT_FACING & set(self.kind.get("substrate_kinds", [])))

    def record(self, tag: str, scan_id: str) -> dict:
        return {"tag": tag, "scan_id": scan_id, "path": self.path,
                "doc_kind_slug": self.kind["slug"], "doc_form_slug": None,
                "source_role_slug": self.role, "git_state": self.git_state,
                "sha256": self.sha256, "bytes": len(self.data),
                "lines": self.text.count("\n") + (1 if self.text and not self.text.endswith("\n") else 0),
                "frontmatter": self.frontmatter, "refs": self.refs,
                "dated": {"modified": self.modified, "source": self.dated_by}}


class Profiles:
    """Scan one target into documents and findings for a tag."""

    class Scan:
        """Walk, classify, read, and date a target's markdown."""

        def __init__(self, root: Path, kinds: list, exclude: list, max_bytes: int):
            self.root = root
            self.kinds = [(k, [glob_regex(p) for p in k["patterns"]])
                          for k in sorted(kinds, key=lambda k: k["sort_order"])]
            self.exclude = [(p, glob_regex(p)) for p in exclude]
            self.max_bytes = max_bytes
            self.files: list = []          # every non-excluded file (relative posix)
            self.excluded: dict = {}       # exclude pattern -> count of pruned entries
            self.pruned: set = set()       # excluded paths: they exist, they are just not walked
            self.skipped: list = []        # (path, why) for markdown not read

        def excluded_by(self, rel: str) -> Optional[str]:
            name = rel.rsplit("/", 1)[-1]
            for glob, regex in self.exclude:
                if regex.match(name) or regex.match(rel):
                    return glob
            return None

        def walk(self) -> None:
            for current, dirs, files in os.walk(self.root):
                base = Path(current).relative_to(self.root).as_posix()
                base = "" if base == "." else base + "/"
                kept = []
                for d in sorted(dirs):
                    hit = self.excluded_by(base + d)
                    if hit:
                        self.excluded[hit] = self.excluded.get(hit, 0) + 1
                        self.pruned.add(base + d)
                    elif not os.path.islink(os.path.join(current, d)):
                        kept.append(d)
                dirs[:] = kept
                for f in sorted(files):
                    rel = base + f
                    hit = self.excluded_by(rel)
                    if hit:
                        self.excluded[hit] = self.excluded.get(hit, 0) + 1
                        self.pruned.add(rel)
                    else:
                        self.files.append(rel)

        def classify(self, rel: str) -> Optional[dict]:
            for kind, regexes in self.kinds:
                if any(r.match(rel) for r in regexes):
                    return kind
            return None

        def read(self, role: str) -> list:
            docs = []
            for rel in self.files:
                kind = self.classify(rel)
                if kind is None:
                    continue
                path = self.root / rel
                if path.is_symlink() and not path.exists():
                    doc = Doc(rel, kind, role, b"")
                    doc.modified = utc(path.lstat().st_mtime)
                    doc.frontmatter_error = f"symlink to a missing target: {os.readlink(path)}"
                    docs.append(doc)
                    continue
                size = path.stat().st_size
                if size > self.max_bytes:
                    self.skipped.append((rel, f"{size} bytes > profiles.max_file_bytes"))
                    continue
                doc = Doc(rel, kind, role, path.read_bytes())
                doc.modified = utc(path.stat().st_mtime)
                try:
                    doc.text = doc.data.decode("utf-8")
                except UnicodeDecodeError as exc:
                    doc.frontmatter_error = f"not UTF-8 ({exc.reason} at byte {exc.start})"
                    docs.append(doc)
                    continue
                Profiles.Frontmatter.read(doc)
                docs.append(doc)
            return docs

    class Frontmatter:
        """YAML frontmatter between leading --- fences (PyYAML, D6)."""

        @staticmethod
        def available() -> bool:
            import importlib.util
            return importlib.util.find_spec("yaml") is not None

        @staticmethod
        def read(doc: Doc) -> None:
            if not doc.text.startswith("---"):
                return
            lines = doc.text.split("\n")
            if lines[0].strip() != "---":
                return
            end = next((i for i, line in enumerate(lines[1:], 1) if line.strip() == "---"), None)
            if end is None:
                doc.frontmatter_error = "frontmatter opened with --- but never closed"
                return
            import yaml
            try:
                value = yaml.safe_load("\n".join(lines[1:end]))
            except yaml.YAMLError as exc:
                doc.frontmatter_error = f"frontmatter is not valid YAML: {str(exc).splitlines()[0]}"
                return
            if value is None:
                doc.frontmatter = {}
            elif isinstance(value, dict):
                doc.frontmatter = json.loads(json.dumps(value, default=str))
            else:
                doc.frontmatter_error = f"frontmatter is a {type(value).__name__}, not a mapping"

    class Git:
        """Tracked / untracked / ignored, and commit dates, in three git calls."""

        def __init__(self, root: Path):
            self.root = root
            self.git = shutil.which("git")
            self.tracked, self.untracked, self.dates = set(), set(), {}
            self.inside = False
            if not self.git:
                return
            top = self.run("rev-parse", "--show-toplevel")
            if top is None:
                return
            self.inside = True
            self.tracked = set(filter(None, (self.run("ls-files", "-z") or "").split("\0")))
            self.untracked = set(filter(None, (self.run(
                "ls-files", "-z", "--others", "--exclude-standard") or "").split("\0")))
            stamp = None
            for line in (self.run("log", "--format=@%cI", "--name-only", "--relative") or "").splitlines():
                if line.startswith("@"):
                    stamp = line[1:]
                elif line and stamp and line not in self.dates:
                    self.dates[line] = stamp

        def run(self, *args) -> Optional[str]:
            done = subprocess.run([self.git, "-C", str(self.root), *args],
                                  capture_output=True, text=True)
            return done.stdout if done.returncode == 0 else None

        def state(self, rel: str) -> str:
            if not self.inside:
                return "none"
            if rel in self.tracked:
                return "tracked"
            return "untracked" if rel in self.untracked else "ignored"

        def date(self, rel: str, root: Path) -> tuple:
            if rel in self.dates:
                return self.dates[rel], "git"
            return utc((root / rel).lstat().st_mtime), "mtime"

    class Refs:
        """References a document makes, resolved within the target."""

        TRY = ("", ".py", ".md", ".json", ".sh")

        def __init__(self, root: Path, files: list, archived=lambda rel: False, pruned=()):
            self.root = root
            self.files = set(files)
            self.pruned = set(pruned)
            self.dirs = set()
            for f in files:
                parts = PurePosixPath(f).parts[:-1]
                self.dirs.update("/".join(parts[:i]) for i in range(1, len(parts) + 1))
            self.archived = archived
            self.by_name: dict = {}
            for f in files:
                self.by_name.setdefault(f.rsplit("/", 1)[-1], []).append(f)

        @staticmethod
        def candidates(text: str) -> list:
            """(target, line, form) for links and path-like code spans."""
            found = []
            for number, line in enumerate(text.split("\n"), 1):
                for match in LINK.finditer(line):
                    target = match.group(1).split("#", 1)[0]
                    if target and "://" not in target and not target.startswith("mailto:") \
                            and not DOMAIN.match(target):
                        found.append((target, number, "link"))
                for match in CODE_SPAN.finditer(line):
                    token = match.group(1).rstrip(".,:;)")
                    if Profiles.Refs.pathlike(token):
                        found.append((token, number, "path"))
            return found

        @staticmethod
        def pathlike(token: str) -> bool:
            if not PATHLIKE.match(token) or token.startswith(("-", "/", "~", ".git/")) \
                    or DOMAIN.match(token):
                return False
            if "/" in token:
                return not token.endswith("/") or len(token) > 1
            return PurePosixPath(token).suffix.lower() in EXTENSIONS

        def resolve(self, doc_path: str, target: str) -> tuple:
            """(resolved path or None, the matches when ambiguous, bare).
            Tried in order: relative to the document, relative to the root
            (each with common extensions), then any file whose path ends with
            the target. Live files win over archived copies. A bare name
            (no '/') that matches nothing is `bare`: likely illustrative,
            never reported dead."""
            directory = target.endswith("/")
            target = target.rstrip("/")
            tries = ("",) if directory else self.TRY
            base = PurePosixPath(doc_path).parent
            for anchor in (base, PurePosixPath(".")):
                for ext in tries:
                    candidate = os.path.normpath(str(anchor / (target + ext))).replace(os.sep, "/")
                    if candidate.startswith("../"):
                        continue
                    if (candidate in self.files and not directory) or (not ext and candidate in self.dirs) \
                            or any(candidate == p or candidate.startswith(p + "/") for p in self.pruned):
                        return candidate, [], False
            if directory:
                return None, [], "/" not in target
            name = target.rsplit("/", 1)[-1]
            matches = []
            for ext in self.TRY:
                matches += [f for f in self.by_name.get(name + ext, [])
                            if f == target + ext or f.endswith("/" + target + ext)]
            matches = sorted(set(matches))
            live = [m for m in matches if not self.archived(m)]
            pick = live or matches
            if len(pick) == 1:
                return pick[0], [], False
            if len(pick) > 1:
                return None, pick, False
            return None, [], "/" not in target

    class Detect:
        """The mechanical finding kinds."""

        def __init__(self, docs: list, finding_kinds: dict, git, root: Path, settings: dict,
                     reference: Optional[list] = None):
            self.docs = docs
            self.kinds = finding_kinds
            self.git = git
            self.root = root
            self.settings = settings
            self.reference = reference or []
            self.out: list = []

        def add(self, kind: str, title: str, detail: str, subjects: list, evidence: dict,
                record: bool = False) -> None:
            """record: the subject is a dated record (doc_kind.record), true as of
            its date - its dead references and staleness are information."""
            severity = "info" if record and kind in ("dead_ref", "stale") else self.kinds[kind]["severity"]
            self.out.append({"kind_slug": kind, "severity": severity,
                             "title": title, "detail": detail, "subjects": subjects,
                             "evidence": evidence, "status": "open"})

        @staticmethod
        def subject(doc: Doc, line: Optional[int] = None) -> dict:
            return {"entity": "document", "id": doc.id, "path": doc.path, "line": line}

        def run(self) -> list:
            swept = [d for d in self.docs if d.role in ("candidate", "authoritative")]
            self.unreadable(swept)
            self.references(swept)
            self.duplicates(swept)
            self.stale(swept)
            self.orphans(swept)
            self.noncompliant(swept)
            self.untracked(swept)
            return self.out

        def unreadable(self, docs: list) -> None:
            for doc in docs:
                if doc.frontmatter_error:
                    self.add("unreadable", f"Unreadable: {doc.path}", doc.frontmatter_error,
                             [self.subject(doc, 1)], {"error": doc.frontmatter_error})

        def references(self, docs: list) -> None:
            for doc in docs:
                dead = [r for r in doc.refs if r["resolved"] is None
                        and not r.get("ambiguous") and not r.get("bare")]
                if dead:
                    self.add("dead_ref", f"{len(dead)} dead reference(s) in {doc.path}",
                             "; ".join(f"line {r['line']}: {r['target']}" for r in dead[:12])
                             + (" ..." if len(dead) > 12 else ""),
                             [self.subject(doc, dead[0]["line"])],
                             {"refs": [{"target": r["target"], "line": r["line"], "form": r["form"]}
                                       for r in dead]}, record=doc.kind.get("record", False))
                vague = [r for r in doc.refs if r.get("ambiguous")]
                if vague:
                    self.add("ambiguous_ref", f"{len(vague)} ambiguous reference(s) in {doc.path}",
                             "; ".join(f"line {r['line']}: {r['target']} -> {', '.join(r['ambiguous'])}"
                                       for r in vague[:8]),
                             [self.subject(doc, vague[0]["line"])],
                             {"refs": [{"target": r["target"], "line": r["line"],
                                        "matches": r["ambiguous"]} for r in vague]})

        @staticmethod
        def shingles(text: str) -> set:
            lines = set()
            for line in text.split("\n"):
                norm = re.sub(r"\s+", " ", line.strip().lstrip("-*>#0123456789. ").lower())
                if len(norm) >= 24:
                    lines.add(norm)
            return lines

        def duplicates(self, docs: list) -> None:
            by_hash: dict = {}
            for doc in docs:
                by_hash.setdefault(doc.sha256, []).append(doc)
            refs_by_hash: dict = {}
            for path, sha, _ in self.reference:
                refs_by_hash.setdefault(sha, []).append(path)
            for sha, group in by_hash.items():
                elsewhere = refs_by_hash.get(sha, [])
                if len(group) > 1 or elsewhere:
                    paths = [d.path for d in group]
                    self.add("duplicate", f"Identical content: {', '.join(paths[:3])}"
                             + (" ..." if len(paths) > 3 else ""),
                             f"{len(group)} copies in the target"
                             + (f"; also in the reference library: {', '.join(elsewhere[:3])}" if elsewhere else ""),
                             [self.subject(d) for d in group],
                             {"sha256": sha, "paths": paths, "reference": elsewhere, "match": "exact"})
            sets = {d.path: self.shingles(d.text) for d in docs if d.text}
            index: dict = {}
            for path, lines in sets.items():
                for line in lines:
                    index.setdefault(line, set()).add(path)
            pairs: dict = {}
            for paths in index.values():
                if 1 < len(paths) <= 50:
                    ordered = sorted(paths)
                    for i, a in enumerate(ordered):
                        for b in ordered[i + 1:]:
                            pairs[(a, b)] = pairs.get((a, b), 0) + 1
            by_path = {d.path: d for d in docs}
            exact = {(a.path, b.path) for g in by_hash.values() for a in g for b in g}
            floor = self.settings["near_duplicate"]
            for (a, b), shared in sorted(pairs.items()):
                if (a, b) in exact:
                    continue
                small = min(len(sets[a]), len(sets[b]))
                union = len(sets[a] | sets[b])
                if small < 5:
                    continue
                jaccard, contained = shared / union, shared / small
                if jaccard >= floor or contained >= floor:
                    match = "near" if jaccard >= floor else "contained"
                    self.add("duplicate", f"{'Near-identical' if match == 'near' else 'Largely contained'}: "
                             f"{a} and {b}",
                             f"{shared} shared lines; jaccard {jaccard:.2f}, containment {contained:.2f}",
                             [self.subject(by_path[a]), self.subject(by_path[b])],
                             {"paths": [a, b], "shared_lines": shared, "jaccard": round(jaccard, 3),
                              "containment": round(contained, 3), "match": match})

        def stale(self, docs: list) -> None:
            days = self.settings["stale_days"]
            for doc in docs:
                newest, which = None, None
                for ref in doc.refs:
                    target = ref["resolved"]
                    if not target or not (self.root / target).is_file():
                        continue
                    stamp, _ = self.git.date(target, self.root)
                    if newest is None or datetime.fromisoformat(stamp) > datetime.fromisoformat(newest):
                        newest, which = stamp, target
                if newest is None:
                    continue
                gap = (datetime.fromisoformat(newest) - datetime.fromisoformat(doc.modified)).days
                if gap > days:
                    self.add("stale", f"{doc.path} is {gap} days older than what it references",
                             f"last changed {doc.modified[:10]} ({doc.dated_by}); {which} changed {newest[:10]}",
                             [self.subject(doc)],
                             {"document_modified": doc.modified, "dated_by": doc.dated_by,
                              "newest_reference": which, "reference_modified": newest, "days": gap},
                             record=doc.kind.get("record", False))

        def orphans(self, docs: list) -> None:
            loaded = {d.path for d in docs if d.kind.get("harness_slugs")}
            graph = {d.path: {r["resolved"] for r in d.refs if r["resolved"]} for d in docs}
            reached, frontier = set(loaded), list(loaded)
            while frontier:
                for nxt in graph.get(frontier.pop(), ()):
                    if nxt in graph and nxt not in reached:
                        reached.add(nxt)
                        frontier.append(nxt)
            for doc in docs:
                if doc.agent_facing and doc.path not in reached:
                    self.add("orphan", f"No harness reaches {doc.path}",
                             f"{doc.kind['label']}: not loaded by any harness and not referenced "
                             f"from a loaded document", [self.subject(doc)],
                             {"doc_kind": doc.kind["slug"], "hubs": sorted(
                                 d.path for d in docs if d.kind.get("hub"))[:20]})

        def noncompliant(self, docs: list) -> None:
            chains: dict = {}
            for doc in docs:
                problems = []
                contract = doc.kind.get("frontmatter") or {}
                if contract and doc.frontmatter is None and not doc.frontmatter_error:
                    if any(rule.get("required") for rule in contract.values()):
                        problems.append("no frontmatter")
                fm = doc.frontmatter or {}
                for name, rule in contract.items():
                    value = fm.get(name)
                    if value in (None, "") and rule.get("required") and doc.frontmatter is not None:
                        problems.append(f"missing {name}")
                    if isinstance(value, str):
                        if rule.get("max_length") and len(value) > rule["max_length"]:
                            problems.append(f"{name} is {len(value)} chars (max {rule['max_length']})")
                        if rule.get("pattern") and not re.match(rule["pattern"], value):
                            problems.append(f"{name} '{value[:40]}' does not match {rule['pattern']}")
                limit = doc.kind.get("max_bytes")
                if limit and len(doc.data) > limit:
                    problems.append(f"{len(doc.data)} bytes (a reading harness stops at {limit})")
                if limit:
                    chains[doc.path] = (len(doc.data), limit)
                if problems:
                    self.add("noncompliant", f"{doc.path} breaks its {doc.kind['label']} contract",
                             "; ".join(problems), [self.subject(doc, 1)],
                             {"doc_kind": doc.kind["slug"], "problems": problems})
            for path, (size, limit) in chains.items():
                here = PurePosixPath(path).parent.parts
                chain = [p for p in chains
                         if len(PurePosixPath(p).parent.parts) < len(here)
                         and PurePosixPath(p).parent.parts == here[:len(PurePosixPath(p).parent.parts)]]
                total = size + sum(chains[p][0] for p in chain)
                if chain and total > limit:
                    doc = next(d for d in docs if d.path == path)
                    self.add("noncompliant", f"Instruction chain for {path} exceeds {limit} bytes",
                             f"{total} bytes across {len(chain) + 1} files read root to leaf",
                             [self.subject(doc, 1)],
                             {"chain": sorted(chain) + [path], "bytes": total, "limit": limit})

        def untracked(self, docs: list) -> None:
            groups: dict = {}
            for doc in docs:
                if doc.agent_facing and doc.git_state in ("untracked", "ignored"):
                    groups.setdefault(doc.git_state, []).append(doc)
            for state, group in groups.items():
                self.add("untracked_substrate",
                         f"{len(group)} agent-facing document(s) {state} by git",
                         "no history: changes to these leave no trail",
                         [self.subject(d) for d in group],
                         {"git_state": state, "paths": [d.path for d in group]})

    def __init__(self, ds, root: Path, tag: str, role: str = "candidate",
                 exclude: Optional[list] = None, reference: Optional[list] = None,
                 settings: Optional[dict] = None, index_only: Optional[list] = None):
        if not self.Frontmatter.available():
            raise ProfilesError("profiles scan reads YAML frontmatter and needs PyYAML: "
                                "nim.agency install create --group profiles")
        self.ds = ds
        self.root = Path(root).resolve()
        if not self.root.is_dir():
            raise ProfilesError(f"{self.root} is not a directory")
        self.tag = tag
        self.role = role
        config = dict(get_config().get("profiles", {}))
        config.update(settings or {})
        self.settings = config
        self.exclude = list(config["exclude"]) + list(exclude or [])
        self.index_only = [(p, glob_regex(p)) for p in list(config["index_only"]) + list(index_only or [])]
        self.reference_roots = [Path(p).resolve() for p in (reference or [])]
        self.scan_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    def run(self) -> dict:
        """Scan, resolve, detect. Returns the scan (nothing is written)."""
        kinds = self.ds.load_reference("doc_kind")
        scan = self.Scan(self.root, kinds, self.exclude, self.settings["max_file_bytes"])
        scan.walk()
        docs = scan.read(self.role)
        git = self.Git(self.root)
        refs = self.Refs(self.root, scan.files, self.archived, scan.pruned)
        for doc in docs:
            if self.archived(doc.path):
                doc.role = "reference"
            doc.git_state = git.state(doc.path)
            doc.modified, doc.dated_by = git.date(doc.path, self.root)
            for target, line, form in refs.candidates(doc.text):
                resolved, matches, bare = refs.resolve(doc.path, target)
                entry = {"target": target, "line": line, "form": form, "resolved": resolved}
                if matches:
                    entry["ambiguous"] = matches
                if bare and form == "path":
                    entry["bare"] = True
                doc.refs.append(entry)
            doc.id = self.uuid(hashlib.sha1(f"{self.tag}:{doc.path}".encode()).hexdigest())
        reference = [(d.path, d.sha256, d.text) for d in docs if d.role == "reference"]
        for ref_root in self.reference_roots:
            ref_scan = self.Scan(ref_root, kinds, self.exclude, self.settings["max_file_bytes"])
            ref_scan.walk()
            for doc in ref_scan.read("reference"):
                reference.append((f"{ref_root.name}/{doc.path}", doc.sha256, doc.text))
        finding_kinds = {k["slug"]: k for k in self.ds.load_reference("finding_kind")}
        findings = self.Detect(docs, finding_kinds, git, self.root, self.settings, reference).run()
        return {"tag": self.tag, "root": str(self.root), "scan_id": self.scan_id, "role": self.role,
                "docs": docs, "findings": findings, "files": len(scan.files),
                "index_only": sorted({d.path.split("/")[0] for d in docs if d.role == "reference"}),
                "excluded": scan.excluded, "skipped": scan.skipped, "git": git.inside,
                "reference": [str(p) for p in self.reference_roots]}

    def archived(self, rel: str) -> bool:
        """Under an index-only path: matched for duplicates, never swept."""
        parts = rel.split("/")
        prefixes = ["/".join(parts[:i]) for i in range(1, len(parts) + 1)]
        return any(regex.match(p) or regex.match(p.rsplit("/", 1)[-1])
                   for _, regex in self.index_only for p in prefixes[:-1] or prefixes)

    def save(self, result: dict) -> dict:
        """Write the scan into the instance root: this tag's documents are
        replaced; open findings are replaced; findings the operator decided
        (accepted, resolved, wontfix) are kept and not raised again."""
        tag = self.tag
        for old in self.ds.query("document", lambda r: r.get("tag") == tag).data:
            self.ds.delete("document", old["id"], hard=True)
        decided = {}
        for old in self.ds.query("finding", lambda r: r.get("tag") == tag).data:
            if old.get("status") == "open":
                self.ds.delete("finding", old["id"], hard=True)
            else:
                decided[self.fingerprint(old)] = old
        for doc in result["docs"]:
            self.ds.create("document", {**doc.record(tag, result["scan_id"]), "id": doc.id})
        written, kept = 0, 0
        for finding in result["findings"]:
            if self.fingerprint(finding) in decided:
                kept += 1
                continue
            self.ds.create("finding", {"tag": tag, "scan_id": result["scan_id"], **finding})
            written += 1
        return {"documents": len(result["docs"]), "findings": written, "decided_kept": kept}

    @staticmethod
    def uuid(digest: str) -> str:
        """A stable UUID per (tag, path), so re-scans keep document ids."""
        h = digest[:32]
        return f"{h[:8]}-{h[8:12]}-5{h[13:16]}-a{h[17:20]}-{h[20:32]}"

    @staticmethod
    def fingerprint(finding: dict) -> str:
        paths = sorted(s.get("path") or "" for s in finding.get("subjects", []))
        return f"{finding['kind_slug']}|{'|'.join(paths)}|{finding.get('title', '')}"

    class Report:
        """The scan as markdown (for people) and JSON (for tools)."""

        @staticmethod
        def summary(result: dict) -> dict:
            by_kind, by_state, by_finding = {}, {}, {}
            for doc in result["docs"]:
                by_kind[doc.kind["slug"]] = by_kind.get(doc.kind["slug"], 0) + 1
                by_state[doc.git_state] = by_state.get(doc.git_state, 0) + 1
            for f in result["findings"]:
                by_finding[f["kind_slug"]] = by_finding.get(f["kind_slug"], 0) + 1
            swept = [d for d in result["docs"] if d.role != "reference"]
            return {"documents": len(result["docs"]), "swept": len(swept), "files": result["files"],
                    "index_only": result.get("index_only", []),
                    "by_kind": dict(sorted(by_kind.items(), key=lambda kv: -kv[1])),
                    "by_git_state": by_state,
                    "findings": dict(sorted(by_finding.items(), key=lambda kv: -kv[1])),
                    "excluded": result["excluded"], "skipped": result["skipped"]}

        @classmethod
        def as_json(cls, result: dict) -> dict:
            return {"tag": result["tag"], "root": result["root"], "scan_id": result["scan_id"],
                    "summary": cls.summary(result), "reference": result["reference"],
                    "documents": [{**d.record(result["tag"], result["scan_id"]), "id": d.id}
                                  for d in result["docs"]],
                    "findings": result["findings"]}

        @classmethod
        def as_markdown(cls, result: dict) -> str:
            s = cls.summary(result)
            out = [f"# Profiles scan: {result['tag']}", "",
                   f"- root: `{result['root']}`", f"- scan: {result['scan_id']}",
                   f"- files walked: {s['files']}; documents: {s['documents']} ({s['swept']} swept)",
                   f"- index-only (matched for duplicates, never swept): {', '.join(s['index_only']) or 'none'}",
                   f"- git: {'yes' if result['git'] else 'not a work tree'}"]
            if result["reference"]:
                out.append(f"- reference libraries: {', '.join(result['reference'])}")
            out += ["", "## Documents by kind", "", "| kind | count |", "| --- | --- |"]
            out += [f"| {k} | {n} |" for k, n in s["by_kind"].items()]
            out += ["", "## Documents by git state", "", "| state | count |", "| --- | --- |"]
            out += [f"| {k} | {n} |" for k, n in s["by_git_state"].items()]
            out += ["", "## Exclusions", ""]
            out += [f"- `{p}`: {n} pruned" for p, n in s["excluded"].items()] or ["- none"]
            out += [f"- skipped `{p}`: {why}" for p, why in s["skipped"]]
            out += ["", "## Findings", "", "| kind | count |", "| --- | --- |"]
            out += [f"| {k} | {n} |" for k, n in s["findings"].items()] or ["| none | 0 |"]
            severity = {"error": 0, "warn": 1, "info": 2}
            for f in sorted(result["findings"], key=lambda f: (severity[f["severity"]], f["kind_slug"])):
                out += ["", f"### [{f['severity']}] {f['kind_slug']}: {f['title']}", "", f["detail"]]
            return "\n".join(out) + "\n"
