"""Small, bounded prompt-coaching flow for the Web UI.

The coach deliberately runs outside the selected repository. It sees the
user's draft plus coarse context metadata, never repository or attachment
contents. Codex Luna is primary; OpenCode's DeepSeek Flash route is fallback.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from typing import Any


WEBUI_DIR = Path(__file__).resolve().parent
SCRIPTS_DIR = WEBUI_DIR.parent / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from adapters import codex, opencode  # noqa: E402


PRIMARY_MODEL = "gpt-5.6-luna"
FALLBACK_MODEL = "opencode-go/deepseek-v4-flash"
MAX_BRIEF_CHARS = 20_000
MAX_QUESTIONS = 3

_OPTION_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "label": {"type": "string"},
        "description": {"type": "string"},
        "recommended": {"type": "boolean"},
    },
    "required": ["label", "description", "recommended"],
}
ANALYZE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "suitability": {
            "type": "string",
            "enum": ["ready", "needs_clarification", "poor_fit"],
        },
        "score": {"type": "integer", "minimum": 0, "maximum": 100},
        "summary": {"type": "string"},
        "questions": {
            "type": "array",
            "maxItems": MAX_QUESTIONS,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "id": {"type": "string"},
                    "prompt": {"type": "string"},
                    "why": {"type": "string"},
                    "options": {
                        "type": "array",
                        "minItems": 2,
                        "maxItems": 3,
                        "items": _OPTION_SCHEMA,
                    },
                    "allow_custom": {"type": "boolean"},
                },
                "required": ["id", "prompt", "why", "options", "allow_custom"],
            },
        },
    },
    "required": ["suitability", "score", "summary", "questions"],
}
FINALIZE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "optimized_prompt": {"type": "string"},
        "changes": {"type": "array", "items": {"type": "string"}, "maxItems": 6},
        "assumptions": {"type": "array", "items": {"type": "string"}, "maxItems": 6},
        "remaining_risks": {
            "type": "array",
            "items": {"type": "string"},
            "maxItems": 4,
        },
    },
    "required": ["optimized_prompt", "changes", "assumptions", "remaining_risks"],
}


class PromptCoachError(RuntimeError):
    """Raised when neither configured local CLI can complete the coach call."""


def _bounded_brief(value: Any) -> str:
    brief = str(value or "").strip()
    if not brief:
        raise ValueError("Add a task or outcome before strengthening it.")
    if len(brief) > MAX_BRIEF_CHARS:
        raise ValueError(f"Task must be {MAX_BRIEF_CHARS:,} characters or fewer.")
    return brief


def _run(prompt: str, schema: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    errors: list[str] = []
    with tempfile.TemporaryDirectory(prefix="moa-prompt-coach-") as raw_tmp:
        temp_root = Path(raw_tmp)
        schema_path = temp_root / "response.schema.json"
        schema_path.write_text(json.dumps(schema), encoding="utf-8")

        primary = codex.run(
            prompt=prompt,
            schema_path=schema_path,
            repo_path=temp_root,
            model=PRIMARY_MODEL,
            reasoning_effort="low",
            timeout_seconds=75,
        )
        if primary.success and isinstance(primary.payload, dict):
            return primary.payload, {
                "provider": "codex",
                "model": PRIMARY_MODEL,
                "fallback": False,
            }
        errors.append(f"Luna: {primary.error_message or 'invalid response'}")

        fallback = opencode.run(
            prompt=prompt,
            schema_path=schema_path,
            repo_path=temp_root,
            model=FALLBACK_MODEL,
            reasoning_effort="low",
            timeout_seconds=75,
        )
        if fallback.success and isinstance(fallback.payload, dict):
            return fallback.payload, {
                "provider": "opencode",
                "model": FALLBACK_MODEL,
                "fallback": True,
            }
        errors.append(
            f"DeepSeek Flash: {fallback.error_message or 'invalid response'}"
        )
    raise PromptCoachError("Prompt coach unavailable. " + " · ".join(errors))


def analyze(
    brief: Any, *, context_mode: Any = "brief", attachment_count: Any = 0
) -> dict[str, Any]:
    clean_brief = _bounded_brief(brief)
    count = max(0, min(10, int(attachment_count or 0)))
    prompt = f"""You are MoA-X's fast prompt coach. Assess whether this task will
work well as input to a multi-model planning ensemble: independent proposals,
lab-independent refinement, then synthesis.

Ask only questions whose answers would materially improve the final plan.
Return no more than three questions, and return zero when the brief is already
strong. Each question needs 2-3 concise, mutually exclusive suggested answers;
mark exactly one recommended option. Do not ask for information that the
selected repository or attachments can reasonably provide. A poor fit is a
request that is not meaningfully a planning/review/decision task; explain that
gently in summary and ask questions that reshape it if possible.

Context metadata:
- source mode: {str(context_mode)[:40]}
- reference attachment count: {count}

User's draft:
---BEGIN DRAFT---
{clean_brief}
---END DRAFT---

Output only the JSON object required by the supplied schema."""
    payload, model = _run(prompt, ANALYZE_SCHEMA)
    questions = payload.get("questions")
    if not isinstance(questions, list):
        raise PromptCoachError("Prompt coach returned malformed questions.")
    payload["questions"] = questions[:MAX_QUESTIONS]
    payload["model"] = model
    return payload


def finalize(
    brief: Any,
    *,
    questions: Any,
    answers: Any,
    context_mode: Any = "brief",
    attachment_count: Any = 0,
) -> dict[str, Any]:
    clean_brief = _bounded_brief(brief)
    clean_questions = questions if isinstance(questions, list) else []
    clean_answers = answers if isinstance(answers, list) else []
    clean_questions = clean_questions[:MAX_QUESTIONS]
    clean_answers = clean_answers[:MAX_QUESTIONS]
    prompt = f"""You are MoA-X's prompt editor. Rewrite the user's draft into a
clear, self-contained brief for a multi-model planning ensemble. Preserve the
user's intent and facts. Incorporate the answers, state important assumptions,
and make the desired outcome, constraints, and decision criteria explicit.
Do not invent repository facts. Return a usable prompt, not commentary about
how to write one.

Context metadata:
- source mode: {str(context_mode)[:40]}
- reference attachment count: {max(0, min(10, int(attachment_count or 0)))}

Original draft:
---BEGIN DRAFT---
{clean_brief}
---END DRAFT---

Questions:
{json.dumps(clean_questions, ensure_ascii=False)[:12_000]}

User answers:
{json.dumps(clean_answers, ensure_ascii=False)[:12_000]}

Output only the JSON object required by the supplied schema."""
    payload, model = _run(prompt, FINALIZE_SCHEMA)
    optimized = str(payload.get("optimized_prompt") or "").strip()
    if not optimized:
        raise PromptCoachError("Prompt coach returned an empty optimized brief.")
    payload["optimized_prompt"] = optimized[:MAX_BRIEF_CHARS]
    payload["model"] = model
    return payload
