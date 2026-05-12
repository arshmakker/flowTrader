"""Per-instrument IC_MIN_CREDIT regression tests.

Pre-fix: a single ``settings.IC_MIN_CREDIT = 18.0`` floor gated entries for
both NIFTY and BANKNIFTY. Per ``docs/calibration_2026_04_26.md``, BANKNIFTY's
cost-stack break-even at 10 lots is ~₹23.92 (wide IC) — so any BANKNIFTY entry
at credit ∈ [18, 30) was a structural money-loser that the floor accepted.

Post-fix: ``settings.IC_MIN_CREDIT_BY_INSTRUMENT = {"NIFTY": 18.0,
"BANKNIFTY": 30.0}`` and ``IronCondorStrategy._min_credit()`` selects per
instrument. A BANKNIFTY entry at credit=20 is now rejected (would have
passed pre-fix), while a NIFTY entry at credit=20 still passes.
"""

import re
from unittest.mock import MagicMock

import pytest

from trading_system.config import settings
from trading_system.core.iron_condor import IronCondorStrategy


@pytest.fixture(autouse=True)
def _force_sequential_entry(monkeypatch):
    """Pin the legacy sequential entry path; the per-instrument floor is wired
    into both paths but exercising the simpler one is sufficient."""
    monkeypatch.setattr(settings, "IC_ENTRY_MODE", "sequential")


@pytest.fixture
def mock_om():
    om = MagicMock()
    om.build_option_symbol.side_effect = lambda inst, exp, strike, type: f"NFO|{inst}{exp}{type[0]}{int(strike)}"
    om.place_order.return_value = {
        "status": "COMPLETE",
        "fill_price": 15.0,
        "order_id": "PAPER_MOCK",
    }
    om.tracker = None
    om.get_available_margin.return_value = float("inf")
    return om


@pytest.fixture
def mock_md():
    md = MagicMock()
    md.get_ltp.return_value = 10.0
    return md


def _ltp_close_to(spot: float, short_premium: float, wing_premium: float):
    """Return LTPs by distance from ``spot``: shorts near, wings further out.

    Symbol shape from ``mock_om.build_option_symbol`` ends with the strike,
    e.g. ``NFO|BANKNIFTY19-MAR-2026C50200`` → strike 50200. Threshold of 175
    discriminates between VIX-low shorts (~150 OTM) and wings (~200 OTM, since
    VIX_LOW_WIDTH=50).
    """

    def _side_effect(sym: str) -> float:
        m = re.search(r"(\d+)$", sym)
        if not m:
            return 0.0
        strike = int(m.group(1))
        return short_premium if abs(strike - spot) <= 175 else wing_premium

    return _side_effect


def test_constants_define_both_instruments_with_banknifty_higher():
    floors = settings.IC_MIN_CREDIT_BY_INSTRUMENT
    assert "NIFTY" in floors and "BANKNIFTY" in floors
    assert floors["BANKNIFTY"] > floors["NIFTY"]


def test_min_credit_helper_returns_per_instrument_floor(mock_om, mock_md):
    nifty = IronCondorStrategy(mock_om, mock_md, "NIFTY")
    bn = IronCondorStrategy(mock_om, mock_md, "BANKNIFTY")
    assert nifty._min_credit() == settings.IC_MIN_CREDIT_BY_INSTRUMENT["NIFTY"]
    assert bn._min_credit() == settings.IC_MIN_CREDIT_BY_INSTRUMENT["BANKNIFTY"]


def test_banknifty_entry_at_credit_between_floors_is_rejected(mock_om, mock_md):
    s = IronCondorStrategy(mock_om, mock_md, "BANKNIFTY")
    mock_md.get_lot_size.return_value = settings.BANKNIFTY_LOT_SIZE

    sr_mgr = MagicMock()
    sr_mgr.apply_buffer.side_effect = lambda strike, h, l, type, step=50, **kw: strike

    mock_md.get_ltp.side_effect = _ltp_close_to(spot=50000, short_premium=15.0, wing_premium=5.0)

    success = s.enter(50000, 12, 51000, 49000, sr_mgr, "19-MAR-2026", settings.IC_LOT_SIZE)

    assert success is False
    assert mock_om.place_order.call_count == 0


def test_nifty_entry_at_same_credit_is_accepted(mock_om, mock_md):
    """Counterpart: same credit (20) on NIFTY should still pass — the fix did
    not regress NIFTY's gate."""
    s = IronCondorStrategy(mock_om, mock_md, "NIFTY")
    mock_md.get_lot_size.return_value = settings.NIFTY_LOT_SIZE

    sr_mgr = MagicMock()
    sr_mgr.apply_buffer.side_effect = lambda strike, h, l, type, step=50, **kw: strike

    mock_md.get_ltp.side_effect = _ltp_close_to(spot=22000, short_premium=15.0, wing_premium=5.0)

    success = s.enter(22000, 12, 22500, 21500, sr_mgr, "19-MAR-2026", settings.IC_LOT_SIZE)

    assert success is True
    assert mock_om.place_order.call_count == 4
