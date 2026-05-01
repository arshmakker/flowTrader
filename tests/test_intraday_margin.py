"""LIVE-11 — intra-day margin shortfall detection.

Complements LIVE-10's pre-entry check. SEBI peak-margin snapshots fire at
random intervals through the day; a position that cleared the entry gate
can still trip a shortfall if spot moves or SPAN re-prices. Main loop
polls ``order_mgr.get_margin_shortfall()`` once per cycle; any positive
value halts new entries and fires a critical alert.

These tests pin the getter's behavior on both managers. The main-loop
wiring is exercised as part of the existing live-mode integration once
LIVE-01 wires the live OrderManager — until then, the code path is
guarded by ``not settings.PAPER_TRADE_MODE``.
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


def test_live_solvent_reports_zero():
    """cash 500k, used 300k — solvent by 200k."""
    from trading_system.live.live_order_manager import LiveOrderManager

    api = MagicMock()
    api.get_limits.return_value = {"cash": "500000", "marginused": "300000"}
    om = LiveOrderManager(api=api, market_data=MagicMock())
    assert om.get_margin_shortfall() == 0.0


def test_live_shortfall_reports_positive_delta():
    """The scenario LIVE-11 catches: a position that passed LIVE-10 now
    exceeds available margin because SPAN re-priced intraday."""
    from trading_system.live.live_order_manager import LiveOrderManager

    api = MagicMock()
    api.get_limits.return_value = {"cash": "300000", "marginused": "500000"}
    om = LiveOrderManager(api=api, market_data=MagicMock())
    assert om.get_margin_shortfall() == pytest.approx(200_000.0, abs=0.01)


def test_live_fails_open_on_api_error():
    """Transient API errors must not spuriously halt trading. The LIVE-24
    heartbeat already catches a dead process; a missed margin poll is
    recoverable on the next cycle."""
    from trading_system.live.live_order_manager import LiveOrderManager

    api = MagicMock()
    api.get_limits.side_effect = RuntimeError("transient")
    om = LiveOrderManager(api=api, market_data=MagicMock())
    assert om.get_margin_shortfall() == 0.0


def test_live_fails_open_on_malformed_response():
    """Non-dict response → 0 (not shortfall) — same fail-open rationale."""
    from trading_system.live.live_order_manager import LiveOrderManager

    api = MagicMock()
    api.get_limits.return_value = "garbage"
    om = LiveOrderManager(api=api, market_data=MagicMock())
    assert om.get_margin_shortfall() == 0.0


def test_live_fails_open_on_non_numeric_fields():
    """If Shoonya returns 'N/A' for a field, don't crash — return 0 so
    this cycle is skipped rather than halting on a parse error."""
    from trading_system.live.live_order_manager import LiveOrderManager

    api = MagicMock()
    api.get_limits.return_value = {"cash": "N/A", "marginused": "300000"}
    om = LiveOrderManager(api=api, market_data=MagicMock())
    assert om.get_margin_shortfall() == 0.0
