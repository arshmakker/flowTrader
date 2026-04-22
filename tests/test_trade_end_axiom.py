"""Fix #3 regression: pin TRADE_END to 14:15 per CLAUDE.md.

CLAUDE.md encodes "Hard close — All positions closed at 14:15, system shutdown
by 15:30" as a project axiom. This test pins the setting so future edits that
drift the value (e.g., back to 15:10) must update the axiom doc intentionally.
"""
from __future__ import annotations

from datetime import datetime

from trading_system.config import settings


def test_trade_end_is_14_15():
    assert settings.TRADE_END == "14:15"


def test_trade_end_parseable_as_time():
    """Must be a valid HH:MM string; main.py uses strptime on it every loop."""
    t = datetime.strptime(settings.TRADE_END, "%H:%M").time()
    assert t.hour == 14
    assert t.minute == 15


def test_trade_end_leaves_buffer_before_market_close():
    """Must be strictly before 15:30 IST market close — otherwise hard-close
    has no time to place + confirm 4-leg exit + rollback any partial fills."""
    close = datetime.strptime("15:30", "%H:%M").time()
    trade_end = datetime.strptime(settings.TRADE_END, "%H:%M").time()
    assert trade_end < close
    # At least 15 min of slack to retry a rollback on partial-fill.
    close_dt = datetime.combine(datetime.today(), close)
    end_dt = datetime.combine(datetime.today(), trade_end)
    assert (close_dt - end_dt).total_seconds() >= 15 * 60
