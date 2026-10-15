"""Shared, read-only progress facts for the portal and Slack."""

from __future__ import annotations
import json
from pathlib import Path


def evidence_status(session: Path, *, stale=False, status=None) -> dict:
    def read(name):
        path = session / name
        try:
            if path.is_symlink() or path.stat().st_size > 3_000_000:
                return {}
            value = json.loads(path.read_text())
            return value if isinstance(value, dict) else {}
        except (OSError, ValueError):
            return {}

    graph = {} if stale else read("decision-map.json")
    quality = graph.get("quality", {})
    checks = graph.get("verifications", [])
    assignments = read("verification-assignments.json")
    essential = {
        (x.get("proposer"), x.get("claim_index_path"))
        for value in assignments.values()
        for x in value.get("essential", [])
    }
    lineage = read("final-plan.json")
    audit = read("final-audit.json")
    outcome = (
        lineage.get("outcome")
        if not stale and (status is None or status in {"completed", "imported"})
        else None
    )
    reasons = []
    if outcome and outcome != "ready":
        if not audit.get("success"):
            reasons.append("The final evidence check did not complete.")
        reasons.extend(
            x.get("finding", "")[:500]
            for x in audit.get("findings", [])
            if x.get("status") != "supported"
        )
        reasons.extend(
            g.get("detail", "")
            for g in quality.get("gates", [])
            if g.get("status") == "fail"
        )
    return {
        "stage": graph.get("stage", "setup"),
        "verified": sum(x.get("status") == "verified" for x in checks),
        "checks": len(checks),
        "critical_total": quality.get("critical_claims", 0),
        "critical_verified": quality.get("verified_critical_claims", 0),
        "conflicts": quality.get("contradicted_critical_claims", 0),
        "essential_assigned": len(essential),
        "outcome": outcome,
        "audit_success": bool(audit.get("success")),
        "audit_contradicted": sum(
            x.get("status") == "contradicted" for x in audit.get("findings", [])
        ),
        "audit_unverified": sum(
            x.get("status") == "unverified" for x in audit.get("findings", [])
        ),
        "audit_supported": sum(
            x.get("status") == "supported" for x in audit.get("findings", [])
        ),
        "audit_total": len(audit.get("findings", [])),
        "reasons": list(dict.fromkeys(reasons))[:5],
        "confidence": quality.get("effective_confidence", "pending"),
    }


def outcome_label(status: str, outcome: str | None) -> str:
    if status not in {"completed", "imported"}:
        return status.title()
    return {
        "ready": "Ready for review",
        "needs_revision": "Needs revision",
        "insufficient_evidence": "Needs verification",
    }.get(outcome, "Readiness not recorded")


def evidence_text(value: dict) -> str:
    if value.get("outcome") in {"needs_revision", "insufficient_evidence"}:
        if not value.get("audit_success"):
            return "Final evidence check did not complete; review the evidence gaps before implementation"
        return f"Final check: {value['audit_contradicted']} contradicted, {value['audit_unverified']} unverified decisions · {value['critical_verified']}/{value['critical_total']} selected claims verified"
    if value.get("critical_total"):
        return f"{value['critical_verified']} of {value['critical_total']} selected claims verified · {value['conflicts']} unresolved contradictions"
    if value.get("checks"):
        return f"{value['verified']} supported findings from {value['checks']} evidence checks · {value['essential_assigned']} essential checks assigned"
    if value.get("essential_assigned"):
        return f"Checking {value['essential_assigned']} essential factual premises before synthesis"
    return "Repository scout and independent proposals prepare the evidence checklist"
