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
            {"approach": "in-memory LRU per pod", "reason": "doesn't share across replicas"}
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
    "workdir: /home/kyle/repo\n"
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
    print("\n[4c] Strict-mode lint: proposer + refiner schemas are OpenAI-compliant")
    p_schema = run_moa._load_schema(run_moa.PROPOSER_SCHEMA_PATH)
    r_schema = run_moa._load_schema(run_moa.REFINER_SCHEMA_PATH)
    p_violations = run_moa.lint_schema_openai_strict(p_schema)
    r_violations = run_moa.lint_schema_openai_strict(r_schema)
    clean = not p_violations and not r_violations
    detail = f"proposer={len(p_violations)} refiner={len(r_violations)}"
    return _check("both schemas strict-mode clean", clean, detail)


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


def test_unsupported_keyword_warning() -> bool:
    print("\n[12d] _validate_against_schema warns on unsupported keywords (anyOf, if, oneOf)")
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
    # --arm should be gone entirely — check for the exact flag token with a
    # trailing non-name char (space, newline, bracket, equals, end-of-string).
    no_arm_flag = re.search(r"(?<!-)--arm(?![A-Za-z0-9_-])", help_text) is None
    ok = has_self_moa and has_proposers and has_refiners and no_arm_flag
    return _check(
        "--self-moa wired up, --arm removed", ok,
        f"self-moa={has_self_moa} proposers={has_proposers} "
        f"refiners={has_refiners} no-arm-flag={no_arm_flag}",
    )


def test_install_deps_default_config_only_needs_default_harnesses() -> bool:
    """install_deps.py without harness/config.yaml resolves to the default
    proposers/refiners and only needs codex/opencode/claude — not cursor."""
    print("\n[14b] install_deps: default config → needed harnesses {codex, opencode, claude}")
    from config import load_resolved_config
    import tempfile
    from pathlib import Path as _Path
    # Force "no config.yaml" by passing a nonexistent path
    loaded = load_resolved_config(config_path=_Path("/tmp/install_deps_no_yaml_xx_DOES_NOT_EXIST.yaml"))
    needed = {p.harness for p in loaded.proposers + loaded.refiners}
    return _ok(needed == {"codex", "opencode", "claude"}, f"got {sorted(needed)}")


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
    ]
    missing = [str(p.relative_to(skill_dir)) for p in assets if not p.exists()]
    return _check("no missing assets", len(missing) == 0, f"missing={missing}")


def test_config_resolve_builtin_codex() -> bool:
    print("\n[16] config.resolve_provider returns built-in codex triple")
    from config import resolve_provider
    rp = resolve_provider("codex", user_providers={})
    ok = (rp.name == "codex" and rp.harness == "codex" and rp.model == "gpt-5.4")
    return _ok(ok, f"got {rp}")

def test_config_resolve_builtin_sonnet_uses_claude_harness() -> bool:
    print("\n[17] config.resolve_provider: sonnet name maps to claude harness")
    from config import resolve_provider
    rp = resolve_provider("sonnet", user_providers={})
    ok = (rp.name == "sonnet" and rp.harness == "claude" and rp.model == "claude-sonnet-4-6")
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


def test_config_resolve_builtin_glm_uses_opencode() -> bool:
    print("\n[N] config.resolve_provider: glm maps to opencode harness / zhipuai model")
    from config import resolve_provider
    rp = resolve_provider("glm", user_providers={})
    ok = (rp.name == "glm" and rp.harness == "opencode" and rp.model == "zhipuai/glm-5.2")
    return _ok(ok, f"got {rp}")


def test_config_resolve_builtin_kimi_uses_opencode() -> bool:
    print("\n[N] config.resolve_provider: kimi maps to opencode harness / moonshot model")
    from config import resolve_provider
    rp = resolve_provider("kimi", user_providers={})
    ok = (rp.name == "kimi" and rp.harness == "opencode" and rp.model == "moonshotai/kimi-k2.7-code")
    return _ok(ok, f"got {rp}")


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
        test_strict_mode_lint_catches_violation,
        test_codex_extractor_finds_payload_in_framed_output,
        test_claude_extractor_finds_structured_output,
        test_claude_extractor_fallback_to_fenced_result,
        test_refiner_schema_validator_broadcast_codex,
        test_refiner_schema_validator_broadcast_kimi,
        test_refiner_schema_accepts_user_named_provider_refs,
        test_refiner_schema_rejects_malformed_proposer_ref,
        test_evidence_cross_field_rejects_code_with_null_file,
        test_evidence_cross_field_rejects_external_with_null_url,
        test_evidence_cross_field_accepts_valid_payload,
        test_unsupported_keyword_warning,
        test_manifest_config_section_present,
        test_config_precedence_env_over_dotenv_over_yaml,
        test_self_moa_argparse_smoke,
        test_install_deps_default_config_only_needs_default_harnesses,
        test_install_deps_cursor_only_config_skips_other_harnesses,
        test_install_deps_schema_coherence_catches_bad_name,
        test_skill_assets_present,
        test_config_resolve_builtin_codex,
        test_config_resolve_builtin_sonnet_uses_claude_harness,
        test_config_resolve_unknown_name_raises,
        test_config_resolve_user_provider_yaml_timeout,
        test_config_resolve_env_timeout_override,
        test_config_resolve_env_timeout_malformed_raises,
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
        test_cursor_diagnose_failure_quota_is_not_transient,
        test_cursor_diagnose_failure_empty_stdout_is_not_transient,
        test_cursor_result_carries_transient_empty_field,
        test_opencode_extractor_finds_bare_payload,
        test_opencode_extractor_handles_fenced_and_prose,
        test_opencode_diagnose_empty_is_transient,
        test_opencode_diagnose_quota_is_not_transient,
        test_opencode_result_carries_transient_empty_field,
        test_opencode_check_available_returns_tuple,
        test_config_resolve_builtin_glm_uses_opencode,
        test_config_resolve_builtin_kimi_uses_opencode,
        test_layer_result_carries_transient_empty_field,
        test_manifest_summary_includes_transient_empty_arrays,
        test_layer1_manifest_round_trip_via_load,
        test_parse_redispatch_arg_validates_names,
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
