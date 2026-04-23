"""
LIVE-24: external heartbeat / silent-death detection.

Runs periodically (via launchd, every 5 min by default). During market hours,
verifies that ``data/pnl_snapshot.json`` has been touched recently by the
main trading loop. If stale, fires an operator alert through the LIVE-23
alert channel.

Exit codes:
    0  healthy, or outside market hours (silent skip)
    1  heartbeat stale, alert fired
    2  configuration / IO error (distinct from stale)

Designed to be import-safe for unit tests — the heavy work is in
``check_heartbeat``; ``main`` is thin glue.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import datetime
from typing import Optional

# Ensure project root is importable when launchd invokes this by absolute path.
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from trading_system.ops.alerts import Alert, AlertChannel, build_channel
from trading_system.config import settings
from strategy_runner import is_market_hours, get_now_ist

DEFAULT_STALE_SEC = 180  # 3 minutes — main loop writes the snapshot every cycle (<60s)


def _snapshot_age_seconds(path: str, now: datetime) -> Optional[float]:
    """Age of file in seconds, or None if missing."""
    if not os.path.exists(path):
        return None
    mtime = datetime.fromtimestamp(os.path.getmtime(path), tz=now.tzinfo)
    return (now - mtime).total_seconds()


def _load_ntfy_url_from_cred() -> Optional[str]:
    """Best-effort read of cred.yml to get the ntfy topic URL.
    Kept local so this script has no runtime dependency on main.py's plumbing."""
    cred_path = os.path.join(_REPO_ROOT, "cred.yml")
    if not os.path.exists(cred_path):
        return None
    try:
        import yaml
        with open(cred_path) as f:
            data = yaml.safe_load(f) or {}
        url = data.get("ALERTS_NTFY_TOPIC_URL")
        return str(url).strip() if url else None
    except Exception:
        return None


def check_heartbeat(
    snapshot_path: str,
    alerts: AlertChannel,
    stale_sec: int = DEFAULT_STALE_SEC,
    now: Optional[datetime] = None,
) -> int:
    """Core heartbeat check. Returns an exit code (0 healthy, 1 stale)."""
    now = now or get_now_ist()

    if not is_market_hours(now):
        return 0  # off-hours silent skip

    age = _snapshot_age_seconds(snapshot_path, now)
    if age is None:
        alerts.send(Alert(
            event="heartbeat_missing",
            severity="critical",
            title="RegimeTrader heartbeat missing",
            body=f"pnl_snapshot.json not found at {snapshot_path} during market hours.",
        ))
        return 1

    if age > stale_sec:
        alerts.send(Alert(
            event="heartbeat_stale",
            severity="critical",
            title="RegimeTrader heartbeat stale",
            body=(
                f"pnl_snapshot.json last updated {age:.0f}s ago "
                f"(threshold {stale_sec}s). Main loop may be dead."
            ),
        ))
        return 1

    return 0


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="RegimeTrader heartbeat watchdog")
    parser.add_argument(
        "--stale-sec", type=int, default=DEFAULT_STALE_SEC,
        help=f"Staleness threshold in seconds (default {DEFAULT_STALE_SEC}).",
    )
    parser.add_argument(
        "--snapshot", default=os.path.join(_REPO_ROOT, settings.DATA_DIR, "pnl_snapshot.json"),
        help="Path to pnl_snapshot.json.",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s [heartbeat] %(message)s",
    )

    try:
        alerts = build_channel(
            enabled=settings.ALERTS_ENABLED,
            channel_type=settings.ALERTS_CHANNEL,
            ntfy_topic_url=_load_ntfy_url_from_cred(),
        )
    except Exception:
        logging.exception("failed to build alert channel")
        return 2

    rc = check_heartbeat(args.snapshot, alerts, stale_sec=args.stale_sec)
    # Short-lived script: drain any queued async alerts before exit, otherwise
    # the daemon worker (e.g. NtfyAlertChannel) dies with the interpreter and
    # the POST never fires. See trading_system/ops/alerts.py::NtfyAlertChannel.flush.
    alerts.flush()
    return rc


if __name__ == "__main__":
    sys.exit(main())
