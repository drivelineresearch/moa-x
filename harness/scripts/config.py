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

Harnesses supported by the built-in adapters are {codex, claude, opencode,
cursor}. Named providers (built-in codex/sonnet/glm/kimi plus user-defined
entries in harness/config.yaml) map onto those harnesses.

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
    "claude": "claude",
}


@dataclass(frozen=True)
class ResolvedProvider:
    """A provider resolved from a layer config entry to an invocation record.

    `timeout` is per-provider in seconds. None means "use the harness-level
    default" (set by --codex-timeout / --sonnet-timeout CLI flags or their
    MOA_*_TIMEOUT env equivalents). User-named providers
    can set their own timeout via `providers.<name>.timeout` in
    harness/config.yaml or MOA_<NAME>_TIMEOUT env var.
    """
    name: str                         # user-facing label, used as agent_id in payloads
    harness: str                      # which adapter handles the call: codex, claude, opencode, cursor
    model: str                        # model id passed to the harness
    timeout: Optional[int] = None     # per-provider timeout in seconds; None → harness default


# Built-in named providers. Existing configs that reference codex/sonnet/glm/kimi
# resolve through this table for back-compat. User-defined providers in
# harness/config.yaml under `providers:` are layered on top in resolve_provider.
# Built-ins always carry timeout=None so the existing CLI flag / harness-level
# env path (MOA_CODEX_TIMEOUT etc.) continues to apply.
BUILTIN_PROVIDERS: dict[str, ResolvedProvider] = {
    "codex":    ResolvedProvider(name="codex",    harness="codex",    model="gpt-5.4"),
    "sonnet":   ResolvedProvider(name="sonnet",   harness="claude",   model="claude-sonnet-4-6"),
    "glm":      ResolvedProvider(name="glm",      harness="opencode", model="opencode-go/glm-5.2"),
    "kimi":     ResolvedProvider(name="kimi",     harness="opencode", model="opencode-go/kimi-k2.7-code"),
    "composer": ResolvedProvider(name="composer", harness="cursor",   model="composer-2.5"),
}


def resolve_provider(name: str, *, user_providers: dict[str, dict]) -> ResolvedProvider:
    """Resolve a provider name to a ResolvedProvider record.

    Lookup order:
      1. user_providers (from harness/config.yaml `providers:` block)
      2. BUILTIN_PROVIDERS (codex, sonnet, glm, kimi)

    Then env-var overrides apply per-field:
      - MOA_<NAME>_MODEL overrides .model
      - MOA_<NAME>_TIMEOUT overrides .timeout

    Built-in providers always resolve with timeout=None so the existing
    --codex-timeout / --sonnet-timeout CLI flag path continues to apply at
    the harness level. Set MOA_<NAME>_TIMEOUT or a
    YAML `timeout:` field to override per-provider.

    Raises ValueError if the name resolves nowhere or if a timeout value
    is malformed.
    """
    if name in user_providers:
        spec = user_providers[name]
        if not isinstance(spec, dict) or "harness" not in spec or "model" not in spec:
            raise ValueError(
                f"user provider {name!r} must be a mapping with 'harness' and 'model' keys; "
                f"got {spec!r}"
            )
        yaml_timeout = spec.get("timeout")
        if yaml_timeout is not None and not isinstance(yaml_timeout, int):
            raise ValueError(
                f"user provider {name!r} `timeout:` must be an integer (seconds); "
                f"got {yaml_timeout!r}"
            )
        rp = ResolvedProvider(
            name=name,
            harness=spec["harness"],
            model=spec["model"],
            timeout=yaml_timeout,
        )
    elif name in BUILTIN_PROVIDERS:
        rp = BUILTIN_PROVIDERS[name]
    else:
        valid = sorted(set(BUILTIN_PROVIDERS) | set(user_providers))
        if name == "gemini":
            raise ValueError(
                "provider 'gemini' was removed in v0.3.0 (see docs/config.md "
                "\"Migrating from gemini\"). Route a Gemini model through the "
                "cursor harness instead — e.g. under `providers:` in "
                "harness/config.yaml:\n"
                "    cursor-gemini: {harness: cursor, model: gemini-3.1-pro}\n"
                f"then add 'cursor-gemini' to your layers. Valid names now: {valid}"
            )
        raise ValueError(
            f"unknown provider name {name!r}; valid names: {valid}"
        )

    env_prefix = f"MOA_{name.upper().replace('-', '_')}"

    override_model = os.environ.get(f"{env_prefix}_MODEL")
    if override_model:
        rp = ResolvedProvider(name=rp.name, harness=rp.harness, model=override_model, timeout=rp.timeout)

    override_timeout_raw = os.environ.get(f"{env_prefix}_TIMEOUT")
    if override_timeout_raw:
        try:
            override_timeout = int(override_timeout_raw)
        except ValueError as e:
            raise ValueError(
                f"{env_prefix}_TIMEOUT must be an integer (seconds); got {override_timeout_raw!r}"
            ) from e
        rp = ResolvedProvider(name=rp.name, harness=rp.harness, model=rp.model, timeout=override_timeout)

    return rp


def resolve_layer(
    names: list[str],
    *,
    user_providers: dict[str, dict],
) -> list[ResolvedProvider]:
    """Resolve a list of provider names to ResolvedProvider records.

    Order is preserved. Duplicates are kept (caller handles self-moa-style
    suffixing). Raises ValueError on the first unknown name with the list
    of valid options.
    """
    return [resolve_provider(name, user_providers=user_providers) for name in names]


