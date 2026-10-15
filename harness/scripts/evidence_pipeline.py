"""Small, deterministic contracts for evidence-first ensemble runs.

Retrieval proves which bytes were available; a model's entailment judgment is
recorded separately. Neither shared URLs nor nearby lines merge propositions.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from pathlib import Path

POLICIES = {
    "quick": {
        "checks": 12,
        "shared": 6,
        "web_sources": 6,
        "packet_chars": 40000,
        "audit_seconds": 180,
    },
    "balanced": {
        "checks": 20,
        "shared": 10,
        "web_sources": 12,
        "packet_chars": 65000,
        "audit_seconds": 240,
    },
    "thorough": {
        "checks": 30,
        "shared": 15,
        "web_sources": 24,
        "packet_chars": 100000,
        "audit_seconds": 360,
    },
}


def policy(scout: dict) -> dict:
    return dict(POLICIES.get(scout.get("depth"), POLICIES["balanced"]))


def claims_from_results(results) -> list[dict]:
    claims = []
    for result in sorted(results, key=lambda r: r.agent_id):
        if not result.success or not isinstance(result.payload, dict):
            continue
        for si, step in enumerate(result.payload.get("plan", [])):
            for ei, evidence in enumerate(step.get("evidence", [])):
                claims.append(
                    {
                        "proposer": result.agent_id,
                        "step_index": si,
                        "evidence_index": ei,
                        "claim_index_path": f"plan[{si}].evidence[{ei}]",
                        "step": step.get("step", ""),
                        "evidence": evidence,
                    }
                )
    return claims


def verification_assignments(results, reviewers: list[str], scout: dict) -> dict:
    """Overlap essential checks, distribute remaining checks without collisions."""
    settings = policy(scout)
    rows = claims_from_results(results)
    terms = set(re.findall(r"[a-z][a-z0-9_]{3,}", scout.get("frozen_spec", "").lower()))

    def rank(row):
        text = (row["step"] + " " + row["evidence"].get("claim", "")).lower()
        overlap = len(terms & set(re.findall(r"[a-z][a-z0-9_]{3,}", text)))
        return (
            row["evidence_index"] != 0,
            -overlap,
            row["step_index"],
            row["proposer"],
            row["evidence_index"],
        )

    rows.sort(key=rank)
    # A shared check is a factual question, not a vote for its proposer.
    shared = rows[: settings["shared"]]
    extra = rows[settings["shared"] :]
    names = sorted(reviewers)
    return {
        name: {
            "essential": shared,
            "secondary": extra[i :: max(1, len(names))][
                : settings["checks"] - len(shared)
            ],
            "check_limit": settings["checks"],
        }
        for i, name in enumerate(names)
    }


def anonymize(results, reviewer: str):
    """Blind route labels and vary presentation order; retain reversible IDs."""
    order = sorted(
        results,
        key=lambda r: hashlib.sha256(
            (reviewer + ":" + r.agent_id).encode()
        ).hexdigest(),
    )
    aliases = {r.agent_id: f"proposal-{i + 1}" for i, r in enumerate(order)}
    copies = []
    for result in order:
        item = copy.copy(result)
        item.agent_id = aliases[result.agent_id]
        item.payload = copy.deepcopy(result.payload)
        if isinstance(item.payload, dict):
            item.payload["agent_id"] = item.agent_id
        copies.append(item)
    return copies, aliases


def restore_reviewer_ids(payload: dict, aliases: dict) -> dict:
    reverse = {value: key for key, value in aliases.items()}
    result = copy.deepcopy(payload)
    if isinstance(result.get("reviewing"), list):
        result["reviewing"] = [reverse.get(x, x) for x in result["reviewing"]]
    for field in (
        "per_proposer_verdicts",
        "verifications",
        "disagreements",
        "incorrect_steps",
    ):
        for item in result.get(field, []):
            if isinstance(item, dict) and item.get("proposer") in reverse:
                item["proposer"] = reverse[item["proposer"]]

    def prose(value, key=""):
        if isinstance(value, dict):
            return {k: prose(v, k) for k, v in value.items()}
        if isinstance(value, list):
            return [prose(v, key) for v in value]
        if isinstance(value, str) and key not in {
            "url",
            "source_url",
            "file",
            "snippet",
            "claim",
            "proposer",
            "agent_id",
            "reviewing",
        }:
            for alias, canonical in reverse.items():
                value = re.sub(
                    r"(?<![\w-])" + re.escape(alias) + r"(?![\w-])",
                    lambda _: canonical,
                    value,
                )
        return value

    return prose(result)


def render_plan(lineage: dict, audit: dict | None = None) -> str:
    """Render exactly the structured decisions; no second model-authored story."""
    outcome = lineage.get("outcome", "ready")
    heading = {
        "ready": "Final Plan",
        "needs_revision": "REJECTED",
        "insufficient_evidence": "Needs verification",
    }.get(outcome, "Final Plan")
    parts = [f"# {heading}: {lineage['title']}", "", lineage["summary"], ""]
    if outcome != "ready":
        parts += [
            "**This result requires further evidence or revised proposals before implementation.**",
            "",
        ]
    if audit:
        parts += ["## Final evidence check", ""]
        for finding in audit.get("findings", []):
            parts.append(
                f"- **{finding['step_id']} — {finding['status']}**: {finding['finding']}"
            )
        parts.append("")
    parts += ["## Plan", ""]
    for index, step in enumerate(lineage.get("steps", []), 1):
        parts += [
            f"{index}. **{step['title']}** — {step['description']}",
            f"   - Decision: {step['decision']}. {step['adjudication']}",
        ]
        for label, field in [
            ("Strongest counterargument", "counterargument"),
            ("Files", "files_touched"),
            ("Constraints", "constraints"),
            ("Uncertainty", "uncertainties"),
            ("Validation", "validation"),
        ]:
            value = step.get(field)
            if value:
                parts.append(
                    f"   - {label}: "
                    + ("; ".join(value) if isinstance(value, list) else value)
                )
        for ref in step.get("proposer_refs", []):
            indices = ",".join(str(x) for x in ref.get("evidence_indices", []))
            parts.append(
                f"   - Origin: {ref['agent_id']} plan[{ref['step_index']}] — {ref['note']}"
            )
        for support in step.get("supporting_claims", []):
            parts.append(
                f"   - Supporting claim: {support['proposer']} {support['claim_index_path']}"
            )
        for ev in step.get("evidence", []):
            locator = ev.get("url") or f"{ev.get('file')}:{ev.get('line')}"
            parts.append(f"   - Evidence: {ev['claim']} ({locator})")
        parts.append("")
    parts += ["## Rejected inputs", ""]
    parts += [
        f"- {r['proposer']} step {r['step_index'] + 1}: {r['reason']}"
        for r in lineage.get("rejected_inputs", [])
    ]
    parts += ["", "## Open questions", ""] + [
        f"- {q}" for q in lineage.get("open_questions", [])
    ]
    confidence = lineage.get("confidence", {})
    parts += [
        "",
        "## Model-stated confidence",
        "",
        f"{confidence.get('level', 'low')}: {confidence.get('rationale', '')}",
        "",
        "Evidence coverage and the independent audit determine the effective confidence shown in the dashboard.",
        "",
    ]
    return "\n".join(parts)


def selected_claims(lineage: dict, results) -> list[dict]:
    lookup = {
        (r["proposer"], r["step_index"], r["evidence_index"]): r
        for r in claims_from_results(results)
    }
    selected = []
    for step in lineage.get("steps", []):
        for ref in step.get("supporting_claims", []):
            match = re.fullmatch(
                r"plan\[(\d+)\]\.evidence\[(\d+)\]", ref["claim_index_path"]
            )
            if match:
                row = lookup.get((ref["proposer"], int(match[1]), int(match[2])))
                if row:
                    selected.append({**row, "step_id": step["id"]})
        for index, evidence in enumerate(step.get("evidence", [])):
            selected.append(
                {
                    "step_id": step["id"],
                    "proposer": "final-draft",
                    "claim_index_path": f"plan[0].evidence[{index}]",
                    "evidence": evidence,
                }
            )
    return selected


def validate_decisions(lineage: dict, results, reviews) -> list[str]:
    """Mechanical invariants; semantic checks are a separately recorded audit."""
    errors = []
    lookup = {(r["proposer"], r["step_index"]): r for r in claims_from_results(results)}
    rejected = [
        r
        for r in reviews
        if r.success and r.payload and r.payload.get("overall_verdict") == "reject_all"
    ]
    if (
        rejected
        and len(rejected) == sum(bool(r.success and r.payload) for r in reviews)
        and lineage.get("outcome") == "ready"
    ):
        errors.append("All reviewers reject the proposals; outcome cannot be ready.")
    if lineage.get("outcome") == "ready" and not lineage.get("steps"):
        errors.append("A ready result requires at least one grounded decision.")
    for step in lineage.get("steps", []):
        count = 0
        locations = {
            (r["proposer"], r["claim_index_path"]) for r in claims_from_results(results)
        }
        for adopted in step.get("supporting_claims", []):
            if (adopted["proposer"], adopted["claim_index_path"]) not in locations:
                errors.append(
                    f"{step['id']}: supporting claim pointer does not resolve"
                )
            else:
                count += 1
        for ref in step.get("proposer_refs", []):
            row = lookup.get((ref["agent_id"], ref["step_index"]))
            if row is None:
                continue  # Existing lineage validator reports the missing step.
            proposer = next(r for r in results if r.agent_id == ref["agent_id"])
            evidence = proposer.payload["plan"][ref["step_index"]]["evidence"]
            indices = ref.get("evidence_indices", [])
            if len(indices) != len(set(indices)):
                errors.append(f"{step['id']}: duplicate evidence indices")
            for index in indices:
                if type(index) is not int or not 0 <= index < len(evidence):
                    errors.append(
                        f"{step['id']}: evidence index {index} is out of range"
                    )
        from run_moa import _validate_evidence_cross_fields

        errors.extend(
            _validate_evidence_cross_fields(
                {"plan": [{"evidence": step.get("evidence", [])}]}
            )
        )
        count += len(step.get("evidence", []))
        if step.get("decision") != "deferred" and not count:
            errors.append(
                f"{step['id']}: an actionable decision needs an explicit supporting claim"
            )
    return errors


def audit_schema() -> dict:
    item = {
        "type": "object",
        "additionalProperties": False,
        "required": ["step_id", "status", "finding", "source_url"],
        "properties": {
            "step_id": {"type": "string"},
            "status": {
                "type": "string",
                "enum": ["supported", "unverified", "contradicted"],
            },
            "finding": {"type": "string", "minLength": 1},
            "source_url": {"type": ["string", "null"]},
        },
    }
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["findings"],
        "properties": {"findings": {"type": "array", "items": item}},
    }


def decision_docket(proposals, reviews, assignments: dict) -> list[dict]:
    """Expose unresolved factual choices before prose; never count votes as truth."""
    essential = {
        (x["proposer"], x["claim_index_path"])
        for a in assignments.values()
        for x in a.get("essential", [])
    }
    checked = {}
    for result in reviews:
        if not result.success or not result.payload:
            continue
        for finding in result.payload.get("verifications", []):
            key = (finding["proposer"], finding["claim_index_path"])
            checked.setdefault(key, []).append({"reviewer": result.agent_id, **finding})
    docket = []
    for row in claims_from_results(proposals):
        key = (row["proposer"], row["claim_index_path"])
        findings = checked.get(key, [])
        if key in essential or any(f["status"] == "contradicted" for f in findings):
            docket.append(
                {
                    "proposer": row["proposer"],
                    "claim_index_path": row["claim_index_path"],
                    "claim": row["evidence"]["claim"],
                    "essential_check": key in essential,
                    "findings": findings,
                }
            )
    return docket
