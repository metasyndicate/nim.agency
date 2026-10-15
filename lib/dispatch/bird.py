"""
BIRD - Biomimetic Inference Relay Dewarbler.

The layer between the agency and a syndicate that notices when a model
exchange went wrong, says so in a standard shape, and corrects what can be
corrected. It sits at the one place every HTTP request to a syndicate
passes (HttpProvider._post, via `transmit`), so nothing reaches the
dispatcher unexamined.

Layers loosely follow OSI; what each anomaly is, how severe, and what to do
about it is data - the `anomaly` reference table:

    link     the request did not complete      timeout, unreachable, 429, 503
    frame    a reply arrived but is unusable    malformed, empty, cut at limit
    context  the prompt did not fit             refused, silently truncated
    content  the answer is wrong or refused     known-answer miss, refusal

Every detection is recorded as an RFC 9457 problem-details object (type,
title, status, detail, instance, plus extension members) on the current
task's ledger, which the dispatcher writes to the ops log and the report.

Remedies (Guardrails AI OnFailAction vocabulary plus ARQ retransmission):
`retransmit` resends the request - capped exponential backoff with full
jitter, or the server's Retry-After (RFC 9110 §10.2.3) - within the task's
deadline, the row's max_attempts, and a per-task retransmit budget.
`exception` lets the existing code fail the task; `reask` and `noop`
record. Inference requests hold no server-side state, so resending is safe
in effect even though POST is not idempotent by method definition.

Extending: add a row to data/agency/data/reference/anomaly.json and record
its slug where it is detected; tests/test_bird.py holds the two in step.
"""

import asyncio
import contextvars
import email.utils
import json
import random
import socket
import time
import urllib.error
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

TYPE_BASE = "https://nim.agency/bird/anomaly/"

RELAY_DEFAULTS = {
    "base_delay_seconds": 1.0,
    "max_delay_seconds": 20.0,
    "max_retransmits_per_task": 4,
}

# Below this share of the characters/4 estimate, a reported prompt count
# means the endpoint dropped input
TRUNCATION_SUSPECT_RATIO = 0.4
TRUNCATION_SUSPECT_MIN_TOKENS = 512

# A resend is pointless with less than this left before the deadline: a
# model call cannot complete in it
MIN_ATTEMPT_SECONDS = 5.0

# Statuses that mean "try again later" rather than "this request is wrong"
UNAVAILABLE_STATUSES = (502, 503, 504)


def relay_config() -> dict:
    from ..config import get_config
    cfg = dict(RELAY_DEFAULTS)
    cfg.update({k: v for k, v in (get_config().get("relay") or {}).items()
                if k in RELAY_DEFAULTS})
    return cfg


def vocabulary(ds=None) -> dict:
    """The anomaly table by slug."""
    if ds is None:
        from ..data import get_datastore
        ds = get_datastore()
    return {row["slug"]: row for row in ds.load_reference("anomaly")}


# -- the ledger ---------------------------------------------------------------

@dataclass
class Ledger:
    """Problems recorded for one task, in the order they happened."""
    task_id: str = ""
    problems: list = field(default_factory=list)
    retransmits: int = 0
    exchanges: int = 0
    vocab: dict = field(default_factory=dict)

    def record(self, slug: str, detail: str, **extra) -> dict:
        row = self.vocab.get(slug)
        if row is None:
            # A missing row must never cost the caller its real error or a
            # good reply: record it unlabeled (tests keep table and code in step)
            row = {"label": slug, "layer": slug.split("_", 1)[0],
                   "status_code": "UNKNOWN", "severity": "warning",
                   "remedy": "noop"}
        problem = {
            "type": TYPE_BASE + slug,
            "title": row.get("label", slug),
            "detail": detail,
            "instance": f"urn:nim:task:{self.task_id or '-'}#exchange-{self.exchanges}",
            "anomaly": slug,
            "layer": row.get("layer"),
            "status_code": row.get("status_code"),
            "severity": row.get("severity"),
            "remedy": row.get("remedy"),
            "at": datetime.now(timezone.utc).isoformat(),
        }
        if row.get("http_status"):
            problem["status"] = row["http_status"]
        problem.update({k: v for k, v in extra.items() if v is not None})
        self.problems.append(problem)
        return problem


