"""LIVE-11 — intra-day margin shortfall detection.

Complements LIVE-10's pre-entry check. SEBI peak-margin snapshots fire at
random intervals through the day; a position that cleared the entry gate
can still trip a shortfall if spot moves or SPAN re-prices. Main loop
polls ``order_mgr.get_margin_shortfall()`` once per cycle; any positive
value halts new entries and fires a critical alert.

Shoonya get_limits() response uses: cash, cash_coll (haircut-adjusted
collateral), blk_amt (blocked for open positions). Available margin =
cash + cash_coll - blk_amt; shortfall when blk_amt > cash + cash_coll.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from unittest.mock import MagicMock

import pytest


def test_paper_reports_zero_shortfall():
    """Paper has no broker — never in shortfall."""
    from trading_system.paper.paper_order_manager import PaperOrderManager

    om = PaperOrderManager(market_data=MagicMock())
    assert om.get_margin_shortfall() == 0.0


def test_live_solvent_cash_only_reports_zero():
    """cash 500k covers blk_amt 300k — solvent."""
    from trading_system.live.live_order_manager import LiveOrderManager

    api = MagicMock()
    api.get_limits.return_value = {"cash": "500000", "cash_coll": "0", "blk_amt": "300000"}
    om = LiveOrderManager(api=api, market_data=MagicMock())
    assert om.get_margin_shortfall() == 0.0


def test_live_solvent_collateral_only_reports_zero():
    """Accounts funded via pledged stock: cash=0, cash_coll covers blk_amt."""
    from trading_system.live.live_order_manager import LiveOrderManager

    api = MagicMock()
    api.get_limits.return_value = {"cash": "0", "cash_coll": "600000", "blk_amt": "300000"}
    om = LiveOrderManager(api=api, market_data=MagicMock())
    assert om.get_margin_shortfall() == 0.0


def test_live_shortfall_reports_positive_delta():
    """blk_amt 500k > cash + cash_coll 300k → shortfall 200k."""
    from trading_system.live.live_order_manager import LiveOrderManager

    api = MagicMock()
    api.get_limits.return_value = {"cash": "100000", "cash_coll": "200000", "blk_amt": "500000"}
    om = LiveOrderManager(api=api, market_data=MagicMock())
    assert om.get_margin_shortfall() == pytest.approx(200_000.0, abs=0.01)


def test_live_fails_open_on_api_error():
    """Transient API errors must not spuriously halt trading."""
    from trading_system.live.live_order_manager import LiveOrderManager

    api = MagicMock()
    api.get_limits.side_effect = RuntimeError("transient")
    om = LiveOrderManager(api=api, market_data=MagicMock())
    assert om.get_margin_shortfall() == 0.0


def test_live_fails_open_on_malformed_response():
    """Non-dict response → 0 (not shortfall)."""
    from trading_system.live.live_order_manager import LiveOrderManager

    api = MagicMock()
    api.get_limits.return_value = "garbage"
    om = LiveOrderManager(api=api, market_data=MagicMock())
    assert om.get_margin_shortfall() == 0.0


def test_live_fails_open_on_non_numeric_fields():
    """If Shoonya returns 'N/A' for a field, don't crash — return 0."""
    from trading_system.live.live_order_manager import LiveOrderManager

    api = MagicMock()
    api.get_limits.return_value = {"cash": "N/A", "cash_coll": "300000", "blk_amt": "0"}
    om = LiveOrderManager(api=api, market_data=MagicMock())
    assert om.get_margin_shortfall() == 0.0
