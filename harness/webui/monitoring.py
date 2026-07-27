"""Sentry-backed provider health monitoring for the local Web UI."""

from __future__ import annotations

import logging
import os
import socket
import threading
from collections.abc import Callable
from typing import Any


LOGGER = logging.getLogger(__name__)
DEFAULT_INTERVAL_SECONDS = 60 * 60


class ProviderHealthError(RuntimeError):
    """An installed provider CLI cannot currently serve its configured routes."""


class OperationalError(RuntimeError):
    """A handled operational failure that should still create a Sentry issue."""


def configure_sentry() -> bool:
    """Initialize Sentry when a DSN is configured.

    The SDK remains optional for source checkouts that only run the offline
    harness. Production Web UI installs include it through requirements-web.
    """
    dsn = os.environ.get("MOA_SENTRY_DSN") or os.environ.get("SENTRY_DSN")
    if not dsn:
        return False
    try:
        import sentry_sdk
    except ImportError:
        LOGGER.error(
            "MOA_SENTRY_DSN is set but sentry-sdk is not installed; "
            "install requirements-web.txt"
        )
        return False

    sentry_sdk.init(
        dsn=dsn,
        environment=os.environ.get("MOA_SENTRY_ENVIRONMENT", "local"),
        send_default_pii=False,
        include_local_variables=False,
        max_request_body_size="never",
        traces_sample_rate=0.0,
    )
    return True


def capture_operational_error(
    error: BaseException | str,
    *,
    operation: str,
    context: dict[str, Any] | None = None,
) -> bool:
    """Capture a handled infrastructure failure without user payloads."""
    try:
        import sentry_sdk
    except ImportError:
        return False

    exception = error if isinstance(error, BaseException) else OperationalError(error)
    with sentry_sdk.new_scope() as scope:
        scope.fingerprint = ["moa-x-operation", operation]
        scope.set_tag("component", "webui-operations")
        scope.set_tag("operation", operation)
        if context:
            for key in ("exit_code", "phase", "provider"):
                if key in context:
                    scope.set_tag(key, str(context[key]))
            scope.set_context("operation", context)
        event_id = sentry_sdk.capture_exception(exception)
    return event_id is not None


def capture_provider_issue(provider: dict[str, Any]) -> bool:
    """Send one provider failure event, grouped by provider id."""
    try:
        import sentry_sdk
    except ImportError:
        return False

    provider_id = str(provider.get("id") or "unknown")
    label = str(provider.get("label") or provider_id)
    detail = str(provider.get("detail") or "health probe failed")
    error = ProviderHealthError(f"{label} provider unhealthy: {detail}")
    with sentry_sdk.new_scope() as scope:
        scope.fingerprint = ["moa-x-provider-health", provider_id]
        scope.set_tag("component", "provider-health")
        scope.set_tag("provider", provider_id)
        scope.set_tag("provider_status", str(provider.get("status") or "unknown"))
        scope.set_context(
            "provider_health",
            {
                "authenticated": bool(provider.get("authenticated")),
                "binary": provider.get("binary"),
                "binary_path": provider.get("binary_path"),
                "detail": detail,
                "installed": bool(provider.get("installed")),
                "last_checked": provider.get("last_checked"),
                "version": provider.get("version"),
            },
        )
        event_id = sentry_sdk.capture_exception(error)
    return event_id is not None


class ProviderHealthMonitor:
    """Probe provider accounts now and at a fixed interval.

    Failures are emitted only on a healthy-to-unhealthy transition within one
    process. Sentry's fingerprint groups repeats across process restarts.
    """

    def __init__(
        self,
        probe: Callable[[], list[dict[str, Any]]],
        *,
        interval_seconds: float = DEFAULT_INTERVAL_SECONDS,
        capture: Callable[[dict[str, Any]], bool] = capture_provider_issue,
    ) -> None:
        self._probe = probe
        self._capture = capture
        self._interval_seconds = max(1.0, float(interval_seconds))
        self._unhealthy: set[str] = set()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(
            target=self._run,
            name="moa-provider-health",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2)

    def run_once(self) -> list[dict[str, Any]]:
        providers = self._probe()
        current = {
            str(provider["id"])
            for provider in providers
            if provider.get("status") != "ready"
        }
        reported = self._unhealthy & current
        for provider in providers:
            provider_id = str(provider["id"])
            if provider_id not in current or provider_id in self._unhealthy:
                continue
            if self._capture(provider):
                reported.add(provider_id)
                LOGGER.error(
                    "provider health alert sent: %s: %s",
                    provider_id,
                    provider.get("detail"),
                )
        recovered = self._unhealthy - current
        for provider_id in sorted(recovered):
            LOGGER.info("provider recovered: %s", provider_id)
        # A disabled or unavailable Sentry client returns False. Do not mark
        # that failure as reported; the next hourly pass should retry it.
        self._unhealthy = reported
        return providers

    def _run(self) -> None:
        LOGGER.info(
            "provider health monitor started on %s (interval %.0fs)",
            socket.gethostname(),
            self._interval_seconds,
        )
        while not self._stop.is_set():
            try:
                self.run_once()
            except Exception as exc:
                LOGGER.exception("provider health monitor probe failed")
                capture_operational_error(
                    exc,
                    operation="provider_monitor.probe",
                )
            if self._stop.wait(self._interval_seconds):
                break
