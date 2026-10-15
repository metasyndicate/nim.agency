"""
Executor - runs the tool calls a rogue syndicate emits, under policy.

This is where mission constraints stop being advisory. A platformed
syndicate (claude_cli) enforces its own sandbox; a bare model endpoint has
none, so every call it emits passes through here and nowhere else:

    confinement   every path resolves (symlinks followed) under the
                  mission's working directory, or the call is refused
    offer         only functions offered to this task may run - the model
                  cannot call its way past the agent/mission intersection
    read_only     a function whose capability hazard is `mutation` is
                  refused outright on a read-only mission
    deadline      each call has its own wall-clock budget, independent of
                  the mission timeout
    no network    no handler performs egress

Refusals are results, not exceptions: a refused call returns a structured
error to the model so it can adapt, and the mission continues.

Handlers are native Python - no shell. Model-derived regexes (Grep
patterns, compiled Glob patterns) run in a separate, killable process
(matcher.py): a catastrophically backtracking pattern cannot be stopped
from a thread, and would otherwise freeze the dispatcher. Only regular
files are read (no FIFOs or devices). Counting functions report exact
counts so the model never has to count a listing itself. `.git`
directories are not walked: VCS internals are not project content.

Stdlib only.
"""

import fnmatch
import json
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

# Directories never walked by Glob/Grep
SKIP_DIRS = {".git"}

# Files larger than this are not searched by Grep (bytes)
GREP_MAX_FILE_BYTES = 2 * 1024 * 1024

# Default page size for Read (lines)
READ_DEFAULT_LIMIT = 400

# Most matches Grep returns (the result is clipped further downstream)
GREP_MAX_HITS = 1000

# Longest Glob pattern accepted (characters)
GLOB_MAX_PATTERN = 512

# The regex worker, run as a script in isolated mode (stdlib only)
MATCHER = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "matcher.py")


class CallRefused(Exception):
    """Policy refused the call. The message goes back to the model."""


class CallFailed(Exception):
    """The call was allowed but could not complete (missing file, bad
    regex, deadline). The message goes back to the model."""


@dataclass
class ExecutionPolicy:
    """Everything the executor enforces, fixed for one task."""
    root: Path
    read_only: bool = True
    call_timeout: float = 30.0
    max_result_chars: int = 16000
    # function_name -> harness_function row, already intersected
    offered: dict[str, dict] = field(default_factory=dict)
    # function_name -> capability hazard (from channel_capability)
    hazards: dict[str, str] = field(default_factory=dict)


@dataclass
class CallOutcome:
    """What happened to one call. `content` is what the model sees."""
    outcome: str            # ok | refused | error
    content: str
    summary: str = ""

    @property
    def ok(self) -> bool:
        return self.outcome == "ok"


