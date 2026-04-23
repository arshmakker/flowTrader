"""
LIVE-23: Operator alert channel.

Pluggable alert dispatching with a background sender thread, per-event
dedup, and a factory driven by settings + cred.yml. Callers (risk
manager, main loop) never block on network I/O — the hot-path cost of
``send`` is a queue ``put_nowait`` guarded by a dedup check.

Channels:
- ``NtfyAlertChannel`` — posts to an ntfy.sh topic URL via a daemon worker thread.
- ``LogAlertChannel`` — writes to the Python logger (default fallback).
- ``NullAlertChannel`` — no-op; records sends for test inspection.

Future channels (Telegram, webhook, email) implement the same ``AlertChannel``
protocol and slot in via ``build_channel``.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from dataclasses import dataclass
from typing import Optional, Protocol

import requests

logger = logging.getLogger(__name__)

_DEDUP_WINDOW_SEC = 60.0
_SEND_TIMEOUT_SEC = 5.0
_QUEUE_MAX = 256


@dataclass(frozen=True)
class Alert:
    event: str         # stable logical key, e.g. "combined_stop"
    severity: str      # "critical" | "warning" | "info"
    title: str
    body: str


class AlertChannel(Protocol):
    def send(self, alert: Alert) -> bool: ...
    def flush(self, timeout: float = _SEND_TIMEOUT_SEC) -> bool: ...


class NullAlertChannel:
    """Default channel — records sent alerts for test inspection, ships nothing."""

    def __init__(self) -> None:
        self.sent: list[Alert] = []

    def send(self, alert: Alert) -> bool:
        self.sent.append(alert)
        return True

    def flush(self, timeout: float = _SEND_TIMEOUT_SEC) -> bool:  # noqa: ARG002
        return True


class LogAlertChannel:
    """Writes alerts to the process logger. Used when alerts are enabled but no external channel is configured."""

    _LEVEL = {
        "critical": logging.CRITICAL,
        "warning": logging.WARNING,
        "info": logging.INFO,
    }

    def send(self, alert: Alert) -> bool:
        logger.log(
            self._LEVEL.get(alert.severity, logging.INFO),
            "[ALERT:%s] %s — %s",
            alert.event, alert.title, alert.body,
        )
        return True

    def flush(self, timeout: float = _SEND_TIMEOUT_SEC) -> bool:  # noqa: ARG002
        return True


class NtfyAlertChannel:
    """
    Ships alerts to an ntfy.sh topic. A daemon worker thread drains a bounded
    queue so the caller never blocks on HTTP.

    Dedup: repeated sends with the same ``event`` within ``_DEDUP_WINDOW_SEC``
    are dropped to prevent alert floods on ticks-per-minute failure loops.

    The background thread dies with the process; no explicit shutdown needed.
    """

    def __init__(self, topic_url: str, timeout: float = _SEND_TIMEOUT_SEC) -> None:
        if not topic_url:
            raise ValueError("ntfy topic_url is required")
        self.topic_url = topic_url.rstrip("/")
        self.timeout = timeout
        self._q: queue.Queue[Alert] = queue.Queue(maxsize=_QUEUE_MAX)
        self._dedup: dict[str, float] = {}
        self._dedup_lock = threading.Lock()
        self._worker = threading.Thread(
            target=self._run, name="ntfy-alert-sender", daemon=True,
        )
        self._worker.start()

    def send(self, alert: Alert) -> bool:
        now = time.monotonic()
        with self._dedup_lock:
            last = self._dedup.get(alert.event, 0.0)
            if now - last < _DEDUP_WINDOW_SEC:
                return False
            self._dedup[alert.event] = now
        try:
            self._q.put_nowait(alert)
            return True
        except queue.Full:
            logger.warning("ntfy alert queue full; dropping event=%s", alert.event)
            return False

    def _run(self) -> None:
        while True:
            alert = self._q.get()
            try:
                self._post(alert)
            finally:
                self._q.task_done()

    def flush(self, timeout: float = _SEND_TIMEOUT_SEC) -> bool:
        """Block until all queued alerts have POSTed or `timeout` elapses.
        Required for short-lived scripts (e.g. heartbeat_check.py) where the
        daemon worker thread would otherwise die with the interpreter before
        draining the queue. Returns True if drained within the timeout."""
        deadline = time.monotonic() + timeout
        # unfinished_tasks covers both queued and in-flight work — decrements
        # only after the worker calls task_done() at the end of each POST.
        while self._q.unfinished_tasks > 0:
            if time.monotonic() >= deadline:
                logger.warning("ntfy flush timed out with %d alerts still in-flight", self._q.unfinished_tasks)
                return False
            time.sleep(0.05)
        return True

    def _post(self, alert: Alert) -> None:
        headers = {"Title": alert.title}
        if alert.severity == "critical":
            headers["Priority"] = "urgent"
            headers["Tags"] = "rotating_light"
        elif alert.severity == "warning":
            headers["Priority"] = "high"
            headers["Tags"] = "warning"
        try:
            r = requests.post(
                self.topic_url,
                data=alert.body.encode("utf-8"),
                headers=headers,
                timeout=self.timeout,
            )
            r.raise_for_status()
        except Exception:
            logger.warning(
                "ntfy alert post failed event=%s",
                alert.event, exc_info=True,
            )


def build_channel(
    enabled: bool,
    channel_type: str,
    ntfy_topic_url: Optional[str] = None,
) -> AlertChannel:
    """Factory called from main.py at startup. Safe defaults: returns
    ``NullAlertChannel`` whenever the input doesn't describe a concrete channel."""
    if not enabled:
        return NullAlertChannel()
    if channel_type == "ntfy":
        if not ntfy_topic_url:
            logger.warning(
                "alerts channel='ntfy' but ntfy_topic_url is empty; falling back to log"
            )
            return LogAlertChannel()
        return NtfyAlertChannel(ntfy_topic_url)
    if channel_type == "log":
        return LogAlertChannel()
    logger.warning("unknown alerts channel_type=%s; falling back to null", channel_type)
    return NullAlertChannel()
