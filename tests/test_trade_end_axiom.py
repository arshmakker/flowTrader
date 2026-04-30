"""Regression: pin TRADE_END and TRADE_END_EXPIRY close times.

TRADE_END = 15:10 — hard close for all non-expiring positions (20 min before 15:30).
TRADE_END_EXPIRY = 15:00 — early close for positions expiring today (avoids the
final-30-min settlement squeeze on expiry day).
"""
from __future__ import annotations

from datetime import datetime

from trading_system.config import settings


def test_trade_end_is_15_10():
    assert settings.TRADE_END == "15:10"


def test_trade_end_expiry_is_15_00():
    assert settings.TRADE_END_EXPIRY == "15:00"


def test_trade_end_parseable_as_time():
    """Must be a valid HH:MM string; main.py uses strptime on it every loop."""
    t = datetime.strptime(settings.TRADE_END, "%H:%M").time()
    assert t.hour == 15
    assert t.minute == 10


def test_trade_end_expiry_parseable_as_time():
    t = datetime.strptime(settings.TRADE_END_EXPIRY, "%H:%M").time()
    assert t.hour == 15
    assert t.minute == 0


def test_trade_end_leaves_buffer_before_market_close():
    """Must be strictly before 15:30 IST market close with at least 15 min of
    slack to retry a rollback on partial-fill."""
    close = datetime.strptime("15:30", "%H:%M").time()
    trade_end = datetime.strptime(settings.TRADE_END, "%H:%M").time()
    assert trade_end < close
    close_dt = datetime.combine(datetime.today(), close)
    end_dt = datetime.combine(datetime.today(), trade_end)
    assert (close_dt - end_dt).total_seconds() >= 15 * 60


def test_trade_end_expiry_before_trade_end():
    """Expiry-day close must fire before the general EOD close."""
    expiry_t = datetime.strptime(settings.TRADE_END_EXPIRY, "%H:%M").time()
    end_t = datetime.strptime(settings.TRADE_END, "%H:%M").time()
    assert expiry_t < end_t
