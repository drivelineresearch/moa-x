#!/usr/bin/env python3
"""test_offline.py — offline smoke test for the orchestrator's parsing layers.

Exercises the JSON Schema validator, the codex/claude/cursor/opencode JSON
extractors, and the broadcast-refiner payload shape without calling any CLI.
Run before end-to-end to confirm parsing logic is sound.

Usage:
    python3 harness/scripts/test_offline.py   # from the moa-x repo root
    # or
    python3 ~/.claude/skills/mixture-of-agents/scripts/test_offline.py   # from the installed skill location
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

import run_moa  # noqa: E402
import report as report_module  # noqa: E402
from adapters import codex as codex_adapter  # noqa: E402
from adapters import claude as claude_adapter  # noqa: E402

PASS = "\033[32mPASS\033[0m"
FAIL = "\033[31mFAIL\033[0m"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_valid_proposer(agent_id: str) -> dict:
    return {
        "agent_id": agent_id,
        "summary": (
            "Add a Redis-backed cache layer in front of the existing intended_zones_db "
            "queries. The hot path is the per-pitch lookup in the dashboard endpoint, "
            "which currently round-trips to MySQL on every request."
        ),
        "plan": [
            {
                "step": "Create a thin RedisCache wrapper in app/cache/redis_cache.py",
                "why": "Centralizes serialization, TTL, and key namespacing in one place",
                "files_touched": ["app/cache/redis_cache.py"],
                "evidence": [
                    {
                        "type": "code",
                        "file": "app/services/intended_zones.py",
                        "line": 42,
                        "url": None,
                        "snippet": "rows = db.query(IntendedZone).filter(...).all()",
                        "claim": "Direct DB query on hot path with no caching",
                    },
                    {
                        "type": "external",
                        "file": None,
                        "line": None,
                        "url": "https://redis.io/docs/manual/keyspace/",
                        "snippet": "Use a colon-separated naming convention",
                        "claim": "Redis keyspace recommendations for namespaced keys",
                    },
                ],
                "risks": ["Cache stampede on cold start", "TTL tuning required"],
            }
        ],
        "open_questions": ["Should the cache invalidate on every game-day?"],
        "alternatives_rejected": [
            {"approach": "in-memory LRU per pod", "reason": "doesn't share across replicas"},
            {"approach": "cache every ORM query", "reason": "invalidates too broadly"},
        ],
        "research_sources": [
            {"url": "https://redis.io/docs/manual/keyspace/", "title": "Redis Keyspace", "summary": "Naming conventions", "relevance": "key design"},
            {"url": "https://github.com/redis/redis-py", "title": "redis-py", "summary": "Python client", "relevance": "library choice"},
            {"url": "https://docs.python.org/3/library/functools.html", "title": "functools", "summary": "lru_cache reference", "relevance": "rejected alternative"},
            {"url": "https://docs.sqlalchemy.org/en/20/orm/queryguide/cache.html", "title": "SQLA query cache", "summary": "ORM cache option", "relevance": "rejected alternative"},
            {"url": "https://aws.amazon.com/elasticache/", "title": "ElastiCache", "summary": "Managed Redis", "relevance": "deployment option"},
        ],
    }


VALID_PROPOSER_CODEX = _make_valid_proposer("codex")
VALID_PROPOSER_GLM = _make_valid_proposer("glm")
VALID_PROPOSER_SONNET = _make_valid_proposer("sonnet")

INVALID_PROPOSER_PAYLOAD_MISSING_FIELD = {
    "agent_id": "glm",
    "summary": "x" * 80,
    # plan missing
    "open_questions": [],
    "alternatives_rejected": [],
    "research_sources": [],
}

INVALID_PROPOSER_PAYLOAD_BAD_ENUM = {
    "agent_id": "claude",  # valid pattern, but not a configured provider name
    "summary": "x" * 80,
    "plan": [
        {
            "step": "do thing",
            "why": "reasons",
            "files_touched": [],
            "evidence": [],
            "risks": [],
        }
    ],
    "open_questions": [],
    "alternatives_rejected": [],
    "research_sources": [
        {"url": "u", "title": "t", "summary": "s", "relevance": "r"},
        {"url": "u", "title": "t", "summary": "s", "relevance": "r"},
        {"url": "u", "title": "t", "summary": "s", "relevance": "r"},
        {"url": "u", "title": "t", "summary": "s", "relevance": "r"},
        {"url": "u", "title": "t", "summary": "s", "relevance": "r"},
    ],
}


# Payload that violates the nullable-type contract for evidence.items.
# Missing `url` and `snippet` keys entirely — strict mode needs all keys present.
INVALID_PROPOSER_PAYLOAD_MISSING_EVIDENCE_KEY = {
    "agent_id": "codex",
    "summary": "x" * 80,
    "plan": [
        {
            "step": "do thing",
            "why": "reasons",
            "files_touched": ["a.py"],
            "evidence": [
                {
                    "type": "code",
                    "file": "a.py",
                    "line": 10,
                    "claim": "claim",
                    # missing url and snippet
                }
            ],
            "risks": [],
        }
    ],
    "open_questions": [],
    "alternatives_rejected": [],
    "research_sources": [
        {"url": "u", "title": "t", "summary": "s", "relevance": "r"},
        {"url": "u", "title": "t", "summary": "s", "relevance": "r"},
        {"url": "u", "title": "t", "summary": "s", "relevance": "r"},
        {"url": "u", "title": "t", "summary": "s", "relevance": "r"},
        {"url": "u", "title": "t", "summary": "s", "relevance": "r"},
    ],
}


SAMPLE_CODEX_STDOUT = (
    "OpenAI Codex v0.118.0 (research preview)\n"
    "--------\n"
    "workdir: /home/example/repo\n"
    "model: gpt-5.4\n"
    "approval: never\n"
    "sandbox: read-only\n"
    "--------\n"
    "user\n"
    "Build me a plan for adding a cache layer.\n\n"
    "codex\n"
    "I'll think about this and produce a structured plan.\n"
    "codex\n"
    + json.dumps(VALID_PROPOSER_CODEX)
    + "\n"
    "tokens used\n"
    "12345\n"
)


# Claude Code --output-format json envelope with --json-schema set:
# structured_output contains the validated object, result is empty string.
SAMPLE_CLAUDE_STDOUT_STRUCTURED = json.dumps(
    {
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "duration_ms": 45000,
        "result": "",
        "session_id": "fake-claude-session",
        "total_cost_usd": 0.35,
        "structured_output": VALID_PROPOSER_SONNET,
        "usage": {},
        "modelUsage": {
            "claude-sonnet-4-6": {"inputTokens": 5, "outputTokens": 1500},
        },
    }
)


# Claude Code --output-format json envelope without --json-schema:
# result contains fenced JSON, no structured_output field.
SAMPLE_CLAUDE_STDOUT_FENCED = json.dumps(
    {
        "type": "result",
        "subtype": "success",
        "result": (
            "Here is the proposal:\n\n"
            "```json\n"
            + json.dumps(VALID_PROPOSER_SONNET)
            + "\n```\n"
        ),
        "session_id": "fake-claude-session",
    }
)


SAMPLE_CURSOR_STDOUT_SUCCESS = json.dumps({
    "type": "result",
    "subtype": "success",
    "is_error": False,
    "duration_ms": 8394,
    "result": json.dumps(VALID_PROPOSER_CODEX),  # the model returned bare JSON
    "session_id": "abc-123",
    "request_id": "req-456",
    "usage": {"inputTokens": 100, "outputTokens": 500,
              "cacheReadTokens": 0, "cacheWriteTokens": 0},
})

# cursor-grok routes through the cursor harness (built-in cursor-grok ->
# cursor-grok-4.5-high). Cursor wraps the model's bare-JSON output in its
# standard result envelope; the adapter's _extract_payload pulls it out. This
# fixture is the parser-recipe evidence; a live
# `cursor-agent -p --model cursor-grok-4.5-high --output-format json` proposer
# run matches this envelope shape exactly.
SAMPLE_CURSOR_GROK_STDOUT = json.dumps({
    "type": "result",
    "subtype": "success",
    "is_error": False,
    "duration_ms": 9002,
    "result": json.dumps(_make_valid_proposer("cursor-grok")),
    "session_id": "cg-1",
    "request_id": "req-cg-1",
    "usage": {"inputTokens": 130, "outputTokens": 650},
})

SAMPLE_CURSOR_STDOUT_FENCED = json.dumps({
    "type": "result",
    "subtype": "success",
    "is_error": False,
    "duration_ms": 8394,
    "result": "Here is the JSON:\n```json\n" + json.dumps(VALID_PROPOSER_CODEX) + "\n```",
    "session_id": "abc-123",
    "request_id": "req-456",
    "usage": {"inputTokens": 100, "outputTokens": 500,
              "cacheReadTokens": 0, "cacheWriteTokens": 0},
})

SAMPLE_CURSOR_STDOUT_ERROR = json.dumps({
    "type": "result",
    "subtype": "error",
    "is_error": True,
    "duration_ms": 100,
    "result": "rate limit exceeded; please try again in 60 seconds",
    "session_id": "abc-123",
    "request_id": "req-456",
})

# Empirically observed: cursor-agent reports a success envelope but result is
# empty. No quota / auth signal in stderr. The transient pattern that drives
# the redispatch user prompt.
SAMPLE_CURSOR_STDOUT_TRANSIENT_EMPTY = json.dumps({
    "type": "result",
    "subtype": "success",
    "is_error": False,
    "duration_ms": 4321,
    "result": "",
    "session_id": "abc-123",
    "request_id": "req-456",
    "usage": {"inputTokens": 100, "outputTokens": 0,
              "cacheReadTokens": 0, "cacheWriteTokens": 0},
})

# Same envelope shape but with a quota signal in stderr — should NOT be
# treated as transient since redispatch won't help.
SAMPLE_CURSOR_STDERR_QUOTA = "rate limit exceeded for your plan; retry after 60s\n"

# OpenCode emits the model's final text straight to stdout (no JSON envelope),
# so the shared extractor runs directly on it. Payload may be bare or fenced;
# empty stdout under a clean exit is the transient flake.
SAMPLE_OPENCODE_STDOUT_BARE = json.dumps(VALID_PROPOSER_CODEX)
# Grok routes through the opencode harness (built-in provider `grok` →
# xai/grok-4.5). opencode emits the model's final text straight to stdout with
# no JSON envelope, so a Grok proposer's output is a bare (or fenced) JSON
# object the shared extractor runs on directly. This fixture is the parser
# recipe evidence for the built-in grok provider; a live
# `opencode run -m xai/grok-4.5` proposer run matches this shape.
SAMPLE_OPENCODE_GROK_STDOUT = json.dumps(_make_valid_proposer("grok"))  # bare JSON, agent_id "grok"
SAMPLE_OPENCODE_STDOUT_FENCED = (
    "I read the repo and here is the plan:\n\n```json\n"
    + json.dumps(VALID_PROPOSER_CODEX) + "\n```\n"
)
SAMPLE_OPENCODE_STDERR_QUOTA = "Error: 429 quota exceeded for provider zhipuai\n"

def _make_valid_broadcast_refiner(agent_id: str) -> dict:
    """Build a valid broadcast-refiner payload (sees all 3 proposers)."""
    return {
        "agent_id": agent_id,
        "reviewing": ["codex", "glm", "sonnet"],
        "overall_verdict": "converge_with_changes",
        "per_proposer_verdicts": [
            {
                "proposer": "codex",
                "verdict": "accept_with_changes",
                "summary": "Strong plan; missing metrics step, TTL too aggressive.",
            },
            {
                "proposer": "glm",
                "verdict": "accept_with_changes",
                "summary": "Solid evidence citations; suggests wrong library version.",
            },
            {
                "proposer": "sonnet",
                "verdict": "accept_as_is",
                "summary": "Cleanest plan with best risk analysis and real file citations.",
            },
        ],
        "cross_proposer_observations": [
            "All three proposers chose Redis over in-memory cache — strong convergence",
            "codex and sonnet agree on TTL=300s; glm suggests 60s (unresolved)",
            "Only sonnet mentions metrics; others missed it",
        ],
        "verifications": [
            {
                "proposer": "codex",
                "claim_index_path": "plan[0].evidence[0]",
                "status": "verified",
                "actual_finding": "File exists and contains the cited code at line 42.",
                "source_url": "app/services/intended_zones.py:42",
            },
            {
                "proposer": "glm",
                "claim_index_path": "plan[1].evidence[0]",
                "status": "unverified",
                "actual_finding": "Could not locate the cited file; may have been renamed.",
                "source_url": None,
            },
        ],
        "agreements": [
            "All three agree on Redis as the cache backend (strong signal).",
            "All three agree the hot path is the intended_zones dashboard query.",
        ],
        "disagreements": [
            {
                "proposer": "glm",
                "point": "TTL of 60s is too aggressive",
                "why": "We saw cache thrashing in a similar service",
                "what_to_do_instead": "Start at 5 minutes and tune down",
            }
        ],
        "missing_steps": ["Add metrics for cache hit rate (only sonnet mentioned this)"],
        "incorrect_steps": [
            {
                "proposer": "glm",
                "step_index": 2,
                "what_is_wrong": "Cites redis-py 4.0 API which is no longer current",
            }
        ],
        "synthesis_recommendation": (
            "Use sonnet's plan as the base since it is the cleanest and includes "
            "metrics. Adopt codex's TTL=300s over glm's 60s (verified via cache "
            "thrashing research). Pull glm's evidence citations for the DB hot "
            "path since they are the most specific. Reject glm's outdated "
            "redis-py API call."
        ),
        "additional_research": [
            {"url": "u1", "title": "t1", "what_it_adds": "stampede mitigation"},
            {"url": "u2", "title": "t2", "what_it_adds": "ttl tuning"},
            {"url": "u3", "title": "t3", "what_it_adds": "redis client retry"},
            {"url": "u4", "title": "t4", "what_it_adds": "monitoring"},
            {"url": "u5", "title": "t5", "what_it_adds": "deployment"},
        ],
    }


def _make_valid_final_lineage() -> dict:
    return {
        "version": 1,
        "title": "Add the Redis cache safely",
        "summary": "Adopt the shared cache approach with the verified hot-path evidence.",
        "confidence": {
            "level": "medium",
            "rationale": "The approach converged and the primary code claim was verified.",
        },
        "steps": [
            {
                "id": "add-redis-wrapper",
                "title": "Add the Redis wrapper",
                "description": "Create the shared cache wrapper around the hot query path.",
                "files_touched": ["app/cache/redis_cache.py"],
                "decision": "revised",
                "adjudication": "Adapts the proposer step using the refiner's verified evidence.",
                "proposer_refs": [
                    {
                        "agent_id": "codex",
                        "step_index": 0,
                        "relationship": "adapted",
                        "note": "Supplied the cache wrapper and key strategy.",
                    }
                ],
                "refiner_refs": [
                    {
                        "agent_id": "kimi",
                        "kind": "verification",
                        "index": 0,
                        "note": "Verified the cited hot-path query.",
                    }
                ],
            }
        ],
        "rejected_inputs": [],
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def _check(label: str, condition: bool, detail: str = "") -> bool:
    print(f"  [{PASS if condition else FAIL}] {label}" + (f"  -- {detail}" if detail else ""))
    return condition


def _ok(condition: bool, detail: str = "") -> bool:
    status = PASS if condition else FAIL
    print(f"  [{status}]" + (f"  -- {detail}" if detail else ""))
    return condition


def test_schema_validator_accepts_valid_codex_payload() -> bool:
    print("\n[1] Schema validator accepts a valid codex proposer payload")
    schema = run_moa._load_schema(run_moa.PROPOSER_SCHEMA_PATH)
    errors = run_moa._validate_against_schema(VALID_PROPOSER_CODEX, schema)
    return _check("no errors", len(errors) == 0, f"errors={errors[:3]}")


def test_schema_validator_accepts_valid_sonnet_payload() -> bool:
    print("\n[2] Schema validator accepts a valid sonnet proposer payload")
    schema = run_moa._load_schema(run_moa.PROPOSER_SCHEMA_PATH)
    errors = run_moa._validate_against_schema(VALID_PROPOSER_SONNET, schema)
    return _check("no errors", len(errors) == 0, f"errors={errors[:3]}")


def test_schema_validator_rejects_missing_field() -> bool:
    print("\n[3] Schema validator rejects payload with missing required field")
    schema = run_moa._load_schema(run_moa.PROPOSER_SCHEMA_PATH)
    errors = run_moa._validate_against_schema(INVALID_PROPOSER_PAYLOAD_MISSING_FIELD, schema)
    has_plan_error = any("plan" in e for e in errors)
    return _check("flagged missing 'plan' field", has_plan_error, f"errors={errors[:3]}")


def test_schema_validator_rejects_bad_agent_id_pattern() -> bool:
    print("\n[4] Schema validator rejects agent_id that violates the regex pattern")
    schema = run_moa._load_schema(run_moa.PROPOSER_SCHEMA_PATH)
    bad_payload = _make_valid_proposer("Bad Name!")  # uppercase + space + bang
    errors = run_moa._validate_against_schema(bad_payload, schema)
    print(f"  errors: {errors}")
    has_pattern_error = any("pattern" in e for e in errors)
    return _check("expected pattern violation", has_pattern_error, "saw: " + str(errors))


def test_schema_validator_rejects_missing_evidence_key() -> bool:
    print("\n[4b] Schema validator rejects evidence item missing a required nullable key")
    schema = run_moa._load_schema(run_moa.PROPOSER_SCHEMA_PATH)
    errors = run_moa._validate_against_schema(INVALID_PROPOSER_PAYLOAD_MISSING_EVIDENCE_KEY, schema)
    has_url_error = any("url" in e and "required" in e for e in errors)
    return _check("flagged missing evidence.url", has_url_error, f"errors={errors[:3]}")


def test_strict_mode_lint_clean_on_current_schemas() -> bool:
    print("\n[4c] Strict-mode lint: all schemas are OpenAI-compliant")
    p_schema = run_moa._load_schema(run_moa.PROPOSER_SCHEMA_PATH)
    r_schema = run_moa._load_schema(run_moa.REFINER_SCHEMA_PATH)
    f_schema = run_moa._load_schema(run_moa.FINAL_PLAN_SCHEMA_PATH)
    p_violations = run_moa.lint_schema_openai_strict(p_schema)
    r_violations = run_moa.lint_schema_openai_strict(r_schema)
    f_violations = run_moa.lint_schema_openai_strict(f_schema)
    clean = not p_violations and not r_violations and not f_violations
    detail = (
        f"proposer={len(p_violations)} refiner={len(r_violations)} "
        f"final={len(f_violations)}"
    )
    return _check("all schemas strict-mode clean", clean, detail)


def test_final_plan_schema_resolves_local_refs() -> bool:
    print("\n[4cc] Final-plan lineage schema validates local references and bounds")
    schema = run_moa._load_schema(run_moa.FINAL_PLAN_SCHEMA_PATH)
    valid_errors = run_moa._validate_against_schema(_make_valid_final_lineage(), schema)
    invalid = _make_valid_final_lineage()
    invalid["steps"][0]["proposer_refs"][0]["step_index"] = -1
    invalid_errors = run_moa._validate_against_schema(invalid, schema)
    ok = not valid_errors and any("minimum" in e for e in invalid_errors)
    return _check("local $refs enforced", ok, f"valid={valid_errors}, invalid={invalid_errors[:2]}")


def test_strict_mode_lint_catches_violation() -> bool:
    print("\n[4d] Strict-mode lint catches a violation injected into a test schema")
    bad_schema = {
        "type": "object",
        "additionalProperties": False,
        "required": ["a"],
        "properties": {
            "a": {"type": "string"},
            "b": {"type": "string"},  # not in required
        },
    }
    violations = run_moa.lint_schema_openai_strict(bad_schema)
    flagged = any("b" in v for v in violations)
    return _check("lint caught missing-required-field violation", flagged, f"violations={violations}")


def test_codex_extractor_finds_payload_in_framed_output() -> bool:
    print("\n[5] Codex JSON extractor finds payload in framed CLI output")
    payload = codex_adapter._extract_json_payload(SAMPLE_CODEX_STDOUT)
    found = isinstance(payload, dict)
    matches = found and payload.get("agent_id") == "codex"
    return _check("payload found and matches", matches,
                  f"agent_id={payload.get('agent_id') if isinstance(payload, dict) else None}")


def test_claude_extractor_finds_structured_output() -> bool:
    print("\n[8] Claude extractor reads structured_output (when --json-schema was used)")
    payload = claude_adapter._extract_structured_output(SAMPLE_CLAUDE_STDOUT_STRUCTURED)
    found = isinstance(payload, dict)
    matches = found and payload.get("agent_id") == "sonnet"
    return _check("structured_output found and matches", matches,
                  f"agent_id={payload.get('agent_id') if isinstance(payload, dict) else None}")


def test_claude_extractor_fallback_to_fenced_result() -> bool:
    print("\n[9] Claude extractor falls back to fenced JSON in .result when no structured_output")
    payload = claude_adapter._extract_structured_output(SAMPLE_CLAUDE_STDOUT_FENCED)
    found = isinstance(payload, dict)
    matches = found and payload.get("agent_id") == "sonnet"
    return _check("fenced payload found and matches", matches,
                  f"agent_id={payload.get('agent_id') if isinstance(payload, dict) else None}")


def test_claude_schema_copy_omits_dialect_metadata() -> bool:
    print("\n[N] Claude CLI schema copy omits unsupported $schema metadata")
    schema_path = SCRIPT_DIR / "schemas" / "proposer.schema.json"
    schema_json = claude_adapter._schema_json_for_cli(schema_path)
    cli_schema = json.loads(schema_json)
    cmd = claude_adapter._build_cmd(
        "claude",
        model="claude-sonnet-5",
        schema_json=schema_json,
        system_prompt_suffix="read only",
        reasoning_effort="high",
    )
    ok = (
        "$schema" not in cli_schema
        and "agent_id" in cli_schema.get("required", [])
        and "--safe-mode" in cmd
        and cmd[cmd.index("--tools") + 1] == claude_adapter.SONNET_READONLY_TOOLS
        and cmd[cmd.index("--effort") + 1] == "high"
    )
    return _ok(
        ok,
        (
            f"keys={list(cli_schema)[:6]}, "
            f"isolation={claude_adapter.CLAUDE_ISOLATION_FLAG}, tools=read-only"
        ),
    )


def test_cursor_extractor_finds_payload_in_bare_result() -> bool:
    print("\n[N] cursor._extract_payload returns inner JSON from bare result text")
    from adapters import cursor as cursor_adapter
    payload = cursor_adapter._extract_payload(SAMPLE_CURSOR_STDOUT_SUCCESS)
    ok = payload is not None and payload.get("agent_id") == "codex"
    return _ok(ok, f"got {payload!r}")

def test_cursor_extractor_handles_fenced_json() -> bool:
    print("\n[N] cursor._extract_payload pulls JSON out of ```json fences in result text")
    from adapters import cursor as cursor_adapter
    payload = cursor_adapter._extract_payload(SAMPLE_CURSOR_STDOUT_FENCED)
    ok = payload is not None and payload.get("agent_id") == "codex"
    return _ok(ok, f"got {payload!r}")

def test_cursor_extractor_returns_none_on_is_error() -> bool:
    print("\n[N] cursor._extract_payload returns None when envelope is_error=true")
    from adapters import cursor as cursor_adapter
    payload = cursor_adapter._extract_payload(SAMPLE_CURSOR_STDOUT_ERROR)
    return _ok(payload is None, f"got {payload!r}")


def test_cursor_diagnose_failure_flags_transient_empty() -> bool:
    print("\n[N] cursor._diagnose_failure flags empty result + clean stderr as transient_empty")
    from adapters import cursor as cursor_adapter
    msg, transient = cursor_adapter._diagnose_failure(
        SAMPLE_CURSOR_STDOUT_TRANSIENT_EMPTY, ""
    )
    return _ok(transient is True and "transient" in msg.lower(),
               f"transient={transient}, msg={msg!r}")


def test_cursor_diagnose_progress_only_is_transient() -> bool:
    print("\n[N] cursor progress-only success envelopes receive one bounded retry")
    from adapters import cursor as cursor_adapter
    envelope = json.dumps({
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "result": "Researching the requested architecture before drafting.",
    })
    msg, transient = cursor_adapter._diagnose_failure(envelope, "")
    return _ok(
        transient is True and "incomplete" in msg.lower(),
        f"transient={transient}, msg={msg!r}",
    )


def test_cursor_diagnose_failure_quota_is_not_transient() -> bool:
    print("\n[N] cursor._diagnose_failure does NOT flag transient when quota signal in stderr")
    from adapters import cursor as cursor_adapter
    msg, transient = cursor_adapter._diagnose_failure(
        SAMPLE_CURSOR_STDOUT_TRANSIENT_EMPTY, SAMPLE_CURSOR_STDERR_QUOTA
    )
    return _ok(transient is False and "rate-limit" in msg.lower(),
               f"transient={transient}, msg={msg!r}")


def test_cursor_diagnose_failure_empty_stdout_is_not_transient() -> bool:
    print("\n[N] cursor._diagnose_failure does NOT flag transient when stdout is entirely empty")
    from adapters import cursor as cursor_adapter
    msg, transient = cursor_adapter._diagnose_failure("", "")
    return _ok(transient is False and "empty stdout" in msg.lower(),
               f"transient={transient}, msg={msg!r}")


def test_cursor_result_carries_transient_empty_field() -> bool:
    print("\n[N] CursorResult dataclass exposes transient_empty (default False)")
    from adapters import cursor as cursor_adapter
    r = cursor_adapter.CursorResult(
        success=True, payload={}, raw_stdout="", raw_stderr="",
        exit_code=0, duration_seconds=1.0,
    )
    return _ok(r.transient_empty is False, f"got {r.transient_empty!r}")


def test_layer_result_carries_transient_empty_field() -> bool:
    print("\n[N] LayerResult dataclass exposes transient_empty (default False)")
    r = run_moa.LayerResult(agent_id="cursor-grok", layer=1, role="proposer")
    return _ok(r.transient_empty is False, f"got {r.transient_empty!r}")


def test_manifest_summary_includes_transient_empty_arrays() -> bool:
    print("\n[N] write_manifest summary surfaces transient_empty proposer/refiner names")
    import tempfile, shutil, json as _json
    tmp = Path(tempfile.mkdtemp())
    try:
        layer1 = [
            run_moa.LayerResult(agent_id="codex", layer=1, role="proposer", success=True),
            run_moa.LayerResult(agent_id="cursor-grok", layer=1, role="proposer",
                                success=False, transient_empty=True,
                                error="cursor-agent returned empty result text"),
        ]
        layer2 = [
            run_moa.LayerResult(agent_id="kimi", layer=2, role="refiner-broadcast",
                                success=False, transient_empty=True),
        ]
        run_moa.write_manifest(
            session_dir=tmp,
            scout_brief={"session_id": "smoke"},
            layer1=layer1, layer2=layer2,
            started_at=0.0, finished_at=1.0,
            config={}, layer2_mode="broadcast",
        )
        manifest = _json.loads((tmp / "manifest.json").read_text())
        summary = manifest["summary"]
        ok = (summary["transient_empty_proposers"] == ["cursor-grok"]
              and summary["transient_empty_refiners"] == ["kimi"])
        return _ok(ok, f"summary={summary!r}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_layer1_manifest_round_trip_via_load() -> bool:
    print("\n[N] write_layer1_manifest + load_layer_results_from_manifest round-trip")
    import tempfile, shutil
    tmp = Path(tempfile.mkdtemp())
    try:
        # Pretend codex succeeded and wrote a payload file; cursor-grok went transient.
        (tmp / "layer1").mkdir(parents=True, exist_ok=True)
        payload_file = tmp / "layer1" / "codex-proposer.json"
        payload_file.write_text('{"agent_id": "codex", "summary": "ok"}', encoding="utf-8")
        layer1 = [
            run_moa.LayerResult(
                agent_id="codex", layer=1, role="proposer", success=True,
                schema_valid=True, duration_seconds=12.3,
                json_path="layer1/codex-proposer.json",
                log_path="layer1/codex-proposer.log",
            ),
            run_moa.LayerResult(
                agent_id="cursor-grok", layer=1, role="proposer", success=False,
                duration_seconds=4.5, transient_empty=True,
                error="cursor-agent returned empty result text under a success envelope",
            ),
        ]
        manifest_path = run_moa.write_layer1_manifest(
            session_dir=tmp,
            scout_brief={"session_id": "smoke"},
            layer1=layer1,
            started_at=0.0, finished_at=10.0,
            config={"arm": "cross-lab"},
        )
        loaded = run_moa.load_layer_results_from_manifest(manifest_path, "layer1", tmp)
        codex = next(r for r in loaded if r.agent_id == "codex")
        cursor_grok = next(r for r in loaded if r.agent_id == "cursor-grok")
        ok = (
            codex.success and codex.payload is not None and codex.payload.get("agent_id") == "codex"
            and cursor_grok.transient_empty is True
            and cursor_grok.payload is None
        )
        return _ok(ok, f"codex.payload={codex.payload!r}, "
                       f"cursor_grok.transient_empty={cursor_grok.transient_empty}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_redispatch_attempt_keeps_timing_provenance() -> bool:
    print("\n[N] redispatch records the replaced attempt for the timeline")
    prior = run_moa.LayerResult(
        agent_id="cursor-grok",
        layer=1,
        role="proposer",
        started_at=100.0,
        duration_seconds=12.0,
        transient_empty=True,
        error="incomplete output",
    )
    retry = run_moa.LayerResult(
        agent_id="cursor-grok",
        layer=1,
        role="proposer",
        started_at=140.0,
        duration_seconds=22.0,
        success=True,
    )
    run_moa.mark_redispatch_attempts([retry], [prior])
    ok = (
        retry.attempt == 2
        and retry.previous_attempt is not None
        and retry.previous_attempt["started_at"] == 100.0
        and retry.previous_attempt["transient_empty"] is True
    )
    return _ok(ok, f"attempt={retry.attempt} previous={retry.previous_attempt}")


def test_session_started_at_survives_phase_split_and_redispatch() -> bool:
    print("\n[N] resumed sessions preserve the earliest retained Layer 1 start")
    retained = [
        run_moa.LayerResult(
            agent_id="codex", layer=1, role="proposer", started_at=100.0
        )
    ]
    start = run_moa.session_started_at_from_manifest(
        {"started_at": 220.0}, retained, fallback=300.0
    )
    return _ok(start == 100.0, f"start={start}")


def test_parse_redispatch_arg_validates_names() -> bool:
    print("\n[N] parse_redispatch_arg rejects names not in the layer (sys.exit 2)")
    import contextlib, io
    valid = ["codex", "glm", "cursor-grok"]
    # Happy path
    names = run_moa.parse_redispatch_arg("codex,cursor-grok", valid, "proposers")
    if names != ["codex", "cursor-grok"]:
        return _ok(False, f"happy path returned {names!r}")
    # Empty / None → None
    if run_moa.parse_redispatch_arg(None, valid, "proposers") is not None:
        return _ok(False, "None input did not return None")
    # Invalid name → sys.exit(2)
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        try:
            run_moa.parse_redispatch_arg("codex,bogus", valid, "proposers")
            return _ok(False, "did not exit on invalid name")
        except SystemExit as e:
            ok = e.code == 2 and "bogus" in err.getvalue()
            return _ok(ok, f"exit_code={e.code}, stderr={err.getvalue()!r}")


def test_refiner_schema_validator_broadcast_codex() -> bool:
    print("\n[10] Refiner schema validator accepts broadcast codex refiner payload")
    schema = run_moa._load_schema(run_moa.REFINER_SCHEMA_PATH)
    payload = _make_valid_broadcast_refiner("codex")
    errors = run_moa._validate_against_schema(payload, schema)
    return _check("no errors", len(errors) == 0, f"errors={errors[:3]}")


def test_refiner_schema_validator_broadcast_kimi() -> bool:
    print("\n[11] Refiner schema validator accepts broadcast kimi refiner payload")
    schema = run_moa._load_schema(run_moa.REFINER_SCHEMA_PATH)
    payload = _make_valid_broadcast_refiner("kimi")
    errors = run_moa._validate_against_schema(payload, schema)
    return _check("no errors", len(errors) == 0, f"errors={errors[:3]}")


def test_refiner_schema_accepts_user_named_provider_refs() -> bool:
    """Regression: when proposers are user-named (e.g. all routed through cursor as
    c-gpt / c-gemini / c-opus), the refiner echoes those IDs back in `reviewing`,
    `per_proposer_verdicts[].proposer`, `verifications[].proposer`, etc. The
    schema must accept them — Phase 1.2 only loosened the top-level agent_id;
    five proposer-id reference sites needed the same loosening."""
    print("\n[11b] Refiner schema accepts user-named provider refs (c-gpt, c-gemini, c-opus)")
    schema = run_moa._load_schema(run_moa.REFINER_SCHEMA_PATH)
    payload = _make_valid_broadcast_refiner("c-gpt")
    payload["reviewing"] = ["c-gpt", "c-gemini", "c-opus"]
    payload["per_proposer_verdicts"] = [
        {"proposer": "c-gpt",    "verdict": "accept_with_changes",
         "summary": "Strong plan; missing metrics step, TTL too aggressive."},
        {"proposer": "c-gemini", "verdict": "accept_with_changes",
         "summary": "Solid evidence citations; suggests wrong library version."},
        {"proposer": "c-opus",   "verdict": "accept_as_is",
         "summary": "Cleanest plan with best risk analysis and real file citations."},
    ]
    payload["verifications"] = [
        {"proposer": "c-gpt", "claim_index_path": "plan[0].evidence[0]",
         "status": "verified", "actual_finding": "File exists at line 42.",
         "source_url": "app/services/intended_zones.py:42"},
        {"proposer": "c-gemini", "claim_index_path": "plan[1].evidence[0]",
         "status": "unverified", "actual_finding": "Could not locate cited file.",
         "source_url": None},
    ]
    payload["disagreements"] = [
        {"proposer": "c-gemini", "point": "TTL of 60s is too aggressive",
         "why": "We saw cache thrashing in a similar service",
         "what_to_do_instead": "Start at 5 minutes and tune down"},
    ]
    payload["incorrect_steps"] = [
        {"proposer": "c-gemini", "step_index": 2,
         "what_is_wrong": "Cites redis-py 4.0 API which is no longer current"},
    ]
    errors = run_moa._validate_against_schema(payload, schema)
    return _check("no errors with user-named provider refs", len(errors) == 0, f"errors={errors[:3]}")


def test_refiner_schema_rejects_malformed_proposer_ref() -> bool:
    """Negative: confirm the new pattern enforcement actually fires — a
    proposer reference with uppercase/space/punctuation must be rejected."""
    print("\n[11c] Refiner schema rejects malformed proposer ref (regex pattern fires)")
    schema = run_moa._load_schema(run_moa.REFINER_SCHEMA_PATH)
    payload = _make_valid_broadcast_refiner("codex")
    payload["reviewing"] = ["Bad Name!", "glm", "sonnet"]   # uppercase + space + bang
    errors = run_moa._validate_against_schema(payload, schema)
    has_pattern_error = any("pattern" in e for e in errors)
    return _check("flagged pattern violation in reviewing[]", has_pattern_error, f"errors={errors[:3]}")


def test_evidence_cross_field_rejects_code_with_null_file() -> bool:
    print("\n[12a] _validate_evidence_cross_fields rejects type=code with null file")
    payload = {
        "plan": [
            {
                "evidence": [
                    {"type": "code", "file": None, "line": 42, "url": None, "snippet": None, "claim": "c"},
                ]
            }
        ]
    }
    errors = run_moa._validate_evidence_cross_fields(payload)
    flagged = any("type=code requires non-null file" in e for e in errors)
    return _check("flagged null file on code evidence", flagged, f"errors={errors[:3]}")


def test_evidence_cross_field_rejects_external_with_null_url() -> bool:
    print("\n[12b] _validate_evidence_cross_fields rejects type=external with null url")
    payload = {
        "plan": [
            {
                "evidence": [
                    {"type": "external", "file": None, "line": None, "url": None, "snippet": "s", "claim": "c"},
                ]
            }
        ]
    }
    errors = run_moa._validate_evidence_cross_fields(payload)
    flagged = any("type=external requires non-null url" in e for e in errors)
    return _check("flagged null url on external evidence", flagged, f"errors={errors[:3]}")


def test_evidence_cross_field_accepts_valid_payload() -> bool:
    print("\n[12c] _validate_evidence_cross_fields accepts the valid fixture")
    errors = run_moa._validate_evidence_cross_fields(VALID_PROPOSER_CODEX)
    return _check("no errors on valid proposer payload", len(errors) == 0, f"errors={errors[:3]}")


def test_finalize_result_fails_closed_on_cross_field_evidence() -> bool:
    print("\n[12d] _finalize_result rejects cross-field evidence violations")
    import tempfile

    payload = json.loads(json.dumps(VALID_PROPOSER_GLM))
    payload["plan"][0]["evidence"][1]["snippet"] = None
    result = run_moa.LayerResult(
        agent_id="glm",
        layer=1,
        role="proposer",
        success=True,
        payload=payload,
    )
    with tempfile.TemporaryDirectory() as tdir:
        session = Path(tdir)
        persisted = session / "layer1" / "glm-proposer.json"
        persisted.parent.mkdir(parents=True)
        persisted.write_text('{"stale": true}', encoding="utf-8")
        run_moa._finalize_result(
            result,
            payload,
            run_moa.PROPOSER_SCHEMA_PATH,
            session,
        )
        ok = (
            not result.success
            and not result.schema_valid
            and "evidence cross-field violations" in str(result.error)
            and not persisted.exists()
        )
    return _check(
        "invalid evidence is rejected before persistence",
        ok,
        f"success={result.success} schema_valid={result.schema_valid} error={result.error}",
    )


def test_unsupported_keyword_warning() -> bool:
    print("\n[12e] _validate_against_schema warns on unsupported keywords (anyOf, if, oneOf)")
    import warnings
    # Reset dedup cache so this test is reproducible
    run_moa._warned_keywords.clear()
    bad_schema = {
        "type": "object",
        "anyOf": [{"type": "object"}],  # unsupported
        "properties": {
            "x": {"type": "string", "oneOf": [{"const": "a"}]},  # unsupported
        },
    }
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        run_moa._validate_against_schema({}, bad_schema)
    messages = [str(w.message) for w in caught]
    flagged_any_of = any("anyOf" in m for m in messages)
    return _check("warned about anyOf", flagged_any_of, f"warnings={len(messages)}")


def test_manifest_config_section_present() -> bool:
    print("\n[12e] write_manifest includes a `config` section")
    import inspect
    sig = inspect.signature(run_moa.write_manifest)
    has_config_param = "config" in sig.parameters
    return _check("write_manifest accepts config kwarg", has_config_param,
                  f"parameters={list(sig.parameters.keys())}")


def test_config_precedence_env_over_dotenv_over_yaml() -> bool:
    print("\n[13] config loader precedence: shell env > .env > config.yaml")
    import config
    import os
    import tempfile

    # Round-trip precedence: write a yaml, a .env, and set a shell-env
    # var that each disagree on MOA_CODEX_MODEL. Confirm the right one wins.
    with tempfile.TemporaryDirectory() as tdir:
        tdir_p = Path(tdir)
        yaml_path = tdir_p / "config.yaml"
        env_path = tdir_p / ".env"
        yaml_path.write_text("providers:\n  codex:\n    model: yaml-model\n")
        env_path.write_text("MOA_CODEX_MODEL=dotenv-model\n")

        # Case 1: shell env wins over .env and yaml
        prior = os.environ.pop("MOA_CODEX_MODEL", None)
        try:
            os.environ["MOA_CODEX_MODEL"] = "shell-model"
            config.apply_config_to_env(
                config_path=yaml_path, dotenv_path=env_path, overwrite=False,
            )
            if os.environ.get("MOA_CODEX_MODEL") != "shell-model":
                return _check(
                    "shell env wins over .env + yaml", False,
                    f"got {os.environ.get('MOA_CODEX_MODEL')!r}, expected 'shell-model'",
                )
        finally:
            os.environ.pop("MOA_CODEX_MODEL", None)
            if prior is not None:
                os.environ["MOA_CODEX_MODEL"] = prior

        # Case 2: .env wins over yaml when shell env is unset
        prior = os.environ.pop("MOA_CODEX_MODEL", None)
        try:
            config.apply_config_to_env(
                config_path=yaml_path, dotenv_path=env_path, overwrite=True,
            )
            if os.environ.get("MOA_CODEX_MODEL") != "dotenv-model":
                return _check(
                    ".env wins over yaml", False,
                    f"got {os.environ.get('MOA_CODEX_MODEL')!r}, expected 'dotenv-model'",
                )
        finally:
            os.environ.pop("MOA_CODEX_MODEL", None)
            if prior is not None:
                os.environ["MOA_CODEX_MODEL"] = prior

        # Case 3: yaml wins when neither shell env nor .env sets the key
        prior = os.environ.pop("MOA_CODEX_MODEL", None)
        try:
            empty_env = tdir_p / "empty.env"
            empty_env.write_text("# no keys\n")
            config.apply_config_to_env(
                config_path=yaml_path, dotenv_path=empty_env, overwrite=True,
            )
            if os.environ.get("MOA_CODEX_MODEL") != "yaml-model":
                return _check(
                    "yaml wins when .env + shell empty", False,
                    f"got {os.environ.get('MOA_CODEX_MODEL')!r}, expected 'yaml-model'",
                )
        finally:
            os.environ.pop("MOA_CODEX_MODEL", None)
            if prior is not None:
                os.environ["MOA_CODEX_MODEL"] = prior

    return _check("precedence shell > .env > yaml", True, "")


def test_self_moa_argparse_smoke() -> bool:
    print("\n[14] run_moa --help lists --self-moa flag (post-load_arm.py regression)")
    import re
    import subprocess
    proc = subprocess.run(
        [sys.executable, str(SCRIPT_DIR / "run_moa.py"), "--help"],
        capture_output=True, text=True, timeout=30,
    )
    help_text = (proc.stdout or "") + (proc.stderr or "")
    # Presence checks use word-boundary regex so the assertions don't depend
    # on argparse's exact rendering (`--self-moa`, `[--self-moa]`, etc.).
    def _has_flag(name: str) -> bool:
        return re.search(rf"(?<!-){re.escape(name)}(?![A-Za-z0-9_-])", help_text) is not None
    has_self_moa = _has_flag("--self-moa")
    has_proposers = _has_flag("--self-moa-proposers")
    has_refiners = _has_flag("--self-moa-refiners")
    has_reviewer_model = _has_flag("--codex-reviewer-model")
    has_reviewer_effort = _has_flag("--codex-reviewer-effort")
    has_aggregator_model = _has_flag("--aggregator-model")
    has_aggregator_provider = _has_flag("--aggregator-provider")
    has_aggregator_effort = _has_flag("--aggregator-effort")
    has_layer3_phase = "layer3" in help_text
    # --arm should be gone entirely — check for the exact flag token with a
    # trailing non-name char (space, newline, bracket, equals, end-of-string).
    no_arm_flag = re.search(r"(?<!-)--arm(?![A-Za-z0-9_-])", help_text) is None
    ok = all((
        has_self_moa, has_proposers, has_refiners, has_reviewer_model,
        has_reviewer_effort, has_aggregator_model, has_aggregator_provider,
        has_aggregator_effort, has_layer3_phase, no_arm_flag,
    ))
    return _check(
        "--self-moa wired up, --arm removed", ok,
        f"self-moa={has_self_moa} proposers={has_proposers} "
        f"refiners={has_refiners} reviewer-model={has_reviewer_model} "
        f"reviewer-effort={has_reviewer_effort} aggregator={has_aggregator_model} "
        f"no-arm-flag={no_arm_flag}",
    )


def test_install_deps_default_config_only_needs_default_harnesses() -> bool:
    """install_deps.py without harness/config.yaml resolves to the default
    proposers/refiners and needs agy/codex/opencode/claude — not cursor."""
    print("\n[14b] install_deps: default config → needed harnesses {agy, codex, opencode, claude}")
    from config import load_resolved_config
    import tempfile
    from pathlib import Path as _Path
    # Force "no config.yaml" by passing a nonexistent path
    loaded = load_resolved_config(config_path=_Path("/tmp/install_deps_no_yaml_xx_DOES_NOT_EXIST.yaml"))
    needed = {
        p.harness
        for p in loaded.proposers + loaded.refiners + [loaded.aggregator]
    }
    return _ok(
        needed == {"agy", "codex", "opencode", "claude"},
        f"got {sorted(needed)}",
    )


def test_install_deps_cursor_only_config_skips_other_harnesses() -> bool:
    """A cursor-only config means the preflight only needs the cursor harness."""
    print("\n[14c] install_deps: cursor-only config → needed harnesses == {cursor}")
    import tempfile, textwrap
    from pathlib import Path as _Path
    from config import load_resolved_config
    yaml_text = textwrap.dedent("""
        providers:
          c-gpt:    {harness: cursor, model: gpt-5.5-medium}
          c-gemini: {harness: cursor, model: gemini-3.1-pro}
        layers:
          proposers: [c-gpt, c-gemini]
          refiners:  [c-gpt]
          aggregator: c-gpt
    """)
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write(yaml_text)
        tmp_path = _Path(f.name)
    try:
        loaded = load_resolved_config(config_path=tmp_path)
        needed = {p.harness for p in loaded.proposers + loaded.refiners}
        return _ok(needed == {"cursor"}, f"got {sorted(needed)}")
    finally:
        tmp_path.unlink()


def test_install_deps_schema_coherence_catches_bad_name() -> bool:
    """Schema coherence in install_deps must reject names that don't match the
    agent_id regex pattern. Regression for the c-gpt-style mismatch + uppercase
    typos in user configs."""
    print("\n[14d] install_deps: schema coherence catches names that violate the regex")
    import json as _json, re as _re
    from pathlib import Path as _Path
    schema = _json.loads((SCRIPT_DIR / "schemas" / "proposer.schema.json").read_text())
    pattern = schema["properties"]["agent_id"]["pattern"]
    rx = _re.compile(pattern)
    good_names = ["c-gpt", "cursor-grok", "codex", "sonnet-a"]
    bad_names = ["Bad_Name", "C-GPT", "has space", "9-starts-with-digit", "way-too-long-name-that-exceeds-32-chars"]
    good_pass = all(rx.fullmatch(n) for n in good_names)
    bad_fail = not any(rx.fullmatch(n) for n in bad_names)
    return _ok(good_pass and bad_fail,
               f"good_pass={good_pass} bad_fail={bad_fail}; pattern={pattern!r}")


def test_install_deps_qwen_requires_dedicated_key() -> bool:
    print("\n[N] install_deps requires a dedicated sk-sp key for Qwen Token Plan")
    import os as _os
    import install_deps as _install_deps
    from config import LoadedConfig, ResolvedProvider
    loaded = LoadedConfig(
        proposers=[ResolvedProvider("qwen", "opencode", "qwen-token-plan/qwen3.7-max")],
        refiners=[],
        skip_refinement=True,
    )
    old = _os.environ.pop("QWEN_TOKEN_PLAN_API_KEY", None)
    try:
        failures: list[str] = []
        _install_deps._check_provider_credentials(loaded, failures)
        missing_fails = failures == ["Qwen Token Plan credential"]
        _os.environ["QWEN_TOKEN_PLAN_API_KEY"] = "sk-sp-test-only"  # pragma: allowlist secret
        failures = []
        _install_deps._check_provider_credentials(loaded, failures)
        present_passes = not failures
    finally:
        if old is None:
            _os.environ.pop("QWEN_TOKEN_PLAN_API_KEY", None)
        else:
            _os.environ["QWEN_TOKEN_PLAN_API_KEY"] = old
    return _ok(missing_fails and present_passes,
               f"missing_fails={missing_fails} present_passes={present_passes}")


def test_skill_assets_present() -> bool:
    print("\n[15] All required skill assets present on disk")
    skill_dir = SCRIPT_DIR.parent
    assets = [
        skill_dir / "SKILL.md",
        skill_dir / "README.md",
        skill_dir / "prompts" / "scout.md",
        skill_dir / "prompts" / "proposer.md",
        skill_dir / "prompts" / "refiner.md",
        skill_dir / "prompts" / "aggregator.md",
        skill_dir / "scripts" / "run_moa.py",
        skill_dir / "scripts" / "install_deps.py",
        skill_dir / "scripts" / "adapters" / "__init__.py",
        skill_dir / "scripts" / "adapters" / "codex.py",
        skill_dir / "scripts" / "adapters" / "opencode.py",
        skill_dir / "scripts" / "adapters" / "claude.py",
        skill_dir / "scripts" / "adapters" / "cursor.py",
        skill_dir / "scripts" / "schemas" / "proposer.schema.json",
        skill_dir / "scripts" / "schemas" / "refiner.schema.json",
        skill_dir / "scripts" / "schemas" / "final-plan.schema.json",
    ]
    missing = [str(p.relative_to(skill_dir)) for p in assets if not p.exists()]
    return _check("no missing assets", len(missing) == 0, f"missing={missing}")


def test_config_resolve_builtin_codex() -> bool:
    print("\n[16] config.resolve_provider returns built-in codex triple")
    from config import resolve_provider
    rp = resolve_provider("codex", user_providers={})
    ok = (rp.name == "codex" and rp.harness == "codex" and rp.model == "gpt-5.6-terra")
    return _ok(ok, f"got {rp}")

def test_config_resolve_builtin_sonnet_uses_claude_harness() -> bool:
    print("\n[17] config.resolve_provider: sonnet pins Claude Sonnet 5")
    from config import resolve_provider
    rp = resolve_provider("sonnet", user_providers={})
    ok = (
        rp.name == "sonnet"
        and rp.harness == "claude"
        and rp.model == "claude-sonnet-5"
        and rp.effort == "high"
    )
    return _ok(ok, f"got {rp}")

def test_config_resolve_unknown_name_raises() -> bool:
    print("\n[18] config.resolve_provider raises on unknown name")
    from config import resolve_provider
    try:
        resolve_provider("nonexistent-name", user_providers={})
    except ValueError as e:
        return _ok("nonexistent-name" in str(e) and "codex" in str(e),
                   f"error message should list valid names; got: {e}")
    return _ok(False, "expected ValueError")


def test_config_resolve_user_provider_yaml_timeout() -> bool:
    print("\n[18b] config.resolve_provider picks up `timeout:` from YAML user_provider entry")
    from config import resolve_provider
    user = {"slow-grok": {"harness": "cursor", "model": "grok-4-20", "timeout": 1800}}
    rp = resolve_provider("slow-grok", user_providers=user)
    return _ok(rp.timeout == 1800 and rp.model == "grok-4-20", f"got {rp}")


def test_config_resolve_env_timeout_override() -> bool:
    print("\n[18c] config.resolve_provider honors MOA_<NAME>_TIMEOUT env override")
    import os as _os
    from config import resolve_provider
    key = "MOA_SLOW_GROK_TIMEOUT"
    prior = _os.environ.get(key)
    _os.environ[key] = "2400"
    try:
        user = {"slow-grok": {"harness": "cursor", "model": "grok-4-20", "timeout": 1800}}
        rp = resolve_provider("slow-grok", user_providers=user)
        return _ok(rp.timeout == 2400, f"env should win over YAML; got timeout={rp.timeout}")
    finally:
        if prior is None:
            _os.environ.pop(key, None)
        else:
            _os.environ[key] = prior


def test_config_resolve_env_timeout_malformed_raises() -> bool:
    print("\n[18d] config.resolve_provider raises on non-integer MOA_<NAME>_TIMEOUT")
    import os as _os
    from config import resolve_provider
    key = "MOA_SLOW_GROK_TIMEOUT"
    prior = _os.environ.get(key)
    _os.environ[key] = "not-a-number"
    try:
        user = {"slow-grok": {"harness": "cursor", "model": "grok-4-20"}}
        try:
            resolve_provider("slow-grok", user_providers=user)
        except ValueError as e:
            return _ok("integer" in str(e), f"got {e}")
        return _ok(False, "expected ValueError")
    finally:
        if prior is None:
            _os.environ.pop(key, None)
        else:
            _os.environ[key] = prior


def test_config_resolve_provider_effort_precedence() -> bool:
    print("\n[18dd] config.resolve_provider carries YAML effort and honors env override")
    import os as _os
    from config import resolve_provider
    key = "MOA_CLAUDE_DEEP_EFFORT"
    prior = _os.environ.get(key)
    user = {
        "claude-deep": {
            "harness": "claude",
            "model": "claude-opus-5",
            "effort": "medium",
        }
    }
    try:
        yaml_value = resolve_provider("claude-deep", user_providers=user)
        _os.environ[key] = "max"
        env_value = resolve_provider("claude-deep", user_providers=user)
        return _ok(
            yaml_value.effort == "medium" and env_value.effort == "max",
            f"yaml={yaml_value.effort} env={env_value.effort}",
        )
    finally:
        if prior is None:
            _os.environ.pop(key, None)
        else:
            _os.environ[key] = prior


def test_config_builtin_timeout_is_none() -> bool:
    print("\n[18e] config: built-in providers have timeout=None (CLI flag path stays in charge)")
    from config import resolve_provider
    rp = resolve_provider("codex", user_providers={})
    return _ok(rp.timeout is None, f"built-in codex should have timeout=None; got {rp.timeout}")


def test_config_yaml_providers_block() -> bool:
    print("\n[19] config: harness/config.yaml `providers:` block parses into user_providers")
    import tempfile, textwrap
    from pathlib import Path as _Path
    from config import _load_yaml, _user_providers_from_yaml
    yaml_text = textwrap.dedent("""
        providers:
          cursor-grok: {harness: cursor, model: grok-4.20}
          cursor-gpt:  {harness: cursor, model: gpt-5.5}
        layers:
          proposers: [codex, glm, cursor-grok]
    """)
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write(yaml_text)
        tmp_path = _Path(f.name)
    try:
        cfg = _load_yaml(tmp_path)
        user_providers = _user_providers_from_yaml(cfg)
        ok = (
            "cursor-grok" in user_providers
            and user_providers["cursor-grok"]["harness"] == "cursor"
            and user_providers["cursor-grok"]["model"] == "grok-4.20"
            and "cursor-gpt" in user_providers
        )
        return _ok(ok, f"got: {user_providers}")
    finally:
        tmp_path.unlink()


def test_config_resolve_layer_mixed() -> bool:
    print("\n[20] config.resolve_layer resolves mixed builtin + user-named names")
    from config import resolve_layer
    user = {"cursor-grok": {"harness": "cursor", "model": "grok-4.20"}}
    resolved = resolve_layer(["codex", "glm", "cursor-grok"], user_providers=user)
    names = [r.name for r in resolved]
    harnesses = [r.harness for r in resolved]
    ok = (names == ["codex", "glm", "cursor-grok"]
          and harnesses == ["codex", "opencode", "cursor"])
    return _ok(ok, f"got names={names} harnesses={harnesses}")

def test_config_resolve_layer_unknown_fails_loud() -> bool:
    print("\n[21] config.resolve_layer raises on unknown name with helpful error")
    from config import resolve_layer
    try:
        resolve_layer(["codex", "typo-name"], user_providers={})
    except ValueError as e:
        return _ok("typo-name" in str(e), f"error should mention bad name; got: {e}")
    return _ok(False, "expected ValueError")


def test_config_load_resolved_end_to_end() -> bool:
    print("\n[22] config.load_resolved_config resolves YAML into proposer/refiner provider lists")
    import tempfile, textwrap
    from pathlib import Path as _Path
    from config import load_resolved_config
    yaml_text = textwrap.dedent("""
        providers:
          cursor-grok: {harness: cursor, model: grok-4.20}
        layers:
          proposers: [codex, glm, cursor-grok]
          refiners:  [codex, kimi]
    """)
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write(yaml_text)
        tmp_path = _Path(f.name)
    try:
        loaded = load_resolved_config(config_path=tmp_path, dotenv_path=_Path("/nonexistent"))
        prop_names = [p.name for p in loaded.proposers]
        ref_harnesses = [p.harness for p in loaded.refiners]
        ok = (
            prop_names == ["codex", "glm", "cursor-grok"]
            and ref_harnesses == ["codex", "opencode"]
            and loaded.skip_refinement is False
        )
        return _ok(ok, f"got proposers={prop_names} refiners={ref_harnesses} skip={loaded.skip_refinement}")
    finally:
        tmp_path.unlink()


def test_cursor_check_available_returns_tuple() -> bool:
    print("\n[23] cursor.check_available returns (bool, str) tuple")
    from adapters import cursor as cursor_adapter
    result = cursor_adapter.check_available()
    ok = (isinstance(result, tuple) and len(result) == 2
          and isinstance(result[0], bool) and isinstance(result[1], str))
    return _ok(ok, f"got {result}")


def test_opencode_extractor_finds_bare_payload() -> bool:
    print("\n[N] adapters.extract_json_from_text pulls bare JSON from opencode text output")
    from adapters import extract_json_from_text
    payload = extract_json_from_text(SAMPLE_OPENCODE_STDOUT_BARE)
    return _ok(payload is not None and payload.get("agent_id") == "codex", f"got {payload!r}")


def test_opencode_extractor_handles_fenced_and_prose() -> bool:
    print("\n[N] extract_json_from_text pulls fenced JSON out of surrounding prose")
    from adapters import extract_json_from_text
    payload = extract_json_from_text(SAMPLE_OPENCODE_STDOUT_FENCED)
    return _ok(payload is not None and payload.get("agent_id") == "codex", f"got {payload!r}")


def test_extractor_handles_bare_object_larger_than_scan_window() -> bool:
    print("\n[N] extract_json_from_text returns a bare JSON object bigger than the 200KB scan window")
    from adapters import extract_json_from_text
    big = json.dumps({"agent_id": "glm", "summary": "x" * 300_000, "plan": []})
    payload = extract_json_from_text(big)
    return _ok(payload is not None and payload.get("agent_id") == "glm",
               f"len={len(big)}, got dict={isinstance(payload, dict)}")


def test_opencode_extractor_repairs_invalid_markdown_escape() -> bool:
    print("\n[N] OpenCode extractor repairs invalid Markdown escapes in root JSON")
    from adapters import extract_json_from_text
    fixture = _make_valid_proposer("glm")
    fixture["summary"] = "bad escape in otherwise valid proposal output long enough"
    malformed = json.dumps(fixture).replace("bad escape", r"bad \` escape")
    payload = extract_json_from_text(
        "progress prose\n" + malformed,
        required_keys=set(fixture),
    )
    ok = payload is not None and payload.get("agent_id") == "glm" and "\\`" in payload["summary"]
    return _ok(ok, f"agent={payload.get('agent_id') if payload else None}")


def test_opencode_extractor_rejects_valid_nested_object() -> bool:
    print("\n[N] OpenCode extractor does not mistake a nested plan step for the root payload")
    from adapters import extract_json_from_text
    malformed = '{"agent_id":"glm","summary":"bad \\uZZZZ","plan":[{"step":"nested"}]}'
    payload = extract_json_from_text(
        malformed,
        required_keys={"agent_id", "summary", "plan", "open_questions"},
    )
    return _ok(payload is None, f"got {payload!r}")


def test_qwen_token_plan_config_uses_env_secret() -> bool:
    print("\n[N] Qwen Token Plan OpenCode config uses the dedicated endpoint and env key")
    from adapters import opencode as opencode_adapter
    cfg = opencode_adapter._config_for_model("qwen-token-plan/qwen3.7-max")
    provider = cfg.get("provider", {}).get("qwen-token-plan", {})
    options = provider.get("options", {})
    ok = (
        options.get("baseURL") == opencode_adapter._QWEN_TOKEN_PLAN_BASE_URL
        and options.get("apiKey") == "{env:QWEN_TOKEN_PLAN_API_KEY}"
        and "qwen3.7-max" in provider.get("models", {})
        and provider.get("npm") == "@ai-sdk/openai-compatible"
        and "sk-sp-" not in json.dumps(cfg)
    )
    return _ok(ok, f"provider keys={list(provider)}")


def test_opencode_diagnose_empty_is_transient() -> bool:
    print("\n[N] opencode._diagnose_failure flags empty stdout + clean stderr as transient")
    from adapters import opencode as opencode_adapter
    msg, transient = opencode_adapter._diagnose_failure("", "")
    return _ok(transient is True and "transient" in msg.lower(), f"transient={transient}, msg={msg!r}")


def test_opencode_diagnose_quota_is_not_transient() -> bool:
    print("\n[N] opencode._diagnose_failure does NOT flag transient when quota in stderr")
    from adapters import opencode as opencode_adapter
    msg, transient = opencode_adapter._diagnose_failure("", SAMPLE_OPENCODE_STDERR_QUOTA)
    return _ok(transient is False and "quota" in msg.lower(), f"transient={transient}, msg={msg!r}")


def test_opencode_diagnose_not_found_is_not_transient() -> bool:
    print("\n[N] opencode._diagnose_failure treats HTTP routing errors as non-transient")
    from adapters import opencode as opencode_adapter
    msg, transient = opencode_adapter._diagnose_failure("", "Error: Not Found")
    return _ok(transient is False and "routing" in msg.lower(), f"transient={transient}, msg={msg!r}")


def test_opencode_tool_404_does_not_mask_model_output() -> bool:
    print("\n[N] opencode non-empty malformed output outranks tool-level 404 noise")
    from adapters import opencode as opencode_adapter
    msg, transient = opencode_adapter._diagnose_failure(
        '{"agent_id":"glm","plan":[', "WebFetch Transport error: 404"
    )
    return _ok(
        transient is True and "unparseable" in msg.lower(),
        f"transient={transient}, msg={msg!r}",
    )


def test_opencode_result_carries_transient_empty_field() -> bool:
    print("\n[N] OpenCodeResult dataclass exposes transient_empty (default False)")
    from adapters import opencode as opencode_adapter
    r = opencode_adapter.OpenCodeResult(
        success=True, payload={}, raw_stdout="", raw_stderr="",
        exit_code=0, duration_seconds=1.0,
    )
    return _ok(r.transient_empty is False, f"got {r.transient_empty!r}")


def test_opencode_check_available_returns_tuple() -> bool:
    print("\n[N] opencode.check_available returns (bool, str) tuple")
    from adapters import opencode as opencode_adapter
    result = opencode_adapter.check_available()
    ok = (isinstance(result, tuple) and len(result) == 2
          and isinstance(result[0], bool) and isinstance(result[1], str))
    return _ok(ok, f"got {result}")


def test_opencode_model_readiness_is_route_specific() -> bool:
    print("\n[N] opencode model readiness distinguishes persisted accounts from env-key routes")
    import os
    from types import SimpleNamespace
    from unittest.mock import patch
    from adapters import opencode as oc

    auth = SimpleNamespace(
        returncode=0,
        stdout="Credentials\nOpenCode Go api\n",
        stderr="",
    )
    with (
        patch.object(oc.shutil, "which", return_value="/usr/bin/opencode"),
        patch.object(oc.subprocess, "run", return_value=auth),
        patch.dict(os.environ, {"QWEN_TOKEN_PLAN_API_KEY": ""}, clear=False),
    ):
        result = oc.check_models_available(
            ["opencode-go/glm-5.2", "qwen-token-plan/qwen3.8-max-preview"]
        )
    ok = (
        result["opencode-go/glm-5.2"][0] is True
        and result["qwen-token-plan/qwen3.8-max-preview"][0] is False
    )
    return _ok(ok, f"got {result}")


def test_opencode_model_readiness_accepts_qwen_token_plan_key() -> bool:
    print("\n[N] opencode model readiness recognizes Qwen Token Plan env auth")
    import os
    from types import SimpleNamespace
    from unittest.mock import patch
    from adapters import opencode as oc

    auth = SimpleNamespace(returncode=0, stdout="0 credentials", stderr="")
    with (
        patch.object(oc.shutil, "which", return_value="/usr/bin/opencode"),
        patch.object(oc.subprocess, "run", return_value=auth),
        patch.dict(
            os.environ,
            {"QWEN_TOKEN_PLAN_API_KEY": "test-only"},  # pragma: allowlist secret
            clear=False,
        ),
    ):
        result = oc.check_model_available(
            "qwen-token-plan/qwen3.8-max-preview"
        )
    return _ok(result[0] is True, f"got {result}")


def test_config_resolve_builtin_glm_uses_opencode() -> bool:
    print("\n[N] config.resolve_provider: glm maps to opencode harness / opencode-go model")
    from config import resolve_provider
    rp = resolve_provider("glm", user_providers={})
    ok = (rp.name == "glm" and rp.harness == "opencode" and rp.model == "opencode-go/glm-5.2")
    return _ok(ok, f"got {rp}")


def test_config_resolve_builtin_kimi_uses_opencode() -> bool:
    print("\n[N] config.resolve_provider: kimi maps to opencode harness / opencode-go model")
    from config import resolve_provider
    rp = resolve_provider("kimi", user_providers={})
    ok = (
        rp.name == "kimi"
        and rp.harness == "opencode"
        and rp.model == "opencode-go/kimi-k3"
    )
    return _ok(ok, f"got {rp}")


def test_config_resolve_builtin_composer_uses_cursor() -> bool:
    print("\n[N] config.resolve_provider: composer maps to cursor harness / composer-2.5")
    from config import resolve_provider
    rp = resolve_provider("composer", user_providers={})
    ok = (rp.name == "composer" and rp.harness == "cursor" and rp.model == "composer-2.5")
    return _ok(ok, f"got {rp}")


def test_config_resolve_builtin_grok_uses_opencode() -> bool:
    print("\n[N] config.resolve_provider: grok maps to authenticated OpenCode Go route")
    from config import resolve_provider
    rp = resolve_provider("grok", user_providers={})
    ok = (
        rp.name == "grok"
        and rp.harness == "opencode"
        and rp.model == "opencode-go/grok-4.5"
    )
    return _ok(ok, f"got {rp}")


def test_opencode_preflight_recognizes_xai_key() -> bool:
    print("\n[N] opencode preflight registers XAI_API_KEY as valid auth (grok recipe)")
    from adapters import opencode as oc
    return _ok("XAI_API_KEY" in oc._PROVIDER_KEY_ENVS, f"_PROVIDER_KEY_ENVS={oc._PROVIDER_KEY_ENVS}")


def test_opencode_grok_recipe_extracts_valid_grok_payload() -> bool:
    print("\n[N] opencode extractor pulls a schema-valid grok proposer payload (built-in grok recipe)")
    from adapters import extract_json_from_text
    payload = extract_json_from_text(SAMPLE_OPENCODE_GROK_STDOUT)
    if not (isinstance(payload, dict) and payload.get("agent_id") == "grok"):
        return _ok(False, f"extractor did not return a grok payload; got {payload!r}")
    schema = run_moa._load_schema(run_moa.PROPOSER_SCHEMA_PATH)
    errors = run_moa._validate_against_schema(payload, schema)
    return _ok(len(errors) == 0, f"schema errors={errors[:3]}")


def test_config_resolve_builtin_cursor_grok_uses_cursor() -> bool:
    print("\n[N] config.resolve_provider: cursor-grok maps to cursor harness / cursor-grok-4.5-high")
    from config import resolve_provider
    rp = resolve_provider("cursor-grok", user_providers={})
    ok = (rp.name == "cursor-grok" and rp.harness == "cursor" and rp.model == "cursor-grok-4.5-high")
    return _ok(ok, f"got {rp}")


def test_cursor_cmd_always_forces_plan_mode() -> bool:
    print("\n[N] cursor _build_cursor_cmd: ALWAYS forces '--mode plan' (fail-closed read-only)")
    from adapters import cursor as cur
    cmd = cur._build_cursor_cmd("cursor-agent", "cursor-grok-4.5-high")
    ok = (
        "--mode" in cmd and cmd[cmd.index("--mode") + 1] == "plan"
        and "-p" in cmd and "--trust" in cmd
        and "--output-format" in cmd and cmd[cmd.index("--output-format") + 1] == "json"
        and cmd[0] == "cursor-agent" and "cursor-grok-4.5-high" in cmd
        # prompt is NEVER a positional argv entry (stdin only)
        and not any(tok not in {
            "cursor-agent", "-p", "--model", "cursor-grok-4.5-high",
            "--mode", "plan", "--output-format", "json", "--trust",
        } for tok in cmd)
    )
    return _ok(ok, f"cmd={cmd}")


def test_cursor_plan_mode_unsupported_detection() -> bool:
    print("\n[N] cursor _is_plan_mode_unsupported: detects '--mode' rejection, ignores unrelated errors")
    from adapters import cursor as cur
    rejects = [
        ("error: unknown option '--mode'", ""),
        ("", "unexpected argument '--mode' found"),
        ("error: unrecognized option: --mode", ""),
    ]
    non_rejects = [
        ("rate limit exceeded", ""),                       # unrelated failure
        ("", "cursor-agent: authentication error"),        # unrelated failure
        ("some unknown option --trust weirdness", ""),     # 'unknown option' but not about mode
        ("error: unknown option '--model'", ""),           # --model must NOT match --mode
        ("unrecognized model identifier", ""),             # 'mode' substring, no --mode token
        ("error: unknown option '--trust'\n\nUsage: cursor-agent --mode plan ...", ""),  # --mode only in usage text (other line)
        ("", ""),                                          # empty
    ]
    ok = (
        all(cur._is_plan_mode_unsupported(e, o) for e, o in rejects)
        and not any(cur._is_plan_mode_unsupported(e, o) for e, o in non_rejects)
    )
    return _ok(ok, "rejection detection did not match expectations")


def test_cursor_grok_recipe_extracts_valid_payload() -> bool:
    print("\n[N] cursor adapter extracts a schema-valid cursor-grok proposer payload (built-in recipe)")
    from adapters import cursor as cursor_adapter
    payload = cursor_adapter._extract_payload(SAMPLE_CURSOR_GROK_STDOUT)
    if not (isinstance(payload, dict) and payload.get("agent_id") == "cursor-grok"):
        return _ok(False, f"extractor did not return a cursor-grok payload; got {payload!r}")
    schema = run_moa._load_schema(run_moa.PROPOSER_SCHEMA_PATH)
    errors = run_moa._validate_against_schema(payload, schema)
    return _ok(len(errors) == 0, f"schema errors={errors[:3]}")


def test_config_resolve_builtin_qwen_uses_token_plan() -> bool:
    print("\n[N] config.resolve_provider: qwen maps to Qwen Token Plan via OpenCode")
    from config import resolve_provider
    rp = resolve_provider("qwen", user_providers={})
    ok = (
        rp.name == "qwen"
        and rp.harness == "opencode"
        and rp.model == "qwen-token-plan/qwen3.8-max-preview"
        and rp.timeout == 600
    )
    return _ok(ok, f"got {rp}")


def test_provider_catalog_includes_optional_builtins() -> bool:
    print("\n[N] CLI catalog includes curated Codex routes and legacy aliases")
    from config import load_provider_catalog
    catalog = load_provider_catalog(config_path=Path("/nonexistent"))
    ok = (
        catalog.get("qwen") is not None
        and catalog["qwen"].model == "qwen-token-plan/qwen3.8-max-preview"
        and catalog["codex-sol"].model == "gpt-5.6-sol"
        and catalog["codex-sol"].effort == "high"
        and catalog["codex-luna"].model == "gpt-5.6-luna"
        and catalog["codex-luna"].effort == "medium"
        and catalog["deepseek"].model == "opencode-go/deepseek-v4-pro"
        and catalog["deepseek-flash"].model == "opencode-go/deepseek-v4-flash"
        and catalog.get("codex-reviewer") is not None
        and catalog["codex-reviewer"].model == "gpt-5.6-sol"
        and catalog.get("codex-aggregator") is not None
        and catalog.get("opus") is not None
        and catalog["opus"].model == "claude-opus-5"
    )
    return _ok(ok, f"names={sorted(catalog)}")


def test_dispatch_propagates_native_provider_effort() -> bool:
    print("\n[N] orchestrator only forwards effort to harnesses with native effort flags")
    from unittest import mock

    cases = [
        ("claude", "_run_sonnet", "claude-sonnet-5", "max"),
        ("opencode", "_run_opencode", "opencode-go/glm-5.2", "high"),
        ("agy", "_run_agy", "gemini-3.1-pro-low", "low"),
    ]
    observed = {}

    def fake_run(**kwargs):
        observed[kwargs["agent_id"]] = kwargs.get("reasoning_effort")
        return run_moa.LayerResult(
            agent_id=kwargs["agent_id"], layer=1, role="proposer", success=True
        )

    for harness, target, model, effort in cases:
        provider = run_moa.harness_config.ResolvedProvider(
            name=f"{harness}-fixture",
            harness=harness,
            model=model,
            effort=effort,
        )
        with (
            mock.patch.object(run_moa, "_workspace_snapshot", return_value=None),
            mock.patch.object(run_moa, target, side_effect=fake_run),
        ):
            run_moa._dispatch_provider(
                provider=provider,
                layer=1,
                role="proposer",
                prompt="fixture",
                repo_path=Path("."),
                session_dir=Path(".moa/fixture"),
                timeout_for_harness={harness: 30},
                codex_effort="high",
            )
    ok = (
        observed["claude-fixture"] == "max"
        and observed["opencode-fixture"] == "high"
        and observed["agy-fixture"] is None
    )
    return _ok(ok, f"observed={observed}")


def test_webui_model_catalog_is_provider_grouped_and_current() -> bool:
    print("\n[N] Web UI model catalog is provider-grouped with current curated routes")
    repo_root = SCRIPT_DIR.parent.parent
    repo_provider = repo_root / "harness" / "webui" / "providers.py"
    if repo_provider.exists():
        if str(repo_root) not in sys.path:
            sys.path.insert(0, str(repo_root))
        from harness.webui import providers as web_providers
    else:
        # Installed skills have webui/ beside scripts/ rather than inside a
        # top-level harness package. Load the module directly so this offline
        # test does not require importing Flask through webui.__init__.
        import importlib.util
        installed_provider = SCRIPT_DIR.parent / "webui" / "providers.py"
        spec = importlib.util.spec_from_file_location(
            "moax_installed_web_providers", installed_provider
        )
        if spec is None or spec.loader is None:
            return _ok(False, f"cannot load {installed_provider}")
        web_providers = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(web_providers)

    models = web_providers.model_catalog(probe=False)
    by_id = {item["id"]: item for item in models}
    groups = {
        item["id"]: item for item in web_providers.provider_catalog(probe=False)
    }
    expected = {
        "codex-sol": ("codex", "gpt-5.6-sol"),
        "codex-luna": ("codex", "gpt-5.6-luna"),
        "sonnet": ("claude", "claude-sonnet-5"),
        "opus": ("claude", "claude-opus-5"),
        "glm": ("opencode", "opencode-go/glm-5.2"),
        "kimi": ("opencode", "opencode-go/kimi-k3"),
        "qwen": ("opencode", "qwen-token-plan/qwen3.8-max-preview"),
        "qwen-opencode": ("opencode", "opencode-go/qwen3.7-max"),
        "deepseek": ("opencode", "opencode-go/deepseek-v4-pro"),
        "deepseek-flash": ("opencode", "opencode-go/deepseek-v4-flash"),
        "composer": ("cursor", "composer-2.5"),
        "cursor-grok": ("cursor", "cursor-grok-4.5-high"),
        "agy-gemini-pro": ("agy", "gemini-3.1-pro-high"),
        "fable": ("claude", "claude-fable-5"),
    }
    routes_ok = all(
        name in by_id
        and by_id[name]["provider_id"] == provider_id
        and by_id[name]["model"] == model
        for name, (provider_id, model) in expected.items()
    )
    effort_ok = (
        by_id["sonnet"]["effort"] == "high"
        and by_id["sonnet"]["effort_options"]
        == ["low", "medium", "high", "xhigh", "max"]
        and by_id["codex"]["supports_effort"]
        and by_id["agy-gemini-pro"]["effort_options"] == ["low", "high"]
        and by_id["agy-gemini-pro"]["effort_control"] == "model_variant"
        and by_id["fable"]["effort"] == "xhigh"
        and by_id["fable"]["effort_options"] == []
    )
    grouped_ok = (
        groups["claude"]["lab"] == "Anthropic"
        and any(route["id"] == "sonnet" for route in groups["claude"]["routes"])
        and any(route["id"] == "fable" for route in groups["claude"]["routes"])
        and any(route["id"] == "qwen-opencode" for route in groups["opencode"]["routes"])
        and [route["id"] for route in groups["opencode"]["routes"] if route["id"].startswith("deepseek")]
        == ["deepseek", "deepseek-flash"]
        and {route["id"] for route in groups["cursor"]["routes"]}
        == {"composer", "cursor-grok"}
    )
    role_ok = (
        {item["id"] for item in models if "aggregator" in item["roles"]}
        == {"codex-sol", "opus", "fable"}
        and by_id["fable"]["roles"] == ["aggregator"]
        and by_id["opus"]["roles"] == ["proposer", "refiner", "aggregator"]
        and by_id["deepseek"]["roles"] == ["proposer", "refiner"]
        and by_id["deepseek-flash"]["roles"] == ["proposer", "refiner"]
    )
    defaults_ok = (
        by_id["agy-gemini-pro"]["default_roles"] == ["proposer"]
        and by_id["grok"]["default_roles"] == ["proposer"]
        and by_id["glm"]["default_roles"] == ["proposer"]
        and by_id["qwen"]["default_roles"] == ["refiner"]
        and by_id["kimi"]["default_roles"] == ["refiner"]
        and by_id["opus"]["default_roles"] == ["refiner"]
        and by_id["codex-sol"]["default_roles"] == ["aggregator"]
        and by_id["fable"]["default_roles"] == []
    )
    app_js = (
        repo_root / "harness" / "webui" / "static" / "js" / "app.js"
        if repo_provider.exists()
        else SCRIPT_DIR.parent / "webui" / "static" / "js" / "app.js"
    ).read_text(encoding="utf-8")
    preset_ok = all(
        snippet in app_js
        for snippet in (
            'preferred: ["agy-gemini-pro", "grok"]',
            'refiner: { preferred: ["kimi"], limit: 1 }',
            'preferred: ["agy-gemini-pro", "grok", "glm"]',
            'preferred: ["agy-gemini-pro", "grok", "glm", "deepseek"]',
            'preferred: ["qwen", "kimi", "opus"]',
            'efforts: { opus: "max" }',
            'preferred: ["codex-sol"]',
            'efforts: { "codex-sol": "xhigh" }',
            '["codex-sol", "opus", FABLE_ROUTE_ID].includes(id)',
            'const FABLE_WARNING_PASSWORD = "driveline11";',
            "syncSelectedProviderGroups();",
        )
    )
    route_labels_ok = all(
        suffix not in item["name"]
        for provider_id, suffix in (
            ("opencode", "OpenCode Go"),
            ("cursor", "Cursor"),
        )
        for item in groups[provider_id]["routes"]
    )
    hidden_ok = (
        {"codex-reviewer", "codex-aggregator", "gemini-cli-pro",
         "cursor-sol", "cursor-gemini"}.isdisjoint(by_id)
        and "gemini" not in groups
    )
    return _ok(
        routes_ok and effort_ok and grouped_ok and role_ok and defaults_ok
        and preset_ok and route_labels_ok and hidden_ok,
        f"routes={len(models)} grouped={grouped_ok} effort={effort_ok} "
        f"roles={role_ok} defaults={defaults_ok} presets={preset_ok} "
        f"labels={route_labels_ok} hidden={hidden_ok}",
    )


def test_finalize_moves_misplaced_refiner_verification() -> bool:
    print("\n[N] finalizer restores verification records misplaced in additional_research")
    import contextlib
    import io
    import tempfile
    payload = _make_valid_broadcast_refiner("kimi")
    misplaced = payload["verifications"].pop()
    payload["additional_research"].append(misplaced)
    result = run_moa.LayerResult(
        agent_id="kimi", layer=2, role="refiner-broadcast",
        success=True, payload=payload,
    )
    with tempfile.TemporaryDirectory() as td:
        with contextlib.redirect_stderr(io.StringIO()):
            run_moa._finalize_result(
                result,
                payload,
                SCRIPT_DIR / "schemas" / "refiner.schema.json",
                Path(td),
            )
    ok = (
        result.success and result.schema_valid
        and misplaced in payload["verifications"]
        and misplaced not in payload["additional_research"]
    )
    return _ok(ok, f"success={result.success} schema_valid={result.schema_valid}")


def test_google_provider_builtins_are_default_and_resolve() -> bool:
    print("\n[N] config: AGY Gemini Pro is default and retired Gemini routes stay removed")
    import os as _os
    import config as harness_config
    from config import resolve_provider
    agy_pro = resolve_provider("agy-gemini-pro", user_providers={})
    effort_key = "MOA_AGY_GEMINI_PRO_EFFORT"
    old_effort = _os.environ.get(effort_key)
    _os.environ[effort_key] = "low"
    try:
        agy_effort_override = resolve_provider("agy-gemini-pro", user_providers={})
    finally:
        if old_effort is None:
            _os.environ.pop(effort_key, None)
        else:
            _os.environ[effort_key] = old_effort
    defaults = harness_config.load_resolved_config(config_path=Path("/nonexistent"))
    fable = resolve_provider("fable", user_providers={})
    proposer_names = [p.name for p in defaults.proposers]
    refiner_names = [p.name for p in defaults.refiners]
    try:
        resolve_provider("gemini-cli-pro", user_providers={})
    except ValueError:
        gemini_removed = True
    else:
        gemini_removed = False
    flash_removed = True
    for removed_name in ("agy-gemini-flash", "agy-gemini-high"):
        try:
            resolve_provider(removed_name, user_providers={})
            flash_removed = False
        except ValueError:
            pass
    ok = (
        agy_pro.harness == "agy"
        and agy_pro.model == "gemini-3.1-pro-high"
        and agy_pro.effort == "high"
        and agy_effort_override.model == "gemini-3.1-pro-high"
        and agy_effort_override.effort == "high"
        and proposer_names == ["agy-gemini-pro", "grok", "glm"]
        and refiner_names == ["qwen", "kimi", "opus"]
        and defaults.aggregator is not None
        and defaults.aggregator.name == "codex-sol"
        and fable.harness == "claude"
        and fable.model == "claude-fable-5"
        and harness_config.provider_allows_role("fable", "aggregator")
        and not harness_config.provider_allows_role("fable", "proposer")
        and not harness_config.provider_allows_role("fable", "refiner")
        and gemini_removed
        and flash_removed
    )
    return _ok(
        ok,
        f"pro={agy_pro}, effort_override={agy_effort_override}, "
        f"gemini_removed={gemini_removed}, flash_removed={flash_removed}, "
        f"proposers={proposer_names}, refiners={refiner_names}, "
        f"aggregator={defaults.aggregator}",
    )


def test_agy_cmd_is_fail_closed() -> bool:
    print("\n[N] agy command uses plan+sandbox with explicit headless read approval")
    from adapters import agy as agy_adapter
    cmd = agy_adapter._build_cmd(
        "agy",
        instruction="read prompt",
        model="gemini-3.1-pro-high",
        timeout_seconds=60,
        internal_log=Path("/tmp/agy.log"),
        reasoning_effort="low",
    )
    ok = (
        cmd[cmd.index("--mode") + 1] == "plan"
        and "--sandbox" in cmd
        and "--dangerously-skip-permissions" in cmd
        and "--effort" not in cmd
    )
    return _ok(ok, f"cmd={cmd}")


def test_gemini_cmd_is_fail_closed() -> bool:
    print("\n[N] Gemini CLI command always includes plan mode, sandbox, and stream-json")
    from adapters import gemini as gemini_adapter
    cmd = gemini_adapter._build_cmd("gemini", "gemini-3.1-pro-preview")
    ok = (
        cmd[cmd.index("--approval-mode") + 1] == "plan"
        and "--sandbox" in cmd
        and cmd[cmd.index("--output-format") + 1] == "stream-json"
        and "--yolo" not in cmd
    )
    return _ok(ok, f"cmd={cmd}")


def test_gemini_stream_json_extracts_payload() -> bool:
    print("\n[N] Gemini CLI stream-json parser extracts assistant JSON")
    from adapters import gemini as gemini_adapter
    expected = _make_valid_proposer("gemini-cli-pro")
    output = "\n".join([
        json.dumps({"type": "init", "session_id": "offline"}),
        json.dumps({"type": "message", "role": "assistant",
                    "content": json.dumps(expected), "delta": True}),
        json.dumps({"type": "result", "status": "success"}),
    ])
    got = gemini_adapter._extract_payload(output)
    return _ok(got == expected, f"agent_id={got.get('agent_id') if got else None}")


def test_gemini_tier_ineligible_detection() -> bool:
    print("\n[N] Gemini CLI distinguishes migrated consumer tier")
    from adapters import gemini as gemini_adapter
    return _ok(
        gemini_adapter._tier_ineligible(
            "IneligibleTierError: Your account moved to Antigravity CLI"
        ),
        "tier signal recognized",
    )


def test_config_env_provider_definition_parsed() -> bool:
    print("\n[N] MOA_PROVIDER_<NAME> env var defines a user provider (glm-fw → opencode)")
    import os as _os
    from config import _providers_from_env, resolve_provider
    _os.environ["MOA_PROVIDER_GLM_FW"] = "opencode:fireworks-ai/accounts/fireworks/models/glm-5p2"
    try:
        providers = _providers_from_env()
        rp = resolve_provider("glm-fw", user_providers=providers)
        ok = (rp.harness == "opencode"
              and rp.model == "fireworks-ai/accounts/fireworks/models/glm-5p2")
        return _ok(ok, f"got {rp}")
    finally:
        del _os.environ["MOA_PROVIDER_GLM_FW"]


def test_config_env_provider_malformed_raises() -> bool:
    print("\n[N] MOA_PROVIDER_<NAME> without '<harness>:<model>' raises loudly")
    import os as _os
    from config import _providers_from_env
    _os.environ["MOA_PROVIDER_BROKEN"] = "no-colon-here"
    try:
        _providers_from_env()
        return _ok(False, "expected ValueError for malformed provider def")
    except ValueError as e:
        return _ok("MOA_PROVIDER_BROKEN" in str(e), f"got: {e}")
    finally:
        del _os.environ["MOA_PROVIDER_BROKEN"]


def test_refiner_schema_accepts_five_proposer_roster() -> bool:
    print("\n[N] Refiner schema accepts a 5-proposer broadcast roster (maxItems bump)")
    schema = run_moa._load_schema(run_moa.REFINER_SCHEMA_PATH)
    payload = _make_valid_broadcast_refiner("codex")
    names = ["codex", "glm", "sonnet", "composer", "cursor-grok"]
    payload["reviewing"] = names
    payload["per_proposer_verdicts"] = [
        {"proposer": n, "verdict": "accept_with_changes",
         "summary": "Reviewed and mostly acceptable with minor edits."}
        for n in names
    ]
    errors = run_moa._validate_against_schema(payload, schema)
    return _ok(len(errors) == 0, f"errors={errors[:3]}")


# ---------------------------------------------------------------------------
# HTML report generator (report.py)
# ---------------------------------------------------------------------------

import re as _re
import tempfile as _tempfile
import shutil as _shutil


def _extract_embedded_data(html: str) -> dict:
    """Pull the <script type=application/json id=moa-data> blob out of a report.

    Mirrors what a browser does: slice to the first </script>, undo the
    ``</`` -> ``<\\/`` escaping, and JSON.parse. If a log's ``</script>`` had
    leaked unescaped, this slice would truncate the JSON and json.loads would
    raise — which is exactly the regression this guards against.
    """
    m = _re.search(r'<script type="application/json" id="moa-data">(.*?)</script>', html, _re.S)
    if not m:
        raise AssertionError("moa-data script block not found")
    return json.loads(m.group(1).replace("<\\/", "</"))


def _write_fixture_session(tmp: Path, partial: bool = False) -> Path:
    """Create a synthetic .moa session on disk: mixed success/fail/transient.

    Committed nowhere (.moa is gitignored); built fresh per test so report.py
    exercises the real manifest + payload + log loading path offline.
    """
    session = tmp / "sess-fixture"
    (session / "layer1").mkdir(parents=True, exist_ok=True)
    (session / "layer2").mkdir(parents=True, exist_ok=True)

    (session / "scout-brief.json").write_text(json.dumps({
        "session_id": "sess-fixture",
        "frozen_spec": "Add a widget to the thing.\nSecond line ignored for the title.",
        "focus_files": ["a.py", "b.py"],
        "in_scope": ["do the widget"],
        "out_of_scope": ["not the gadget"],
        "clarifications": ["Q: color? A: goldenrod"],
        "notes": "some notes",
    }), encoding="utf-8")

    codex_payload = _make_valid_proposer("codex")
    (session / "layer1" / "codex-proposer.json").write_text(json.dumps(codex_payload), encoding="utf-8")
    # A log carrying an ANSI code AND a literal </script> — both must survive.
    (session / "layer1" / "codex-proposer.log").write_text(
        "=== STDOUT ===\n\x1b[32mgreen\x1b[0m line\nembedded </script> tag here\n=== STDERR ===\nwarn\n",
        encoding="utf-8",
    )

    layer1 = [
        {"agent_id": "codex", "layer": 1, "role": "proposer", "reviewing": None,
         "success": True, "schema_valid": True, "duration_seconds": 120.0,
         "started_at": 100.0, "error": None,
         "log_path": "layer1/codex-proposer.log", "json_path": "layer1/codex-proposer.json",
         "transient_empty": False},
        {"agent_id": "glm", "layer": 1, "role": "proposer", "reviewing": None,
         "success": False, "schema_valid": False, "duration_seconds": 5.0,
         "started_at": 100.0, "error": "hard failure: quota exhausted",
         "log_path": None, "json_path": None, "transient_empty": False},
        {"agent_id": "cursor-grok", "layer": 1, "role": "proposer", "reviewing": None,
         "success": False, "schema_valid": False, "duration_seconds": 4.0,
         "started_at": 100.0, "error": "empty envelope",
         "log_path": None, "json_path": None, "transient_empty": True},
    ]

    manifest = {
        "session_id": "sess-fixture",
        "config": {"arm": "cross-lab",
                   "proposers": [{"name": "codex", "harness": "codex", "model": "gpt-5.4"},
                                 {"name": "glm", "harness": "opencode", "model": "glm-5.2"},
                                 {"name": "cursor-grok", "harness": "cursor", "model": "grok"}],
                   "refiners": [{"name": "kimi", "harness": "opencode", "model": "kimi-k2.7"}]},
        "layer2_mode": "degraded_non_broadcast" if partial else "broadcast",
        "started_at": 100.0, "finished_at": 400.0, "duration_seconds": 300.0,
        "layer1": layer1,
    }

    if partial:
        manifest["phase"] = "layer1"
        (session / "layer1-manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    else:
        kimi_payload = _make_valid_broadcast_refiner("kimi")
        (session / "layer2" / "kimi-refiner-broadcast.json").write_text(json.dumps(kimi_payload), encoding="utf-8")
        (session / "layer2" / "kimi-refiner-broadcast.log").write_text(
            "=== STDOUT ===\nrefiner ran\n=== STDERR ===\n", encoding="utf-8")
        manifest["layer2"] = [
            {"agent_id": "kimi", "layer": 2, "role": "refiner-broadcast",
             "reviewing": ["codex"], "success": True, "schema_valid": True,
             "duration_seconds": 90.0, "started_at": 220.0, "error": None,
             "log_path": "layer2/kimi-refiner-broadcast.log",
             "json_path": "layer2/kimi-refiner-broadcast.json", "transient_empty": False},
        ]
        (session / "final-plan.md").write_text(
            "# Final plan\n\nDo **this** and see `foo.py`.\n\n- step one\n- step two\n\n"
            "```python\nprint('hi')\n```\n", encoding="utf-8")
        (session / "final-plan.json").write_text(
            json.dumps(_make_valid_final_lineage()), encoding="utf-8"
        )
        (session / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    return session


def test_report_generates_single_self_contained_file() -> bool:
    print("\n[N] report.py emits one illustrated file with no external asset refs")
    tmp = Path(_tempfile.mkdtemp())
    try:
        session = _write_fixture_session(tmp)
        out = report_module.generate(session, session / "report.html")
        html = out.read_text(encoding="utf-8")
        external = _re.findall(r'(?:src|href)="https?://[^"]*"', html)
        art_count = html.count("data:image/webp;base64,")
        # The report script and all six editorial illustrations must be
        # embedded directly in the portable HTML artifact.
        inlined = "<script>" in html and art_count == 6
        return _ok(not external and inlined and len(html) > 100_000,
                   f"external_refs={external[:2]}, art={art_count}, bytes={len(html)}")
    finally:
        _shutil.rmtree(tmp, ignore_errors=True)


def test_report_embedded_json_round_trips() -> bool:
    print("\n[N] embedded moa-data JSON parses and lists every roster agent")
    tmp = Path(_tempfile.mkdtemp())
    try:
        session = _write_fixture_session(tmp)
        html = report_module.generate(session, session / "report.html").read_text(encoding="utf-8")
        data = _extract_embedded_data(html)
        ids = [r["agent_id"] for r in data["layer1"]] + [r["agent_id"] for r in data["layer2"]]
        ok = (set(ids) == {"codex", "glm", "cursor-grok", "kimi"}
              and data["title"].startswith("Add a widget")
              and data["final_plan_markdown"].startswith("# Final plan")
              and data["final_plan_html"] and "<strong>this</strong>" in data["final_plan_html"]
              and data["final_plan_lineage"]["steps"][0]["id"] == "add-redis-wrapper"
              and data["lineage_warnings"] == [])
        return _ok(ok, f"ids={ids}")
    finally:
        _shutil.rmtree(tmp, ignore_errors=True)


def test_report_normalizes_legacy_phase_local_timing() -> bool:
    print("\n[N] report repairs legacy final manifests that start at Layer 2")
    tmp = Path(_tempfile.mkdtemp())
    try:
        session = _write_fixture_session(tmp)
        manifest_path = session / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest.update({"started_at": 220.0, "finished_at": 400.0, "duration_seconds": 180.0})
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        data = _extract_embedded_data(
            report_module.generate(session, session / "report.html").read_text(encoding="utf-8")
        )
        ok = (
            data["started_at"] == 100.0
            and data["finished_at"] == 400.0
            and data["duration_seconds"] == 300.0
            and data["recorded_duration_seconds"] == 180.0
            and data["timing_normalized"] is True
        )
        return _ok(ok, f"timing={data['started_at']}/{data['finished_at']}/{data['duration_seconds']}")
    finally:
        _shutil.rmtree(tmp, ignore_errors=True)


def test_report_lineage_reference_warning_is_nonfatal() -> bool:
    print("\n[N] stale lineage pointers warn without hiding the usable explorer")
    tmp = Path(_tempfile.mkdtemp())
    try:
        session = _write_fixture_session(tmp)
        lineage_path = session / "final-plan.json"
        lineage = json.loads(lineage_path.read_text(encoding="utf-8"))
        lineage["steps"][0]["proposer_refs"][0]["step_index"] = 99
        lineage_path.write_text(json.dumps(lineage), encoding="utf-8")
        html = report_module.generate(session, session / "report.html").read_text(encoding="utf-8")
        data = _extract_embedded_data(html)
        ok = (
            data["final_plan_lineage"] is not None
            and any("out of range" in warning for warning in data["lineage_warnings"])
            and "buildLineageExplorer" in html
            and "Decision lineage — why each step survived" in html
        )
        return _ok(ok, f"warnings={data['lineage_warnings']}")
    finally:
        _shutil.rmtree(tmp, ignore_errors=True)


def test_report_renders_failed_and_transient_agents() -> bool:
    print("\n[N] report carries the failed (glm) and transient-empty (cursor-grok) agents")
    tmp = Path(_tempfile.mkdtemp())
    try:
        session = _write_fixture_session(tmp)
        data = _extract_embedded_data(
            report_module.generate(session, session / "report.html").read_text(encoding="utf-8"))
        glm = next(r for r in data["layer1"] if r["agent_id"] == "glm")
        grok = next(r for r in data["layer1"] if r["agent_id"] == "cursor-grok")
        ok = (glm["success"] is False and "quota" in (glm["error"] or "")
              and grok["transient_empty"] is True and grok["payload"] is None)
        return _ok(ok, f"glm.success={glm['success']}, grok.transient={grok['transient_empty']}")
    finally:
        _shutil.rmtree(tmp, ignore_errors=True)


def test_report_backfills_legacy_retry_from_webui_log() -> bool:
    print("\n[N] report labels a legacy retry from the immutable Web UI transcript")
    tmp = Path(_tempfile.mkdtemp())
    try:
        session = _write_fixture_session(tmp)
        manifest_path = session / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        retry = next(row for row in manifest["layer1"] if row["agent_id"] == "cursor-grok")
        retry.update({"success": True, "schema_valid": True, "transient_empty": False,
                      "duration_seconds": 22.0, "started_at": 140.0, "error": None})
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        (session / "webui.log").write_text(
            "[orchestrator] Layer 1: spawning ['cursor-grok'] in parallel...\n"
            "[orchestrator]   cursor-grok proposer: FAIL (4.0s) — cursor-agent returned non-JSON/incomplete result text under a success envelope. Likely transient — one re-dispatch may recover.\n"
            "[orchestrator] Layer 1: spawning ['cursor-grok'] in parallel... (redispatch)\n",
            encoding="utf-8",
        )
        data = _extract_embedded_data(
            report_module.generate(session, session / "report.html").read_text(encoding="utf-8")
        )
        row = next(item for item in data["layer1"] if item["agent_id"] == "cursor-grok")
        ok = (
            row["attempt"] == 2
            and row["previous_attempt"]["started_at"] == 100.0
            and row["previous_attempt"]["duration_seconds"] == 4.0
            and row["previous_attempt"]["backfilled_from"] == "webui.log"
        )
        return _ok(ok, f"attempt={row.get('attempt')} previous={row.get('previous_attempt')}")
    finally:
        _shutil.rmtree(tmp, ignore_errors=True)


def test_report_escapes_script_close_in_logs() -> bool:
    print("\n[N] a </script> inside a log survives embedding (JSON still slices+parses)")
    tmp = Path(_tempfile.mkdtemp())
    try:
        session = _write_fixture_session(tmp)
        html = report_module.generate(session, session / "report.html").read_text(encoding="utf-8")
        data = _extract_embedded_data(html)  # raises if the log's </script> truncated the blob
        codex = next(r for r in data["layer1"] if r["agent_id"] == "codex")
        ok = ("</script>" in codex["log"]["stdout"]      # preserved for the reader
              and "\x1b" not in codex["log"]["stdout"])   # ANSI stripped
        return _ok(ok, f"stdout={codex['log']['stdout']!r}")
    finally:
        _shutil.rmtree(tmp, ignore_errors=True)


def test_report_phase_split_partial_session() -> bool:
    print("\n[N] report.py renders a layer1-only (phase-split) session as partial")
    tmp = Path(_tempfile.mkdtemp())
    try:
        session = _write_fixture_session(tmp, partial=True)
        data = _extract_embedded_data(
            report_module.generate(session, session / "report.html").read_text(encoding="utf-8"))
        ok = (data["partial"] is True and len(data["layer1"]) == 3
              and data["layer2"] == [] and data["final_plan_html"] is None)
        return _ok(ok, f"partial={data['partial']}, layer2={data['layer2']}")
    finally:
        _shutil.rmtree(tmp, ignore_errors=True)


def test_report_missing_manifest_exits_2() -> bool:
    print("\n[N] report.py --session with no manifest exits 2")
    tmp = Path(_tempfile.mkdtemp())
    try:
        empty = tmp / "no-manifest"
        empty.mkdir()
        # Drive main() directly with argv.
        import contextlib, io
        old_argv = sys.argv
        sys.argv = ["report.py", "--session", str(empty)]
        buf = io.StringIO()
        try:
            with contextlib.redirect_stderr(buf):
                code = report_module.main()
        finally:
            sys.argv = old_argv
        return _ok(code == 2 and "no manifest" in buf.getvalue(), f"code={code}, err={buf.getvalue()!r}")
    finally:
        _shutil.rmtree(tmp, ignore_errors=True)


def test_report_markdown_subset_renders() -> bool:
    print("\n[N] render_markdown handles headings, bold, code fences, lists, links")
    md = ("# Title\n\nA **bold** word and `code`.\n\n- one\n- two\n\n"
          "```\nraw <b> not escaped-as-tag\n```\n\n[link](https://example.com)\n")
    html = report_module.render_markdown(md)
    ok = ("<h1>Title</h1>" in html and "<strong>bold</strong>" in html
          and "<code>code</code>" in html and "<li>one" in html
          and "&lt;b&gt;" in html  # code fence content HTML-escaped
          and '<a href="https://example.com"' in html)
    return _ok(ok, f"html={html[:80]!r}")


def test_report_markdown_code_span_shields_bold() -> bool:
    print("\n[N] inline code span is not mangled by bold/link substitution")
    html = report_module.render_markdown("Use `arr[0]` and `a**b**c`, not **real bold**.\n")
    ok = ("<code>arr[0]</code>" in html and "<code>a**b**c</code>" in html
          and "<strong>real bold</strong>" in html
          and "<strong>b</strong>" not in html)  # the ** inside code stayed literal
    return _ok(ok, f"html={html!r}")


def test_report_markdown_tables_and_emphasis_render() -> bool:
    print("\n[N] final-plan Markdown tables render safely and responsively")
    md = (
        "## Where the proposers disagreed\n\n"
        "| Point | Positions | Adjudication |\n"
        "| :--- | :---: | ---: |\n"
        "| Bottleneck | `cudaStreamSynchronize(0)` and *both* paths | Step 6 |\n"
        r"| Escaped pipe | `a|b` and left \| right | **Keep evidence** |"
        "\n\nOrdinary prose | remains prose.\n"
    )
    html = report_module.render_markdown(md)
    ok = (
        '<table class="dl markdown-table">' in html
        and "<thead><tr>" in html
        and '<th class="align-left">Point</th>' in html
        and '<th class="align-center">Positions</th>' in html
        and '<th class="align-right">Adjudication</th>' in html
        and "<code>cudaStreamSynchronize(0)</code>" in html
        and "<em>both</em>" in html
        and "<code>a|b</code>" in html
        and "left | right" in html
        and "<strong>Keep evidence</strong>" in html
        and "<p>Ordinary prose | remains prose.</p>" in html
        and 'role="region"' in html
    )
    return _ok(ok, f"html={html!r}")


def test_report_markdown_malformed_table_stays_text() -> bool:
    print("\n[N] malformed table-like text does not become an HTML table")
    html = report_module.render_markdown(
        "| Header | Other |\n| -- | not-a-separator |\n| value | other |\n"
    )
    ok = "<table" not in html and "| Header | Other |" in html
    return _ok(ok, f"html={html!r}")


def test_report_markdown_nested_ordered_list_keeps_numbering() -> bool:
    print("\n[N] final-plan ordered list stays open across nested metadata bullets")
    md = (
        "1. **First step** — do one\n"
        "   - Why: reason one\n"
        "   - Risks: low\n\n"
        "2. **Second step** — do two\n"
        "   - Why: reason two\n"
    )
    html = report_module.render_markdown(md)
    ok = (
        html.count("<ol>") == 1
        and html.count("</ol>") == 1
        and html.count("<ul>") == 2
        and html.find("First step") < html.find("Second step")
    )
    return _ok(ok, f"ol={html.count('<ol>')} ul={html.count('<ul>')}")


def test_boolean_env_flags_parse_explicit_values() -> bool:
    print("\n[N] boolean env flags treat 0/false/no as false")
    import os as _os
    key = "MOA_NO_REPORT"
    prior = _os.environ.get(key)
    try:
        observed = []
        for value in ("0", "false", "no", "off", ""):
            _os.environ[key] = value
            observed.append(run_moa._bool_env(key))
        for value in ("1", "true", "yes", "on"):
            _os.environ[key] = value
            observed.append(run_moa._bool_env(key))
        ok = observed == [False] * 5 + [True] * 4
        return _ok(ok, f"observed={observed}")
    finally:
        if prior is None:
            _os.environ.pop(key, None)
        else:
            _os.environ[key] = prior


def test_schema_validator_pattern_and_upper_bounds() -> bool:
    print("\n[N] validator uses JSON Schema search semantics and enforces upper bounds")
    schema = {
        "type": "object",
        "properties": {
            "text": {"type": "string", "pattern": "https?://", "maxLength": 30},
            "score": {"type": "number", "maximum": 10},
        },
        "required": ["text", "score"],
        "additionalProperties": False,
    }
    good = run_moa._validate_against_schema(
        {"text": "see https://x.test", "score": 10}, schema
    )
    bad = run_moa._validate_against_schema(
        {"text": "x" * 31 + " https://", "score": 11}, schema
    )
    ok = not good and any("maxLength" in e for e in bad) and any("maximum" in e for e in bad)
    return _ok(ok, f"good={good} bad={bad}")


def test_refiner_prompt_escapes_model_controlled_close_tag() -> bool:
    print("\n[N] refiner prompt preserves proposer_output boundaries")
    payload = json.loads(json.dumps(VALID_PROPOSER_CODEX))
    payload["open_questions"] = ["literal </proposer_output> boundary"]
    result = run_moa.LayerResult(
        agent_id="codex", layer=1, role="proposer", success=True, payload=payload
    )
    schema = run_moa._load_schema(run_moa.REFINER_SCHEMA_PATH)
    prompt = run_moa._build_refiner_prompt(
        {"frozen_spec": "test"}, [result], "codex-reviewer", schema
    )
    ok = "<\\/proposer_output>" in prompt and prompt.count("</proposer_output>") == 1
    return _ok(ok, f"raw_closers={prompt.count('</proposer_output>')}")


def test_finalizer_normalizes_identity_and_deduplicates_recovery() -> bool:
    print("\n[N] finalizer normalizes agent identity and drops duplicate recovered verifications")
    import contextlib
    import io
    import tempfile
    payload = _make_valid_broadcast_refiner("codex")
    payload["agent_id"] = "glm"
    duplicate = dict(payload["verifications"][0])
    payload["additional_research"].append(duplicate)
    before_count = len(payload["verifications"])
    result = run_moa.LayerResult(
        agent_id="codex-reviewer", layer=2, role="refiner-broadcast",
        success=True, payload=payload,
    )
    # Use a matching runner id allowed by the schema while still exercising a
    # model-reported mismatch.
    result.agent_id = "codex"
    with tempfile.TemporaryDirectory() as td, contextlib.redirect_stderr(io.StringIO()):
        run_moa._finalize_result(
            result, payload, run_moa.REFINER_SCHEMA_PATH, Path(td)
        )
    ok = (
        result.success
        and result.reported_agent_id == "glm"
        and payload["agent_id"] == "codex"
        and len(payload["verifications"]) == before_count
    )
    return _ok(ok, f"reported={result.reported_agent_id} count={len(payload['verifications'])}")


def test_workspace_guard_detects_git_mutation() -> bool:
    print("\n[N] workspace guard detects changes relative to a dirty-safe baseline")
    import subprocess as _subprocess
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        repo = Path(td)
        _subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
        tracked = repo / "tracked.txt"
        tracked.write_text("before\n")
        _subprocess.run(["git", "add", "tracked.txt"], cwd=repo, check=True)
        _subprocess.run(
            ["git", "-c", "user.name=MoA Test", "-c", "user.email=moa@example.invalid", "commit", "-qm", "fixture"],
            cwd=repo, check=True,
        )
        session = repo / ".moa" / "fixture"
        session.mkdir(parents=True)
        before = run_moa._workspace_snapshot(repo, session)
        tracked.write_text("after\n")
        after = run_moa._workspace_snapshot(repo, session)
        result = run_moa.LayerResult(
            agent_id="fixture", layer=1, role="proposer", success=True
        )
        run_moa._apply_workspace_guard(result, before, after)
        ok = not result.success and result.workspace_mutations == ["tracked.txt"]
        return _ok(ok, f"mutations={result.workspace_mutations}")


def test_report_template_accessibility_contracts() -> bool:
    print("\n[N] report template has accessible disclosures, copy status, and compact stages")
    template = report_module.TEMPLATE_PATH.read_text(encoding="utf-8")
    webui_app = (SCRIPT_DIR.parent / "webui" / "static" / "js" / "app.js").read_text(
        encoding="utf-8"
    )
    ok = (
        'el("button", { class: "c-head"' in template
        and '"aria-expanded"' in template
        and 'event.key === "ArrowDown"' in template
        and ".lineage-node:focus { outline: 3px" in template
        and ".lineage-node:focus { outline: none" not in template
        and 'text: "Copy final plan as Markdown"' in template
        and '"aria-live": "polite"' in template
        and "navigator.clipboard.writeText(markdown)" in template
        and "document.execCommand" not in template
        and "The Markdown is selected below" in template
        and 'class: "timeline-shell"' in template
        and "Stage 1, 2, and 3 use distinct striped colors." in template
        and "previous_attempt" in template
        and "timeline-retry-wait" in template
        and "Hover a card to trace its influence" in template
        and "setLineageFocus" in template
        and "lineage-card-shadow" in template
        and "shareCurrentWebReport" in template
        and "currentArtifactJobId" in template
        and 'title: "Missing: " + summarizeLineageFinding' in template
        and 'title: "Verified: " + summarizeLineageFinding' in template
        and 'title: "Recommendation: " + summarizeLineageFinding' in template
        and "proposerReviewGrades" in template
        and "lineageInfluenceCounts" in template
        and "syncSelectedProviderGroups" in webui_app
        and 'group.querySelector(".route-choice:checked")' in webui_app
    )
    return _ok(ok)


def test_webui_effort_copy_and_control_contract() -> bool:
    print("\n[N] Web UI effort copy and controls share one fail-closed contract")
    app_source = (
        SCRIPT_DIR.parent / "webui" / "static" / "js" / "app.js"
    ).read_text(encoding="utf-8")
    css_source = (
        SCRIPT_DIR.parent / "webui" / "static" / "css" / "app.css"
    ).read_text(encoding="utf-8")
    ok = (
        "function effortPresentation(option)" in app_source
        and "const effort = effortPresentation(option);" in app_source
        and 'data-effort-mode="${escapeHtml(effort.mode)}"' in app_source
        and 'data-effort-adjustable="${String(effort.adjustable)}"' in app_source
        and 'fieldset class="effort-slider" data-effort-control=' in app_source
        and 'const control = $("fieldset[data-effort-control]", card);' in app_source
        and 'range.closest("fieldset[data-effort-control]")' in app_source
        and 'route.dataset.effortAdjustable === "true"' in app_source
        and "Effort control contract violated for route" in app_source
        and 'card.dataset.effortContractError = "true"' in app_source
        and 'label: mode === "model_variant" ? "Adjust model depth" : "Adjust reasoning effort"' in app_source
        and 'aria-label="${escapeHtml(effort.legend)} for ${escapeHtml(option.name)}"' in app_source
        and '`Fixed ${titleCase(configuredEffort)} effort`' in app_source
        and '"Provider-managed effort"' in app_source
        and "input.dataset.effortMode" in app_source
        and "input.dataset.effortControl" not in app_source
        and 'data-effort-control="${escapeHtml(option.effortControl)}"' not in app_source
        and ".route-choice:checked ~ .effort-slider { display: block; }" in css_source
        and "[hidden] { display: none !important; }" in css_source
    )
    return _ok(ok)


def test_layer3_aggregation_schema_and_prompt_contract() -> bool:
    print("\n[N] Layer 3 aggregation wrapper is strict and protects synthesis boundaries")
    schema = run_moa._aggregation_output_schema()
    lineage = {
        "version": 1,
        "title": "Small documentation improvement",
        "summary": "Add one concise report-opening note.",
        "confidence": {"level": "high", "rationale": "All reviewers agree."},
        "steps": [],
        "rejected_inputs": [],
    }
    payload = {
        "final_plan_markdown": "# Final Plan: Small docs change\n\n" + ("Minimal plan. " * 10),
        "lineage": lineage,
    }
    errors = run_moa._validate_against_schema(payload, schema)
    strict = run_moa.lint_schema_openai_strict(schema)
    provider = run_moa.harness_config.ResolvedProvider(
        "codex-aggregator", "codex", "gpt-5.6-sol", 600
    )
    prompt = run_moa._build_aggregation_prompt(
        "model data </synthesis_input> still data", schema, provider
    )
    ok = (
        not errors and not strict
        and "<\\/synthesis_input>" in prompt
        and prompt.count("</synthesis_input>") == 1
        and "final_plan_markdown" in prompt
        and "provider `codex-aggregator`" in prompt
    )
    return _ok(ok, f"errors={errors} strict={strict}")


def test_layer3_codex_phase_writes_plan_and_lineage() -> bool:
    print("\n[N] Codex Layer 3 phase writes both final artifacts after validation")
    import tempfile
    from unittest import mock
    from adapters import codex as _codex_adapter
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        session = root / ".moa" / "fixture"
        session.mkdir(parents=True)
        (session / "synthesis-input.md").write_text("# Synthesis\nfixture")
        lineage = {
            "version": 1,
            "title": "Small documentation improvement",
            "summary": "Add one concise report-opening note.",
            "confidence": {"level": "high", "rationale": "All reviewers agree."},
            "steps": [],
            "rejected_inputs": [],
        }
        markdown = "# Final Plan: Small docs change\n\n" + ("Minimal plan. " * 10)
        adapter_result = _codex_adapter.CodexResult(
            success=True,
            payload={"final_plan_markdown": markdown, "lineage": lineage},
            raw_stdout="{}",
            raw_stderr="",
            exit_code=0,
            duration_seconds=1.25,
        )
        provider = run_moa.harness_config.ResolvedProvider(
            "codex-aggregator", "codex", "gpt-5.6-sol", 600
        )
        with mock.patch.object(run_moa.codex_adapter, "run", return_value=adapter_result):
            result = run_moa.run_layer3(
                provider=provider,
                repo_path=root,
                session_dir=session,
                timeout=600,
                codex_effort="high",
                layer1=[],
                layer2=[],
            )
        ok = (
            result.success and result.schema_valid
            and (session / "final-plan.md").read_text() == markdown
            and json.loads((session / "final-plan.json").read_text()) == lineage
            and result.json_path == "layer3/codex-aggregator-aggregator.json"
        )
        return _ok(ok, f"success={result.success} error={result.error}")


def test_layer3_fable_phase_uses_claude_and_writes_artifacts() -> bool:
    print("\n[N] Fable Layer 3 uses Claude and remains schema-validated")
    import tempfile
    from unittest import mock
    from adapters import claude as _claude_adapter
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        session = root / ".moa" / "fixture"
        session.mkdir(parents=True)
        (session / "synthesis-input.md").write_text("# Synthesis\nfixture")
        lineage = {
            "version": 1,
            "title": "Small documentation improvement",
            "summary": "Add one concise report-opening note.",
            "confidence": {"level": "high", "rationale": "All reviewers agree."},
            "steps": [],
            "rejected_inputs": [],
        }
        markdown = "# Final Plan: Small docs change\n\n" + ("Minimal plan. " * 10)
        adapter_result = _claude_adapter.ClaudeResult(
            success=True,
            payload={"final_plan_markdown": markdown, "lineage": lineage},
            raw_stdout="{}",
            raw_stderr="",
            exit_code=0,
            duration_seconds=1.25,
        )
        provider = run_moa.harness_config.resolve_provider(
            "fable", user_providers={}
        )
        with mock.patch.object(run_moa.claude_adapter, "run", return_value=adapter_result) as call:
            result = run_moa.run_layer3(
                provider=provider,
                repo_path=root,
                session_dir=session,
                timeout=600,
                codex_effort="xhigh",
                layer1=[],
                layer2=[],
            )
        ok = (
            result.success and result.schema_valid
            and call.call_args.kwargs["model"] == "claude-fable-5"
            and call.call_args.kwargs["reasoning_effort"] == "xhigh"
            and (session / "final-plan.md").read_text() == markdown
            and json.loads((session / "final-plan.json").read_text()) == lineage
        )
        return _ok(ok, f"success={result.success} error={result.error}")


def main() -> int:
    print("Mixture-of-Agents — offline smoke test (v2: 3 proposers + broadcast refiners)")
    print("=" * 72)
    tests = [
        test_schema_validator_accepts_valid_codex_payload,
        test_schema_validator_accepts_valid_sonnet_payload,
        test_schema_validator_rejects_missing_field,
        test_schema_validator_rejects_bad_agent_id_pattern,
        test_schema_validator_rejects_missing_evidence_key,
        test_strict_mode_lint_clean_on_current_schemas,
        test_final_plan_schema_resolves_local_refs,
        test_strict_mode_lint_catches_violation,
        test_codex_extractor_finds_payload_in_framed_output,
        test_claude_extractor_finds_structured_output,
        test_claude_extractor_fallback_to_fenced_result,
        test_claude_schema_copy_omits_dialect_metadata,
        test_refiner_schema_validator_broadcast_codex,
        test_refiner_schema_validator_broadcast_kimi,
        test_refiner_schema_accepts_user_named_provider_refs,
        test_refiner_schema_rejects_malformed_proposer_ref,
        test_evidence_cross_field_rejects_code_with_null_file,
        test_evidence_cross_field_rejects_external_with_null_url,
        test_evidence_cross_field_accepts_valid_payload,
        test_finalize_result_fails_closed_on_cross_field_evidence,
        test_unsupported_keyword_warning,
        test_manifest_config_section_present,
        test_config_precedence_env_over_dotenv_over_yaml,
        test_self_moa_argparse_smoke,
        test_install_deps_default_config_only_needs_default_harnesses,
        test_install_deps_cursor_only_config_skips_other_harnesses,
        test_install_deps_schema_coherence_catches_bad_name,
        test_install_deps_qwen_requires_dedicated_key,
        test_skill_assets_present,
        test_config_resolve_builtin_codex,
        test_config_resolve_builtin_sonnet_uses_claude_harness,
        test_config_resolve_unknown_name_raises,
        test_config_resolve_user_provider_yaml_timeout,
        test_config_resolve_env_timeout_override,
        test_config_resolve_env_timeout_malformed_raises,
        test_config_resolve_provider_effort_precedence,
        test_config_builtin_timeout_is_none,
        test_config_yaml_providers_block,
        test_config_resolve_layer_mixed,
        test_config_resolve_layer_unknown_fails_loud,
        test_config_load_resolved_end_to_end,
        test_cursor_check_available_returns_tuple,
        test_cursor_extractor_finds_payload_in_bare_result,
        test_cursor_extractor_handles_fenced_json,
        test_cursor_extractor_returns_none_on_is_error,
        test_cursor_diagnose_failure_flags_transient_empty,
        test_cursor_diagnose_progress_only_is_transient,
        test_cursor_diagnose_failure_quota_is_not_transient,
        test_cursor_diagnose_failure_empty_stdout_is_not_transient,
        test_cursor_result_carries_transient_empty_field,
        test_opencode_extractor_finds_bare_payload,
        test_opencode_extractor_handles_fenced_and_prose,
        test_extractor_handles_bare_object_larger_than_scan_window,
        test_opencode_extractor_repairs_invalid_markdown_escape,
        test_opencode_extractor_rejects_valid_nested_object,
        test_qwen_token_plan_config_uses_env_secret,
        test_opencode_diagnose_empty_is_transient,
        test_opencode_diagnose_quota_is_not_transient,
        test_opencode_diagnose_not_found_is_not_transient,
        test_opencode_tool_404_does_not_mask_model_output,
        test_opencode_result_carries_transient_empty_field,
        test_opencode_check_available_returns_tuple,
        test_opencode_model_readiness_is_route_specific,
        test_opencode_model_readiness_accepts_qwen_token_plan_key,
        test_config_resolve_builtin_glm_uses_opencode,
        test_config_resolve_builtin_kimi_uses_opencode,
        test_config_resolve_builtin_composer_uses_cursor,
        test_config_resolve_builtin_grok_uses_opencode,
        test_opencode_preflight_recognizes_xai_key,
        test_opencode_grok_recipe_extracts_valid_grok_payload,
        test_config_resolve_builtin_cursor_grok_uses_cursor,
        test_cursor_grok_recipe_extracts_valid_payload,
        test_cursor_cmd_always_forces_plan_mode,
        test_cursor_plan_mode_unsupported_detection,
        test_config_resolve_builtin_qwen_uses_token_plan,
        test_provider_catalog_includes_optional_builtins,
        test_dispatch_propagates_native_provider_effort,
        test_webui_model_catalog_is_provider_grouped_and_current,
        test_finalize_moves_misplaced_refiner_verification,
        test_google_provider_builtins_are_default_and_resolve,
        test_agy_cmd_is_fail_closed,
        test_gemini_cmd_is_fail_closed,
        test_gemini_stream_json_extracts_payload,
        test_gemini_tier_ineligible_detection,
        test_config_env_provider_definition_parsed,
        test_config_env_provider_malformed_raises,
        test_refiner_schema_accepts_five_proposer_roster,
        test_layer_result_carries_transient_empty_field,
        test_manifest_summary_includes_transient_empty_arrays,
        test_layer1_manifest_round_trip_via_load,
        test_session_started_at_survives_phase_split_and_redispatch,
        test_redispatch_attempt_keeps_timing_provenance,
        test_parse_redispatch_arg_validates_names,
        test_report_generates_single_self_contained_file,
        test_report_embedded_json_round_trips,
        test_report_normalizes_legacy_phase_local_timing,
        test_report_lineage_reference_warning_is_nonfatal,
        test_report_renders_failed_and_transient_agents,
        test_report_backfills_legacy_retry_from_webui_log,
        test_report_escapes_script_close_in_logs,
        test_report_phase_split_partial_session,
        test_report_missing_manifest_exits_2,
        test_report_markdown_subset_renders,
        test_report_markdown_code_span_shields_bold,
        test_report_markdown_tables_and_emphasis_render,
        test_report_markdown_malformed_table_stays_text,
        test_report_markdown_nested_ordered_list_keeps_numbering,
        test_boolean_env_flags_parse_explicit_values,
        test_schema_validator_pattern_and_upper_bounds,
        test_refiner_prompt_escapes_model_controlled_close_tag,
        test_finalizer_normalizes_identity_and_deduplicates_recovery,
        test_workspace_guard_detects_git_mutation,
        test_report_template_accessibility_contracts,
        test_webui_effort_copy_and_control_contract,
        test_layer3_aggregation_schema_and_prompt_contract,
        test_layer3_codex_phase_writes_plan_and_lineage,
        test_layer3_fable_phase_uses_claude_and_writes_artifacts,
    ]
    results = [t() for t in tests]
    print("\n" + "=" * 72)
    passed = sum(1 for r in results if r)
    total = len(results)
    print(f"Result: {passed}/{total} tests passed")
    if passed == total:
        print("\nAll offline tests passed. Safe to authenticate the CLIs and run end-to-end.")
        return 0
    print("\nSome tests failed. Investigate before running end-to-end.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
