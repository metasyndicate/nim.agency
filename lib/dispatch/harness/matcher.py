"""
Matcher - regex work for the executor, run in a separate process.

A model-chosen pattern can backtrack catastrophically, and Python's `re`
runs in C without releasing the GIL: no thread-level deadline can stop
it, and the whole dispatcher would freeze with it. So the executor hands
every model-derived regex to this script in its own process (`python -I`,
stdlib only) and kills it when the per-call deadline passes.

Confinement is NOT this script's job: the executor resolves and checks
every path before it is listed here. This script only reads what it is
handed.

Protocol: one JSON request on stdin, one JSON reply on stdout.

    {"mode": "lines", "pattern": str, "ignore_case": bool,
     "files": [[label, absolute_path], ...], "max_bytes": int,
     "max_hits": int}
        -> {"hits": ["label:line:text", ...], "searched": int,
            "truncated": bool}

    {"mode": "paths", "pattern": str, "paths": [str, ...]}
        -> {"matches": [str, ...]}

    any failure -> {"error": str}
"""

import json
import os
import re
import stat
import sys


def match_lines(request: dict) -> dict:
    flags = re.IGNORECASE if request.get("ignore_case") else 0
    regex = re.compile(request["pattern"], flags)
    max_bytes = request.get("max_bytes", 2 * 1024 * 1024)
    max_hits = request.get("max_hits", 1000)
    hits, searched = [], 0
    for label, path in request["files"]:
        try:
            info = os.stat(path)
            if not stat.S_ISREG(info.st_mode) or info.st_size > max_bytes:
                continue
            with open(path, "rb") as handle:
                data = handle.read()
        except OSError:
            continue
        if b"\x00" in data[:8192]:
            continue
        searched += 1
        for number, line in enumerate(
                data.decode(errors="replace").splitlines(), 1):
            if regex.search(line):
                hits.append(f"{label}:{number}:{line[:300]}")
                if len(hits) >= max_hits:
                    return {"hits": hits, "searched": searched,
                            "truncated": True}
    return {"hits": hits, "searched": searched, "truncated": False}


def match_paths(request: dict) -> dict:
    regex = re.compile(request["pattern"])
    return {"matches": [p for p in request["paths"] if regex.fullmatch(p)]}


def main() -> int:
    try:
        request = json.loads(sys.stdin.read())
        if request.get("mode") == "lines":
            reply = match_lines(request)
        elif request.get("mode") == "paths":
            reply = match_paths(request)
        else:
            reply = {"error": f"unknown mode {request.get('mode')!r}"}
    except re.error as exc:
        reply = {"error": f"invalid regular expression: {exc}"}
    except Exception as exc:  # report, never traceback: the parent parses stdout
        reply = {"error": f"{type(exc).__name__}: {exc}"}
    sys.stdout.write(json.dumps(reply))
    return 0


if __name__ == "__main__":
    sys.exit(main())
