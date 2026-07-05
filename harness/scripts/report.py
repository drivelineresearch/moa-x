#!/usr/bin/env python3
"""report.py — render a MoA-X session into a single self-contained HTML report.

Reads a `.moa/<session>/` directory (manifest.json, scout-brief.json, the
per-agent payload JSONs and logs, and final-plan.md if aggregation has run)
and emits one standalone `report.html` with zero external requests: the page
template, the vendored Three.js build, the session data, and the rendered
final plan are all inlined.

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

    frozen_spec = scout.get("frozen_spec", "")
    title = frozen_spec.strip().splitlines()[0] if frozen_spec.strip() else manifest.get("session_id", "MoA-X run")
    if len(title) > 96:
        title = title[:95].rstrip() + "…"

    final_plan_md = ""
    fp = session_dir / "final-plan.md"
    if fp.exists():
        final_plan_md = fp.read_text(encoding="utf-8")

    return {
        "session_id": manifest.get("session_id", session_dir.name),
        "generated_at": time.strftime("%Y-%m-%d %H:%M"),
        "partial": partial,
        "title": title,
        "scout_brief": scout,
        "config": manifest.get("config", {}),
        "layer2_mode": manifest.get("layer2_mode", "skipped" if partial else "broadcast"),
        "started_at": manifest.get("started_at"),
        "finished_at": manifest.get("finished_at"),
        "duration_seconds": manifest.get("duration_seconds"),
        "layer1": layer1,
        "layer2": layer2,
        "final_plan_html": render_markdown(final_plan_md) if final_plan_md else None,
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
    list_stack: Optional[str] = None

    def close_list():
        nonlocal list_stack
        if list_stack:
            html.append("</" + list_stack + ">")
            list_stack = None

    while i < n:
        line = lines[i]

        if line.strip().startswith("```"):
            close_list()
            i += 1
            code: list[str] = []
            while i < n and not lines[i].strip().startswith("```"):
                code.append(_esc(lines[i]))
                i += 1
            i += 1  # consume closing fence
            html.append("<pre><code>" + "\n".join(code) + "</code></pre>")
            continue

        if not line.strip():
            close_list()
            i += 1
            continue

        if re.match(r"^(-{3,}|\*{3,}|_{3,})\s*$", line.strip()):
            close_list()
            html.append("<hr>")
            i += 1
            continue

        heading = re.match(r"^(#{1,6})\s+(.*)$", line)
        if heading:
            close_list()
            level = len(heading.group(1))
            html.append(f"<h{level}>" + _inline(_esc(heading.group(2).strip())) + f"</h{level}>")
            i += 1
            continue

        if line.lstrip().startswith(">"):
            close_list()
            html.append("<blockquote>" + _inline(_esc(line.lstrip()[1:].strip())) + "</blockquote>")
            i += 1
            continue

        ol = re.match(r"^\s*\d+\.\s+(.*)$", line)
        ul = re.match(r"^\s*[-*]\s+(.*)$", line)
        if ol or ul:
            want = "ol" if ol else "ul"
            if list_stack != want:
                close_list()
                html.append("<" + want + ">")
                list_stack = want
            html.append("<li>" + _inline(_esc((ol or ul).group(1).strip())) + "</li>")
            i += 1
            continue

        close_list()
        html.append("<p>" + _inline(_esc(line.strip())) + "</p>")
        i += 1

    close_list()
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
