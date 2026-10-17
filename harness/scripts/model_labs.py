"""Canonical model-lab identity metadata.

The execution harness is transport, including the native Muse and DeepSeek CLIs.
It must never determine a model's visual identity or diversity attribution.
Every UI and report consumer should use ``lab_id`` from this module instead.
"""
from __future__ import annotations

from typing import Any
try:
    from .muse_models import MUSE_ROUTES
except ImportError:  # Standalone runner and installed skill layout.
    from muse_models import MUSE_ROUTES


MODEL_LABS: dict[str, dict[str, str]] = {
    "openai": {
        "label": "OpenAI",
        "accent": "teal",
        "avatar": "lab-openai-avatar.webp",
        "pixel": "lab-openai-pixel.webp",
    },
    "google": {
        "label": "Google",
        "accent": "blue",
        "avatar": "lab-google-avatar.webp",
        "pixel": "lab-google-pixel.webp",
    },
    "anthropic": {
        "label": "Anthropic",
        "accent": "amber",
        "avatar": "lab-anthropic-avatar.webp",
        "pixel": "lab-anthropic-pixel.webp",
    },
    "xai": {
        "label": "xAI",
        "accent": "violet",
        "avatar": "lab-xai-avatar.webp",
        "pixel": "lab-xai-pixel.webp",
    },
    "moonshot": {
        "label": "Moonshot",
        "accent": "indigo",
        "avatar": "lab-moonshot-avatar.webp",
        "pixel": "lab-moonshot-pixel.webp",
    },
    "alibaba": {
        "label": "Alibaba",
        "accent": "cyan",
        "avatar": "lab-alibaba-avatar.webp",
        "pixel": "lab-alibaba-pixel.webp",
    },
    # Retained for accurate rendering of archived runs and optional routes.
    "deepseek": {
        "label": "DeepSeek",
        "accent": "navy",
        "avatar": "lab-deepseek-avatar.webp",
        "pixel": "lab-deepseek-pixel.webp",
    },
    "zhipu": {
        "label": "Zhipu",
        "accent": "green",
        "avatar": "lab-zhipu-avatar.webp",
        "pixel": "lab-zhipu-pixel.webp",
    },
    "minimax": {
        "label": "MiniMax", "accent": "gold",
        "avatar": "lab-minimax-avatar.webp", "pixel": "lab-minimax-pixel.webp",
    },
    "meta": {
        "label": "Meta", "accent": "blue",
        "avatar": "lab-meta-avatar.webp", "pixel": "lab-meta-pixel.webp",
    },
    "independent": {
        "label": "Independent lab",
        "accent": "gold",
        "avatar": "lab-independent-avatar.webp",
        "pixel": "lab-independent-pixel.webp",
    },
}