_CURRENT: contextvars.ContextVar = contextvars.ContextVar("bird_ledger",
                                                          default=None)


def begin(task_id: str = "", ds=None) -> contextvars.Token:
    """Open a ledger for the task running in this context (asyncio tasks
    and to_thread workers each see their own)."""
    return _CURRENT.set(Ledger(task_id=task_id, vocab=vocabulary(ds)))


def end(token: contextvars.Token) -> list:
    """Close the ledger opened by `begin`; returns its problems."""
    ledger = _CURRENT.get()
    _CURRENT.reset(token)
    return list(ledger.problems) if ledger else []


def current() -> Ledger:
    """The open ledger, or - outside any begin/end - a fresh detached one
    whose records go nowhere. Never stored: a stored ledger would carry its
    retransmit budget and problems into unrelated calls."""
    ledger = _CURRENT.get()
    return ledger if ledger is not None else Ledger(vocab=vocabulary())


def record(slug: str, detail: str, **extra) -> dict:
    return current().record(slug, detail, **extra)


# -- classification -----------------------------------------------------------

def parse_retry_after(value: Optional[str]) -> Optional[float]:
    """Retry-After as seconds: delay-seconds or an HTTP-date (RFC 9110)."""
    if not value:
        return None
    value = value.strip()
    if value.isascii() and value.isdigit():
        return float(value)
    try:
        when = email.utils.parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    return max(0.0, (when - datetime.now(when.tzinfo)).total_seconds())


def classify_exception(exc: Exception, provider) -> Optional[tuple]:
    """(slug, detail, retry_after, http_status) for a failed request, or
    None when the failure is not a relay matter."""
    url = getattr(getattr(provider, "config", None), "url", "")
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError, socket.timeout)):
        return "link_timeout", f"no reply from {url}", None, None
    if isinstance(exc, urllib.error.HTTPError):
        body = provider.http_body(exc) if hasattr(provider, "http_body") else ""
        after = parse_retry_after(exc.headers.get("Retry-After")
                                  if exc.headers else None)
        detail = f"HTTP {exc.code} from {url}" + (f": {body[:200]}" if body else "")
        if exc.code == 429:
            return "link_rate_limited", detail, after, 429
        if exc.code in UNAVAILABLE_STATUSES:
            return "link_unavailable", detail, after, exc.code
        if exc.code == 400 and ("exceed_context_size" in body
                                or "exceeds the available context" in body):
            return "context_exceeded", detail, None, 400
        return "link_http_error", detail, None, exc.code
    if isinstance(exc, urllib.error.URLError):
        if isinstance(exc.reason, (socket.timeout, TimeoutError)):
            return "link_timeout", f"no reply from {url}", None, None
        return "link_unreachable", f"{url}: {exc.reason}", None, None
    if isinstance(exc, (json.JSONDecodeError, UnicodeDecodeError)):
        return "frame_malformed_body", f"reply from {url} is not JSON: {exc}", None, None
    if isinstance(exc, (ConnectionError, OSError)):
        return "link_unreachable", f"{url}: {exc}", None, None
    return None


def estimate_prompt_tokens(payload: dict) -> int:
    """A characters/4 estimate of what a request asks the model to read."""
    chars = len(str(payload.get("prompt") or "")) + len(str(payload.get("system") or ""))
    for message in payload.get("messages") or []:
        chars += len(str(message.get("content") or ""))
    if payload.get("tools"):
        chars += len(json.dumps(payload["tools"]))
    return chars // 4


