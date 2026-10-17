"""Reasoning controls supported by the current native model routes."""

from muse_models import muse_efforts

CODEX_EFFORTS = ("low", "medium", "high", "xhigh", "max", "ultra")
CLAUDE_EFFORTS = ("low", "medium", "high", "xhigh", "max")
GEMINI_EFFORTS = ("low", "medium", "high")
OPENAI_MODELS = {
    "gpt-6.1-sol": "GPT-6.1 Sol",
    "gpt-6-astra": "GPT-6 Astra",
    "gpt-6-sol": "GPT-6 Sol",
    "gpt-6-luna": "GPT-6 Luna",
    "gpt-5.6-sol": "GPT-5.6 Sol",
    "gpt-5.6-terra": "GPT-5.6 Terra",
    "gpt-5.6-luna": "GPT-5.6 Luna",
}


def effort_options(harness, model, configured_effort=None):
    """Keep known limits strict and preserve declared custom-route capabilities."""
    if harness == "codex":
        if model in OPENAI_MODELS:
            return list(CODEX_EFFORTS[:-1] if model.endswith("-luna") else CODEX_EFFORTS)
        levels = list(CODEX_EFFORTS[:4])
        if configured_effort in CODEX_EFFORTS[4:]:
            levels.append(configured_effort)
        return levels
    if harness == "claude":
        return list(CLAUDE_EFFORTS)
    if harness == "agy":
        return list(GEMINI_EFFORTS)
    if harness == "muse":
        return muse_efforts(model)
    if harness == "deepseek":
        return ["low", "high", "max"]
    return []
