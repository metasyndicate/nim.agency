"""
Verification - known-answer checks on task output, settled before payroll.

A task may carry an expectation: an operator-supplied statement of what a
correct answer contains. The dispatcher checks it the moment the provider
returns, before the paymaster runs, so a confident wrong answer is a
failed task rather than a paid one.

This is the known-answer test (KAT) pattern from validation practice: a
fixed input with an answer established independently of the system under
test. It measures correctness, which grounding alone does not guarantee -
a model reading a real directory listing can still miscount it.

Expectation kinds:

    contains      output contains the expected text (case-sensitive)
    json_subset   output carries a JSON object; every expected key is
                  present with an equal value. Objects compare as subsets
                  recursively; arrays compare as multisets (order-free)

Stdlib only.
"""

import json
from dataclasses import dataclass
from typing import Any, Optional


# Task errors beginning with this prefix are verification failures: the
# provider answered, the answer was wrong. Distinct from policy refusals so
# the conduct ledger is not charged for an honest miss.
VERIFICATION_FAILED_PREFIX = "verification_failed"

EXPECTATION_KINDS = ("contains", "json_subset")


@dataclass
class Expectation:
    """What a correct answer must contain."""
    kind: str
    expected: Any

    def __post_init__(self):
        if self.kind not in EXPECTATION_KINDS:
            raise ValueError(
                f"unknown expectation kind '{self.kind}' "
                f"(one of: {', '.join(EXPECTATION_KINDS)})")
        if self.kind == "json_subset" and not isinstance(self.expected, dict):
            raise ValueError("json_subset expects a JSON object")

    def to_dict(self) -> dict:
        return {"kind": self.kind, "expected": self.expected}

    @classmethod
    def from_dict(cls, data: Optional[dict]) -> Optional["Expectation"]:
        if not data:
            return None
        return cls(kind=data["kind"], expected=data["expected"])


@dataclass
class Verdict:
    """The outcome of checking one output against one expectation."""
    passed: bool
    detail: str

    def to_dict(self) -> dict:
        return {"passed": self.passed, "detail": self.detail}


def extract_json_object(text: str) -> Optional[dict]:
    """The first JSON object embedded in model output.

    Models wrap JSON in prose and code fences; scanning for each opening
    brace and attempting a decode there tolerates both without a regex
    guess at where the object ends.
    """
    decoder = json.JSONDecoder()
    index = text.find("{")
    while index != -1:
        try:
            value, _ = decoder.raw_decode(text, index)
        except json.JSONDecodeError:
            index = text.find("{", index + 1)
            continue
        if isinstance(value, dict):
            return value
        index = text.find("{", index + 1)
    return None


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True)


def json_mismatches(expected: Any, actual: Any, path: str = "$") -> list[str]:
    """Every way `actual` fails to contain `expected`, as JSONPath-ish
    locations. Empty means the subset holds."""
    if isinstance(expected, dict):
        if not isinstance(actual, dict):
            return [f"{path}: expected an object, got {_canonical(actual)}"]
        problems = []
        for key, value in expected.items():
            if key not in actual:
                problems.append(f"{path}.{key}: missing")
            else:
                problems.extend(
                    json_mismatches(value, actual[key], f"{path}.{key}"))
        return problems

    if isinstance(expected, list):
        if not isinstance(actual, list):
            return [f"{path}: expected an array, got {_canonical(actual)}"]
        want = sorted(_canonical(v) for v in expected)
        got = sorted(_canonical(v) for v in actual)
        if want == got:
            return []
        missing = sorted(set(want) - set(got))
        extra = sorted(set(got) - set(want))
        parts = []
        if missing:
            parts.append(f"missing {', '.join(missing)}")
        if extra:
            parts.append(f"unexpected {', '.join(extra)}")
        return [f"{path}: {'; '.join(parts) or 'multiplicity differs'}"]

    if expected != actual:
        return [f"{path}: expected {_canonical(expected)}, "
                f"got {_canonical(actual)}"]
    return []


def verify(expectation: Expectation, output: str) -> Verdict:
    """Check one task output against its expectation."""
    if expectation.kind == "contains":
        if str(expectation.expected) in output:
            return Verdict(True, "expected text present")
        return Verdict(False,
                       f"expected text not found: {expectation.expected!r}")

    found = extract_json_object(output)
    if found is None:
        return Verdict(False, "no JSON object in output")
    problems = json_mismatches(expectation.expected, found)
    if problems:
        return Verdict(False, "; ".join(problems))
    return Verdict(True, f"{len(expectation.expected)} expected key(s) matched")
