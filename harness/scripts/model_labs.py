"""Canonical model-lab identity metadata.

The execution harness (Codex, Claude Code, OpenCode, or AGY) is transport.
It must never determine a model's visual identity or diversity attribution.
Every UI and report consumer should use ``lab_id`` from this module instead.
"""
from __future__ import annotations

from typing import Any


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
    # Retained for accurate rendering of archived runs even though these
    # unreliable routes are no longer offered in the curated launch roster.
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
    "independent": {
        "label": "Independent lab",
        "accent": "gold",
        "avatar": "lab-independent-avatar.webp",
        "pixel": "lab-independent-pixel.webp",
    },
}


ROUTE_META: dict[str, dict[str, Any]] = {
    "codex": {
        "label": "GPT-5.6 Terra",
        "lab_id": "openai",
        "roles": ["proposer", "refiner"],
    },
    "codex-sol": {
        "label": "GPT-5.6 Sol",
        "lab_id": "openai",
        "roles": ["proposer", "refiner", "aggregator"],
    },
    "codex-luna": {
        "label": "GPT-5.6 Luna",
        "lab_id": "openai",
        "roles": ["proposer", "refiner"],
    },
    "sonnet": {
        "label": "Claude Sonnet 5",
        "lab_id": "anthropic",
        "roles": ["proposer", "refiner"],
    },
    "opus": {
        "label": "Claude Opus 5",
        "lab_id": "anthropic",
        "roles": ["proposer", "refiner", "aggregator"],
    },
    "kimi": {
        "label": "Kimi K3",
        "lab_id": "moonshot",
        "roles": ["proposer", "refiner"],
    },
    "qwen": {
        "label": "Qwen 3.8 Max Preview · Token Plan",
        "lab_id": "alibaba",
        "roles": ["proposer", "refiner"],
    },
    "qwen-opencode": {
        "label": "Qwen 3.7 Max",
        "lab_id": "alibaba",
        "roles": ["proposer", "refiner"],
    },
    "grok": {
        "label": "Grok 4.5",
        "lab_id": "xai",
        "roles": ["proposer", "refiner"],
    },
    "agy-gemini-pro": {
        "label": "Gemini 3.1 Pro",
        "lab_id": "google",
        "roles": ["proposer", "refiner"],
    },
    "fable": {
        "label": "Fable 5 1M Thinking",
        "lab_id": "anthropic",
        "roles": ["aggregator"],
    },
    # Legacy-only identities. These remain resolvable for archived manifests
    # but are intentionally absent from the curated provider catalog.
    "glm": {
        "label": "GLM-5.2",
        "lab_id": "zhipu",
        "roles": ["proposer", "refiner"],
    },
    "deepseek": {
        "label": "DeepSeek V4 Pro",
        "lab_id": "deepseek",
        "roles": ["proposer", "refiner"],
    },
    "deepseek-flash": {
        "label": "DeepSeek V4 Flash",
        "lab_id": "deepseek",
        "roles": ["proposer", "refiner"],
    },
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
    )
    for prefixes, lab_id in prefix_rules:
        if any(prefix in value for prefix in prefixes):
            return lab_id
    return "independent"
