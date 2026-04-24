"""LIVE-10 — pre-entry margin check.

Pins the behavior of the three moving parts:
  * ``estimate_ic_required_margin`` — pure arithmetic upper bound
  * ``get_available_margin`` on both OrderManager flavors
  * ``IronCondorStrategy._pre_entry_margin_ok`` — the refuse-or-pass gate

The integration tests target the exact scenario LIVE-10 prevents: a
leg-3 or leg-4 mid-entry rejection caused by insufficient margin, which
would cascade into LIVE-03 rollback.
"""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from unittest.mock import MagicMock
import pytest

from trading_system.config import settings
from trading_system.core.margin import estimate_ic_required_margin


# ── Pure function ──────────────────────────────────────────────────────

def test_estimate_canonical_10_lot_nifty_ic():
    """10 lots NIFTY (lot_size=65), 100-pt wings, ₹18 credit.
    Max loss per lot = 100-18 = 82. Total = 82 × 65 × 10 = 53,300.
    With 1.2× buffer = 63,960."""
    m = estimate_ic_required_margin(
        wing_width=100, lot_size=65, lots=10,
        net_credit_unit=18, buffer=1.2,
    )
    assert m == pytest.approx(63_960.0, abs=0.01)


def test_estimate_zero_credit_is_full_width():
    """A zero-credit IC (unlikely but possible) has max loss = full width."""
    m = estimate_ic_required_margin(
        wing_width=100, lot_size=65, lots=10,
        net_credit_unit=0, buffer=1.0,
    )
    assert m == pytest.approx(65_000.0, abs=0.01)


def test_estimate_credit_above_width_clamps_at_zero():
    """Pathological: credit > wing width means max loss is effectively 0 —
    mechanically the IC would be a credit spread in your favor. Margin
    still isn't negative, so clamp at 0 rather than returning a negative
    requirement (which would disable the check via `< 0` never triggering)."""
    m = estimate_ic_required_margin(
        wing_width=100, lot_size=65, lots=10,
        net_credit_unit=200, buffer=1.2,
    )
    assert m == 0.0


def test_estimate_scales_linearly_with_lots():
    m10 = estimate_ic_required_margin(100, 65, 10, 18, 1.0)
    m20 = estimate_ic_required_margin(100, 65, 20, 18, 1.0)
    assert m20 == pytest.approx(m10 * 2, abs=0.01)


# ── PaperOrderManager.get_available_margin ─────────────────────────────

def test_paper_order_manager_reports_infinite_margin():
    """Paper mode has no broker constraint. Must report infinity so the
    check is a no-op in paper."""
    from trading_system.paper.paper_order_manager import PaperOrderManager
    om = PaperOrderManager(market_data=MagicMock())
    assert om.get_available_margin() == float("inf")


# ── LiveOrderManager.get_available_margin ──────────────────────────────

def test_live_order_manager_parses_shoonya_limits():
    """Standard Shoonya response: cash 500k, marginused 100k → 400k available."""
    from trading_system.live.live_order_manager import LiveOrderManager
    api = MagicMock()
    api.get_limits.return_value = {"cash": "500000.00", "marginused": "100000.00"}
    om = LiveOrderManager(api=api, market_data=MagicMock())
    assert om.get_available_margin() == pytest.approx(400_000.0, abs=0.01)


def test_live_order_manager_raising_api_reports_zero():
    """get_limits() exception → 0 available → margin check will refuse.
    Safety over continuity: we don't trade blind to margin state."""
    from trading_system.live.live_order_manager import LiveOrderManager
    api = MagicMock()
    api.get_limits.side_effect = RuntimeError("network down")
    om = LiveOrderManager(api=api, market_data=MagicMock())
    assert om.get_available_margin() == 0.0


def test_live_order_manager_malformed_response_reports_zero():
    """Non-dict response → 0."""
    from trading_system.live.live_order_manager import LiveOrderManager
    api = MagicMock()
    api.get_limits.return_value = "unexpected-string"
    om = LiveOrderManager(api=api, market_data=MagicMock())
    assert om.get_available_margin() == 0.0


def test_live_order_manager_missing_fields_defaults_to_zero():
    """If marginused is missing from the response, default 0 → cash reported
    as available. If cash is also missing → 0."""
    from trading_system.live.live_order_manager import LiveOrderManager
    api = MagicMock()
    api.get_limits.return_value = {}
    om = LiveOrderManager(api=api, market_data=MagicMock())
    assert om.get_available_margin() == 0.0