def resolve_bin(provider: str) -> str:
    """Return the binary name/path for a provider.

    Honors MOA_<PROVIDER>_BIN (e.g. MOA_CODEX_BIN, MOA_CLAUDE_BIN) with a
    default of the bare binary name on PATH. Only covers harnesses whose
    binary the orchestrator resolves centrally (codex, claude); the cursor
    and opencode adapters resolve their own bin via MOA_CURSOR_BIN /
    MOA_OPENCODE_BIN.
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
            # Providers use MOA_<NAME>_MODEL naming, except the `claude`
            # provider is exposed as the SONNET role (it's the claude binary
            # in sonnet mode). We expose MOA_SONNET_MODEL for that role.
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


def _user_providers_from_yaml(cfg: dict[str, Any]) -> dict[str, dict]:
    """Extract the `providers:` block from a parsed YAML config.

    Returns a name → spec dict where spec is a mapping with at least
    `harness` and `model` keys. Validation of harness/model values
    happens at resolve_provider() time, not here.
    """
    raw = cfg.get("providers") or {}
    if not isinstance(raw, dict):
        raise ValueError(
            "harness/config.yaml: top-level `providers:` must be a mapping"
        )
    out: dict[str, dict] = {}
    for name, spec in raw.items():
        if not isinstance(spec, dict):
            raise ValueError(
                f"harness/config.yaml: provider {name!r} must be a mapping with "
                f"`harness:` and `model:` keys; got {type(spec).__name__}"
            )
        out[str(name)] = dict(spec)
    return out


# Harnesses a provider spec may target. Used to validate MOA_PROVIDER_* env
# definitions loudly at parse time instead of failing deep in dispatch.
_KNOWN_HARNESSES = frozenset({"codex", "claude", "opencode", "cursor"})


def _providers_from_env() -> dict[str, dict]:
    """Parse `MOA_PROVIDER_<NAME>=<harness>:<model>` env vars into provider specs.

    <NAME> is lowercased with `_` → `-`, so `MOA_PROVIDER_GLM_FW` defines the
    provider `glm-fw`. This is a shell/.env shorthand for the YAML `providers:`
    block, so a full roster swap needs no YAML file. YAML definitions win on a
    name conflict (they can also set timeout/effort/bin); the MOA_<NAME>_MODEL /
    MOA_<NAME>_TIMEOUT field overrides still apply on top in resolve_provider.

    Raises ValueError on a malformed value (no colon, unknown harness, empty
    model) — a broken provider definition should fail loudly, not silently.
    """
    prefix = "MOA_PROVIDER_"
    out: dict[str, dict] = {}
    for key, value in os.environ.items():
        if not key.startswith(prefix):
            continue
        name = key[len(prefix):].lower().replace("_", "-")
        if not name:
            continue
        if ":" not in value:
            raise ValueError(
                f"{key} must be '<harness>:<model>' "
                f"(e.g. opencode:zhipuai/glm-5.2); got {value!r}"
            )
        harness, _, model = value.partition(":")
        harness, model = harness.strip(), model.strip()
        if harness not in _KNOWN_HARNESSES:
            raise ValueError(
                f"{key}: unknown harness {harness!r}; "
                f"must be one of {sorted(_KNOWN_HARNESSES)}"
            )
        if not model:
            raise ValueError(f"{key}: empty model in {value!r}")
        out[name] = {"harness": harness, "model": model}
    return out


@dataclass(frozen=True)
class LoadedConfig:
    """Fully resolved config ready for run_moa.py to dispatch from."""
    proposers: list[ResolvedProvider]
    refiners: list[ResolvedProvider]
    skip_refinement: bool


# Default layer assignments when no YAML / env override is set.
_DEFAULT_PROPOSERS = ["codex", "glm", "sonnet"]
_DEFAULT_REFINERS = ["codex", "kimi"]


def load_resolved_config(
    *,
    config_path: Optional[Path] = None,
    dotenv_path: Optional[Path] = None,
) -> LoadedConfig:
    """Load YAML + .env and resolve all named providers in the layer assignments.

    Caller is responsible for having previously called apply_config_to_env()
    so MOA_PROPOSERS / MOA_REFINERS env vars (if set) are visible. The
    `dotenv_path` argument is currently unused by this function (env state
    has already been applied) but is reserved for future use; pass it for
    parity with apply_config_to_env.
    """
    cfg_path = config_path or DEFAULT_CONFIG_PATH
    cfg = _load_yaml(cfg_path)
    # env-defined providers first, YAML layered on top (YAML wins name conflicts).
    user_providers = {**_providers_from_env(), **_user_providers_from_yaml(cfg)}

    proposer_names = _resolve_layer_names(
        env_key="MOA_PROPOSERS",
        yaml_value=(cfg.get("layers") or {}).get("proposers"),
        default=_DEFAULT_PROPOSERS,
    )
    refiner_names = _resolve_layer_names(
        env_key="MOA_REFINERS",
        yaml_value=(cfg.get("layers") or {}).get("refiners"),
        default=_DEFAULT_REFINERS,
    )

    proposers = resolve_layer(proposer_names, user_providers=user_providers)
    refiners = resolve_layer(refiner_names, user_providers=user_providers)

    skip_refinement = bool(os.environ.get("MOA_SKIP_LAYER2")) or bool(
        (cfg.get("layers") or {}).get("skip_refinement")
    )

    return LoadedConfig(
        proposers=proposers,
        refiners=refiners,
        skip_refinement=skip_refinement,
    )


def _resolve_layer_names(
    *, env_key: str, yaml_value: Any, default: list[str]
) -> list[str]:
    """Pick layer names from env > yaml > default."""
    env_val = os.environ.get(env_key)
    if env_val:
        return [s.strip() for s in env_val.split(",") if s.strip()]
    if isinstance(yaml_value, list):
        return [str(s) for s in yaml_value]
    return list(default)


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
