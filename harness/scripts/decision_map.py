#!/usr/bin/env python3
"""Build the deterministic, evidence-weighted MoA-X decision map.

The model-facing proposer, refiner, and final-plan schemas remain the source
artifacts.  This module turns their existing references into one
orchestrator-owned graph; models never manufacture graph IDs, quality scores,
or confidence gates.
"""
from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import re
import stat
import subprocess
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

try:  # Package import from the Web UI.
    from .model_labs import ROUTE_META, model_lab, route_lab_id
except ImportError:  # Script import from run_moa.py/report.py.
    from model_labs import ROUTE_META, model_lab, route_lab_id


SCRIPT_DIR = Path(__file__).resolve().parent
DECISION_MAP_SCHEMA_PATH = SCRIPT_DIR / "schemas" / "decision-map.schema.json"
DECISION_MAP_FILENAME = "decision-map.json"
CLAIM_PATH_RE = re.compile(r"^plan\[(\d+)]\.evidence\[(\d+)]$")
CONFIDENCE_RANK = {"low": 0, "medium": 1, "high": 2}
USABLE_RECEIPT_STATES = {"captured", "declared_excerpt", "repository_drift"}
USABLE_VERIFICATION_SOURCE_STATES = {"captured", "declared_locator"}
NON_IDENTITY_QUERY_KEYS = {
    "access_token", "api_key", "apikey", "auth", "authorization",
    "credential", "expires", "fbclid", "gclid", "key", "msclkid",
    "password", "secret", "sig", "signature", "token",
}
SENSITIVE_PATH_NAMES = {
    ".env", ".git", ".netrc", ".npmrc", ".pypirc", ".ssh",
    "credentials", "secrets",
}
SENSITIVE_PATH_SUFFIXES = {".key", ".p12", ".pem", ".pfx"}


def _non_identity_query_key(value: str) -> bool:
    normalized = re.sub(r"[^a-z0-9]+", "_", value.casefold()).strip("_")
    segments = set(normalized.split("_"))
    return (
        normalized.startswith("utm_")
        or normalized in NON_IDENTITY_QUERY_KEYS
        or bool(
            segments
            & {
                "apikey", "auth", "authorization", "credential", "key",
                "password", "secret", "sig", "signature", "token",
            }
        )
    )


def _portable_code_locator(value: Any) -> Optional[str]:
    text = str(value or "").strip().replace("\\", "/")
    if not text or re.match(r"^[a-zA-Z]:/", text):
        return None
    path = Path(text)
    lowered = {part.casefold() for part in path.parts}
    if path.is_absolute() or ".." in path.parts:
        return None
    if path.suffix.casefold() in SENSITIVE_PATH_SUFFIXES:
        return None
    if any(
        part in SENSITIVE_PATH_NAMES
        or part.startswith(".env.")
        or "credential" in part
        or part.startswith("secret.")
        for part in lowered
    ):
        return None
    return path.as_posix()


def _portable_verification_source(value: Any) -> Optional[str]:
    source = str(value or "").strip()
    if not source:
        return None
    if source.startswith(("http://", "https://")):
        return _canonical_url(source)
    match = re.fullmatch(r"(.+):([1-9]\d*)", source)
    if not match:
        return None
    locator = _portable_code_locator(match.group(1))
    return f"{locator}:{match.group(2)}" if locator else None


def _read_json(path: Path) -> Optional[dict[str, Any]]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _content_id(prefix: str, value: Any) -> str:
    digest = hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()
    return f"{prefix}-{digest}"


def _text_key(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).casefold()
    text = re.sub(r"[^\w]+", " ", text, flags=re.UNICODE)
    return " ".join(text.split())


def _canonical_url(value: Any) -> Optional[str]:
    raw = str(value or "").strip()
    if not raw or any(
        character.isspace() or ord(character) < 0x20 or ord(character) == 0x7F
        for character in raw
    ):
        return None
    try:
        parts = urlsplit(raw)
        host_value = parts.hostname
        port_number = parts.port
    except ValueError:
        return None
    scheme = parts.scheme.lower()
    if scheme not in {"http", "https"} or not parts.netloc or not host_value:
        return None

    host = host_value.lower()
    authority = parts.netloc.rsplit("@", 1)[-1]
    if authority.endswith(":"):
        return None

    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        if ":" in host:
            return None
        unqualified_host = host[:-1] if host.endswith(".") else host
        try:
            ascii_host = unqualified_host.encode("idna").decode("ascii")
        except UnicodeError:
            return None
        numeric_parts = ascii_host.lower().split(".")
        noncanonical_numeric_ipv4 = (
            1 <= len(numeric_parts) <= 4
            and all(
                re.fullmatch(r"(?:[0-9]+|0x[0-9a-f]*)", part) is not None
                for part in numeric_parts
            )
        )
        if (
            not unqualified_host
            or len(ascii_host) > 253
            or noncanonical_numeric_ipv4
            or any(
                re.fullmatch(
                    r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label
                )
                is None
                for label in ascii_host.lower().split(".")
            )
        ):
            return None
        host = ascii_host.lower() + ("." if host.endswith(".") else "")
        if ":" in authority:
            _authority_host, raw_port = authority.rsplit(":", 1)
            if not raw_port.isascii() or not raw_port.isdecimal():
                return None
        rendered_host = host
    else:
        host = address.compressed.lower()
        if address.version == 6:
            if re.fullmatch(r"\[[^]]+\](?::[0-9]+)?", authority) is None:
                return None
            rendered_host = f"[{host}]"
        else:
            if ":" in authority:
                _authority_host, raw_port = authority.rsplit(":", 1)
                if not raw_port.isascii() or not raw_port.isdecimal():
                    return None
            rendered_host = host

    default_port = (scheme, port_number) in {("http", 80), ("https", 443)}
    port = (
        f":{port_number}"
        if port_number is not None and not default_port
        else ""
    )
    # Credentials never belong in a portable evidence receipt. Source identity
    # is host/path/query; URL userinfo is deliberately discarded.
    netloc = f"{rendered_host}{port}"
    path = parts.path or "/"
    if path != "/":
        path = path.rstrip("/")
    query = urlencode(sorted(
        (key, item)
        for key, item in parse_qsl(parts.query, keep_blank_values=True)
        if not _non_identity_query_key(key)
    ))
    return urlunsplit((scheme, netloc, path, query, ""))


def _source_identity_url(url: Optional[str]) -> Optional[str]:
    """Collapse query variants for diversity math while preserving display URL."""
    if not url:
        return None
    parts = urlsplit(url)
    if not parts.scheme or not parts.netloc:
        return url
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