def test_live_order_manager_non_numeric_fields_report_zero():
    """Operator-facing error case: Shoonya returns 'N/A' or similar. Must
    not crash; must refuse by returning 0."""
    from trading_system.live.live_order_manager import LiveOrderManager
    api = MagicMock()
    api.get_limits.return_value = {"cash": "N/A", "marginused": "0"}
    om = LiveOrderManager(api=api, market_data=MagicMock())
    assert om.get_available_margin() == 0.0


# ── Strategy integration — refuse when margin insufficient ─────────────

def _build_strategy_with_mocked_om(available_margin):
    """Build an IC strategy with a real enough mock order manager that
    _pre_entry_margin_ok's margin branch actually triggers. We can't use
    a bare MagicMock for `om` because the isinstance(int, float) check in
    _pre_entry_margin_ok is the test-compat escape hatch — we need a real
    numeric return value to exercise the refuse path."""
    from trading_system.core.iron_condor import IronCondorStrategy
    om = MagicMock()
    om.get_available_margin.return_value = available_margin
    md = MagicMock()
    return IronCondorStrategy(order_manager=om, market_data=md, instrument="NIFTY")


def test_insufficient_margin_refuses_entry_with_structured_log(caplog, monkeypatch):
    """The core protection: if broker says we can't afford the IC, refuse
    upfront instead of letting leg-3 reject mid-entry."""
    import logging
    caplog.set_level(logging.ERROR)
    monkeypatch.setattr(settings, "IC_MARGIN_CHECK_ENABLED", True, raising=False)

    strat = _build_strategy_with_mocked_om(available_margin=1_000.0)
    # Required for 10-lot NIFTY IC at 100-pt wings / ₹18 credit ≈ ₹63,960.
    # 1,000 is way below; must refuse.
    assert strat._pre_entry_margin_ok(
        wing_width=100, lot_size=65, lots=10,
        qty=650, net_credit_unit=18,
    ) is False

    # Structured reason tag must surface so ops can grep for it.
    assert any(
        "IC_REJECT reason=INSUFFICIENT_MARGIN" in rec.message
        for rec in caplog.records
    )
    # Must include enough context to diagnose without re-running.
    last = [r for r in caplog.records if "INSUFFICIENT_MARGIN" in r.message][-1].message
    assert "required=" in last and "available=" in last
    assert "instrument=NIFTY" in last


def test_sufficient_margin_passes():
    """Available comfortably over required → allow entry."""
    strat = _build_strategy_with_mocked_om(available_margin=500_000.0)
    assert strat._pre_entry_margin_ok(
        wing_width=100, lot_size=65, lots=10,
        qty=650, net_credit_unit=18,
    ) is True


def test_margin_check_disabled_is_a_noop(monkeypatch):
    """Feature flag off → the helper doesn't even query the order manager.
    Useful for bootstrap / debugging when the operator wants to bypass."""
    monkeypatch.setattr(settings, "IC_MARGIN_CHECK_ENABLED", False, raising=False)
    strat = _build_strategy_with_mocked_om(available_margin=1.0)  # would fail if checked

    assert strat._pre_entry_margin_ok(
        wing_width=100, lot_size=65, lots=10,
        qty=650, net_credit_unit=18,
    ) is True
    # And the order manager was never asked.
    strat.om.get_available_margin.assert_not_called()


def test_margin_query_exception_refuses_entry():
    """If get_available_margin() raises, we can't verify margin — refuse
    entry. Axiom 3: uncertain state halts new entries."""
    strat = _build_strategy_with_mocked_om(available_margin=None)
    strat.om.get_available_margin.side_effect = RuntimeError("api exploded")

    assert strat._pre_entry_margin_ok(
        wing_width=100, lot_size=65, lots=10,
        qty=650, net_credit_unit=18,
    ) is False


def test_buffer_multiplier_is_honoured(monkeypatch):
    """Raising the buffer from 1.2× to 2.0× must push a previously-sufficient
    margin into 'insufficient' territory."""
    # Canonical IC requires 53,300 at 1.0×, 63,960 at 1.2×, 106,600 at 2.0×.
    strat = _build_strategy_with_mocked_om(available_margin=70_000.0)

    monkeypatch.setattr(settings, "IC_MARGIN_BUFFER_MULT", 1.2, raising=False)
    assert strat._pre_entry_margin_ok(100, 65, 10, 650, 18) is True

    monkeypatch.setattr(settings, "IC_MARGIN_BUFFER_MULT", 2.0, raising=False)
    assert strat._pre_entry_margin_ok(100, 65, 10, 650, 18) is False
