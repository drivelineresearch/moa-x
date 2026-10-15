"""Classify CLI failures without treating model output as provider diagnostics."""
from __future__ import annotations

import re
import time
from dataclasses import dataclass
from email.utils import parsedate_to_datetime


@dataclass(frozen=True)
class Failure:
    kind: str
    retryable: bool = False
    retry_after: float = 0


def _retry_delay(text: str) -> float:
    hint = re.search(
        r'(?:retry[-_ ]after\s*[:=]?\s*|(?:retry|try again) in\s+|retrydelay"?\s*[:=]\s*"?)'
        r'(\d+(?:\.\d+)?)\s*(milliseconds?|ms|minutes?|mins?|m\b|seconds?|secs?|s\b)?', text,
    )
    if hint:
        amount = float(hint.group(1))
        unit = hint.group(2) or "seconds"
        return amount / 1000 if unit.startswith("millisecond") or unit == "ms" else amount * 60 if unit.startswith("min") or unit == "m" else amount
    header = re.search(r"retry-after\s*:\s*([^\r\n]+)", text)
    if header:
        try:
            date = parsedate_to_datetime(header.group(1))
            if date.tzinfo is not None:
                return max(0, date.timestamp() - time.time())
        except (ValueError, TypeError, OverflowError):
            pass
    return 0


def classify_failure(error: str | None, stderr: str = "", *, incomplete: bool = False) -> Failure:
    # stdout often contains the entire research prompt, including quoted errors.
    # Never use it to decide whether another billable invocation is safe.
    # Adapters mark incomplete output only after a clean model response. Its
    # research tools may have emitted HTTP errors; they aren't account errors.
    diagnostics = "" if incomplete else (stderr or "")[-8000:]
    terminal_lines = re.findall(r"^ERROR:\s*(.+)$", diagnostics, re.MULTILINE)
    if terminal_lines:
        # Codex echoes the whole prompt to stderr before its terminal errors.
        # A source fixture mentioning auth/quota must not override the cause.
        diagnostics = "\n".join(terminal_lines[-3:])
    text = ((error or "") + "\n" + diagnostics).lower()
    permanent = {
        "auth": r"(?:http(?:/\S+)?\s+|status(?: code)?\s*[:=]?\s*)(?:401|403)\b|unauthorized|unauthenticated|invalid api.?key|invalid token|login required|not authenticated|authentication error|access is disabled|no credentials",
        "quota": r"insufficient_quota|insufficient (?:credits?|balance)|(?:billing|spending) limit|credit balance|budget (?:exceeded|exhausted)|payment required|monthly spending limit|account[^\n]{0,80}suspended",
        "configuration": r"adapter failure in|provider\.invalid-request|model.?not.?found|unknown model|unsupported model|not eligible|ineligibletier|routing error|route is unavailable|model unavailable|invalid (?:response )?schema|missing schema|binary not found|not on path|argument list too long|could not launch|oserror launching|unrecognized flag|unknown flag|incompatible with the installed|can only be used when",
    }
    for kind, pattern in permanent.items():
        if re.search(pattern, text):
            return Failure(kind)
    # Per-minute quota exhaustion is a rate limit; exhausted account budgets
    # are permanent. Don't let generic "quota exceeded" hide retryable 429s.
    if re.search(r"quota (?:exceeded|exhausted)", text) and not re.search(r"\b429\b|per[- ](?:minute|second)|rate.?limit|resource_exhausted|retrydelay|retry in", text):
        return Failure("quota")
    retry_after = _retry_delay(text)
    if re.search(r"\b429\b|rate.?limit|too many requests|at capacity|overloaded|resource_exhausted", text):
        return Failure("capacity", True, retry_after)
    # Streaming APIs can report an internal failure inside an HTTP 200 body.
    # The adapter's typed diagnostic remains a server failure, not success or
    # an unknown permanent error. Auth/billing/config causes above still win.
    trusted_error = (error or "").lower() + "\n" + "\n".join(terminal_lines[-3:]).lower()
    if re.search(r"\bprovider\.internal\b|\bserver_error\b", trusted_error):
        return Failure("transport", True, retry_after)
    if re.search(r"\b(?:500|502|503|504)\b|connection (?:reset|closed|refused)|econnreset|etimedout|stream disconnected|stream.*(?:closed|interrupted)|temporarily unavailable|service unavailable|upstream error|network error", text):
        return Failure("transport", True, retry_after)
    if "timeout after" in text or "timed out" in text:
        return Failure("timeout", True)
    # OpenCode reports a prematurely terminated Chat stream as a typed
    # invalid-output error even when the upstream HTTP status was 200.
    # Other invalid-output errors remain permanent; partial text is never
    # accepted as a completed response.
    if re.search(r"stream ended without (?:a )?finish_reason", text):
        return Failure("incomplete", True)
    if incomplete or re.search(r"(?:could not extract|empty output|no payload|incomplete json|non-json/incomplete)", text):
        return Failure("incomplete", True)
    return Failure("unknown")
