"""Fix #5 regression tests — shutdown-path hygiene.

Pins the two guards introduced to prevent silent restore after an abnormal
shutdown that crossed a non-trading day (the 2026-04-17 → 2026-04-20 incident
where a DNS-triggered Ctrl-C bypassed the in-loop weekend-flatten gate and
left a NIFTY 21APR26 IC stranded into max loss).
"""

import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import main


def test_crossed_non_trading_day_detects_weekend():
    """Fri 13:48 → Mon 09:23 must trip the guard (Saturday is in the span)."""
    # 2026-04-17 is Friday, 2026-04-20 is Monday.
    saved_at = "2026-04-17T13:48:16"
    now = datetime(2026, 4, 20, 9, 23, 0)
    assert main._crossed_non_trading_day(saved_at, now) is True


def test_crossed_non_trading_day_same_day_clean():
    """Same-day shutdown and restart within a trading day does not trip."""
    saved_at = "2026-04-20T10:00:00"
    now = datetime(2026, 4, 20, 11, 0, 0)
    # 2026-04-20 is a Monday with no holiday configured, so purely within a
    # trading day → guard should stay quiet.
    assert main._crossed_non_trading_day(saved_at, now) is False


def test_crossed_non_trading_day_two_adjacent_trading_days():
    """Thu → Fri shutdown/restart (both trading) must not trip."""
    saved_at = "2026-04-16T18:00:00"  # Thursday evening
    now = datetime(2026, 4, 17, 9, 0, 0)  # Friday morning
    assert main._crossed_non_trading_day(saved_at, now) is False


def test_crossed_non_trading_day_detects_holiday():
    """A configured IST holiday in the span must trip the guard."""
    # 2026-04-03 = Good Friday (in TRADING_HOLIDAYS_IST).
    # 2026-04-02 (Thu) → 2026-04-06 (Mon) spans Good Friday + weekend.
    saved_at = "2026-04-02T15:30:00"
    now = datetime(2026, 4, 6, 9, 30, 0)
    assert main._crossed_non_trading_day(saved_at, now) is True


def test_crossed_non_trading_day_blank_saved_at():
    """Empty saved_at (first-ever run, no prior state) must not trip."""
    assert main._crossed_non_trading_day("", datetime(2026, 4, 20)) is False


def test_crossed_non_trading_day_malformed_iso():
    """Garbage saved_at must not crash — treat as no-trip."""
    assert main._crossed_non_trading_day("not-an-iso-date", datetime(2026, 4, 20)) is False


def test_crossed_non_trading_day_clock_skew_future_saved_at():
    """If saved_at is after now (impossible but defensively), don't trip."""
    saved_at = "2026-04-25T10:00:00"
    now = datetime(2026, 4, 20, 10, 0, 0)
    assert main._crossed_non_trading_day(saved_at, now) is False