ROUTE_META: dict[str, dict[str, Any]] = {
    "muse-native": {
        "label": "Muse Spark 1.3 · Muse Code", "lab_id": "meta",
        "roles": ["proposer", "refiner"],
    },
    "deepseek-native": {
        "label": "DeepSeek Flash · Native Harness", "lab_id": "deepseek",
        "roles": ["proposer", "refiner"],
    },
    "codex": {
        "label": "GPT-6 Astra",
        "lab_id": "openai",
        "roles": ["proposer", "refiner", "aggregator"],
    },
    "codex-sol": {
        "label": "GPT-6.1 Sol",
        "lab_id": "openai",
        "roles": ["proposer", "refiner", "aggregator"],
    },
    "codex-luna": {
        "label": "GPT-6 Luna",
        "lab_id": "openai",
        "roles": ["proposer", "refiner", "aggregator"],
    },
    "sonnet": {
        "label": "Claude Sonnet 5.5",
        "lab_id": "anthropic",
        "roles": ["proposer", "refiner"],
    },
    "opus": {
        "label": "Claude Opus 5.5",
        "lab_id": "anthropic",
        "roles": ["proposer", "refiner", "aggregator"],
    },
    "kimi": {
        "label": "Kimi K3",
        "lab_id": "moonshot",
        # Retained for archived manifests and explicit CLI compatibility.
        # The Web UI blocks new launches because the OpenCode Go route rejects
        # requests before inference even when balance fallback is enabled.
        "roles": [],
    },
    "qwen": {
        "label": "Qwen 3.8 Max · Go",
        "lab_id": "alibaba",
        "roles": ["proposer", "refiner"],
    },
    "qwen-opencode": {
        "label": "Qwen 3.8 Max · Go",
        "lab_id": "alibaba",
        "roles": ["proposer", "refiner"],
    },
    "grok": {
        "label": "Grok 4.7",
        "lab_id": "xai",
        "roles": ["proposer", "refiner"],
    },
    "agy-gemini-pro": {
        "label": "Gemini 4 Argon",
        "lab_id": "google",
        "roles": ["proposer", "refiner", "aggregator"],
    },
    "fable": {
        "label": "Fable 5.1 1M Thinking",
        "lab_id": "anthropic",
        "roles": ["aggregator"],
    },
    "minimax": {"label": "MiniMax M3", "lab_id": "minimax", "roles": ["proposer", "refiner"]},
    "muse-spark": {
        "label": "Muse Spark 1.3 Contributor", "lab_id": "meta",
        "roles": ["proposer", "refiner"],
        "usage_note": "Contributor endpoint: prompts and responses may train Meta models.",
    },
    "glm": {
        "label": "GLM-5.3",
        "lab_id": "zhipu",
        "roles": ["proposer", "refiner"],
    },
    "deepseek": {
        "label": "DeepSeek V4 Pro",
        "lab_id": "deepseek",
        "roles": [],
    },
    "deepseek-flash": {
        "label": "DeepSeek V4.1 Flash",
        "lab_id": "deepseek",
        "roles": ["proposer", "refiner"],
    },
    # Legacy-only identities. These remain resolvable for archived manifests.
    "composer": {
        "label": "Composer 2.5",
        "lab_id": "independent",
        "roles": ["proposer", "refiner"],
    },
    "cursor-grok": {
        "label": "Grok 4.5 High",
        "lab_id": "xai",
        "roles": ["proposer", "refiner"],
    },
}

for _model, _label in (
    ("gpt-6-sol", "GPT-6 Sol"), ("gpt-5.6-sol", "GPT-5.6 Sol"),
    ("gpt-5.6-terra", "GPT-5.6 Terra"), ("gpt-5.6-luna", "GPT-5.6 Luna"),
):
    ROUTE_META["codex-" + _model.replace(".", "-")] = {
        "label": _label, "lab_id": "openai", "roles": ["aggregator"],
    }


for _name, (_model, _label) in MUSE_ROUTES.items():
    ROUTE_META[_name] = {
        "label": _label + " · Muse Code", "lab_id": "meta",
        "roles": ["proposer", "refiner"],
        "usage_note": ("Contributor mode: prompts and responses may train Meta models."
                       if _model.endswith("-contributor") else "Standard mode: content is not used to train Meta models."),
    }


def model_lab(lab_id: str | None) -> dict[str, str]:
    """Return a complete lab record with a safe independent fallback."""
    return MODEL_LABS.get(str(lab_id or "").lower(), MODEL_LABS["independent"])


def route_lab_id(route_id: str | None, model: str | None = None) -> str:
    """Resolve the producing lab from a stable route id or model prefix."""
    route_key = str(route_id or "")
    configured = ROUTE_META.get(route_key, {}).get("lab_id")
    if configured in MODEL_LABS:
        return str(configured)

    value = str(model or route_key).lower()
    prefix_rules = (
        (("gpt-", "openai/", "codex"), "openai"),
        (("gemini", "google/", "agy-"), "google"),
        (("claude", "anthropic/", "sonnet", "opus", "fable"), "anthropic"),
        (("grok", "xai/", "cursor-grok"), "xai"),
        (("kimi", "moonshot/"), "moonshot"),
        (("qwen", "alibaba/", "qwen-token-plan/"), "alibaba"),
        (("deepseek",), "deepseek"),
        (("glm", "zhipu"), "zhipu"),
        (("minimax",), "minimax"),
        (("muse-spark", "muse-wonder", "meta/"), "meta"),
    )
    for prefixes, lab_id in prefix_rules:
        if any(prefix in value for prefix in prefixes):
            return lab_id
    return "independent"