def _source_domain(url: Optional[str]) -> Optional[str]:
    if not url:
        return None
    try:
        return (urlsplit(url).hostname or "").lower() or None
    except ValueError:
        return None


def _run_git(repo_path: Path, *args: str) -> Optional[bytes]:
    try:
        completed = subprocess.run(
            ["git", *args],
            cwd=repo_path,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=8,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return completed.stdout if completed.returncode == 0 else None


def capture_repository_state(repo_path: Optional[Path]) -> dict[str, Any]:
    """Capture integrity receipts without placing diff contents in the map."""
    state: dict[str, Any] = {
        "commit": None,
        "tree": None,
        "dirty": None,
        "status_sha256": None,
        "diff_sha256": None,
        "captured_at": None,
        "capture_status": "unavailable",
    }
    if repo_path is None or not repo_path.is_dir():
        return state
    commit = _run_git(repo_path, "rev-parse", "--verify", "HEAD")
    tree = _run_git(repo_path, "rev-parse", "HEAD^{tree}")
    status = _run_git(repo_path, "status", "--porcelain=v1", "-z")
    diff = _run_git(repo_path, "diff", "--binary", "--no-ext-diff", "HEAD")
    if commit is None or tree is None or status is None or diff is None:
        return state
    state.update(
        commit=commit.decode("ascii", errors="replace").strip() or None,
        tree=tree.decode("ascii", errors="replace").strip() or None,
        dirty=bool(status),
        status_sha256=hashlib.sha256(status).hexdigest(),
        diff_sha256=hashlib.sha256(diff).hexdigest(),
        captured_at=datetime.now(timezone.utc).isoformat(),
        capture_status="live_run",
    )
    return state


def _safe_session_path(session_dir: Path, relative: Any) -> Optional[Path]:
    if not relative:
        return None
    root = session_dir.resolve()
    candidate = (root / str(relative)).resolve()
    if candidate != root and root not in candidate.parents:
        return None
    return candidate


def _repo_file_receipt(
    repo_path: Optional[Path], relative: Any, line_number: Any
) -> tuple[Optional[str], Optional[str], str]:
    """Return file and cited-line hashes without copying repository text."""
    if repo_path is None or not relative:
        return None, None, "unavailable"
    portable = _portable_code_locator(relative)
    if portable is None:
        raw_text = str(relative or "").replace("\\", "/")
        raw = Path(raw_text)
        if raw.is_absolute() or ".." in raw.parts or re.match(r"^[a-zA-Z]:/", raw_text):
            return None, None, "unsafe_path"
        return None, None, "sensitive_path"
    raw = Path(portable)
    if raw.is_absolute() or ".." in raw.parts:
        return None, None, "unsafe_path"
    root = repo_path.resolve()
    unresolved = root
    for part in raw.parts:
        unresolved = unresolved / part
        if unresolved.is_symlink():
            return None, None, "symlink"
    candidate = (root / portable).resolve()
    if candidate != root and root not in candidate.parents:
        return None, None, "unsafe_path"
    descriptor: Optional[int] = None
    try:
        flags = os.O_RDONLY
        flags |= getattr(os, "O_NONBLOCK", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(candidate, flags)
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            return None, None, "non_regular"
        if metadata.st_size > 2 * 1024 * 1024:
            return None, None, "oversized"
        chunks: list[bytes] = []
        remaining = 2 * 1024 * 1024 + 1
        while remaining:
            chunk = os.read(descriptor, min(64 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        payload = b"".join(chunks)
    except OSError:
        return None, None, "unreadable"
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass
    if len(payload) > 2 * 1024 * 1024:
        return None, None, "oversized"
    if b"\x00" in payload[:8192]:
        return None, None, "binary"
    file_hash = hashlib.sha256(payload).hexdigest()
    line_hash = None
    if isinstance(line_number, int) and line_number > 0:
        lines = payload.splitlines()
        if line_number <= len(lines):
            line_hash = hashlib.sha256(lines[line_number - 1]).hexdigest()
    if line_hash is None:
        return file_hash, None, "line_out_of_range"
    return file_hash, line_hash, "captured"


def _verification_source_receipt(
    repo_path: Optional[Path],
    source: Any,
    *,
    repository_matches: bool,
    previous: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Canonicalize and, for local sources, hash a reviewer receipt."""
    raw_source = str(source or "").strip()
    canonical = _portable_verification_source(raw_source)
    if canonical is None:
        return {
            "source_url": None,
            "source_type": None,
            "source_capture_status": (
                "missing_locator" if not raw_source else "invalid_locator"
            ),
            "file_sha256": None,
            "line_sha256": None,
        }
    if canonical.startswith(("http://", "https://")):
        return {
            "source_url": canonical,
            "source_type": "external",
            "source_capture_status": "declared_locator",
            "file_sha256": None,
            "line_sha256": None,
        }

    file_name, line_text = canonical.rsplit(":", 1)
    previous = previous if isinstance(previous, dict) else {}
    previous_captured = (
        previous.get("source_capture_status") in {"captured", "repository_drift"}
        and bool(previous.get("file_sha256"))
        and bool(previous.get("line_sha256"))
    )
    if repository_matches:
        file_hash, line_hash, capture_status = _repo_file_receipt(
            repo_path, file_name, int(line_text)
        )
        if (
            previous.get("file_sha256")
            and (
                file_hash != previous.get("file_sha256")
                or line_hash != previous.get("line_sha256")
            )
        ):
            file_hash = previous.get("file_sha256")
            line_hash = previous.get("line_sha256")
            capture_status = "repository_drift"
    elif previous_captured:
        file_hash = previous.get("file_sha256")
        line_hash = previous.get("line_sha256")
        capture_status = "repository_drift"
    else:
        file_hash = line_hash = None
        capture_status = "unavailable_legacy"
    return {
        "source_url": canonical,
        "source_type": "code",
        "source_capture_status": capture_status,
        "file_sha256": file_hash,
        "line_sha256": line_hash,
    }


def active_manifest_for_session(
    session_dir: Path,
) -> tuple[Optional[Path], dict[str, Any]]:
    """Choose the newest manifest for the active session, not a copied retry.

    Targeted redispatch starts by copying the source session, so an older full
    ``manifest.json`` can coexist briefly with a newly written Layer-1 bridge.
    Prefer the manifest whose session id matches the current scout, then use
    retained completion time (and filesystem mtime only as a tie-breaker).
    """
    full_path = session_dir / "manifest.json"
    layer1_path = session_dir / "layer1-manifest.json"
    candidates = [
        (full_path, _read_json(full_path)),
        (layer1_path, _read_json(layer1_path)),
    ]
    available = [(path, value) for path, value in candidates if value is not None]
    if not available:
        return None, {}
    if len(available) == 1:
        return available[0]

    scout = _read_json(session_dir / "scout-brief.json") or {}
    session_id = str(scout.get("session_id") or "")
    matching = [
        item
        for item in available
        if session_id and str(item[1].get("session_id") or "") == session_id
    ]
    pool = matching or available

    def recency(item: tuple[Path, dict[str, Any]]) -> tuple[float, int, int]:
        path, value = item
        finished_at = value.get("finished_at")
        retained_time = (
            float(finished_at)
            if isinstance(finished_at, (int, float))
            and not isinstance(finished_at, bool)
            and finished_at > 0
            else 0.0
        )
        try:
            modified = path.stat().st_mtime_ns
        except OSError:
            modified = 0
        # Prefer a full manifest only when retained and filesystem times tie.
        return retained_time, modified, int(path.name == "manifest.json")

    return max(pool, key=recency)


def _manifest_for_session(session_dir: Path) -> dict[str, Any]:
    return active_manifest_for_session(session_dir)[1]


def final_artifact_staleness(
    session_dir: Path,
    manifest: Optional[dict[str, Any]] = None,
) -> Optional[str]:
    """Return the fail-closed reason retained final outputs are no longer active."""
    active = manifest if isinstance(manifest, dict) else _manifest_for_session(session_dir)
    layer3 = active.get("layer3")
    attempts = layer3 if isinstance(layer3, list) else []
    latest = attempts[-1] if attempts and isinstance(attempts[-1], dict) else None
    if isinstance(latest, dict) and latest.get("success") is False:
        return "layer3_failed"
    if active.get("phase") == "layer1":
        return "layer1_retry"
    if any(isinstance(item, dict) and item.get("success") for item in attempts):
        return None

    finished_at = active.get("finished_at")
    if (
        not isinstance(finished_at, (int, float))
        or isinstance(finished_at, bool)
        or finished_at <= 0
    ):
        return None
    for filename in ("final-plan.md", "final-plan.json"):
        path = session_dir / filename
        try:
            if path.is_file() and path.stat().st_mtime < float(finished_at):
                return "predates_manifest"
        except OSError:
            continue
    return None


def _load_payload(session_dir: Path, entry: dict[str, Any]) -> Optional[dict[str, Any]]:
    path = _safe_session_path(session_dir, entry.get("json_path"))
    return _read_json(path) if path and path.is_file() else None


def _configured_agents(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    config = manifest.get("config") if isinstance(manifest.get("config"), dict) else {}
    agents: list[dict[str, Any]] = []

    def add_many(values: Any, role: str) -> None:
        if not isinstance(values, list):
            return
        for value in values:
            if isinstance(value, dict):
                agent_id = str(value.get("name") or value.get("id") or "")
                model = str(value.get("model") or agent_id)
            else:
                agent_id = str(value or "")
                model = agent_id
            if agent_id:
                agents.append({"id": agent_id, "role": role, "model": model})

    add_many(config.get("proposers") or config.get("proposer_instances"), "proposer")
    add_many(config.get("refiners") or config.get("refiner_instances"), "refiner")
    aggregator = config.get("aggregator")
    if isinstance(aggregator, dict) and aggregator.get("name"):
        agents.append(
            {
                "id": str(aggregator["name"]),
                "role": "aggregator",
                "model": str(aggregator.get("model") or aggregator["name"]),
            }
        )
    return agents


def _agent_records(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    configured = {(a["role"], a["id"]): a for a in _configured_agents(manifest)}
    results: dict[tuple[str, str], dict[str, Any]] = {}
    for layer_key, role in (("layer1", "proposer"), ("layer2", "refiner"), ("layer3", "aggregator")):
        values = manifest.get(layer_key)
        if not isinstance(values, list):
            continue
        for value in values:
            if not isinstance(value, dict) or not value.get("agent_id"):
                continue
            agent_id = str(value["agent_id"])
            results[(role, agent_id)] = value
            configured.setdefault(
                (role, agent_id),
                {"id": agent_id, "role": role, "model": str(value.get("model") or agent_id)},
            )

    stage_order = {"proposer": 1, "refiner": 2, "aggregator": 3}
    completed_stage = 0
    if manifest.get("layer1"):
        completed_stage = 1
    if manifest.get("layer2") or manifest.get("layer2_mode") == "skipped":
        completed_stage = 2
    if manifest.get("layer3"):
        completed_stage = 3

    records: list[dict[str, Any]] = []
    for (role, agent_id), base in sorted(
        configured.items(), key=lambda item: (stage_order[item[0][0]], item[0][1])
    ):
        result = results.get((role, agent_id))
        status = "queued"
        if result is not None:
            status = "completed" if result.get("success") else "failed"
        elif stage_order[role] < completed_stage:
            status = "blocked"
        model = str(base.get("model") or agent_id)
        lab_id = route_lab_id(agent_id, model)
        records.append(
            {
                "id": agent_id,
                "label": str(ROUTE_META.get(agent_id, {}).get("label") or agent_id),
                "role": role,
                "lab_id": lab_id,
                "lab": str(model_lab(lab_id)["label"]),
                "model": model,
                "status": status,
            }
        )
    return records


def _stage(manifest: dict[str, Any], lineage: Optional[dict[str, Any]]) -> str:
    if lineage is not None:
        return "complete"
    if manifest.get("layer2") or manifest.get("layer2_mode") == "skipped":
        return "review"
    if manifest.get("layer1"):
        return "proposals"
    return "setup"


def _generated_at(manifest: dict[str, Any]) -> Optional[str]:
    timestamp = manifest.get("finished_at")
    if not isinstance(timestamp, (int, float)) or timestamp <= 0:
        return None
    return datetime.fromtimestamp(float(timestamp), timezone.utc).isoformat()


def _effective_confidence(stated: Optional[str], ceiling: str) -> str:
    if ceiling not in CONFIDENCE_RANK:
        return stated if stated in CONFIDENCE_RANK else "pending"
    if stated not in CONFIDENCE_RANK:
        return ceiling
    return min((stated, ceiling), key=lambda value: CONFIDENCE_RANK[value])


def _edge(edges: list[dict[str, Any]], source: str, target: str, kind: str, status: str) -> None:
    value = {"from": source, "to": target, "kind": kind, "status": status}
    value["id"] = _content_id("edge", value)
    if all(item["id"] != value["id"] for item in edges):
        edges.append(value)


def build_decision_map(
    session_dir: Path,
    *,
    repository_state: Optional[dict[str, Any]] = None,
    prior_map: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Derive a stable decision graph from retained session artifacts."""
    session_dir = session_dir.resolve()
    manifest = _manifest_for_session(session_dir)
    scout = _read_json(session_dir / "scout-brief.json") or {}
    lineage_path = session_dir / "final-plan.json"
    lineage = _read_json(lineage_path)
    prior_map = prior_map or _read_json(session_dir / DECISION_MAP_FILENAME) or {}
    prior_evidence = {
        str(item.get("id")): item
        for item in prior_map.get("evidence", [])
        if isinstance(item, dict) and item.get("id")
    }
    prior_verifications = {
        str(item.get("id")): item
        for item in prior_map.get("verifications", [])
        if isinstance(item, dict) and item.get("id")
    }
    repo_raw = scout.get("repo_path") or (manifest.get("config") or {}).get("repo_path")
    repo_path = Path(str(repo_raw)).expanduser().resolve() if repo_raw else None
    repository = repository_state or (
        prior_map.get("repository")
        if isinstance(prior_map.get("repository"), dict)
        else None
    ) or {
        "commit": None,
        "tree": None,
        "dirty": None,
        "status_sha256": None,
        "diff_sha256": None,
        "captured_at": None,
        "capture_status": "unavailable_legacy",
    }
    current_repository = capture_repository_state(repo_path)
    repository_matches = (
        repository.get("capture_status") == "live_run"
        and current_repository.get("capture_status") == "live_run"
        and all(
            repository.get(key) == current_repository.get(key)
            for key in ("commit", "tree", "status_sha256", "diff_sha256")
        )
    )
    contract_warnings: list[str] = []
    pointer_warnings: list[str] = []

    proposer_payloads: dict[str, dict[str, Any]] = {}
    refiner_payloads: dict[str, dict[str, Any]] = {}
    for key, destination in (("layer1", proposer_payloads), ("layer2", refiner_payloads)):
        for entry in manifest.get(key, []) if isinstance(manifest.get(key), list) else []:
            if not isinstance(entry, dict) or not entry.get("agent_id"):
                continue
            payload = _load_payload(session_dir, entry)
            if payload is not None:
                destination[str(entry["agent_id"])] = payload

    input_digest = hashlib.sha256(
        _canonical_json(
            {
                "layer2_mode": manifest.get("layer2_mode"),
                "proposers": proposer_payloads,
                "refiners": refiner_payloads,
                "lineage": lineage,
                "results": {
                    key: [
                        {
                            "agent_id": item.get("agent_id"),
                            "success": item.get("success"),
                            "schema_valid": item.get("schema_valid"),
                            "reviewing": item.get("reviewing"),
                        }
                        for item in manifest.get(key, [])
                        if isinstance(item, dict)
                    ]
                    for key in ("layer1", "layer2", "layer3")
                },
            }
        ).encode("utf-8")
    ).hexdigest()
    stale_final_reason = final_artifact_staleness(session_dir, manifest)
    if isinstance(lineage, dict) and stale_final_reason:
        if stale_final_reason == "layer3_failed":
            warning = (
                "The latest Layer 3 attempt failed; older final-plan artifacts "
                "were excluded from the active map"
            )
        elif stale_final_reason == "layer1_retry":
            warning = (
                "The active Layer 1 checkpoint supersedes copied final-plan "
                "artifacts; older final decisions were excluded from the active map"
            )
        else:
            warning = (
                "Final-plan artifacts predate the active manifest and were "
                "excluded from the active map"
            )
        contract_warnings.append(warning)
        lineage = None

    claims: dict[str, dict[str, Any]] = {}
    evidence: dict[str, dict[str, Any]] = {}
    verifications: list[dict[str, Any]] = []
    decisions: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    claim_location: dict[tuple[str, str], tuple[str, str]] = {}
    step_claims: dict[tuple[str, int], list[str]] = {}
    agents = _agent_records(manifest)
    agent_models = {item["id"]: item["model"] for item in agents}

    for proposer_id in sorted(proposer_payloads):
        payload = proposer_payloads[proposer_id]
        plan = payload.get("plan") if isinstance(payload.get("plan"), list) else []
        for step_index, step in enumerate(plan):
            if not isinstance(step, dict):
                continue
            step_evidence = step.get("evidence") if isinstance(step.get("evidence"), list) else []
            for evidence_index, item in enumerate(step_evidence):
                if not isinstance(item, dict):
                    continue
                claim_text = str(item.get("claim") or step.get("why") or step.get("step") or "").strip()
                if not claim_text:
                    continue
                claim_id = _content_id("claim", {"text": _text_key(claim_text)})
                ev_type = str(item.get("type") or "unknown")
                canonical_url = _canonical_url(item.get("url")) if ev_type == "external" else None
                raw_file_name = item.get("file")
                file_name = _portable_code_locator(raw_file_name)
                line_number = item.get("line") if isinstance(item.get("line"), int) else None
                snippet = str(item.get("snippet")) if item.get("snippet") is not None else None
                if ev_type == "code":
                    source_key = f"code:{file_name or 'unknown'}"
                    label = f"{file_name or 'unknown file'}:{line_number or '?'}"
                else:
                    source_key = f"external:{_source_identity_url(canonical_url) or 'unknown'}"
                    label = _source_domain(canonical_url) or canonical_url or "External source"
                evidence_id = _content_id(
                    "evidence",
                    {
                        "type": ev_type,
                        "source": source_key,
                        "locator": (
                            f"{file_name or 'blocked'}:{line_number or 0}"
                            if ev_type == "code"
                            else canonical_url
                        ),
                        "snippet": _text_key(snippet),
                    },
                )
                previous_receipt = prior_evidence.get(evidence_id, {})
                previous_code_receipt_captured = (
                    previous_receipt.get("capture_status")
                    in {"captured", "repository_drift"}
                    and bool(previous_receipt.get("file_sha256"))
                    and bool(previous_receipt.get("line_sha256"))
                )
                if ev_type == "code" and repository_matches:
                    file_hash, line_hash, capture_status = _repo_file_receipt(
                        repo_path, raw_file_name, line_number
                    )
                    if (
                        previous_receipt.get("file_sha256")
                        and file_hash != previous_receipt.get("file_sha256")
                    ):
                        file_hash = previous_receipt.get("file_sha256")
                        line_hash = previous_receipt.get("line_sha256")
                        capture_status = "repository_drift"
                elif ev_type == "code" and previous_code_receipt_captured:
                    file_hash = previous_receipt.get("file_sha256")
                    line_hash = previous_receipt.get("line_sha256")
                    capture_status = "repository_drift"
                elif ev_type == "code":
                    file_hash, line_hash, capture_status = None, None, "unavailable_legacy"
                elif ev_type == "external" and canonical_url:
                    file_hash, line_hash, capture_status = None, None, "declared_excerpt"
                else:
                    file_hash, line_hash, capture_status = None, None, "invalid_locator"
                retained_snippet = snippet
                if ev_type == "code" and capture_status not in {
                    "captured",
                    "repository_drift",
                }:
                    # Do not amplify model-returned text from a path the
                    # orchestrator could not safely validate (for example,
                    # .env, a symlink, FIFO, or out-of-range locator).
                    retained_snippet = None
                receipt = evidence.setdefault(
                    evidence_id,
                    {
                        "id": evidence_id,
                        "type": ev_type,
                        "label": label,
                        "claim_ids": [],
                        "proposer_ids": [],
                        "file": file_name,
                        "line": line_number,
                        "url": canonical_url,
                        "source_key": source_key,
                        "source_domain": _source_domain(canonical_url),
                        "snippet": retained_snippet,
                        "snippet_sha256": (
                            hashlib.sha256(retained_snippet.encode("utf-8")).hexdigest()
                            if retained_snippet is not None
                            else None
                        ),
                        "file_sha256": file_hash,
                        "line_sha256": line_hash,
                        "capture_status": capture_status,
                    },
                )
                if claim_id not in receipt["claim_ids"]:
                    receipt["claim_ids"].append(claim_id)
                if proposer_id not in receipt["proposer_ids"]:
                    receipt["proposer_ids"].append(proposer_id)
                claim = claims.setdefault(
                    claim_id,
                    {
                        "id": claim_id,
                        "text": claim_text,
                        "status": "unreviewed",
                        "critical": False,
                        "proposer_ids": [],
                        "evidence_ids": [],
                        "verification_ids": [],
                        "decision_ids": [],
                        "independent_sources": 0,
                        "verified_reviewer_labs": [],
                    },
                )
                if proposer_id not in claim["proposer_ids"]:
                    claim["proposer_ids"].append(proposer_id)
                if evidence_id not in claim["evidence_ids"]:
                    claim["evidence_ids"].append(evidence_id)
                path = f"plan[{step_index}].evidence[{evidence_index}]"
                claim_location[(proposer_id, path)] = (claim_id, evidence_id)
                step_claims.setdefault((proposer_id, step_index), []).append(claim_id)
                _edge(
                    edges,
                    f"agent:{proposer_id}",
                    evidence_id,
                    "contributes",
                    "recorded",
                )
                _edge(edges, evidence_id, claim_id, "supports", "recorded")

    verification_statuses: dict[str, list[str]] = {}
    verification_lookup: dict[tuple[str, int], dict[str, Any]] = {}
    for reviewer_id in sorted(refiner_payloads):
        payload = refiner_payloads[reviewer_id]
        values = payload.get("verifications") if isinstance(payload.get("verifications"), list) else []
        for index, item in enumerate(values):
            if not isinstance(item, dict):
                continue
            proposer_id = str(item.get("proposer") or "")
            path = str(item.get("claim_index_path") or "")
            linked = claim_location.get((proposer_id, path))
            claim_id, evidence_id = linked if linked else (None, None)
            declared_status = str(item.get("status") or "unverified")
            finding = str(item.get("actual_finding") or "")
            source_url = _portable_verification_source(item.get("source_url"))
            verification_id = _content_id(
                "verification",
                {
                    "reviewer": reviewer_id,
                    "index": index,
                    "proposer": proposer_id,
                    "path": path,
                    "declared_status": declared_status,
                    "finding": finding,
                    "source": source_url,
                },
            )
            source_receipt = _verification_source_receipt(
                repo_path,
                item.get("source_url"),
                repository_matches=repository_matches,
                previous=prior_verifications.get(verification_id),
            )
            status = declared_status
            if (
                declared_status in {"verified", "contradicted"}
                and source_receipt["source_capture_status"]
                not in USABLE_VERIFICATION_SOURCE_STATES
            ):
                status = "unverified"
                contract_warnings.append(
                    f"{reviewer_id} verification {index + 1} declared "
                    f"{declared_status} but its source receipt is "
                    f"{source_receipt['source_capture_status']}; treated as unverified"
                )
            verification = {
                "id": verification_id,
                "index": index,
                "reviewer_id": reviewer_id,
                "reviewer_lab_id": route_lab_id(
                    reviewer_id, agent_models.get(reviewer_id, reviewer_id)
                ),
                "proposer_id": proposer_id,
                "claim_id": claim_id,
                "evidence_id": evidence_id,
                "declared_status": declared_status,
                "status": status,
                "finding": finding,
                **source_receipt,
            }
            verifications.append(verification)
            verification_lookup[(reviewer_id, index)] = verification
            if claim_id and claim_id in claims:
                claims[claim_id]["verification_ids"].append(verification["id"])
                verification_statuses.setdefault(claim_id, []).append(status)
                if status == "verified":
                    lab_id = verification["reviewer_lab_id"]
                    if lab_id not in claims[claim_id]["verified_reviewer_labs"]:
                        claims[claim_id]["verified_reviewer_labs"].append(lab_id)
                _edge(
                    edges,
                    f"agent:{reviewer_id}",
                    claim_id,
                    "contradicts" if status == "contradicted" else "verifies",
                    status,
                )
            else:
                warning = (
                    f"{reviewer_id} verification {index + 1} points to missing "
                    f"{proposer_id}:{path}"
                )
                contract_warnings.append(warning)
                pointer_warnings.append(warning)

    for claim_id, claim in claims.items():
        statuses = verification_statuses.get(claim_id, [])
        if "contradicted" in statuses and "verified" in statuses:
            claim["status"] = "disputed"
        elif "contradicted" in statuses:
            claim["status"] = "contradicted"
        elif "verified" in statuses:
            claim["status"] = "verified"
        elif "unverified" in statuses:
            claim["status"] = "unverified"
        claim["independent_sources"] = len(
            {
                evidence[evidence_id]["source_key"]
                for evidence_id in claim["evidence_ids"]
                if evidence[evidence_id]["capture_status"] in USABLE_RECEIPT_STATES
            }
        )

    def proposer_step_exists(proposer_id: str, step_index: Any) -> bool:
        payload = proposer_payloads.get(proposer_id)
        plan = payload.get("plan") if isinstance(payload, dict) else None
        return (
            type(step_index) is int
            and step_index >= 0
            and isinstance(plan, list)
            and step_index < len(plan)
        )

    refiner_arrays = {
        "verification": "verifications",
        "missing_step": "missing_steps",
        "incorrect_step": "incorrect_steps",
        "disagreement": "disagreements",
    }

    def refiner_reference_exists(
        ref: dict[str, Any], *, require_resolved_claim: bool
    ) -> bool:
        reviewer_id = str(ref.get("agent_id") or "")
        payload = refiner_payloads.get(reviewer_id)
        if not isinstance(payload, dict):
            return False
        kind = str(ref.get("kind") or "")
        index = ref.get("index")
        if kind == "synthesis_recommendation":
            return index is None and bool(str(payload.get(kind) or "").strip())
        field = refiner_arrays.get(kind)
        values = payload.get(field) if field else None
        if type(index) is not int or index < 0 or not isinstance(values, list):
            return False
        if index >= len(values):
            return False
        if kind == "verification" and require_resolved_claim:
            verification = verification_lookup.get((reviewer_id, index))
            return bool(verification and verification.get("claim_id"))
        return True

    critical_pointer_failure = False
    final_step_count = 0
    final_steps_with_evidence = 0
    if isinstance(lineage, dict):
        for step_index, step in enumerate(_as_list(lineage.get("steps"))):
            if not isinstance(step, dict):
                continue
            final_step_count += 1
            decision_id = _content_id(
                "decision",
                {
                    "index": step_index,
                    "title": step.get("title"),
                    "description": step.get("description"),
                    "status": step.get("decision"),
                },
            )
            linked_claims: list[str] = []
            relationships: dict[str, str] = {}
            for ref in _as_list(step.get("proposer_refs")):
                if not isinstance(ref, dict):
                    continue
                proposer_id = str(ref.get("agent_id") or "")
                referenced_index = ref.get("step_index")
                if not proposer_step_exists(proposer_id, referenced_index):
                    critical_pointer_failure = True
                    warning = (
                        f"Final step {step_index + 1} points to missing proposer "
                        f"step {proposer_id}:{referenced_index}"
                    )
                    contract_warnings.append(warning)
                    pointer_warnings.append(warning)
                    continue
                key = (proposer_id, referenced_index)
                for claim_id in step_claims.get(key, []):
                    if claim_id not in linked_claims:
                        linked_claims.append(claim_id)
                    relationships[claim_id] = str(ref.get("relationship") or "adopted")
            for ref in _as_list(step.get("refiner_refs")):
                if not isinstance(ref, dict):
                    continue
                reviewer_id = str(ref.get("agent_id") or "")
                kind = str(ref.get("kind") or "")
                index = ref.get("index")
                if kind == "verification" and refiner_reference_exists(
                    ref, require_resolved_claim=True
                ):
                    verification = verification_lookup[(reviewer_id, index)]
                    claim_id = str(verification["claim_id"])
                    if claim_id not in linked_claims:
                        linked_claims.append(claim_id)
                    relationships.setdefault(claim_id, "reviewed")
                elif not refiner_reference_exists(
                    ref, require_resolved_claim=True
                ):
                    critical_pointer_failure = True
                    warning = (
                        f"Final step {step_index + 1} points to missing refiner "
                        f"finding {reviewer_id}:{kind}:{index}"
                    )
                    contract_warnings.append(warning)
                    pointer_warnings.append(warning)
            if not linked_claims:
                synthetic_text = f"{step.get('title') or 'Final decision'}: {step.get('description') or ''}".strip()
                claim_id = _content_id("claim", {"text": _text_key(synthetic_text)})
                claims.setdefault(
                    claim_id,
                    {
                        "id": claim_id,
                        "text": synthetic_text,
                        "status": "unreviewed",
                        "critical": True,
                        "proposer_ids": [],
                        "evidence_ids": [],
                        "verification_ids": [],
                        "decision_ids": [],
                        "independent_sources": 0,
                        "verified_reviewer_labs": [],
                    },
                )
                linked_claims.append(claim_id)
                relationships[claim_id] = "new"
            linked_evidence: list[str] = []
            for claim_id in linked_claims:
                claim = claims[claim_id]
                claim["critical"] = True
                if decision_id not in claim["decision_ids"]:
                    claim["decision_ids"].append(decision_id)
                for evidence_id in claim["evidence_ids"]:
                    if evidence_id not in linked_evidence:
                        linked_evidence.append(evidence_id)
                _edge(edges, claim_id, decision_id, relationships.get(claim_id, "adopted"), str(step.get("decision") or "accepted"))
            if linked_evidence:
                final_steps_with_evidence += 1
            decisions.append(
                {
                    "id": decision_id,
                    "title": str(step.get("title") or f"Step {step_index + 1}"),
                    "description": str(step.get("description") or ""),
                    "status": str(step.get("decision") or "accepted"),
                    "claim_ids": linked_claims,
                    "evidence_ids": linked_evidence,
                    "files_touched": [str(value) for value in _as_list(step.get("files_touched"))],
                }
            )

        for rejected_index, rejected in enumerate(_as_list(lineage.get("rejected_inputs"))):
            if not isinstance(rejected, dict):
                continue
            proposer_id = str(rejected.get("proposer") or "")
            raw_step_index = rejected.get("step_index")
            step_index = raw_step_index if isinstance(raw_step_index, int) and raw_step_index >= 0 else 0
            rejected_pointer_valid = proposer_step_exists(proposer_id, raw_step_index)
            if not rejected_pointer_valid:
                warning = (
                    f"Rejected input {rejected_index + 1} points to missing proposer "
                    f"step {proposer_id}:{raw_step_index}"
                )
                contract_warnings.append(warning)
                pointer_warnings.append(warning)
            for ref in _as_list(rejected.get("refiner_refs")):
                if isinstance(ref, dict) and not refiner_reference_exists(
                    ref, require_resolved_claim=False
                ):
                    warning = (
                        f"Rejected input {rejected_index + 1} points to missing "
                        f"refiner finding {ref.get('agent_id')}:{ref.get('kind')}:{ref.get('index')}"
                    )
                    contract_warnings.append(warning)
                    pointer_warnings.append(warning)
            claim_ids = list(
                dict.fromkeys(
                    step_claims.get((proposer_id, step_index), [])
                    if rejected_pointer_valid
                    else []
                )
            )
            decision_id = _content_id(
                "rejected",
                {"proposer": proposer_id, "step": step_index, "reason": rejected.get("reason")},
            )
            for claim_id in claim_ids:
                if decision_id not in claims[claim_id]["decision_ids"]:
                    claims[claim_id]["decision_ids"].append(decision_id)
                _edge(edges, claim_id, decision_id, "rejected", "rejected")
            decisions.append(
                {
                    "id": decision_id,
                    "title": f"Rejected {proposer_id} step {step_index + 1}",
                    "description": str(rejected.get("reason") or "Rejected during synthesis."),
                    "status": "rejected",
                    "claim_ids": claim_ids,
                    "evidence_ids": list(
                        dict.fromkeys(
                            evidence_id
                            for claim_id in claim_ids
                            for evidence_id in claims[claim_id]["evidence_ids"]
                        )
                    ),
                    "files_touched": [],
                }
            )

    completed_aggregators = [
        agent
        for agent in agents
        if agent["role"] == "aggregator" and agent["status"] == "completed"
    ]
    for aggregator in completed_aggregators:
        for decision in decisions:
            if decision["status"] != "rejected":
                _edge(
                    edges,
                    f"agent:{aggregator['id']}",
                    decision["id"],
                    "synthesizes",
                    "completed",
                )

    critical = [claim for claim in claims.values() if claim["critical"]]
    supported = [
        claim
        for claim in critical
        if any(
            evidence[evidence_id]["capture_status"] in USABLE_RECEIPT_STATES
            for evidence_id in claim["evidence_ids"]
        )
    ]
    reviewed = [claim for claim in critical if claim["verification_ids"]]
    verified = [claim for claim in critical if claim["status"] == "verified"]
    independently_verified = [
        claim
        for claim in verified
        if len(set(claim["verified_reviewer_labs"])) >= 2
    ]
    contradicted = [
        claim for claim in critical if claim["status"] in {"contradicted", "disputed"}
    ]
    single_source = [claim for claim in critical if claim["independent_sources"] < 2]
    stale_code_claims = [
        claim
        for claim in critical
        if any(
            evidence[evidence_id]["type"] == "code"
            and evidence[evidence_id]["capture_status"] == "repository_drift"
            for evidence_id in claim["evidence_ids"]
        )
    ]
    unknown_external_claims = [
        claim
        for claim in critical
        if any(
            evidence[evidence_id]["type"] == "external"
            and evidence[evidence_id]["capture_status"] == "declared_excerpt"
            for evidence_id in claim["evidence_ids"]
        )
        and len(set(claim["verified_reviewer_labs"])) < 2
    ]
    support_coverage = len(supported) / len(critical) if critical else 0.0
    review_coverage = len(reviewed) / len(critical) if critical else 0.0
    verification_coverage = len(verified) / len(critical) if critical else 0.0
    independent_verified_coverage = (
        len(independently_verified) / len(critical) if critical else 0.0
    )
    step_evidence_coverage = (
        final_steps_with_evidence / final_step_count if final_step_count else 0.0
    )
    critical_receipts = [
        evidence_id
        for claim in critical
        for evidence_id in claim["evidence_ids"]
        if evidence[evidence_id]["capture_status"] in USABLE_RECEIPT_STATES
    ]
    critical_source_keys = {
        evidence[evidence_id]["source_key"] for evidence_id in critical_receipts
    }
    all_usable_source_keys = {
        item["source_key"]
        for item in evidence.values()
        if item["capture_status"] in USABLE_RECEIPT_STATES
    }
    quality_independent_sources = len(
        critical_source_keys if critical else all_usable_source_keys
    )
    source_counts: dict[str, int] = {}
    for evidence_id in critical_receipts:
        source_key = evidence[evidence_id]["source_key"]
        source_counts[source_key] = source_counts.get(source_key, 0) + 1
    source_concentration = (
        max(source_counts.values()) / len(critical_receipts)
        if critical_receipts
        else 0.0
    )

    gates: list[dict[str, str]] = []

    def gate(gate_id: str, label: str, status: str, detail: str) -> None:
        gates.append({"id": gate_id, "label": label, "status": status, "detail": detail})

    if not critical:
        gate("receipt-integrity", "Evidence receipts", "pending", "No final claims exist yet.")
        gate("review-coverage", "Review coverage", "pending", "No final claims exist yet.")
        gate("contradiction", "Contradictions", "pending", "No final claims exist yet.")
        gate("reviewer-independence", "Independent verification", "pending", "No final claims exist yet.")
        gate("source-diversity", "Source diversity", "pending", "No final claims exist yet.")
        gate("lineage-coverage", "Decision lineage", "pending", "No final steps exist yet.")
        gate("pointer-integrity", "Pointer integrity", "pending", "No final decision pointers exist yet.")
        gate("freshness", "Evidence freshness", "pending", "No final evidence receipts exist yet.")
    else:
        gate(
            "receipt-integrity",
            "Evidence receipts",
            "pass" if support_coverage == 1 else "fail",
            f"{len(supported)} of {len(critical)} critical claims have a receipt.",
        )
        gate(
            "review-coverage",
            "Review coverage",
            "pass" if review_coverage == 1 else ("warn" if review_coverage >= 0.5 else "fail"),
            f"{len(reviewed)} of {len(critical)} critical claims have a resolved review.",
        )
        gate(
            "contradiction",
            "Contradictions",
            "fail" if contradicted else "pass",
            f"{len(contradicted)} critical claims are contradicted or disputed.",
        )
        gate(
            "reviewer-independence",
            "Independent verification",
            "pass" if independent_verified_coverage == 1 else "warn",
            f"{len(independently_verified)} of {len(critical)} critical claims were verified by two model labs.",
        )
        diversity_status = "warn" if single_source else "pass"
        if len(critical) >= 2 and source_concentration > 0.60:
            diversity_status = "warn"
        gate(
            "source-diversity",
            "Source diversity",
            diversity_status,
            (
                f"{len(single_source)} critical claims rely on fewer than two independent sources; "
                f"the most-used source supplies {source_concentration:.0%} of critical receipts."
                if single_source
                else f"The most-used source supplies {source_concentration:.0%} of critical receipts."
            ),
        )
        gate(
            "lineage-coverage",
            "Decision lineage",
            "pass" if step_evidence_coverage == 1 else ("warn" if step_evidence_coverage > 0 else "fail"),
            f"{final_steps_with_evidence} of {final_step_count} final steps connect to evidence.",
        )
        gate(
            "pointer-integrity",
            "Pointer integrity",
            "fail" if critical_pointer_failure else ("warn" if pointer_warnings else "pass"),
            "A final decision uses an unresolved verification pointer."
            if critical_pointer_failure
            else (f"{len(pointer_warnings)} non-final verification pointers are unresolved." if pointer_warnings else "Every retained pointer resolved."),
        )
        freshness_status = "fail" if stale_code_claims else ("warn" if unknown_external_claims else "pass")
        gate(
            "freshness",
            "Evidence freshness",
            freshness_status,
            f"{len(stale_code_claims)} critical code claims changed after capture."
            if stale_code_claims
            else (
                f"{len(unknown_external_claims)} critical external claims have declared excerpts without two-lab verification."
                if unknown_external_claims
                else "Critical code receipts still match; external claims have independent review."
            ),
        )

    failed_gate = any(item["status"] == "fail" for item in gates)
    warning_gate = any(item["status"] in {"warn", "pending"} for item in gates)
    rejected_only = bool(decisions) and not any(
        item["status"] != "rejected" for item in decisions
    )
    if (
        not critical
        or rejected_only
        or manifest.get("layer2_mode") in {"skipped", "degraded_non_broadcast"}
        or failed_gate
        or verification_coverage < 0.5
    ):
        ceiling = "low" if isinstance(lineage, dict) else "pending"
    elif warning_gate or verification_coverage < 1 or independent_verified_coverage < 1:
        ceiling = "medium"
    else:
        ceiling = "high"
    stated = None
    if isinstance(lineage, dict) and isinstance(lineage.get("confidence"), dict):
        value = lineage["confidence"].get("level")
        stated = str(value) if value in CONFIDENCE_RANK else None
    effective = _effective_confidence(stated, ceiling)
    warnings = list(dict.fromkeys(contract_warnings))
    if critical:
        unsupported_count = len(critical) - len(supported)
        unverified_count = len(critical) - len(verified) - len(contradicted)
        if unsupported_count:
            warnings.append(f"{unsupported_count} critical claim(s) have no evidence receipt")
        if unverified_count:
            warnings.append(f"{unverified_count} critical claim(s) remain independently unverified")
        if contradicted:
            warnings.append(f"{len(contradicted)} critical claim(s) are contradicted or disputed")
        if single_source:
            warnings.append(f"{len(single_source)} critical claim(s) rely on fewer than two independent sources")
    else:
        warnings.append("The final decision is not available yet; evidence gates are pending")

    external_domains = sorted(
        {
            receipt["source_domain"]
            for receipt in evidence.values()
            if receipt["source_domain"]
        }
    )
    first_spec_line = str(scout.get("frozen_spec") or manifest.get("session_id") or "MoA-X run").strip().splitlines()[0]
    title = str((lineage or {}).get("title") or first_spec_line or "MoA-X run")
    summary = str(
        (lineage or {}).get("summary")
        or ({"setup": "The ensemble is configured and waiting to start.", "proposals": "Proposal evidence is available; review is pending.", "review": "Evidence has been reviewed; the final decision is pending.", "complete": "The final decision is linked to its supporting evidence."}[_stage(manifest, lineage)])
    )
    return {
        "version": 1,
        "session_id": str(manifest.get("session_id") or session_dir.name),
        "input_digest": input_digest,
        "stage": _stage(manifest, lineage),
        "generated_at": _generated_at(manifest),
        "title": title,
        "summary": summary,
        "repository": repository,
        "agents": agents,
        "evidence": sorted(evidence.values(), key=lambda item: item["id"]),
        "claims": sorted(claims.values(), key=lambda item: (not item["critical"], item["id"])),
        "verifications": sorted(verifications, key=lambda item: item["id"]),
        "decisions": decisions,
        "edges": sorted(edges, key=lambda item: item["id"]),
        "quality": {
            "level": {"high": "strong", "medium": "guarded", "low": "weak"}.get(effective, "pending"),
            "evidence_ceiling": ceiling,
            "stated_confidence": stated,
            "effective_confidence": effective,
            "total_claims": len(claims),
            "critical_claims": len(critical),
            "supported_critical_claims": len(supported),
            "verified_critical_claims": len(verified),
            "contradicted_critical_claims": len(contradicted),
            "support_coverage": round(support_coverage, 4),
            "review_coverage": round(review_coverage, 4),
            "verification_coverage": round(verification_coverage, 4),
            "independent_verified_coverage": round(independent_verified_coverage, 4),
            "step_evidence_coverage": round(step_evidence_coverage, 4),
            "source_concentration": round(source_concentration, 4),
            "independent_sources": quality_independent_sources,
            "external_domains": external_domains,
            "gates": gates,
            "warnings": warnings,
        },
        "warnings": warnings,
    }


def write_decision_map(
    session_dir: Path,
    *,
    validator: Optional[Callable[[dict[str, Any]], list[str]]] = None,
    capture_repository: bool = False,
) -> Path:
    """Atomically refresh ``decision-map.json`` from retained artifacts.

    ``capture_repository`` is an explicit live-checkpoint signal. Report and
    legacy rebuilds default to conservative unavailable receipts rather than
    reconstructing current repository contents and backdating that capture to
    the historical run. Once a live receipt exists, later refreshes preserve
    it so drift checks remain anchored to the original checkpoint.
    """
    output = session_dir / DECISION_MAP_FILENAME
    temporary = session_dir / f".{DECISION_MAP_FILENAME}.tmp"
    prior = _read_json(output) or {}
    scout = _read_json(session_dir / "scout-brief.json") or {}
    manifest = _manifest_for_session(session_dir)
    prior_repository = prior.get("repository")
    captured_now = False
    if (
        isinstance(prior_repository, dict)
        and prior_repository.get("capture_status") == "live_run"
    ):
        repository = dict(prior_repository)
    elif capture_repository:
        repository = capture_repository_state(
            Path(str(scout.get("repo_path"))).expanduser().resolve()
            if scout.get("repo_path")
            else None
        )
        captured_now = repository.get("capture_status") == "live_run"
    else:
        repository = {
            "commit": None,
            "tree": None,
            "dirty": None,
            "status_sha256": None,
            "diff_sha256": None,
            "captured_at": None,
            "capture_status": "unavailable_legacy",
        }
    if captured_now:
        captured_timestamp = manifest.get("finished_at")
        if not isinstance(captured_timestamp, (int, float)) or captured_timestamp <= 0:
            captured_timestamp = manifest.get("started_at")
        repository["captured_at"] = (
            datetime.fromtimestamp(float(captured_timestamp), timezone.utc).isoformat()
            if isinstance(captured_timestamp, (int, float)) and captured_timestamp > 0
            else None
        )
    payload = build_decision_map(
        session_dir, repository_state=repository, prior_map=prior
    )
    if validator is not None:
        errors = validator(payload)
        if errors:
            raise ValueError(
                "decision-map schema validation failed: " + "; ".join(errors[:20])
            )
    temporary.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    temporary.replace(output)
    return output


def load_or_build_decision_map(session_dir: Path) -> dict[str, Any]:
    """Always digest retained inputs so interactive/legacy sessions stay fresh."""
    prior = _read_json(session_dir / DECISION_MAP_FILENAME) or {}
    rebuilt = build_decision_map(session_dir, prior_map=prior)
    if (
        prior.get("version") == 1
        and prior.get("input_digest") == rebuilt.get("input_digest")
        and _canonical_json(prior) == _canonical_json(rebuilt)
    ):
        return prior
    return rebuilt
