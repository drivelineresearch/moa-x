"""Deliver cards and final artifacts with persistent reconciliation state."""

import urllib.request
import urllib.error
import logging
import uuid
from pathlib import Path


class Delivery:
    def __init__(self, db, client):
        self.db, self.client = db, client

    def existing_message(self, c, key):
        cursor = None
        for _ in range(4):
            args = {
                "channel": c["channel"],
                "ts": c["thread"],
                "limit": 100,
                "include_all_metadata": True,
            }
            if cursor:
                args["cursor"] = cursor
            result = self.client.conversations_replies(**args)
            for message in result.get("messages", []):
                if (message.get("metadata") or {}).get("event_payload", {}).get(
                    "delivery_id"
                ) == key:
                    return message["ts"]
            cursor = (result.get("response_metadata") or {}).get("next_cursor")
            if not cursor:
                break
        return None

    def deliver(self, item):
        c = self.db.get(item["conversation"])
        if not c:
            self.db.done("slack_outbox", item)
            return
        try:
            if item["kind"] == "file":
                self.file(item, c)
            elif item["kind"] == "card" and c.get("message_ts"):
                self.client.chat_update(
                    channel=c["channel"], ts=c["message_ts"], **item["payload"]
                )
            else:
                ts = (
                    self.existing_message(c, item["id"])
                    if item["attempts"] > 1 or item.get("generation", 0) > 0
                    else None
                )
                if not ts:
                    reply = self.client.chat_postMessage(
                        channel=c["channel"],
                        thread_ts=c["thread"],
                        client_msg_id=str(uuid.uuid5(uuid.NAMESPACE_URL, item["id"])),
                        metadata={
                            "event_type": "moa_x",
                            "event_payload": {"delivery_id": item["id"]},
                        },
                        unfurl_links=False,
                        unfurl_media=False,
                        **item["payload"],
                    )
                    ts = reply["ts"]
                if item["kind"] == "card":
                    self.db.message_sent(c, ts)
            self.db.done("slack_outbox", item)
        except Exception as error:
            response = getattr(error, "response", {})
            code = response.get("error", type(error).__name__)
            if code == "message_not_found" and item["kind"] == "card":
                self.db.message_sent(c, None)
            if isinstance(error, urllib.error.HTTPError) and error.code == 403 and item["kind"] == "file" and not item["payload"].get("uploaded"):
                self.db.file_state(item, file_id=None, upload_url=None, uploaded=False)
            if code in {"channel_not_found", "not_in_channel", "is_archived", "invalid_auth", "token_revoked", "missing_scope", "account_inactive"} or (item["attempts"] >= 12 and code != "ratelimited"):
                self.db.fail_delivery(item, code)
                logging.getLogger("moa.slack").warning("Slack delivery needs operator attention: %s", code)
                return
            headers = getattr(response, "headers", {}) or {}
            delay = int(
                headers.get(
                    "Retry-After",
                    headers.get("retry-after", min(300, 2 ** min(item["attempts"], 8))),
                )
            )
            self.db.retry("slack_outbox", item, code, delay=max(1, delay))

    def file(self, item, c):
        p = item["payload"]
        path = Path(p["path"])
        if not p.get("file_id"):
            ticket = self.client.files_getUploadURLExternal(
                filename=p["filename"], length=path.stat().st_size
            )
            self.db.file_state(
                item, file_id=ticket["file_id"], upload_url=ticket["upload_url"]
            )
        if not p.get("uploaded"):
            # Signed upload URL comes from Slack, with no bot credential forwarded.
            req = urllib.request.Request(
                p["upload_url"],
                data=path.read_bytes(),
                method="POST",
                headers={"Content-Type": "application/octet-stream"},
            )
            with urllib.request.urlopen(req, timeout=60) as response:
                response.read()
            self.db.file_state(item, uploaded=True)
        try:
            self.client.files_completeUploadExternal(
                files=[{"id": p["file_id"], "title": p["title"]}],
                channel_id=c["channel"],
                thread_ts=c["thread"],
            )
        except Exception as error:
            if getattr(error, "response", {}).get("error") not in (
                "already_complete",
                "file_already_complete",
            ):
                raise
            info = self.client.files_info(file=p["file_id"])["file"]
            shares = info.get("shares") or {}
            if not any(c["channel"] in locations for locations in shares.values()):
                raise
