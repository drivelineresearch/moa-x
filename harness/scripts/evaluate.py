#!/usr/bin/env python3
"""Summarize held-out planning runs without treating model confidence as truth."""

from __future__ import annotations
import argparse
import json
from pathlib import Path
import re
import statistics


def summarize(session: Path, case: dict | None = None) -> dict:
    def read(name):
        try:
            return json.loads((session / name).read_text())
        except (OSError, ValueError):
            return {}

    manifest = read("manifest.json")
    graph = read("decision-map.json")
    lineage = read("final-plan.json")
    audit = read("final-audit.json")
    try:
        text = (session / "final-plan.md").read_text().casefold()
    except OSError:
        text = ""
    rows = [
        x for layer in ("layer1", "layer2", "layer3") for x in manifest.get(layer, [])
    ]
    usage = {}
    metered = 0
    for row in rows:
        for attempt in row.get("attempts") or []:
            if not attempt.get("usage"):
                continue
            metered += 1
            for key, value in attempt["usage"].items():
                usage[key] = usage.get(key, 0) + value
    for attempt in audit.get("attempts") or []:
        if attempt.get("usage"):
            metered += 1
            for key, value in attempt["usage"].items():
                usage[key] = usage.get(key, 0) + value
    critical = graph.get("quality", {})
    return {
        "session_id": session.name,
        "case": (case or {}).get("id"),
        "outcome": lineage.get("outcome", "legacy"),
        "lanes": len(rows),
        "accepted_lanes": sum(bool(r.get("success")) for r in rows),
        "duration_seconds": manifest.get("duration_seconds"),
        "critical_claims": critical.get("critical_claims"),
        "verification_coverage": critical.get("verification_coverage"),
        "independent_verified_coverage": critical.get("independent_verified_coverage"),
        "contradicted_critical_claims": critical.get("contradicted_critical_claims"),
        "effective_confidence": critical.get("effective_confidence"),
        "audit_success": audit.get("success"),
        "audit_supported_steps": sum(
            x.get("status") == "supported" for x in audit.get("findings", [])
        ),
        "audit_total_steps": len(audit.get("findings", [])),
        "contract_mentions": {
            key: any(term.casefold() in text for term in terms)
            for key, terms in (case or {}).get("contract_mentions", {}).items()
        },
        "usage": usage or None,
        "metered_attempts": metered,
        "limitations": [
            "Contract mentions are not an accuracy score.",
            "Unreported provider usage remains unknown.",
            "Model-stated confidence is not a calibrated probability.",
        ],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--session", type=Path, action="append", required=True)
    parser.add_argument("--case", type=Path)
    args = parser.parse_args()
    case = json.loads(args.case.read_text()) if args.case else None
    rows = [summarize(path, case) for path in args.session]
    times = sorted(
        r["duration_seconds"]
        for r in rows
        if isinstance(r.get("duration_seconds"), (int, float))
    )
    print(
        json.dumps(
            {
                "runs": rows,
                "latency": {
                    "p50_seconds": statistics.median(times) if times else None,
                    "p95_seconds": times[min(len(times) - 1, int(len(times) * 0.95))]
                    if times
                    else None,
                },
                "next_evaluation": "Use paired tasks, repeat each depth, randomize display order, and independently label factual correctness and missed requirements before changing routing weights.",
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