def inspect(provider, payload: dict, response: Any) -> list[tuple]:
    """Frame and context findings for a reply that arrived: (slug, detail)."""
    from .provider import TRUNCATION_FINISH_REASONS
    try:
        text, calls, finish, prompt_tokens = provider.reply_parts(response)
    except (KeyError, IndexError, TypeError, AttributeError) as exc:
        return [("frame_malformed_body",
                 f"reply lacks the transport's fields ({type(exc).__name__}: {exc})")]
    findings = []
    if finish in TRUNCATION_FINISH_REASONS:
        findings.append(("frame_output_truncated",
                         f"finish reason {finish!r}: the answer was cut at the "
                         f"output limit"))
    elif not (text or "").strip() and not calls:
        findings.append(("frame_empty_message", "reply has no text and no tool calls"))
    sent = estimate_prompt_tokens(payload)
    if (prompt_tokens and sent >= TRUNCATION_SUSPECT_MIN_TOKENS
            and prompt_tokens < TRUNCATION_SUSPECT_RATIO * sent):
        findings.append(("context_truncation_suspected",
                         f"endpoint evaluated {prompt_tokens} prompt tokens of "
                         f"~{sent} sent"))
    return findings


# -- transmission -------------------------------------------------------------

def _delay(attempt: int, cfg: dict, retry_after: Optional[float]) -> float:
    """Server's Retry-After when given, else capped exponential backoff
    with full jitter (AWS Architecture Blog, 'Exponential Backoff And
    Jitter')."""
    if retry_after is not None:
        return min(retry_after, cfg["max_delay_seconds"])
    ceiling = min(cfg["max_delay_seconds"], cfg["base_delay_seconds"] * (2 ** attempt))
    return random.uniform(0, ceiling)


async def transmit(provider, payload: dict, timeout: float) -> Any:
    """Send one request through the relay; returns the decoded reply or
    raises the final failure. Every anomaly lands on the current ledger."""
    ledger = current()
    cfg = relay_config()
    deadline = time.monotonic() + max(1.0, float(timeout))
    tries = Counter()

    def may_retry(slug: str) -> bool:
        row = ledger.vocab.get(slug, {})
        return (row.get("remedy") == "retransmit"
                and tries[slug] < row.get("max_attempts", 0)
                and ledger.retransmits < cfg["max_retransmits_per_task"])

    async def back_off(slug: str, detail: str, retry_after, status) -> bool:
        delay = _delay(tries[slug], cfg, retry_after)
        if deadline - time.monotonic() - delay < MIN_ATTEMPT_SECONDS:
            return False
        ledger.record(slug, f"{detail}; retransmitting in {delay:.1f}s",
                      attempt=tries[slug] + 1, status=status)
        tries[slug] += 1
        ledger.retransmits += 1
        await asyncio.sleep(delay)
        return True

    while True:
        ledger.exchanges += 1
        remaining = max(1.0, deadline - time.monotonic())
        try:
            response = await asyncio.wait_for(
                asyncio.to_thread(provider._post, payload, remaining),
                timeout=remaining + 5)
        except Exception as exc:
            found = classify_exception(exc, provider)
            if found is None:
                raise
            slug, detail, retry_after, status = found
            if may_retry(slug) and await back_off(slug, detail, retry_after, status):
                continue
            ledger.record(slug, detail, status=status,
                          exhausted=tries[slug] or None)
            raise

        findings = inspect(provider, payload, response)
        retry = next(((s, d) for s, d in findings if may_retry(s)), None)
        if retry and await back_off(retry[0], retry[1], None, None):
            continue
        for slug, detail in findings:
            ledger.record(slug, detail)
        return response


def note_result(result) -> None:
    """Content-layer findings on a settled task result."""
    from .provider import POLICY_VIOLATION_PREFIX
    from .verification import VERIFICATION_FAILED_PREFIX
    error = getattr(result, "error", "") or ""
    if error.startswith(VERIFICATION_FAILED_PREFIX):
        record("content_known_answer_mismatch", error)
    elif error.startswith(POLICY_VIOLATION_PREFIX):
        record("content_refusal", error)


def summary_line(problem: dict) -> str:
    """One line for reports and event streams."""
    remedy = problem.get("remedy")
    if problem.get("exhausted"):
        remedy = f"gave up after {problem['exhausted']} retransmit(s)"
    return (f"[{problem.get('layer')}/{problem.get('severity')}] "
            f"{problem.get('anomaly')}: {problem.get('detail', '')}"
            f"{' -> ' + remedy if remedy not in (None, 'noop') else ''}")
