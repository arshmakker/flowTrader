"""Fix #4 regression — overnight-DTE block.

Closes the loop that let the 2026-04-17 NIFTY 21APR IC carry Fri→Mon into a
DTE=1 position. Entry-gate DTE check ran only at entry (Thursday, DTE=5);
after that, nothing re-evaluated. This fix re-checks at every TRADE_END and
force-flattens if next-session DTE < IC_DTE_THRESHOLD.
"""

import os
import sys
from datetime import date

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import main
from trading_system.config import settings


class _FakePosition:
    def __init__(self, expiry_date: str):
        self.expiry_date = expiry_date


class _FakeStrategy:
    def __init__(self, instrument, expiry_date, active=True):
        self.instrument = instrument
        self._position = _FakePosition(expiry_date) if expiry_date is not None else None
        self._active = active

    def is_active(self):
        return self._active


def test_next_trading_session_skips_weekend():
    """Friday → Monday (2026-04-20 is a Monday, no holiday)."""
    friday = date(2026, 4, 17)
    assert main._next_trading_session_date(friday) == date(2026, 4, 20)


def test_next_trading_session_skips_weekend_and_holiday():
    """Thursday 2026-04-02 → Monday 2026-04-06 (Good Friday 2026-04-03)."""
    thu = date(2026, 4, 2)
    assert main._next_trading_session_date(thu) == date(2026, 4, 6)


def test_next_trading_session_mid_week():
    """Mon → Tue, no holiday in between."""
    mon = date(2026, 4, 20)
    assert main._next_trading_session_date(mon) == date(2026, 4, 21)


def test_near_dte_flags_friday_to_monday_weekly():
    """The exact incident: Fri 2026-04-17 TRADE_END with a Tue 2026-04-21 IC.
    Next session = Mon 2026-04-20, DTE at Mon = 1, threshold = 3 → flag."""
    strats = [_FakeStrategy("NIFTY", "2026-04-21")]
    next_session = date(2026, 4, 20)
    flagged = main._find_near_dte_at_next_session(strats, next_session, settings.IC_DTE_THRESHOLD)
    assert len(flagged) == 1
    assert flagged[0].instrument == "NIFTY"


def test_near_dte_allows_thursday_to_friday_weekly():
    """Thu 2026-04-16 TRADE_END, Tue 2026-04-21 IC. Next session = Fri 4/17,
    DTE at Fri = 4 — above threshold 3. Position allowed to carry Thu→Fri."""
    strats = [_FakeStrategy("NIFTY", "2026-04-21")]
    next_session = date(2026, 4, 17)
    flagged = main._find_near_dte_at_next_session(strats, next_session, settings.IC_DTE_THRESHOLD)
    assert flagged == []


def test_near_dte_allows_monthly_on_friday():
    """Fri 2026-04-17 TRADE_END, 28APR26 monthly IC. Next session = Mon 4/20,
    DTE = 8 — well above threshold. No flag."""
    strats = [_FakeStrategy("NIFTY", "2026-04-28")]
    next_session = date(2026, 4, 20)
    flagged = main._find_near_dte_at_next_session(strats, next_session, settings.IC_DTE_THRESHOLD)
    assert flagged == []


def test_near_dte_skips_inactive_strategy():
    """Inactive strategy must not be flagged regardless of expiry."""
    strats = [_FakeStrategy("NIFTY", "2026-04-21", active=False)]
    next_session = date(2026, 4, 20)
    flagged = main._find_near_dte_at_next_session(strats, next_session, settings.IC_DTE_THRESHOLD)
    assert flagged == []


def test_near_dte_skips_strategy_without_position():
    """Active strategy with no _position must not blow up or flag."""
    strats = [_FakeStrategy("NIFTY", None)]
    next_session = date(2026, 4, 20)
    flagged = main._find_near_dte_at_next_session(strats, next_session, settings.IC_DTE_THRESHOLD)
    assert flagged == []


def test_near_dte_skips_blank_expiry_date():
    """Position loaded from pre-field-existed state has empty expiry_date;
    don't flag (avoids false positives that would spuriously close positions)."""
    strats = [_FakeStrategy("NIFTY", "")]
    next_session = date(2026, 4, 20)
    flagged = main._find_near_dte_at_next_session(strats, next_session, settings.IC_DTE_THRESHOLD)
    assert flagged == []


def test_near_dte_flags_expiry_in_past():
    """Expiry strictly before next session = DTE negative < threshold → flag.
    Overlap with _find_past_expiry is fine; both paths force-exit and the
    force-exit is idempotent."""
    strats = [_FakeStrategy("NIFTY", "2026-04-10")]  # well past 2026-04-20
    next_session = date(2026, 4, 20)
    flagged = main._find_near_dte_at_next_session(strats, next_session, settings.IC_DTE_THRESHOLD)
    assert len(flagged) == 1


def test_near_dte_flags_exactly_at_threshold_minus_one():
    """Threshold=3, DTE=2 → flag (strict less-than)."""
    # Session=4/19 (Sunday, but we're testing the math), expiry=4/21. DTE=2.
    strats = [_FakeStrategy("NIFTY", "2026-04-21")]
    flagged = main._find_near_dte_at_next_session(strats, date(2026, 4, 19), 3)
    assert len(flagged) == 1


def test_near_dte_allows_dte_equal_to_threshold():
    """DTE=3, threshold=3 → not flagged (matches entry-gate semantics in
    expiry_manager.py: `if dte < threshold`)."""
    # Session=4/18, expiry=4/21. DTE=3.
    strats = [_FakeStrategy("NIFTY", "2026-04-21")]
    flagged = main._find_near_dte_at_next_session(strats, date(2026, 4, 18), 3)
    assert flagged == []