class Executor:
    """Runs offered harness functions for one task."""

    def __init__(self, policy: ExecutionPolicy):
        self.policy = policy
        self.root = policy.root.resolve()

    # -- public -------------------------------------------------------------

    def run(self, function_name: str, arguments: Optional[dict]) -> CallOutcome:
        """Execute one call. Never raises for policy or call errors."""
        try:
            if not isinstance(function_name, str):
                raise CallRefused("function name must be a string")
            row = self._admit(function_name)
            if not isinstance(arguments, dict):
                raise CallRefused("arguments must be a JSON object")
            self._check_arguments(row, arguments)
            handler = getattr(self, f"_fn_{row['handler']}", None)
            if handler is None:
                raise CallRefused(
                    f"function '{function_name}' has no executor handler")
            deadline = time.monotonic() + self.policy.call_timeout
            content, summary = handler(arguments, deadline)
            return CallOutcome("ok", self._clip(content), summary)
        except CallRefused as exc:
            return CallOutcome("refused", f"REFUSED: {exc}", str(exc))
        except CallFailed as exc:
            return CallOutcome("error", f"ERROR: {exc}", str(exc))
        except Exception as exc:
            # Anything a handler did not anticipate is still a result for
            # the model, never an exception that aborts the mission
            detail = f"{type(exc).__name__}: {exc}"[:300]
            return CallOutcome("error", f"ERROR: {detail}", detail)

    # -- policy -------------------------------------------------------------

    def _admit(self, function_name: str) -> dict:
        row = self.policy.offered.get(function_name)
        if row is None:
            offered = ", ".join(sorted(self.policy.offered)) or "none"
            raise CallRefused(
                f"function '{function_name}' is not available to this task "
                f"(available: {offered})")
        if (self.policy.read_only
                and self.policy.hazards.get(function_name) == "mutation"):
            raise CallRefused(
                f"'{function_name}' modifies files and this mission is "
                f"read-only")
        return row

    def _check_arguments(self, row: dict, arguments: dict) -> None:
        """Validate arguments against the row's JSON Schema subset in use:
        required keys, known keys, and primitive types."""
        schema = row.get("parameters") or {}
        props = schema.get("properties") or {}
        missing = [k for k in schema.get("required") or []
                   if k not in arguments]
        if missing:
            raise CallRefused(f"missing required argument(s): "
                              f"{', '.join(missing)}")
        unknown = [k for k in arguments if k not in props]
        if unknown:
            raise CallRefused(f"unknown argument(s): {', '.join(unknown)} "
                              f"(accepts: {', '.join(props) or 'none'})")
        types = {"string": str, "integer": int, "boolean": bool}
        for key, value in arguments.items():
            expected = types.get(props[key].get("type"))
            if expected is None:
                continue
            # bool is an int subclass; keep the two distinct
            if expected is int and isinstance(value, bool):
                value_ok = False
            else:
                value_ok = isinstance(value, expected)
            if not value_ok:
                raise CallRefused(
                    f"argument '{key}' must be {props[key]['type']}")
            minimum = props[key].get("minimum")
            if minimum is not None and value < minimum:
                raise CallRefused(f"argument '{key}' must be >= {minimum}")

    def resolve(self, raw: Optional[str], default_root: bool = True) -> Path:
        """Resolve a model-supplied path under the root, or refuse.

        Relative paths are taken from the root; absolute paths must already
        lie within it. Resolution follows symlinks, so a link pointing out
        of the root is refused the same as `../`.
        """
        if raw is None or raw == "":
            if default_root:
                return self.root
            raise CallRefused("a path is required")
        if "\x00" in raw:
            raise CallRefused("path contains a NUL byte")
        candidate = Path(os.path.expanduser(raw)) if raw.startswith("~") \
            else Path(raw)
        if not candidate.is_absolute():
            candidate = self.root / candidate
        resolved = candidate.resolve()
        if not self._inside(resolved):
            raise CallRefused(
                f"path '{raw}' is outside the working directory "
                f"{self.root}")
        return resolved

    def _inside(self, path: Path) -> bool:
        return path == self.root or self.root in path.parents

    def _relative(self, path: Path) -> str:
        return "." if path == self.root else path.relative_to(self.root).as_posix()

    def _clip(self, text: str) -> str:
        limit = self.policy.max_result_chars
        if len(text) <= limit:
            return text
        return (text[:limit]
                + f"\n[truncated: result exceeded {limit} characters - "
                  f"narrow the request]")

    @staticmethod
    def _tick(deadline: float) -> None:
        if time.monotonic() > deadline:
            raise CallFailed("call exceeded its time budget - narrow the "
                             "request")

    def _walk(self, start: Path, deadline: float):
        """Regular files under `start`, confined and symlink-safe, in
        sorted order."""
        for dirpath, dirnames, filenames in os.walk(start, followlinks=False):
            self._tick(deadline)
            dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
            for name in sorted(filenames):
                path = Path(dirpath) / name
                # A file symlink may point anywhere; follow and re-check
                if path.is_symlink() and not self._inside(path.resolve()):
                    continue
                if not path.is_file():      # FIFOs, sockets, devices
                    continue
                yield path

    def _bounded(self, request: dict, deadline: float) -> dict:
        """Run regex work in the matcher process, killed at the deadline."""
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise CallFailed("call exceeded its time budget - narrow the "
                             "request")
        try:
            completed = subprocess.run(
                [sys.executable, "-I", MATCHER],
                input=json.dumps(request), capture_output=True, text=True,
                timeout=remaining)
        except subprocess.TimeoutExpired:
            raise CallFailed("pattern matching exceeded the call's time "
                             "budget and was stopped - simplify the pattern "
                             "or narrow the path")
        try:
            reply = json.loads(completed.stdout or "{}")
        except json.JSONDecodeError:
            raise CallFailed("matcher returned no result")
        if "error" in reply:
            raise CallFailed(reply["error"])
        return reply

    # -- handlers -----------------------------------------------------------
    # Each returns (content for the model, one-line summary for the log).

    def _fn_read(self, args: dict, deadline: float) -> tuple[str, str]:
        path = self.resolve(args.get("path"), default_root=False)
        if not path.exists():
            raise CallFailed(f"no such file: {args['path']}")
        if path.is_dir():
            raise CallFailed(f"'{args['path']}' is a directory - use LS")
        if not path.is_file():
            raise CallFailed(f"'{args['path']}' is not a regular file")

        offset = args.get("offset", 1)
        limit = args.get("limit", READ_DEFAULT_LIMIT)
        window, total = [], 0
        # Streamed: memory stays bounded by the window, not the file
        try:
            with open(path, "rb") as handle:
                if b"\x00" in handle.read(8192):
                    raise CallFailed(f"'{args['path']}' is a binary file")
                handle.seek(0)
                for total, raw in enumerate(handle, 1):
                    if total % 10000 == 0:
                        self._tick(deadline)
                    if offset <= total < offset + limit:
                        window.append(raw.decode(errors="replace")
                                      .rstrip("\r\n"))
        except OSError as exc:
            raise CallFailed(f"cannot read '{args['path']}': {exc.strerror}")

        width = len(str(offset + len(window)))
        body = "\n".join(f"{offset + i:>{width}}\t{line}"
                         for i, line in enumerate(window))
        last = offset - 1 + len(window)
        header = (f"{self._relative(path)}: lines {offset}-{last} of "
                  f"{total}" if window else
                  f"{self._relative(path)}: no lines at offset {offset} "
                  f"(file has {total})")
        return f"{header}\n{body}".rstrip(), header

    def _fn_ls(self, args: dict, deadline: float) -> tuple[str, str]:
        path = self.resolve(args.get("path"))
        if not path.exists():
            raise CallFailed(f"no such directory: {args.get('path')}")
        if not path.is_dir():
            raise CallFailed(f"'{args.get('path')}' is a file - use Read")
        entries = []
        try:
            children = sorted(path.iterdir(), key=lambda p: p.name)
        except OSError as exc:
            raise CallFailed(f"cannot list '{args.get('path')}': "
                             f"{exc.strerror}")
        for entry in children:
            entries.append(entry.name + ("/" if entry.is_dir() else ""))
        files = sum(1 for e in entries if not e.endswith("/"))
        header = (f"{self._relative(path)}: {len(entries)} entries "
                  f"({files} files, {len(entries) - files} directories)")
        return "\n".join([header, *entries]), header

    def _fn_glob(self, args: dict, deadline: float) -> tuple[str, str]:
        start = self.resolve(args.get("path"))
        if not start.is_dir():
            raise CallFailed(f"'{args.get('path')}' is not a directory")
        if len(args["pattern"]) > GLOB_MAX_PATTERN:
            raise CallRefused(f"pattern longer than {GLOB_MAX_PATTERN} "
                              f"characters")
        source = glob_regex_source(args["pattern"])
        candidates = {path.relative_to(start).as_posix(): path
                      for path in self._walk(start, deadline)}
        reply = self._bounded({"mode": "paths", "pattern": source,
                               "paths": list(candidates)}, deadline)
        matches = [self._relative(candidates[p]) for p in reply["matches"]]
        header = (f"{len(matches)} file(s) match '{args['pattern']}' under "
                  f"{self._relative(start)}")
        return "\n".join([header, *matches]), header

    def _fn_grep(self, args: dict, deadline: float) -> tuple[str, str]:
        target = self.resolve(args.get("path"))
        if not target.exists():
            raise CallFailed(
                f"no such file or directory: {args.get('path')} (path is "
                f"taken literally - filter by name with the glob argument)")
        name_filter = args.get("glob")

        if target.is_file():
            files = [target]
        elif target.is_dir():
            files = list(self._walk(target, deadline))
        else:
            raise CallFailed(f"'{args.get('path')}' is not a regular file "
                             f"or directory")
        if name_filter:
            files = [p for p in files if fnmatch.fnmatch(p.name, name_filter)]
        reply = self._bounded({
            "mode": "lines", "pattern": args["pattern"],
            "ignore_case": bool(args.get("ignore_case")),
            "files": [[self._relative(p), str(p)] for p in files],
            "max_bytes": GREP_MAX_FILE_BYTES, "max_hits": GREP_MAX_HITS,
        }, deadline)
        hits, searched = reply["hits"], reply["searched"]
        header = (f"{len(hits)}{'+' if reply.get('truncated') else ''} "
                  f"match(es) for /{args['pattern']}/ in {searched} file(s) "
                  f"searched")
        if not hits:
            # The common misuse: grepping contents to match file names
            return (f"{header}\nNo line inside any file matched. Grep "
                    f"searches file contents; to find files by name, use "
                    f"Glob or LS."), header
        return "\n".join([header, *hits]), header


