"""Reconcile recent bot DMs when Socket Mode misses an event.

Only bot DMs and known request threads are read. Persisted watermarks prevent
historical messages from becoming new requests, and the shared inbox dedupes
Socket Mode, history and thread delivery. Each tick makes one history request
and at most one thread request; Retry-After pauses reconciliation.
"""

import logging
import time
from collections import deque

log = logging.getLogger("moa.slack")


class DMReconciler:
    def __init__(self, bot, client, *, now=time.time):
        self.bot, self.client, self.now = bot, client, now
        self.floor = bot.db.watermark("dm-start", str(now() - 60))
        self.channels = deque()
        self.known = set()
        self.pages = {}
        self.discover_cursor = None
        self.next_discovery = 0
        self.next_thread = 0
        self.thread_index = 0
        self.pause_until = 0
        self.received = 0
        self.last_message_at = None
        self.last_check_at = None
        self.error = None

    def _read(self, channel, thread=None):
        target = channel + (":" + thread if thread else "")
        page = self.pages.setdefault(target, {
            "oldest": self.bot.db.poll_window(target, str(max(float(self.floor), self.now() - 60)), self.floor),
            "latest": str(self.now()), "messages": [], "cursor": None,
        })
        args = {"channel": channel, "oldest": page["oldest"], "latest": page["latest"], "limit": 200}
        if page["cursor"]:
            args["cursor"] = page["cursor"]
        if thread:
            result = self.client.conversations_replies(ts=thread, **args)
        else:
            result = self.client.conversations_history(**args)
        page["messages"].extend(result.get("messages", []))
        page["cursor"] = (result.get("response_metadata") or {}).get("next_cursor")
        if result.get("has_more") and not page["cursor"]:
            # Never advance past an incomplete result set.
            self.pages.pop(target)
            raise ValueError("incomplete_history")
        if page["cursor"]:
            return
        for message in sorted(page["messages"], key=lambda row: float(row["ts"])):
            if float(message["ts"]) <= float(page["oldest"]):
                continue
            event = {**message, "channel": channel, "channel_type": "im"}
            if thread and message["ts"] != thread:
                event["thread_ts"] = thread
            if self.bot.enqueue("events_api", {"team_id": self.bot.team, "event": event}, fast_cancel=False):
                self.received += 1
                self.last_message_at = self.now()
                log.info("Recovered a recent DM message into the durable inbox")
        self.bot.db.advance_watermark(target, page["latest"])
        self.pages.pop(target)
        self.last_check_at = self.now()

    def tick(self):
        now = self.now()
        if now < self.pause_until:
            return
        try:
            if now >= self.next_discovery:
                args = {"types": "im", "limit": 200, "exclude_archived": True}
                if self.discover_cursor:
                    args["cursor"] = self.discover_cursor
                result = self.client.conversations_list(**args)
                for channel in result.get("channels", []):
                    ident = channel["id"]
                    if ident.startswith("D") and ident not in self.known:
                        self.known.add(ident)
                        self.channels.appendleft(ident)
                self.discover_cursor = (result.get("response_metadata") or {}).get("next_cursor")
                self.next_discovery = now + (2 if self.discover_cursor else 20)
            if self.channels:
                channel = self.channels.popleft()
                self.channels.append(channel)
                self._read(channel)
            if now >= self.next_thread:
                threads = [(c["channel"], root) for c in self.bot.db.dm_threads(self.bot.team)
                           for root in set(filter(None, (c["thread"], c.get("message_ts"))))]
                if threads:
                    self.thread_index %= len(threads)
                    self._read(*threads[self.thread_index])
                    self.thread_index += 1
                self.next_thread = now + 2
            self.error = None
        except Exception as error:
            response = getattr(error, "response", {})
            self.error = response.get("error", type(error).__name__)
            headers = getattr(response, "headers", {}) or {}
            delay = headers.get("Retry-After", headers.get("retry-after", 10))
            self.pause_until = now + max(2, int(delay))
            log.warning("DM reconciliation will retry: %s", self.error)

    def status(self):
        return {"dm_count": len(self.known), "reconciled_messages": self.received,
                "last_reconciled_message_at": self.last_message_at,
                "last_dm_check_at": self.last_check_at, "reconciliation_error": self.error}
