"""One model/effort policy shared by the browser and Slack."""

from .providers import model_catalog

DEPTH_PROFILES = {
    "quick": {
        "proposer": {
            "preferred": ["agy-gemini-pro", "grok"],
            "limit": 2,
            "efforts": {"agy-gemini-pro": "low"},
        },
        "refiner": {"preferred": ["deepseek-flash"], "limit": 1},
        "aggregator": {
            "preferred": ["codex-sol"],
            "limit": 1,
            "efforts": {"codex-sol": "medium"},
        },
    },
    "balanced": {
        "proposer": {
            "preferred": ["agy-gemini-pro", "grok", "codex-luna"],
            "limit": 3,
            "efforts": {"agy-gemini-pro": "high"},
        },
        "refiner": {
            "preferred": ["deepseek-flash", "opus"],
            "limit": 2,
            "efforts": {"opus": "high"},
        },
        "aggregator": {
            "preferred": ["codex-sol"],
            "limit": 1,
            "efforts": {"codex-sol": "xhigh"},
        },
    },
    "thorough": {
        "proposer": {
            "preferred": ["agy-gemini-pro", "grok", "codex", "glm"],
            "limit": 4,
            "efforts": {"agy-gemini-pro": "high"},
        },
        "refiner": {
            "preferred": ["deepseek-flash", "opus"],
            "limit": 2,
            "efforts": {"opus": "max"},
        },
        "aggregator": {
            "preferred": ["codex-sol"],
            "limit": 1,
            "efforts": {"codex-sol": "xhigh"},
        },
    },
}


def resolve_roster(depth="balanced", *, effort_overrides=None, catalog=None):
    if depth not in DEPTH_PROFILES:
        raise ValueError("Choose quick, balanced, or thorough depth.")
    by_id = {
        row["id"]: row for row in (catalog if catalog is not None else model_catalog())
    }
    profile = DEPTH_PROFILES[depth]
    result = {
        "proposers": list(profile["proposer"]["preferred"]),
        "refiners": list(profile["refiner"]["preferred"]),
        "aggregator": profile["aggregator"]["preferred"][0],
        "options": {"model_overrides": {}, "effort_overrides": {}, "depth": depth},
        "models": [],
    }
    overrides = effort_overrides or {}
    selected = set(result["proposers"] + result["refiners"] + [result["aggregator"]])
    if set(overrides) - selected:
        raise ValueError("Effort override refers to a route outside this roster.")
    for role, settings in profile.items():
        for name in settings["preferred"]:
            row = by_id.get(name)
            if not row or not row.get("available") or role not in row.get("roles", []):
                raise ValueError(
                    f"{name} is unavailable for {role}; retry or choose another depth."
                )
            effort = overrides.get(
                name, settings.get("efforts", {}).get(name, row.get("effort"))
            )
            model = row["model"]
            if name in overrides and effort not in row.get("effort_options", []):
                raise ValueError(f"{name} does not support {effort} effort.")
            if row["harness"] == "agy" and effort:
                model = model.rsplit("-", 1)[0] + "-" + effort
                result["options"]["model_overrides"][name] = model
            elif effort and row.get("supports_effort"):
                result["options"]["effort_overrides"][name] = effort
            result["options"]["model_overrides"][name] = model
            result["models"].append(
                {
                    "route": name,
                    "role": role,
                    "model": model,
                    "effort": effort,
                    "effort_options": row.get("effort_options", [])
                    if row.get("supports_effort")
                    else [],
                }
            )
    return result
