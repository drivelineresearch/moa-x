"""Keep connector attribution separate from the user's message and commands."""

import re


def message_content(event, bot_user):
    value = str(event.get("text") or "").strip()
    if event.get("app_id"):
        for block in reversed(event.get("blocks") or []):
            if block.get("type") != "context":
                continue
            for element in reversed(block.get("elements") or []):
                attribution = str(element.get("text") or "").strip()
                if (re.fullmatch(r"\*Sent using\* <@[A-Z0-9]+>", attribution)
                        and value.endswith(attribution)):
                    value = value[:-len(attribution)].rstrip()
    return re.sub(r"<@" + re.escape(bot_user) + r">", "", value).strip()
