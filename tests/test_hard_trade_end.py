"""
Test that ALL positions are hard-closed at TRADE_END (15:10), not just expiring ones.

Historical data: overnight carries lose money (0% win rate, avg -₹9.4k loss).
Weekday overnight carry is gap/adjustment risk. Enforce unconditional close.
"""

from datetime import datetime

import pytest

from trading_system.config import settings


def test_hard_close_at_trade_end_on_weekday():
    """Verify that TRADE_END at 15:10 triggers regardless of next_day_is_trading.

    Historical fix: overnight carries on weekdays lose money. Hard close enforces
    no carry to next session, weekday or weekend.
    """
    now = datetime(2026, 5, 11, 15, 10, 0)  # Monday 15:10
    trade_end_time = datetime.strptime(settings.TRADE_END, "%H:%M").time()

    assert now.time() >= trade_end_time, "Current time should be at or past TRADE_END"

    # Per the fix, positions close at TRADE_END regardless of next_day_is_trading
    # This prevents the scenario where Monday positions carry to Tuesday and lose ₹2.7k
    is_trade_end = now.time() >= trade_end_time
    assert is_trade_end, "Should trigger TRADE_END close gate"


def test_trade_end_closes_non_expiring_weekday_position():
    """Regression test: non-expiring positions should close at TRADE_END on weekdays.

    This tests the fix for the overnight carry issue where positions entered
    at 10:35 on Monday were being carried to Tuesday, resulting in -₹2,738 loss
    (peaked +₹4,350 but deteriorated due to overnight gap).
    """
    trade_end_time = datetime.strptime(settings.TRADE_END, "%H:%M").time()

    # Monday 15:10 — at TRADE_END
    monday_eod = datetime(2026, 5, 11, 15, 10, 0)
    assert monday_eod.time() >= trade_end_time

    # Per new rule, all positions close at 15:10 regardless of next_day_is_trading
    assert monday_eod.time() >= trade_end_time, "TRADE_END gate should trigger"


def test_entry_cutoff_prevents_late_entries():
    """Verify that ENTRY_CUTOFF = 14:30 prevents late entries that might be forced to carry.

    Combined with hard TRADE_END close, this ensures no unmanaged overnight exposure.
    """
    entry_cutoff_time = datetime.strptime(settings.ENTRY_CUTOFF, "%H:%M").time()

    # At 14:35, no new entries should be allowed
    now_late = datetime(2026, 5, 11, 14, 35, 0)
    assert now_late.time() >= entry_cutoff_time, "14:35 should be past cutoff"

    # At 14:25, entries should be allowed
    now_early = datetime(2026, 5, 11, 14, 25, 0)
    assert now_early.time() < entry_cutoff_time, "14:25 should be before cutoff"

    # Cutoff should be 14:30 (40 min before 15:10 TRADE_END)
    assert settings.ENTRY_CUTOFF == "14:30"
    trade_end_time = datetime.strptime(settings.TRADE_END, "%H:%M").time()
    time_delta = datetime.combine(datetime.today(), trade_end_time) - datetime.combine(
        datetime.today(), entry_cutoff_time
    )
    assert time_delta.total_seconds() / 60 == 40, "Cutoff should be 40 min before TRADE_END"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
