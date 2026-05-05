"""tests/test_ic_lifecycle.py — BUG-03 and BUG-04 regressions (tracker unwind on exit and rollback)."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from trading_system.config import settings
from trading_system.core.iron_condor import IronCondorStrategy
from trading_system.paper.paper_order_manager import PaperOrderManager
from trading_system.paper.paper_position_tracker import PaperPositionTracker


@pytest.fixture(autouse=True)
def _force_sequential_entry(monkeypatch):
    """Lifecycle tests cover the legacy sequential entry path. Pin it so they
    keep exercising sequential regardless of the branch-level default."""
    monkeypatch.setattr(settings, "IC_ENTRY_MODE", "sequential")


class FakeMD:
    """Minimal MarketData double: returns fixed LTPs keyed by symbol."""

    def __init__(self, prices):
        self._prices = prices

    def get_ltp(self, sym):
        return self._prices.get(sym, 10.0)

    def get_lot_size(self, sym):
        return 65


class FakeSR:
    """No-op SR manager — returns strikes unchanged."""

    def apply_buffer(self, strike, sr_high, sr_low, opt_type, step):
        return strike


def _prices_for_22000():
    """With spot=22000, vix=12 (low tier: OTM=150, width=50): strikes 22150/21850/22200/21800."""
    return {
        "NFO|NIFTY19MAR26C22150": 18.0,  # sc — SELL
        "NFO|NIFTY19MAR26P21850": 18.0,  # sp — SELL
        "NFO|NIFTY19MAR26C22200": 5.0,  # lc — BUY
        "NFO|NIFTY19MAR26P21800": 5.0,  # lp — BUY
    }


def _make_strategy():
    md = FakeMD(_prices_for_22000())
    tracker = PaperPositionTracker()
    om = PaperOrderManager(md, tracker)
    strat = IronCondorStrategy(om, md, instrument="NIFTY")
    return strat, tracker, om


def test_tracker_unwinds_after_exit():
    """BUG-03: IC.exit() must remove the 4 legs from the tracker."""
    strat, tracker, _ = _make_strategy()
    ok = strat.enter(22000, 12, 22500, 21500, FakeSR(), "19-MAR-2026", lots=1)
    assert ok is True
    assert len(tracker._positions) == 4, f"expected 4 legs tracked after entry, got {tracker._positions}"
    strat.exit("PROFIT_HARVEST", 100.0)
    assert len(tracker._positions) == 0, f"tracker must be empty after exit, got {tracker._positions}"


def test_tracker_unwinds_after_force_exit():
    """BUG-03: force_exit() → exit() path must also unwind the tracker."""
    strat, tracker, _ = _make_strategy()
    strat.enter(22000, 12, 22500, 21500, FakeSR(), "19-MAR-2026", lots=1)
    assert len(tracker._positions) == 4
    result = strat.force_exit()
    assert result is not None
    assert len(tracker._positions) == 0


def test_tracker_unwinds_after_rollback():
    """BUG-04: rollback on mid-entry leg rejection must unwind the tracker."""
    strat, tracker, om = _make_strategy()

    # Inject a leg-3 rejection: the 3rd call to place_order returns REJECTED.
    real_place_order = om.place_order
    call_count = {"n": 0}

    def flaky_place_order(symbol, side, qty, **kwargs):
        call_count["n"] += 1
        if call_count["n"] == 3:
            return {
                "order_id": "FAIL_LEG3",
                "symbol": symbol,
                "side": side,
                "quantity": qty,
                "fill_price": 0.0,
                "stt": 0.0,
                "brokerage": 0.0,
                "status": "REJECTED",
                "reason": "simulated-leg3-reject",
                "paper": True,
            }
        return real_place_order(symbol, side, qty, **kwargs)

    om.place_order = flaky_place_order

    ok = strat.enter(22000, 12, 22500, 21500, FakeSR(), "19-MAR-2026", lots=1)
    assert ok is False
    assert strat._position is None
    assert len(tracker._positions) == 0, f"rollback must clean tracker, got {tracker._positions}"


def test_entry_sets_expiry_date_iso():
    """BUG-18: IC_Position.expiry_date must be set to the ISO date parsed from the DD-MMM-YYYY expiry string."""
    strat, _, _ = _make_strategy()
    ok = strat.enter(22000, 12, 22500, 21500, FakeSR(), "19-MAR-2026", lots=1)
    assert ok is True
    assert strat._position is not None
    assert strat._position.expiry_date == "2026-03-19"


def test_exit_uses_realised_pnl_from_tracker():
    """Regression: exit() must use realised PnL from tracker.close_position(),
    not the pre-calculated unrealized pnl passed in. This ensures transaction
    costs are deducted from the recorded PnL."""
    md = FakeMD(_prices_for_22000())
    tracker = PaperPositionTracker()
    om = PaperOrderManager(md, tracker)
    strat = IronCondorStrategy(om, md, instrument="NIFTY")

    ok = strat.enter(22000, 12, 22500, 21500, FakeSR(), "19-MAR-2026", lots=1)
    assert ok is True

    mock_close_returns = [100.0, 150.0, -20.0, -30.0]
    close_call_idx = {"n": 0}

    # _original_close = tracker.close_position  # noqa: F841

    def mock_close(symbol, price):
        idx = close_call_idx["n"]
        close_call_idx["n"] += 1
        return mock_close_returns[idx]

    tracker.close_position = mock_close

    unrealised_estimate = 500.0
    result = strat.exit("PROFIT_HARVEST", unrealised_estimate)

    assert result["pnl"] == 200.0, f"expected 200 (sum of [100,150,-20,-30]), got {result['pnl']}"
    assert result["pnl"] != unrealised_estimate, "must not use the unrealised estimate"


def test_rollback_failure_records_stuck_legs():
    """BUG-05: when a rollback reverse order itself fails, stuck legs must be
    recorded on the strategy so main.py can escalate the halt."""
    strat, tracker, om = _make_strategy()

    # Leg 3 rejects the entry AND every subsequent call (rollbacks) also rejects.
    call_count = {"n": 0}

    def always_after_2_fails(symbol, side, qty, **kwargs):
        call_count["n"] += 1
        if call_count["n"] <= 2:
            # Let the first two entry legs fill via the real path.
            return real_place_order(symbol, side, qty, **kwargs)
        return {
            "order_id": f"FAIL_{call_count['n']}",
            "symbol": symbol,
            "side": side,
            "quantity": qty,
            "fill_price": 0.0,
            "stt": 0.0,
            "brokerage": 0.0,
            "status": "REJECTED",
            "reason": "simulated-total-broker-outage",
            "paper": True,
        }

    real_place_order = om.place_order
    om.place_order = always_after_2_fails

    ok = strat.enter(22000, 12, 22500, 21500, FakeSR(), "19-MAR-2026", lots=1)
    assert ok is False
    # Legs are now ordered [SC(SELL), LC(BUY), SP(SELL), LP(BUY)].
    # Leg 3 (SP SELL) rejects → rollback of legs 1 (SC SELL) and 2 (LC BUY) both fail.
    assert len(strat._last_rollback_stuck_legs) == 2
    original_sides = {leg["original_side"] for leg in strat._last_rollback_stuck_legs}
    assert original_sides == {"SELL", "BUY"}
    for leg in strat._last_rollback_stuck_legs:
        assert leg["intended_qty"] == 65
