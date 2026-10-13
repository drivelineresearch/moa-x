"""Socket Mode listener; expensive operations run outside envelope callbacks."""

import concurrent.futures
import fcntl
import json
import logging
import os
import signal
import threading
import time
from pathlib import Path
from slack_sdk import WebClient
from slack_sdk.socket_mode import SocketModeClient
from slack_sdk.socket_mode.response import SocketModeResponse
from harness.webui.store import Store
from harness.scripts.config import apply_config_to_env
from .bot import Bot
from .delivery import Delivery
from .ingress import DMReconciler

log = logging.getLogger("moa.slack")


def main():
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )
    logging.getLogger("slack_sdk").setLevel(logging.CRITICAL)
    apply_config_to_env()
    token = os.environ["SLACK_BOT_TOKEN"]
    app_token = os.environ["SLACK_APP_TOKEN"]
    team = os.environ["MOA_SLACK_TEAM_ID"]
    base_url = os.environ["MOA_SLACK_BASE_URL"]
    if not base_url.startswith("https://"):
        raise ValueError("Use an HTTPS dashboard base URL.")
    root = (
        Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local/share")))
        / "moa-x"
    )
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    lock_file = (root / "slack.lock").open("a")
    fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    # Durable outbox reconciliation owns retries; SDK connection retries can
    # otherwise repeat a write whose successful response was lost.
    client = WebClient(token=token, timeout=20, retry_handlers=[])
    identity = client.auth_test()
    if identity["team_id"] != team:
        raise ValueError("Bot token belongs to another workspace.")
    bot_identity = client.bots_info(bot=identity["bot_id"])["bot"]
    if bot_identity.get("app_id") != app_token.split("-")[2]:
        raise ValueError("Socket Mode and bot tokens belong to different apps.")
    bot = Bot(
        Store(root / "webui.sqlite3"),
        root,
        client,
        team=team,
        bot_user=identity["user_id"],
        token=token,
        base_url=base_url,
        github_owner=os.environ.get("MOA_WEBUI_GITHUB_OWNER", "drivelineresearch"),
        private_repos=[
            x.strip()
            for x in os.environ.get("MOA_SLACK_PRIVATE_REPOS", "").split(",")
            if x.strip()
        ],
    )
    bot.db.recover()
    delivery = Delivery(bot.db, client)
    stop = threading.Event()
    socket = SocketModeClient(app_token=app_token, web_client=client)
    ingress = {"socket_envelopes": 0, "socket_messages_accepted": 0, "last_socket_envelope_at": None}
    ingress_lock = threading.Lock()
    reconciler = DMReconciler(bot, client)

    def receive(socket_client, request):
        try:
            accepted = bot.enqueue(request.type, request.payload, fast_cancel=False)
            with ingress_lock:
                ingress["socket_envelopes"] += 1
                ingress["socket_messages_accepted"] += int(accepted)
                ingress["last_socket_envelope_at"] = time.time()
            log.info("Slack envelope: %s / %s; accepted=%s", request.type,
                     (request.payload.get("event") or {}).get("type", request.payload.get("type", "unknown")), accepted)
            socket_client.send_socket_mode_response(
                SocketModeResponse(envelope_id=request.envelope_id)
            )
        except Exception:
            # No acknowledgment on persistence failure: Slack retries the envelope.
            log.error("Could not persist incoming Slack envelope")

    socket.socket_mode_request_listeners.append(receive)

    def hello(_, message, __):
        if message.get("type") == "hello":
            with ingress_lock:
                ingress["socket_connections"] = message.get("num_connections")
            log.info("Socket Mode hello received")

    socket.message_listeners.append(hello)
    socket.on_error_listeners.append(lambda error: log.warning("Socket transport error: %s", type(error).__name__))

    def shutdown(*_):
        stop.set()

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)
    socket.connect()
    log.info("Slack listener started for workspace %s", team)

    def send(files):
        while not stop.is_set():
            item = bot.db.claim("slack_outbox", files=files)
            if item:
                delivery.deliver(item)
            else:
                stop.wait(0.4)

    senders = [threading.Thread(target=send, args=(files,), daemon=True)
               for files in (False, True)]
    for sender in senders:
        sender.start()

    def cancel_requests():
        while not stop.is_set():
            item = bot.db.claim("slack_inbox", priority=True)
            if item:
                bot.process_priority(item)
            else:
                stop.wait(0.1)

    canceller = threading.Thread(target=cancel_requests, daemon=True)
    canceller.start()

    def monitor_runs():
        while not stop.is_set():
            try:
                bot.monitor()
            except Exception:
                log.warning("Progress reconciliation will retry")
            stop.wait(3)

    monitor = threading.Thread(target=monitor_runs, daemon=True)
    monitor.start()

    def reconcile_dms():
        while not stop.is_set():
            started = time.monotonic()
            reconciler.tick()
            # Internal apps have Tier 3 history limits: at most 48 calls/minute.
            stop.wait(max(0.01, 1.25 - (time.monotonic() - started)))

    dm_reader = threading.Thread(target=reconcile_dms, daemon=True)
    dm_reader.start()
    with concurrent.futures.ThreadPoolExecutor(
        max_workers=4, thread_name_prefix="moa-slack"
    ) as pool:
        futures = []
        for c in bot.db.live():
            if c["stage"] == "coaching":
                futures.append(pool.submit(bot.coach, c))
            elif c["stage"] == "launching":
                futures.append(pool.submit(bot.resume_launch, c))
        last_monitor = 0
        while not stop.is_set():
            futures = [f for f in futures if not f.done()]
            if len(futures) < 4:
                item = bot.db.claim("slack_inbox", priority=False)
                if item:
                    futures.append(pool.submit(bot.process, item))
            if time.time() - last_monitor >= 3:
                for c in bot.db.live():
                    if c["stage"] == "launching" and len(futures) < 4:
                        futures.append(pool.submit(bot.resume_launch, c))
                status = {
                    "connected": socket.is_connected(),
                    "team": team,
                    "updated_at": time.time(),
                    "interviews_in_progress": len(futures),
                    **reconciler.status(),
                }
                with ingress_lock:
                    status.update(ingress)
                p = root / "slack-status.json"
                p.write_text(json.dumps(status))
                p.chmod(0o600)
                last_monitor = time.time()
            stop.wait(0.2)
    socket.close()
    for sender in senders:
        sender.join(timeout=3)
    canceller.join(timeout=3)
    monitor.join(timeout=3)
    dm_reader.join(timeout=3)


if __name__ == "__main__":
    main()
