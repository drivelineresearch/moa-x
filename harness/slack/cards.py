"""Small Slack cards; every interactive control binds to a draft version."""

import html
import hashlib
import json


def text(value):
    return html.escape(str(value), quote=False)


def section(value):
    return {"type": "section", "text": {"type": "mrkdwn", "text": str(value)[:3000]}}


def button(conversation, label, action, extra=None, style=None):
    value = json.dumps(
        {"id": conversation["id"], "v": conversation["version"], **(extra or {})},
        separators=(",", ":"),
    )
    if extra:
        action += (
            ":"
            + hashlib.sha256(json.dumps(extra, sort_keys=True).encode()).hexdigest()[
                :10
            ]
        )
    item = {
        "type": "button",
        "text": {"type": "plain_text", "text": label[:75]},
        "action_id": action,
        "value": value,
    }
    if style:
        item["style"] = style
    return item


def card(conversation):
    d = conversation["data"]
    stage = conversation["stage"]
    emoji = {"collect": "👋", "coaching": "🧠", "interview": "💬", "preview": "📋",
             "launching": "🚀", "running": "⏳", "completed": "✅", "failed": "❌", "cancelled": "🛑"}.get(stage, "🧠")
    blocks = [section(emoji + " *MoA-X · " + text(stage.title()) + "*")]
    if d.get("repository"):
        pointer = text(d["repository"])
        blocks.append(section("📂 Repository: <https://github.com/" + pointer + "|" + pointer + "> · read-only context"))
    if d.get("repository_error"):
        blocks.append(section("⚠️ " + text(d["repository_error"])))
    if stage == "collect":
        blocks.append(
            section(
                "MoA-X is online. Send me a task, decision or outcome, with optional PDF, image or text references.\n🧠 Luna asks up to three useful questions; then preview the brief, models and effort and press *🚀 Run MoA-X*. Results and progress stay here in Slack.\nTry: `new: Compare two approaches to reducing API latency and recommend a rollout plan.`\nUse `help`, `status` or `cancel` anytime."
            )
        )
    elif stage == "coaching":
        blocks.append(
            section("👀 Got your request. 🧠 Luna is preparing your brief and up to three useful questions. This can take a minute or two. Then review the models and effort and press *🚀 Run MoA-X* to start the ensemble.")
        )
    elif stage == "interview":
        questions = d["analysis"]["questions"]
        index = len(d.get("answers", []))
        q = questions[index]
        blocks.append(
            section(f"*Question {index + 1} of {len(questions)}*\n" + text(q["prompt"]))
        )
        blocks.append(
            {
                "type": "actions",
                "elements": [
                    button(
                        conversation,
                        o["label"] + (" (recommended)" if o["recommended"] else ""),
                        "moa_answer",
                        {"answer": o["label"], "question": q["id"]},
                    )
                    for o in q["options"]
                ],
            }
        )
        blocks.append(section("Or type your own answer in this thread."))
    elif stage == "preview":
        blocks.append(
            section(
                "*Optimized brief*\n" + text(d["finalized"]["optimized_prompt"][:2300])
            )
        )
        depth = d.get("depth", "balanced")
        blocks.append(
            section(
                "*Depth: "
                + depth.title()
                + "* · "
                + text(d.get("analysis", {}).get("depth_rationale", ""))
            )
        )
        roster = d.get("roster")
        if roster:
            rows = [
                f"{r['role'].title()}: {r['model']}"
                + (" · " + r["effort"] if r["effort"] else "")
                for r in roster["models"]
            ]
            blocks.append(section(text("\n".join(rows))))
        if d.get("roster_error"):
            blocks.append(section("Cannot launch yet: " + text(d["roster_error"])))
        actions = [
            button(conversation, "Quick", "moa_depth", {"depth": "quick"}),
            button(conversation, "Balanced", "moa_depth", {"depth": "balanced"}),
            button(conversation, "Thorough", "moa_depth", {"depth": "thorough"}),
            button(conversation, "Adjust effort", "moa_advanced"),
        ]
        blocks.append({"type": "actions", "elements": actions})
        if d.get("advanced") and roster:
            for r in roster["models"]:
                if not r["effort_options"]:
                    continue
                blocks.append(section(text(r["route"]) + " effort"))
                options = [
                    {
                        "text": {"type": "plain_text", "text": level.title()},
                        "value": json.dumps(
                            {
                                "id": conversation["id"],
                                "v": conversation["version"],
                                "route": r["route"],
                                "effort": level,
                            },
                            separators=(",", ":"),
                        ),
                    }
                    for level in r["effort_options"]
                ]
                select = {
                    "type": "static_select",
                    "action_id": "moa_effort",
                    "placeholder": {"type": "plain_text", "text": "Choose effort"},
                    "options": options,
                }
                initial = next(
                    (
                        o
                        for o, level in zip(options, r["effort_options"])
                        if level == r["effort"]
                    ),
                    None,
                )
                if initial:
                    select["initial_option"] = initial
                blocks.append({"type": "actions", "elements": [select]})
        actions = []
        if roster and not d.get("roster_error"):
            actions.append(
                button(conversation, "🚀 Run MoA-X", "moa_run", style="primary")
            )
        actions.append(button(conversation, "Edit brief", "moa_edit"))
        blocks.append({"type": "actions", "elements": actions})
        blocks.append(
            section(
                "Add more reference files before Run. The ensemble can take several minutes; inputs and settings freeze at launch."
            )
        )
    elif stage in ("launching", "running"):
        blocks.append(
            section(text(d.get("progress", "Submitting your run to the queue…")))
        )
        if d.get("dashboard"):
            blocks.append(section("<" + d["dashboard"] + "|Open live dashboard>"))
    else:
        blocks.append(section(text(d.get("progress", stage.title()))))
        if d.get("dashboard"):
            blocks.append(section("<" + d["dashboard"] + "|Open run and results>"))
    if stage not in ("completed", "failed", "cancelled"):
        blocks.append(
            {
                "type": "actions",
                "elements": [button(conversation, "Cancel", "moa_cancel")],
            }
        )
    return {"text": emoji + " MoA-X: " + stage + (" — online; send a task to begin" if stage == "collect" else ""), "blocks": blocks}