def glob_regex(pattern: str) -> "re.Pattern":
    """Compile a pathlib-style glob (see glob_regex_source). In-process -
    for trusted patterns only; the executor matches in the matcher process."""
    return re.compile(glob_regex_source(pattern))


def glob_regex_source(pattern: str) -> str:
    """Translate a pathlib-style glob to a regex over relative POSIX paths.

    `*` and `?` stay within one path segment; `**/` matches zero or more
    whole directories; `[...]` passes through as a character class. A
    pattern without `/` therefore matches only directly in the start
    directory, as pathlib.Path.glob does.
    """
    # Repeated **/ is equivalent to one, and each copy multiplies the
    # backtracking a non-matching path costs
    while "**/**/" in pattern:
        pattern = pattern.replace("**/**/", "**/")
    out, i = [], 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out.append("(?:[^/]+/)*")
            i += 3
        elif pattern.startswith("**", i):
            out.append(".*")
            i += 2
        elif pattern[i] == "*":
            out.append("[^/]*")
            i += 1
        elif pattern[i] == "?":
            out.append("[^/]")
            i += 1
        elif pattern[i] == "[":
            end = pattern.find("]", i + 1)
            if end == -1:
                out.append(re.escape(pattern[i]))
                i += 1
            else:
                body = pattern[i + 1:end].replace("\\", "\\\\")
                if body.startswith("!"):
                    body = "^" + body[1:]
                out.append(f"[{body}]")
                i = end + 1
        else:
            out.append(re.escape(pattern[i]))
            i += 1
    return "".join(out)
