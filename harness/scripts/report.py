#!/usr/bin/env python3
"""report.py — render a MoA-X session into a single self-contained HTML report.

Reads a `.moa/<session>/` directory (manifest.json, scout-brief.json, the
per-agent payload JSONs and logs, plus final-plan.md/final-plan.json if
aggregation has run)
and emits one standalone `report.html` with zero external requests: the page
template, the vendored Three.js build, the session data, the decision
lineage, and the rendered final plan are all inlined.

Usage:
    report.py --session .moa/<id> [-o OUT.html]
    report.py --latest [--moa-dir .moa] [-o OUT.html]

Default output is `<session>/report.html`. stdlib only — no third-party deps,
matching the rest of the harness.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path
from typing import Any, Optional

from run_moa import (
    FINAL_PLAN_SCHEMA_PATH,
    _load_schema,
    _validate_against_schema,
)

SCRIPT_DIR = Path(__file__).resolve().parent
REPORT_DIR = SCRIPT_DIR.parent / "report"
TEMPLATE_PATH = REPORT_DIR / "template.html"
THREE_JS_PATH = REPORT_DIR / "three.min.js"

ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")


# ---------------------------------------------------------------------------
# Loading a session off disk
# ---------------------------------------------------------------------------

def _read_json(path: Path) -> Optional[dict]:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _split_log(raw: str) -> dict:
    """Split a `=== STDOUT ===` / `=== STDERR ===` log dump into its streams.

    Adapters write both streams to one file with these fixed markers. ANSI
    escape sequences are stripped so the browser renders clean text.
    """
    raw = ANSI_RE.sub("", raw)
    stdout, stderr = raw, ""
    if "=== STDERR ===" in raw:
        head, _, tail = raw.partition("=== STDERR ===")
        stdout, stderr = head, tail
    stdout = stdout.replace("=== STDOUT ===", "", 1).strip("\n")
    stderr = stderr.strip("\n")
    return {"stdout": stdout, "stderr": stderr}


def _load_agent(entry: dict, session_dir: Path) -> dict:
    """Enrich a manifest layer entry with its on-disk payload and log."""
    out = dict(entry)
    out.pop("payload", None)
    payload = None
    if entry.get("json_path"):
        payload = _read_json(session_dir / entry["json_path"])
    out["payload"] = payload
    log = {"stdout": "", "stderr": ""}
    if entry.get("log_path"):
        log_file = session_dir / entry["log_path"]
        if log_file.exists():
            log = _split_log(log_file.read_text(encoding="utf-8", errors="replace"))
    out["log"] = log
    return out


def _normalized_timing(manifest: dict, agents: list[dict]) -> dict:
    """Normalize session bounds across phase-split and redispatched runs.

    v0.4.1 and older final manifests could carry the Layer-2 invocation start
    while retaining Layer-1 agent timestamps from an earlier invocation. Use
    every positive recorded timestamp to recover the real session span. New
    manifests preserve the original start, so this becomes a no-op for them.
    """
    raw_start = manifest.get("started_at")
    raw_finish = manifest.get("finished_at")
    starts = [float(raw_start)] if isinstance(raw_start, (int, float)) and raw_start > 0 else []
    finishes = [float(raw_finish)] if isinstance(raw_finish, (int, float)) and raw_finish > 0 else []
    for agent in agents:
        start = agent.get("started_at")
        duration = agent.get("duration_seconds")
        if isinstance(start, (int, float)) and start > 0:
            starts.append(float(start))
            if isinstance(duration, (int, float)) and duration >= 0:
                finishes.append(float(start) + float(duration))

    start = min(starts) if starts else raw_start
    finish = max(finishes) if finishes else raw_finish
    if isinstance(start, (int, float)) and isinstance(finish, (int, float)):
        duration = max(0.0, float(finish) - float(start))
    else:
        duration = manifest.get("duration_seconds")

    raw_duration = manifest.get("duration_seconds")
    start_changed = (
        isinstance(start, (int, float))
        and isinstance(raw_start, (int, float))
        and abs(float(start) - float(raw_start)) > 0.5
    )
    finish_changed = (
        isinstance(finish, (int, float))
        and isinstance(raw_finish, (int, float))
        and abs(float(finish) - float(raw_finish)) > 0.5
    )
    duration_changed = (
        isinstance(duration, (int, float))
        and isinstance(raw_duration, (int, float))
        and abs(float(duration) - float(raw_duration)) > 0.5
    )
    corrected = start_changed or finish_changed or duration_changed
    return {
        "started_at": start,
        "finished_at": finish,
        "duration_seconds": duration,
        "recorded_duration_seconds": raw_duration,
        "timing_normalized": corrected,
    }


def _validate_final_plan_lineage(
    lineage: dict,
    proposers: list[dict],
    refiners: list[dict],
) -> list[str]:
    """Validate the companion JSON and resolve its pointers into agent payloads.

    Schema errors and stale/out-of-range references are warnings rather than a
    report-generation failure: the rest of a run report remains useful even
    when an aggregator produced an incomplete lineage file.
    """
    schema = _load_schema(FINAL_PLAN_SCHEMA_PATH)
    warnings_out = _validate_against_schema(lineage, schema)
    if warnings_out:
        return warnings_out

    proposer_by_id = {r.get("agent_id"): r for r in proposers}
    refiner_by_id = {r.get("agent_id"): r for r in refiners}
    refiner_arrays = {
        "verification": "verifications",
        "missing_step": "missing_steps",
        "incorrect_step": "incorrect_steps",
        "disagreement": "disagreements",
    }

    def check_proposer_ref(ref: dict, location: str) -> None:
        agent_id = ref["agent_id"]
        agent = proposer_by_id.get(agent_id)
        payload = (agent or {}).get("payload")
        plan = payload.get("plan", []) if isinstance(payload, dict) else []
        if not agent or not agent.get("payload"):
            warnings_out.append(f"{location}: proposer {agent_id!r} has no payload")
        elif ref["step_index"] >= len(plan):
            warnings_out.append(
                f"{location}: proposer {agent_id!r} step_index {ref['step_index']} "
                f"is out of range (plan has {len(plan)} steps)"
            )

    def check_refiner_ref(ref: dict, location: str) -> None:
        agent_id = ref["agent_id"]
        agent = refiner_by_id.get(agent_id)
        payload = (agent or {}).get("payload")
        if not agent or not payload:
            warnings_out.append(f"{location}: refiner {agent_id!r} has no payload")
            return
        kind = ref["kind"]
        index = ref["index"]
        if kind == "synthesis_recommendation":
            if index is not None:
                warnings_out.append(
                    f"{location}: synthesis_recommendation index must be null"
                )
            return
        if index is None:
            warnings_out.append(f"{location}: {kind} index must be an integer")
            return
        values = payload.get(refiner_arrays[kind], [])
        if index >= len(values):
            warnings_out.append(
                f"{location}: refiner {agent_id!r} {kind} index {index} "
                f"is out of range ({len(values)} available)"
            )

    seen_ids: set[str] = set()
    for step_i, step in enumerate(lineage["steps"]):
        location = f"steps[{step_i}]"
        if step["id"] in seen_ids:
            warnings_out.append(f"{location}.id: duplicate id {step['id']!r}")
        seen_ids.add(step["id"])
        for ref_i, ref in enumerate(step["proposer_refs"]):
            check_proposer_ref(ref, f"{location}.proposer_refs[{ref_i}]")
        for ref_i, ref in enumerate(step["refiner_refs"]):
            check_refiner_ref(ref, f"{location}.refiner_refs[{ref_i}]")

    for rejected_i, rejected in enumerate(lineage["rejected_inputs"]):
        check_proposer_ref(
            {
                "agent_id": rejected["proposer"],
                "step_index": rejected["step_index"],
            },
            f"rejected_inputs[{rejected_i}]",
        )
        for ref_i, ref in enumerate(rejected["refiner_refs"]):
            check_refiner_ref(
                ref, f"rejected_inputs[{rejected_i}].refiner_refs[{ref_i}]"
            )
    return warnings_out


def load_session(session_dir: Path) -> dict:
    """Assemble the data object the template consumes from a session directory.

    Prefers the full manifest.json; falls back to layer1-manifest.json for a
    phase-split (Layer 1 only) run, marking the result `partial`. Raises
    FileNotFoundError when neither manifest exists.
    """
    full = session_dir / "manifest.json"
    layer1_only = session_dir / "layer1-manifest.json"

    if full.exists():
        manifest = _read_json(full)
        partial = False
    elif layer1_only.exists():
        manifest = _read_json(layer1_only)
        partial = True
    else:
        raise FileNotFoundError(
            f"no manifest.json or layer1-manifest.json in {session_dir}"
        )

    scout = _read_json(session_dir / "scout-brief.json") or {}
    layer1 = [_load_agent(e, session_dir) for e in manifest.get("layer1", [])]
    layer2 = [_load_agent(e, session_dir) for e in manifest.get("layer2", [])]
    layer3 = [_load_agent(e, session_dir) for e in manifest.get("layer3", [])]
    timing = _normalized_timing(manifest, layer1 + layer2 + layer3)

    frozen_spec = scout.get("frozen_spec", "")
    title = frozen_spec.strip().splitlines()[0] if frozen_spec.strip() else manifest.get("session_id", "MoA-X run")
    if len(title) > 96:
        title = title[:95].rstrip() + "…"

    final_plan_md = ""
    fp = session_dir / "final-plan.md"
    if fp.exists():
        final_plan_md = fp.read_text(encoding="utf-8")

    final_plan_lineage = None
    lineage_warnings: list[str] = []
    lineage_path = session_dir / "final-plan.json"
    if lineage_path.exists():
        try:
            final_plan_lineage = _read_json(lineage_path)
            if not isinstance(final_plan_lineage, dict):
                lineage_warnings.append("final-plan.json must contain a JSON object")
                final_plan_lineage = None
            else:
                validation_warnings = _validate_final_plan_lineage(
                    final_plan_lineage, layer1, layer2
                )
                lineage_warnings.extend(validation_warnings)
                if any(w.startswith("$") for w in validation_warnings):
                    final_plan_lineage = None
        except (OSError, json.JSONDecodeError) as exc:
            lineage_warnings.append(f"could not read final-plan.json: {exc}")

    return {
        "session_id": manifest.get("session_id", session_dir.name),
        "generated_at": time.strftime("%Y-%m-%d %H:%M"),
        "partial": partial,
        "title": title,
        "scout_brief": scout,
        "config": manifest.get("config", {}),
        "summary": manifest.get("summary", {}),
        "layer2_mode": manifest.get("layer2_mode", "skipped" if partial else "broadcast"),
        **timing,
        "layer1": layer1,
        "layer2": layer2,
        "layer3": layer3,
        "final_plan_html": render_markdown(final_plan_md) if final_plan_md else None,
        "final_plan_lineage": final_plan_lineage,
        "lineage_warnings": lineage_warnings,
    }


# ---------------------------------------------------------------------------
# Minimal markdown → HTML (final-plan.md only)
# ---------------------------------------------------------------------------

def _esc(s: str) -> str:
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def _inline(text: str) -> str:
    """Inline markdown on an already HTML-escaped string: code, bold, links.

    Inline-code spans are stashed to placeholders before bold/link run, so a
    span like ``**not bold**`` or a bracketed URL inside backticks is rendered
    verbatim instead of being reformatted.
    """
    codes: list[str] = []

    def _stash(m: "re.Match") -> str:
        codes.append(m.group(1))
        return f"\x00{len(codes) - 1}\x00"

    text = re.sub(r"`([^`]+)`", _stash, text)
    text = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(
        r"\[([^\]]+)\]\((https?://[^)\s]+)\)",
        r'<a href="\2" target="_blank" rel="noopener">\1</a>',
        text,
    )
    return re.sub(r"\x00(\d+)\x00", lambda m: "<code>" + codes[int(m.group(1))] + "</code>", text)


def render_markdown(md: str) -> str:
    """Render the markdown subset used by final-plan.md.

    Supports ATX headings, fenced code blocks, unordered/ordered lists,
    blockquotes, horizontal rules, and inline code/bold/links. Deliberately
    small — final plans are the only input and they stick to this subset.
    """
    lines = md.replace("\r\n", "\n").split("\n")
    html: list[str] = []
    i, n = 0, len(lines)

    list_item = re.compile(r"^(\s*)(?:(\d+)\.|([-*]))\s+(.*)$")

    def render_list(start: int) -> tuple[str, int]:
        """Render one list, including recursively nested child lists.

        Final plans conventionally use a top-level ordered implementation plan
        with indented unordered metadata below every step.  The old renderer
        treated each nested bullet group as a new top-level list, which closed
        and reopened the ``<ol>`` and made every visible step restart at 1.
        Keeping the parent ``<li>`` open while child lists are rendered fixes
        the numbering and produces valid nested-list HTML.
        """
        first = list_item.match(lines[start])
        if first is None:  # pragma: no cover - guarded by the caller
            return "", start + 1
        indent = len(first.group(1).expandtabs(4))
        tag = "ol" if first.group(2) else "ul"
        out = [f"<{tag}>"]
        cursor = start

        while cursor < n:
            match = list_item.match(lines[cursor])
            if match is None:
                break
            item_indent = len(match.group(1).expandtabs(4))
            item_tag = "ol" if match.group(2) else "ul"
            if item_indent != indent or item_tag != tag:
                break

            out.append("<li>" + _inline(_esc(match.group(4).strip())))
            cursor += 1

            # Blank lines are allowed between a parent item and its child
            # list, and between sibling items. Consume them only while we can
            # prove the list continues; otherwise the main renderer handles
            # the following block normally.
            lookahead = cursor
            while lookahead < n and not lines[lookahead].strip():
                lookahead += 1
            child = list_item.match(lines[lookahead]) if lookahead < n else None
            if child is not None:
                child_indent = len(child.group(1).expandtabs(4))
                child_tag = "ol" if child.group(2) else "ul"
                if child_indent > indent:
                    nested, cursor = render_list(lookahead)
                    out.append(nested)
                    lookahead = cursor
                    while lookahead < n and not lines[lookahead].strip():
                        lookahead += 1
                    child = list_item.match(lines[lookahead]) if lookahead < n else None
                    child_indent = (
                        len(child.group(1).expandtabs(4)) if child is not None else -1
                    )
                    child_tag = "ol" if child is not None and child.group(2) else "ul"

                if child is not None and child_indent == indent and child_tag == tag:
                    cursor = lookahead
            out.append("</li>")

            next_item = list_item.match(lines[cursor]) if cursor < n else None
            if next_item is None:
                break
            next_indent = len(next_item.group(1).expandtabs(4))
            next_tag = "ol" if next_item.group(2) else "ul"
            if next_indent != indent or next_tag != tag:
                break

        out.append(f"</{tag}>")
        return "\n".join(out), cursor

    while i < n:
        line = lines[i]

        if line.strip().startswith("```"):
            i += 1
            code: list[str] = []
            while i < n and not lines[i].strip().startswith("```"):
                code.append(_esc(lines[i]))
                i += 1
            i += 1  # consume closing fence
            html.append("<pre><code>" + "\n".join(code) + "</code></pre>")
            continue

        if not line.strip():
            i += 1
            continue

        if re.match(r"^(-{3,}|\*{3,}|_{3,})\s*$", line.strip()):
            html.append("<hr>")
            i += 1
            continue

        heading = re.match(r"^(#{1,6})\s+(.*)$", line)
        if heading:
            level = len(heading.group(1))
            html.append(f"<h{level}>" + _inline(_esc(heading.group(2).strip())) + f"</h{level}>")
            i += 1
            continue

        if line.lstrip().startswith(">"):
            html.append("<blockquote>" + _inline(_esc(line.lstrip()[1:].strip())) + "</blockquote>")
            i += 1
            continue

        if list_item.match(line):
            rendered, i = render_list(i)
            html.append(rendered)
            continue

        html.append("<p>" + _inline(_esc(line.strip())) + "</p>")
        i += 1

    return "\n".join(html)


# ---------------------------------------------------------------------------
# Rendering the single-file HTML
# ---------------------------------------------------------------------------

def render_html(data: dict) -> str:
    """Inline template + Three.js + session data into one standalone document."""
    if not TEMPLATE_PATH.exists():
        raise FileNotFoundError(f"template missing: {TEMPLATE_PATH}")
    if not THREE_JS_PATH.exists():
        raise FileNotFoundError(f"vendored three.min.js missing: {THREE_JS_PATH}")

    template = TEMPLATE_PATH.read_text(encoding="utf-8")
    three_js = THREE_JS_PATH.read_text(encoding="utf-8")

    # Embed the data as raw text inside <script type="application/json">.
    # Escaping "</" as "<\/" keeps any "</script>" in a log or plan from
    # terminating the script element; JSON.parse restores it in the browser.
    data_json = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")

    # str.replace (not re.sub) so "$" / "\1" in the data are never treated as
    # substitution backreferences. Inject the fixed assets (title, library)
    # first and the session data LAST, so an attacker-free but arbitrary log
    # that happens to contain a later token string can't have that token
    # expanded into the embedded JSON.
    out = template.replace("__PAGE_TITLE__", "MoA-X — " + _esc(data.get("session_id", "run")))
    out = out.replace("/*__THREE_JS_LIB__*/", three_js)
    out = out.replace("__MOA_DATA_JSON__", data_json)
    return out


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def find_latest_session(moa_dir: Path) -> Optional[Path]:
    """Newest session dir under moa_dir that has a manifest, by mtime."""
    if not moa_dir.exists():
        return None
    candidates = []
    for d in moa_dir.iterdir():
        if not d.is_dir():
            continue
        manifest = d / "manifest.json"
        layer1 = d / "layer1-manifest.json"
        chosen = manifest if manifest.exists() else (layer1 if layer1.exists() else None)
        if chosen:
            candidates.append((chosen.stat().st_mtime, d))
    if not candidates:
        return None
    candidates.sort(reverse=True)
    return candidates[0][1]


def generate(session_dir: Path, out_path: Path) -> Path:
    """Load a session and write its report. Returns the written path."""
    data = load_session(session_dir)
    out_path.write_text(render_html(data), encoding="utf-8")
    return out_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Render a MoA-X session to a self-contained HTML report")
    src = parser.add_mutually_exclusive_group(required=True)
    src.add_argument("--session", type=Path, help="Path to a .moa/<session> directory")
    src.add_argument("--latest", action="store_true", help="Use the newest session under --moa-dir")
    parser.add_argument("--moa-dir", type=Path, default=Path(".moa"),
                        help="Directory holding session dirs (default .moa), used with --latest")
    parser.add_argument("-o", "--output", type=Path, default=None,
                        help="Output HTML path (default <session>/report.html)")
    args = parser.parse_args()

    if args.latest:
        session_dir = find_latest_session(args.moa_dir)
        if session_dir is None:
            print(f"ERROR: no sessions with a manifest found under {args.moa_dir}", file=sys.stderr)
            return 2
    else:
        session_dir = args.session

    if not session_dir.exists():
        print(f"ERROR: session directory not found: {session_dir}", file=sys.stderr)
        return 2

    out_path = args.output or (session_dir / "report.html")
    try:
        generate(session_dir, out_path)
    except FileNotFoundError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2

    print(f"[report] wrote {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
