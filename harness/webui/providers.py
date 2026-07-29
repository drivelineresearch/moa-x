"""Provider catalog and local account-state probes."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


WEBUI_DIR = Path(__file__).resolve().parent
SCRIPTS_DIR = WEBUI_DIR.parent / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import config as harness_config  # noqa: E402
from adapters import agy, claude, codex, cursor, opencode  # noqa: E402


PROVIDER_META = {
    "codex": {
        "label": "Codex",
        "lab": "OpenAI",
        "group": "OpenAI · Codex CLI",
        "binary": lambda: os.environ.get("MOA_CODEX_BIN") or "codex",
        "probe": codex.check_available,
        "install": "npm i -g @openai/codex",
        "login": "codex login",
    },
    "claude": {
        "label": "Claude",
        "lab": "Anthropic",
        "group": "Anthropic · Claude Code",
        "binary": lambda: os.environ.get("MOA_CLAUDE_BIN") or "claude",
        "probe": claude.check_available,
        "install": "See docs.claude.com/claude-code",
        "login": "claude",
    },
    "opencode": {
        "label": "OpenCode",
        "lab": "Multi-lab",
        "group": "OpenCode routes",
        "binary": lambda: os.environ.get("MOA_OPENCODE_BIN") or "opencode",
        "probe": opencode.check_available,
        "install": "curl -fsSL https://opencode.ai/install | bash",
        "login": "opencode auth login",
    },
    "cursor": {
        "label": "Cursor",
        "lab": "Multi-lab",
        "group": "Cursor subscription routes",
        "binary": cursor._cursor_bin,
        "probe": cursor.check_available,
        "install": "curl https://cursor.com/install -fsS | bash",
        "login": "cursor-agent login",
    },
    "agy": {
        "label": "Antigravity",
        "lab": "Google",
        "group": "Google · Antigravity",
        "binary": lambda: os.environ.get("MOA_AGY_BIN") or "agy",
        "probe": agy.check_available,
        "install": "Install/update Antigravity, then run: agy install",
        "login": "agy",
    },
}

# Human-facing metadata stays keyed by the named provider used on the
# run_moa CLI. Model ids themselves come from config.BUILTIN_PROVIDERS, so the
# API cannot drift from actual dispatch. In particular, Claude 5 routes show
# the pinned canonical ids rather than the stale rolling aliases.
ROUTE_META: dict[str, dict[str, Any]] = {
    "codex": {"label": "GPT-5.6 Terra", "lab": "OpenAI",
              "roles": ["proposer", "refiner"]},
    "codex-sol": {"label": "GPT-5.6 Sol", "lab": "OpenAI",
                  "roles": ["proposer", "refiner", "aggregator"]},
    "codex-luna": {"label": "GPT-5.6 Luna", "lab": "OpenAI",
                   "roles": ["proposer", "refiner"]},
    "sonnet": {"label": "Claude Sonnet 5", "lab": "Anthropic",
               "roles": ["proposer", "refiner"]},
    "opus": {"label": "Claude Opus 5", "lab": "Anthropic",
             "roles": ["proposer", "refiner"]},
    "glm": {"label": "GLM-5.2", "lab": "Zhipu",
            "roles": ["proposer", "refiner"]},
    "kimi": {"label": "Kimi K3", "lab": "Moonshot",
             "roles": ["proposer", "refiner"]},
    "qwen": {"label": "Qwen 3.8 Max Preview · Token Plan", "lab": "Alibaba",
             "roles": ["proposer", "refiner"]},
    "qwen-opencode": {"label": "Qwen 3.7 Max", "lab": "Alibaba",
                      "roles": ["proposer", "refiner"]},
    "deepseek": {"label": "DeepSeek V4 Pro", "lab": "DeepSeek",
                 "roles": ["proposer", "refiner"]},
    "deepseek-flash": {"label": "DeepSeek V4 Flash", "lab": "DeepSeek",
                       "roles": ["proposer", "refiner"]},
    "grok": {"label": "Grok 4.5", "lab": "xAI",
             "roles": ["proposer", "refiner"]},
    "composer": {"label": "Composer 2.5", "lab": "Cursor",
                 "roles": ["proposer", "refiner"]},
    "cursor-grok": {"label": "Grok 4.5 High", "lab": "xAI",
                    "roles": ["proposer", "refiner"]},
    "agy-gemini-pro": {"label": "Gemini 3.1 Pro", "lab": "Google",
                       "roles": ["proposer", "refiner"]},
    "fable": {"label": "Fable 5 1M Thinking", "lab": "Anthropic",
                  "roles": ["aggregator"]},
}

HIDDEN_ROUTES = {"codex-reviewer", "codex-aggregator"}

# Routes that authenticated successfully but failed repeated full-schema live
# validation. Keep them visible for provenance, but prevent paid launches until
# the upstream CLI/model reliably returns its final structured response.
LIVE_BLOCKED_ROUTES = {
    "composer": (
        "Disabled after two live attempts returned progress text without "
        "the required final JSON"
    ),
}

EFFORT_OPTIONS = {
    "codex": ["low", "medium", "high", "xhigh"],
    "claude": ["low", "medium", "high", "xhigh", "max"],
    # AGY model slugs encode depth, so the UI changes the slug instead of
    # sending a separate effort flag.
    "agy": ["low", "medium", "high"],
}
ROUTE_EFFORT_OPTIONS = {
    "agy-gemini-pro": ["low", "high"],
    "fable": [],
}


def _effort_control(harness: str) -> str:
    """Describe whether depth is a CLI flag or part of a model id."""
    if harness == "agy":
        return "model_variant"
    if harness == "opencode":
        return "configured_variant"
    if harness in EFFORT_OPTIONS:
        return "flag"
    return "model_id"

_CACHE_LOCK = threading.Lock()
_CATALOG_CACHE: tuple[float, list[dict[str, Any]]] = (0, [])


def _route_records(harness: str) -> list[dict[str, Any]]:
    """Return curated named-provider routes for one CLI harness."""
    records = []
    for item in harness_config.load_provider_catalog().values():
        if (
            item.harness != harness
            or item.name in HIDDEN_ROUTES
            or item.harness not in PROVIDER_META
        ):
            continue
        meta = ROUTE_META.get(item.name, {})
        records.append(
            {
                "id": item.name,
                "name": meta.get("label", item.name),
                "model": item.model,
                "lab": meta.get("lab", PROVIDER_META[harness]["lab"]),
                "roles": meta.get("roles", ["proposer", "refiner"]),
                "effort": item.effort,
                "supports_effort": harness in EFFORT_OPTIONS,
                "effort_options": ROUTE_EFFORT_OPTIONS.get(
                    item.name, EFFORT_OPTIONS.get(harness, [])
                ),
                "effort_control": _effort_control(harness),
            }
        )
    return records


def _run(cmd: list[str], timeout: int = 12) -> tuple[bool, str]:
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout, shell=False
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, str(exc)
    text = ((proc.stdout or "") + "\n" + (proc.stderr or "")).strip()
    return proc.returncode == 0, text[:4000]


def probe_provider(provider_id: str) -> dict[str, Any]:
    """Use the same HOME, PATH, keychain, and env as the logged-in OS user."""
    if provider_id not in PROVIDER_META:
        raise KeyError(provider_id)
    meta = PROVIDER_META[provider_id]
    binary = meta["binary"]()
    installed = bool(shutil.which(binary))
    started = time.monotonic()
    routes = _route_records(provider_id)
    models: list[dict[str, Any]] = [
        {"id": route["model"], "name": route["name"]} for route in routes
    ]
    detail = "not installed"
    authenticated = False
    version = None
    discovered_models: set[str] = set()

    if installed:
        version_ok, version_output = _run([binary, "--version"], timeout=5)
        if version_ok and version_output:
            version = version_output.splitlines()[0].strip()[:160]
    if installed and "probe" in meta:
        authenticated, detail = meta["probe"]()
    if installed and provider_id == "opencode":
        route_checks = opencode.check_models_available(
            route["model"] for route in routes
        )
        routes = [
            {
                **route,
                "available": route_checks[route["model"]][0],
                "availability_detail": route_checks[route["model"]][1],
            }
            for route in routes
        ]
        ready_count = sum(route["available"] for route in routes)
        authenticated = ready_count > 0
        detail = f"{ready_count}/{len(routes)} configured routes ready"
    if installed and provider_id == "agy":
        models_ok, discovered, model_detail = agy.list_models()
        if not models_ok:
            authenticated = False
            detail = model_detail
        else:
            discovered_models = set(discovered)
            detail = (
                f"{version or 'agy'}; persisted account ready; "
                f"{len(discovered_models)} model variants available"
            )
    if provider_id == "agy":
        routes = [
            {
                **route,
                "available": authenticated and route["model"] in discovered_models,
                "availability_detail": (
                    detail
                    if route["model"] in discovered_models
                    else f"{route['model']} is not available to this AGY account"
                ),
            }
            for route in routes
        ]
    elif provider_id != "opencode":
        routes = [
            {
                **route,
                "available": authenticated,
                "availability_detail": detail,
            }
            for route in routes
        ]
    routes = [
        (
            {
                **route,
                "available": False,
                "availability_detail": LIVE_BLOCKED_ROUTES[route["id"]],
            }
            if route["id"] in LIVE_BLOCKED_ROUTES
            else route
        )
        for route in routes
    ]

    return {
        "id": provider_id,
        "label": meta["label"],
        "lab": meta["lab"],
        "group": meta["group"],
        "harness": provider_id,
        "binary": binary,
        "binary_path": shutil.which(binary),
        "installed": installed,
        "authenticated": authenticated,
        "status": "ready" if authenticated else ("needs_auth" if installed else "missing"),
        "detail": detail,
        "version": version,
        "models": models,
        "routes": routes,
        "supports_effort": provider_id in EFFORT_OPTIONS,
        "effort_options": EFFORT_OPTIONS.get(provider_id, []),
        "install_command": meta["install"],
        "login_command": meta["login"],
        "uses_machine_account": True,
        "last_checked": datetime.now(UTC).isoformat(),
        "probe_ms": round((time.monotonic() - started) * 1000),
    }


def provider_catalog(probe: bool = True) -> list[dict[str, Any]]:
    global _CATALOG_CACHE
    # Provider keys may live in the user's existing MoA config rather than the
    # shell that launched Flask. Load them before any route-level auth probe.
    harness_config.apply_config_to_env()
    if probe:
        with _CACHE_LOCK:
            cached_at, cached = _CATALOG_CACHE
            if cached and time.monotonic() - cached_at < 30:
                return cached
        ids = list(PROVIDER_META)
        with ThreadPoolExecutor(max_workers=len(ids)) as pool:
            by_id = dict(zip(ids, pool.map(probe_provider, ids)))
        items = [by_id[provider_id] for provider_id in ids]
        with _CACHE_LOCK:
            _CATALOG_CACHE = (time.monotonic(), items)
        return items

    items = []
    for provider_id, meta in PROVIDER_META.items():
        binary = meta["binary"]()
        items.append(
            {
                "id": provider_id,
                "label": meta["label"],
                "lab": meta["lab"],
                "group": meta["group"],
                "harness": provider_id,
                "binary": binary,
                "installed": bool(shutil.which(binary)),
                "install_command": meta["install"],
                "login_command": meta["login"],
                "uses_machine_account": True,
                "models": [
                    {"id": route["model"], "name": route["name"]}
                    for route in _route_records(provider_id)
                ],
                "routes": _route_records(provider_id),
                "supports_effort": (
                    provider_id in EFFORT_OPTIONS
                ),
                "effort_options": EFFORT_OPTIONS.get(provider_id, []),
            }
        )
    return items


def model_catalog(*, probe: bool = True) -> list[dict[str, Any]]:
    harness_config.apply_config_to_env()
    resolved = harness_config.load_resolved_config()
    proposer_defaults = {item.name for item in resolved.proposers}
    refiner_defaults = {item.name for item in resolved.refiners}
    aggregator_default = resolved.aggregator.name if resolved.aggregator else None
    status_by_harness = {
        item["id"]: item for item in provider_catalog(probe=probe)
    }
    models = []
    for item in harness_config.load_provider_catalog().values():
        if item.name in HIDDEN_ROUTES or item.harness not in PROVIDER_META:
            continue
        meta = ROUTE_META.get(item.name, {})
        roles = meta.get("roles", ["proposer", "refiner"])
        default_roles = []
        if item.name in proposer_defaults:
            default_roles.append("proposer")
        if item.name in refiner_defaults:
            default_roles.append("refiner")
        if item.name == aggregator_default:
            default_roles.append("aggregator")
        provider_status = status_by_harness.get(item.harness, {})
        route_status = next(
            (
                route
                for route in provider_status.get("routes", [])
                if route["id"] == item.name
            ),
            {},
        )
        models.append(
            {
                "id": item.name,
                "name": meta.get("label", item.name),
                # Group by the local CLI account card while retaining the
                # named provider id used in run_moa arguments.
                "provider_id": item.harness,
                "named_provider": item.name,
                "provider_label": PROVIDER_META[item.harness]["label"],
                "harness": item.harness,
                "model": item.model,
                "lab": meta.get(
                    "lab",
                    PROVIDER_META[item.harness]["lab"],
                ),
                "timeout": item.timeout,
                "effort": item.effort,
                "supports_effort": (
                    item.harness in EFFORT_OPTIONS
                ),
                "effort_options": ROUTE_EFFORT_OPTIONS.get(
                    item.name, EFFORT_OPTIONS.get(item.harness, [])
                ),
                "effort_control": _effort_control(item.harness),
                "roles": roles,
                "default_roles": default_roles,
                "available": bool(
                    route_status.get(
                        "available",
                        provider_status.get("authenticated"),
                    )
                ),
                "availability_detail": route_status.get(
                    "availability_detail",
                    provider_status.get("detail"),
                ),
            }
        )
    return models
