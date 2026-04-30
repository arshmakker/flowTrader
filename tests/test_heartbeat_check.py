"""LIVE-24: heartbeat watchdog tests."""
import os
import sys
import time
from datetime import datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from tools import heartbeat_check
from trading_system.ops.alerts import NullAlertChannel

try:
    from zoneinfo import ZoneInfo
    IST = ZoneInfo("Asia/Kolkata")
except ImportError:
    IST = None


def _ist(hour: int, minute: int = 0, weekday_date=(2026, 4, 23)):
    """Build an IST datetime for a known trading day (2026-04-23 = Thursday)."""
    y, m, d = weekday_date
    if IST is not None:
        return datetime(y, m, d, hour, minute, tzinfo=IST)
    return datetime(y, m, d, hour, minute)


def _weekend_ist(hour: int = 10, minute: int = 0):
    # 2026-04-25 is a Saturday.
    if IST is not None:
        return datetime(2026, 4, 25, hour, minute, tzinfo=IST)
    return datetime(2026, 4, 25, hour, minute)


def _touch(path: str, now: datetime, age_seconds: float = 0):
    """Create file and set its mtime so its age relative to `now` equals age_seconds."""
    open(path, "w").close()
    target = now.timestamp() - age_seconds
    os.utime(path, (target, target))


def test_off_hours_is_silent(tmp_path):
    """Before market open: no alert, exit 0."""
    snap = tmp_path / "pnl_snapshot.json"
    # Don't even create the file — off-hours path should short-circuit.
    alerts = NullAlertChannel()
    rc = heartbeat_check.check_heartbeat(
        str(snap), alerts, stale_sec=180, now=_ist(8, 30),
    )
    assert rc == 0
    assert alerts.sent == []


def test_weekend_is_silent(tmp_path):
    """Saturday during what would be market hours: no alert."""
    snap = tmp_path / "pnl_snapshot.json"
    alerts = NullAlertChannel()
    rc = heartbeat_check.check_heartbeat(
        str(snap), alerts, stale_sec=180, now=_weekend_ist(10),
    )
    assert rc == 0
    assert alerts.sent == []


def test_fresh_snapshot_healthy(tmp_path):
    """During market hours with a freshly-written snapshot: no alert."""
    snap = tmp_path / "pnl_snapshot.json"
    now = _ist(10, 30)
    _touch(str(snap), now=now, age_seconds=10)
    alerts = NullAlertChannel()
    rc = heartbeat_check.check_heartbeat(
        str(snap), alerts, stale_sec=180, now=now,
    )
    assert rc == 0
    assert alerts.sent == []


def test_stale_snapshot_fires_alert(tmp_path):
    """Snapshot older than threshold: critical alert fires, exit 1."""
    snap = tmp_path / "pnl_snapshot.json"
    now = _ist(11, 0)
    _touch(str(snap), now=now, age_seconds=400)
    alerts = NullAlertChannel()
    rc = heartbeat_check.check_heartbeat(
        str(snap), alerts, stale_sec=180, now=now,
    )
    assert rc == 1
    assert len(alerts.sent) == 1
    assert alerts.sent[0].event == "heartbeat_stale"
    assert alerts.sent[0].severity == "critical"


def test_missing_snapshot_fires_alert(tmp_path):
    """Snapshot file doesn't exist during market hours: critical alert, exit 1."""
    snap = tmp_path / "never_written.json"
    alerts = NullAlertChannel()
    rc = heartbeat_check.check_heartbeat(
        str(snap), alerts, stale_sec=180, now=_ist(12, 0),
    )
    assert rc == 1
    assert len(alerts.sent) == 1
    assert alerts.sent[0].event == "heartbeat_missing"


def test_after_close_is_silent(tmp_path):
    """Post-3:30 PM: no alert even if snapshot is stale."""
    snap = tmp_path / "pnl_snapshot.json"
    now = _ist(16, 0)
    _touch(str(snap), now=now, age_seconds=10_000)
    alerts = NullAlertChannel()
    rc = heartbeat_check.check_heartbeat(
        str(snap), alerts, stale_sec=180, now=now,
    )
    assert rc == 0
    assert alerts.sent == []


def test_boundary_fresh_at_threshold(tmp_path):
    """Exactly-at-threshold treated as healthy; just-over as stale."""
    snap = tmp_path / "pnl_snapshot.json"
    now = _ist(11, 0)
    _touch(str(snap), now=now, age_seconds=180)
    alerts = NullAlertChannel()
    rc = heartbeat_check.check_heartbeat(
        str(snap), alerts, stale_sec=180, now=now,
    )
    # age == stale_sec → not strictly greater than → healthy
    assert rc == 0
    assert alerts.sent == []


def test_main_flushes_alert_channel_before_exit(tmp_path, monkeypatch):
    """LIVE-24: main() must call alerts.flush() before returning. Otherwise
    NtfyAlertChannel's daemon worker dies with the interpreter and the POST
    never fires — advisor-flagged regression."""
    flush_calls: list[float] = []

    class TrackingChannel:
        def __init__(self):
            self.sent = []
        def send(self, alert):
            self.sent.append(alert)
            return True
        def flush(self, timeout=5.0):
            flush_calls.append(timeout)
            return True

    chan = TrackingChannel()
    monkeypatch.setattr(heartbeat_check, "build_channel", lambda **_: chan)

    snap = tmp_path / "missing.json"  # force an alert path so send is called
    # Patch is_market_hours to force market-hours branch so the alert actually fires.
    monkeypatch.setattr(heartbeat_check, "is_market_hours", lambda now=None: True)

    rc = heartbeat_check.main(["--snapshot", str(snap)])
    assert rc == 1
    assert len(chan.sent) == 1
    assert chan.sent[0].event == "heartbeat_missing"
    # Flush must have been called exactly once after check_heartbeat.
    assert len(flush_calls) == 1
