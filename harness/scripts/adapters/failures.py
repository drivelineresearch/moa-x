"""Classify CLI failures without treating model output as provider diagnostics."""
from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Failure:
    kind: str
    retryable: bool = False
    retry_after: float = 0


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
        "configuration": r"model.?not.?found|unknown model|unsupported model|not eligible|ineligibletier|routing error|route is unavailable|model unavailable|invalid (?:response )?schema|missing schema|binary not found|not on path|argument list too long|could not launch|oserror launching|unrecognized flag|unknown flag|incompatible with the installed|can only be used when",
    }
    for kind, pattern in permanent.items():
        if re.search(pattern, text):
            return Failure(kind)
    # Per-minute quota exhaustion is a rate limit; exhausted account budgets
    # are permanent. Don't let generic "quota exceeded" hide retryable 429s.
    if re.search(r"quota (?:exceeded|exhausted)", text) and not re.search(r"per[- ](?:minute|second)|rate.?limit|resource_exhausted|retrydelay|retry in", text):
        return Failure("quota")
    delay = re.search(r"(?:retry[-_ ]after\s*[:=]?\s*|retry in\s+|retrydelay\"?\s*[:=]\s*\"?)(\d+(?:\.\d+)?)", text)
    retry_after = float(delay.group(1)) if delay else 0
    if re.search(r"\b429\b|rate.?limit|too many requests|at capacity|overloaded|resource_exhausted", text):
        return Failure("capacity", True, retry_after)
    if re.search(r"\b(?:500|502|503|504)\b|connection (?:reset|closed|refused)|econnreset|etimedout|stream disconnected|stream.*(?:closed|interrupted)|temporarily unavailable|service unavailable|upstream error|network error", text):
        return Failure("transport", True, retry_after)
    if "timeout after" in text or "timed out" in text:
        return Failure("timeout", True)
    if incomplete or re.search(r"(?:could not extract|empty output|no payload|incomplete json|non-json/incomplete)", text):
        return Failure("incomplete", True)
    return Failure("unknown")
