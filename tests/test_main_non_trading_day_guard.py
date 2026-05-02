"""Regression: run() must short-circuit before OAuth on a non-trading day.

Failure mode this prevents: starting the system on a weekend/holiday triggers
OAuth, which validates the cached token via Shoonya endpoints that return 502
during weekend maintenance. The system interprets 502 as "token expired",
falls into the unattended re-auth flow, and crashes at input() because no TTY
is present. The guard exits cleanly before any of that happens.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import main


def test_run_exits_before_oauth_on_non_trading_day(monkeypatch):
    monkeypatch.setattr(main, "_acquire_pid_lock", lambda: None)
    monkeypatch.setattr(main, "_require_live_ack", lambda: None)
    monkeypatch.setattr(main, "is_trading_day_ist", lambda _now: False)

    oauth_called = []
    monkeypatch.setattr(
        main,
        "initialize_api",
        lambda *a, **kw: oauth_called.append(True),
    )

    main.run()

    assert oauth_called == [], "initialize_api must not be called on a non-trading day"


def test_run_proceeds_past_guard_on_a_trading_day(monkeypatch):
    """Symmetric check: the guard must not fire on a weekday/non-holiday."""
    monkeypatch.setattr(main, "_acquire_pid_lock", lambda: None)
    monkeypatch.setattr(main, "_require_live_ack", lambda: None)
    monkeypatch.setattr(main, "is_trading_day_ist", lambda _now: True)

    class _ProceededPastGuard(Exception):
        pass

    def _raise(*_a, **_kw):
        raise _ProceededPastGuard

    monkeypatch.setattr(main, "initialize_api", _raise)

    try:
        main.run()
    except _ProceededPastGuard:
        return
    raise AssertionError("run() did not reach initialize_api on a trading day")
