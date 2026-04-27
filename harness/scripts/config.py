"""Lightweight config loader for the MoA-X harness.

Resolves user-customizable knobs (models, efforts, timeouts, binary
paths, layer/agent selection) from three sources. Precedence (highest
first):

    1. Shell / process environment variables (MOA_* namespace)
    2. .env file at the repo root
    3. harness/config.yaml (if present, next to this file's parent dir)

Then falls back to built-in defaults inside run_moa.py / the adapters.

CLI flags passed to run_moa.py still override everything — they are
parsed after this module populates os.environ.

Supported providers are hard-coded to {codex, claude-code, gemini}
by design. Adding providers is a separate design discussion, not a
config edit.

Typical usage:

    # From run_moa.py, before argparse:
    from config import apply_config_to_env
    apply_config_to_env()

    # In an adapter:
    from config import resolve_bin
    bin_path = resolve_bin("codex")   # → $MOA_CODEX_BIN or "codex"

    # Resolve a named provider to a triple:
    from config import resolve_provider
    rp = resolve_provider("codex", user_providers={})
    # rp.name == "codex", rp.harness == "codex", rp.model == "gpt-5.4"

The config.yaml schema is documented in harness/config.example.yaml.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

try:
    import yaml  # type: ignore[import-untyped]
    _HAVE_YAML = True
except ImportError:
    _HAVE_YAML = False


REPO_ROOT = Path(__file__).resolve().parent.parent.parent
HARNESS_DIR = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = HARNESS_DIR / "config.yaml"
DEFAULT_DOTENV_PATH = REPO_ROOT / ".env"


# --- binary names ----------------------------------------------------------

_DEFAULT_BINS = {
    "codex": "codex",
    "gemini": "gemini",
    "claude": "claude",
}


@dataclass(frozen=True)
class ResolvedProvider:
    """A provider resolved from a layer config entry to a concrete invocation triple."""
    name: str         # user-facing label, used as agent_id in payloads
    harness: str      # which adapter handles the call: codex, gemini, claude, cursor
    model: str        # model id passed to the harness


# Built-in named providers. Existing configs that reference codex/gemini/sonnet
# resolve through this table for back-compat. User-defined providers in
# harness/config.yaml under `providers:` are layered on top in resolve_provider.
BUILTIN_PROVIDERS: dict[str, ResolvedProvider] = {
    "codex":  ResolvedProvider(name="codex",  harness="codex",  model="gpt-5.4"),
    "gemini": ResolvedProvider(name="gemini", harness="gemini", model="gemini-2.5-pro"),
    "sonnet": ResolvedProvider(name="sonnet", harness="claude", model="claude-sonnet-4-6"),
}


def resolve_provider(name: str, *, user_providers: dict[str, dict]) -> ResolvedProvider:
    """Resolve a provider name to a ResolvedProvider triple.

    Lookup order:
      1. user_providers (from harness/config.yaml `providers:` block)
      2. BUILTIN_PROVIDERS (codex, gemini, sonnet)

    Then env-var overrides MOA_<NAME>_MODEL apply.

    Raises ValueError if the name resolves nowhere.
    """
    if name in user_providers:
        spec = user_providers[name]
        if not isinstance(spec, dict) or "harness" not in spec or "model" not in spec:
            raise ValueError(
                f"user provider {name!r} must be a mapping with 'harness' and 'model' keys; "
                f"got {spec!r}"
            )
        rp = ResolvedProvider(name=name, harness=spec["harness"], model=spec["model"])
    elif name in BUILTIN_PROVIDERS:
        rp = BUILTIN_PROVIDERS[name]
    else:
        valid = sorted(set(BUILTIN_PROVIDERS) | set(user_providers))
        raise ValueError(
            f"unknown provider name {name!r}; valid names: {valid}"
        )

    # MOA_<NAME>_MODEL env override. Name is uppercased with - → _.
    env_key = f"MOA_{name.upper().replace('-', '_')}_MODEL"
    override_model = os.environ.get(env_key)
    if override_model:
        rp = ResolvedProvider(name=rp.name, harness=rp.harness, model=override_model)

    return rp


def resolve_bin(provider: str) -> str:
    """Return the binary name/path for a provider.

    Honors MOA_<PROVIDER>_BIN (e.g. MOA_CODEX_BIN, MOA_CLAUDE_BIN,
    MOA_GEMINI_BIN) with a default of the bare binary name on PATH.
    """
    provider = provider.lower()
    if provider not in _DEFAULT_BINS:
        raise ValueError(
            f"unsupported provider {provider!r}; "
            f"must be one of {sorted(_DEFAULT_BINS)}"
        )
    env_key = f"MOA_{provider.upper()}_BIN"
    return os.environ.get(env_key) or _DEFAULT_BINS[provider]


# --- .env and config.yaml loading -----------------------------------------

def _load_dotenv(path: Path) -> dict[str, str]:
    """Parse a minimal .env file. Supports KEY=VALUE and # comments.

    Quoted values (single or double) are unwrapped. Missing file -> {}.
    This is intentionally tiny — no shell expansion, no export keyword.
    Users who want the full thing can `source .env` before invoking.
    """
    if not path.exists():
        return {}
    out: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        if key:
            out[key] = value
    return out


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    if not _HAVE_YAML:
        raise RuntimeError(
            f"{path} exists but PyYAML is not installed. "
            "Install with `pip install pyyaml` or remove config.yaml "
            "and use env vars only."
        )
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ValueError(f"{path} must be a YAML mapping at the top level")
    return raw


def _yaml_to_env(cfg: dict[str, Any]) -> dict[str, str]:
    """Flatten the nested YAML schema into MOA_* env vars.

    Only keys documented in config.example.yaml are honored. Unknown
    keys are silently ignored so the loader doesn't surprise users who
    sketch extra notes into the file.
    """
    env: dict[str, str] = {}

    providers = cfg.get("providers") or {}
    for name, spec in providers.items():
        if not isinstance(spec, dict):
            continue
        key_upper = name.upper()
        if "bin" in spec:
            env[f"MOA_{key_upper}_BIN"] = str(spec["bin"])
        if "model" in spec:
            # codex + gemini + sonnet all use the same MOA_*_MODEL naming,
            # except sonnet is really the `claude` binary in sonnet mode.
            # We expose MOA_SONNET_MODEL for that role.
            role = "SONNET" if name.lower() == "claude" else key_upper
            env[f"MOA_{role}_MODEL"] = str(spec["model"])
        if "effort" in spec:
            env[f"MOA_{key_upper}_EFFORT"] = str(spec["effort"])
        if "timeout" in spec:
            role = "SONNET" if name.lower() == "claude" else key_upper
            env[f"MOA_{role}_TIMEOUT"] = str(spec["timeout"])

    layers = cfg.get("layers") or {}
    if "proposers" in layers and isinstance(layers["proposers"], list):
        env["MOA_PROPOSERS"] = ",".join(str(x) for x in layers["proposers"])
    if "refiners" in layers and isinstance(layers["refiners"], list):
        env["MOA_REFINERS"] = ",".join(str(x) for x in layers["refiners"])
    if layers.get("skip_refinement") is True:
        env["MOA_SKIP_LAYER2"] = "1"

    return env


def apply_config_to_env(
    *,
    config_path: Optional[Path] = None,
    dotenv_path: Optional[Path] = None,
    overwrite: bool = False,
) -> dict[str, str]:
    """Populate os.environ from .env and config.yaml.

    Precedence (highest wins):
        1. Existing os.environ entries (shell export, systemd, etc.)
        2. .env file values
        3. config.yaml derived values

    Pass overwrite=True only from tests. Returns the dict of keys that
    were newly set (for logging).
    """
    cfg_path = config_path or DEFAULT_CONFIG_PATH
    env_path = dotenv_path or DEFAULT_DOTENV_PATH

    merged: dict[str, str] = {}
    # Lowest priority first so we can .update() to layer higher ones on top.
    merged.update(_yaml_to_env(_load_yaml(cfg_path)))
    merged.update(_load_dotenv(env_path))

    newly_set: dict[str, str] = {}
    for k, v in merged.items():
        if overwrite or k not in os.environ:
            os.environ[k] = v
            newly_set[k] = v
    return newly_set
