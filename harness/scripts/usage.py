"""Normalize native CLI usage receipts without guessing missing provider data."""

from __future__ import annotations
import json


def extract(stdout: str) -> dict | None:
    try:
        outer = json.loads(stdout)
        events = [outer]
    except (ValueError, TypeError):
        events = []
        for line in (stdout or "").splitlines():
            try:
                events.append(json.loads(line))
            except ValueError:
                continue
    found = None
    for event in events:
        if not isinstance(event, dict):
            continue
        data = event.get("usage")
        if not isinstance(data, dict):
            continue
        recognized = event.get("type") in {"result", "turn.completed"} or (
            "structured_output" in event and "conversation_id" in event
        )
        if not recognized:
            continue
        result = {}
        for target, keys in {
            "input_tokens": ["input_tokens", "inputTokens", "prompt_tokens"],
            "output_tokens": ["output_tokens", "outputTokens", "completion_tokens"],
            "cached_input_tokens": ["cached_input_tokens", "cache_read_input_tokens"],
        }.items():
            value = next((data[k] for k in keys if k in data), None)
            if type(value) is int and value >= 0:
                result[target] = value
        cost = event.get("total_cost_usd")
        if type(cost) in (int, float) and cost >= 0:
            result["provider_reported_cost_usd"] = cost
        if result:
            found = result
    return found
