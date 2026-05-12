"""
Regression tests for LIVE-01 (async order plumbing) and LIVE-05 stub
(partial fill halt).

Tests use a fake order manager that simulates Shoonya-like state sequences
without any network calls. The LiveOrderManager's polling loop is tested
separately via a fake API stub.
"""

from itertools import cycle
from unittest.mock import MagicMock, patch

import pytest

from trading_system.config import settings
from trading_system.core.iron_condor import IronCondorStrategy


@pytest.fixture(autouse=True)
def _force_sequential_entry(monkeypatch):
    """LIVE-01 / LIVE-05 tests cover the legacy sequential path's state machine;
    pin IC_ENTRY_MODE='sequential' regardless of the branch-level default."""
    monkeypatch.setattr(settings, "IC_ENTRY_MODE", "sequential")


from trading_system.live.live_order_manager import LiveOrderManager, OrderPollingAbandoned

# ── Helpers ──────────────────────────────────────────────────────────────────


def _make_order(status, fill_qty, quantity=650, symbol="NFO|NIFTY21APR26C24200", side="SELL"):
    return {
        "order_id": "LIVE_1",
        "symbol": symbol,
        "side": side,
        "quantity": quantity,
        "fill_qty": fill_qty,
        "fill_price": 18.0 if fill_qty > 0 else 0.0,
        "status": status,
        "stt": 0.0,
        "brokerage": 0.0,
        "timestamp": "2026-04-22T10:00:00",
        "paper": False,
        "reason": "",
    }


def _complete(qty=650, symbol="NFO|NIFTY21APR26C24200", side="SELL"):
    return _make_order("COMPLETE", qty, qty, symbol, side)


def _rejected(qty=650, symbol="NFO|NIFTY21APR26C24200", side="SELL"):
    return _make_order("REJECTED", 0, qty, symbol, side)


def _canceled_no_fill(qty=650, symbol="NFO|NIFTY21APR26C24200", side="SELL"):
    return _make_order("CANCELED", 0, qty, symbol, side)


def _canceled_partial(fill_qty=300, qty=650, symbol="NFO|NIFTY21APR26C24200", side="SELL"):
    return _make_order("CANCELED", fill_qty, qty, symbol, side)


# ── LiveOrderManager._await_terminal ─────────────────────────────────────────


class TestAwaitTerminal:
    """Tests for the polling loop inside LiveOrderManager."""

    def _make_manager(self, api):
        md = MagicMock()
        return LiveOrderManager(api=api, market_data=md)

    def test_pending_then_complete_returns_complete(self):
        """PENDING x2 → COMPLETE: _await_terminal returns the COMPLETE record."""
        pending = {"status": "PENDING", "fillshares": "0", "avgprc": "0", "norenordno": "X1"}
        complete = {
            "status": "COMPLETE",
            "fillshares": "650",
            "avgprc": "18.0",
            "norenordno": "X1",
            "exch_tm": "10:00:00",
        }
        api = MagicMock()
        api.single_order_history.side_effect = [
            [pending],
            [pending],
            [complete],
        ]
        mgr = self._make_manager(api)
        with patch("time.sleep"):
            result = mgr._await_terminal("X1")
        assert result["status"] == "COMPLETE"
        assert api.single_order_history.call_count == 3

    def test_transient_error_retried_not_rejected(self):
        """A single network error is retried; second call returns COMPLETE."""
        complete = {
            "status": "COMPLETE",
            "fillshares": "650",
            "avgprc": "18.0",
            "norenordno": "X2",
            "exch_tm": "10:00:00",
        }
        api = MagicMock()
        api.single_order_history.side_effect = [
            Exception("network blip"),
            [complete],
        ]
        mgr = self._make_manager(api)
        with patch("time.sleep"):
            result = mgr._await_terminal("X2")
        assert result["status"] == "COMPLETE"

    def test_max_poll_errors_raises_abandoned(self):
        """MAX_POLL_ERRORS consecutive errors → OrderPollingAbandoned."""
        api = MagicMock()
        api.single_order_history.side_effect = Exception("persistent failure")
        mgr = self._make_manager(api)
        with patch("time.sleep"):
            with pytest.raises(OrderPollingAbandoned):
                mgr._await_terminal("X3")
        assert api.single_order_history.call_count == settings.MAX_POLL_ERRORS

    def test_rjt_abbreviation_is_terminal(self):
        """Shoonya uses 'RJT' for risk-rule rejections (e.g. RED:RULE collateral
        shortfall). Must not poll indefinitely — regression for shakedown day-1
        where the system polled for 4 min on a rejected wing order."""
        rjt_record = {
            "status": "RJT",
            "rejreason": "RED:RULE:{Allow CAC credit but disallow collateral}Shortfall:INR 188775",
            "fillshares": "0",
            "avgprc": "0",
            "norenordno": "X4",
        }
        api = MagicMock()
        api.single_order_history.return_value = [rjt_record]
        mgr = self._make_manager(api)
        with patch("time.sleep"):
            result = mgr._await_terminal("X4")
        assert result["status"] in {"RJT", "REJECTED"}
        assert api.single_order_history.call_count == 1  # resolves on first poll

    def test_unknown_non_open_status_treated_as_rejected(self):
        """Any unrecognised status that isn't OPEN/PENDING must not loop forever —
        it is coerced to REJECTED so the caller can handle it."""
        mystery_record = {
            "status": "SOME_NEW_STATUS",
            "rejreason": "unknown",
            "fillshares": "0",
            "avgprc": "0",
            "norenordno": "X5",
        }
        api = MagicMock()
        api.single_order_history.return_value = [mystery_record]
        mgr = self._make_manager(api)
        with patch("time.sleep"):
            result = mgr._await_terminal("X5")
        assert result["status"] == "REJECTED"
        assert api.single_order_history.call_count == 1

    def test_poll_timeout_cancels_and_returns_rejected(self):
        """Shoonya returns OPEN indefinitely for broker-rejected (RED:RULE)
        orders. After MAX_POLL_WAIT_SEC the poller must cancel the order and
        return REJECTED — regression for 5-min stuck poll on shakedown day-1."""
        open_record = {"status": "OPEN", "fillshares": "0", "avgprc": "0", "norenordno": "X6"}
        api = MagicMock()
        api.single_order_history.return_value = [open_record]

        mgr = self._make_manager(api)
        # Drive monotonic past the deadline immediately on first check
        with patch("time.sleep"), patch("time.monotonic", side_effect=[0.0, 0.0, 999.0]):
            result = mgr._await_terminal("X6")

        assert result["status"] == "REJECTED"
        assert result.get("rejreason") == "poll_timeout"
        api.cancel_order.assert_called_once_with(orderno="X6")


# ── IronCondorStrategy entry state machine ────────────────────────────────────


@pytest.fixture
def sr_mgr():
    m = MagicMock()
    m.apply_buffer.side_effect = lambda strike, h, l, t, step=50, **kw: strike
    return m


@pytest.fixture
def mock_md():
    md = MagicMock()
    # enter() calls get_ltp in order: sc, sp, lc, lp.
    # Cycle [25, 25, 5, 5] so credit = (25+25)-(5+5) = 40 >= IC_MIN_CREDIT.
    md.get_ltp.side_effect = cycle([25.0, 25.0, 5.0, 5.0])
    md.get_lot_size.return_value = 65
    return md


def _make_ic(om, md):
    return IronCondorStrategy(om, md, "NIFTY")


class TestEntryStateMachine:
    def _om_all_complete(self):
        om = MagicMock()
        om.build_option_symbol.side_effect = lambda inst, exp, s, t: f"NFO|{inst}{exp}{t[0]}{int(s)}"
        # All 4 legs succeed; fill_qty must equal requested qty (650) so the
        # entry loop's status == "COMPLETE" branch is taken for every leg.
        om.place_order.return_value = _complete(qty=650)
        om.tracker = None
        om.get_available_margin.return_value = float("inf")
        return om

    def test_all_complete_entry_succeeds(self, mock_md, sr_mgr):
        """All 4 legs COMPLETE → entry returns True, no rollback."""
        om = self._om_all_complete()
        ic = _make_ic(om, mock_md)
        result = ic.enter(24000, 12.0, 24500, 23500, sr_mgr, "17-APR-2026", 10)
        assert result is True
        assert om.place_order.call_count == 4

    def test_leg2_rejected_triggers_rollback_for_leg1_only(self, mock_md, sr_mgr, tmp_path):
        """Leg 2 REJECTED → rollback covers only leg 1 (fill_qty=650)."""
        om = MagicMock()
        om.build_option_symbol.side_effect = lambda inst, exp, s, t: f"NFO|{inst}{exp}{t[0]}{int(s)}"
        om.place_order.side_effect = [
            _complete(side="SELL"),  # leg 1 (SC) — success
            _rejected(side="BUY"),  # leg 2 (LC) — rejected
            _complete(side="SELL"),  # rollback of leg 1
        ]
        om.tracker = None
        om.get_available_margin.return_value = float("inf")

        ic = _make_ic(om, mock_md)
        stuck_path = tmp_path / "stuck_legs.json"
        with patch("trading_system.live.live_order_manager._STUCK_LEGS_PATH", str(stuck_path)):
            result = ic.enter(24000, 12.0, 24500, 23500, sr_mgr, "17-APR-2026", 10)

        assert result is False
        # 2 entry attempts + 1 rollback = 3 calls
        assert om.place_order.call_count == 3
        # Rollback must use fill_qty=650 (not some other qty)
        rollback_call = om.place_order.call_args_list[2]
        assert rollback_call.args[2] == 650 or rollback_call[0][2] == 650

    def test_leg3_canceled_no_fill_treated_as_rejected(self, mock_md, sr_mgr, tmp_path):
        """CANCELED with fill_qty=0 on leg 3 → same rollback path as REJECTED."""
        om = MagicMock()
        om.build_option_symbol.side_effect = lambda inst, exp, s, t: f"NFO|{inst}{exp}{t[0]}{int(s)}"
        om.place_order.side_effect = [
            _complete(side="SELL"),  # leg 1 SC
            _complete(side="BUY"),  # leg 2 LC
            _canceled_no_fill(side="SELL"),  # leg 3 SP — canceled, no fill
            _complete(),  # rollback leg 2
            _complete(),  # rollback leg 1
        ]
        om.tracker = None
        om.get_available_margin.return_value = float("inf")

        ic = _make_ic(om, mock_md)
        stuck_path = tmp_path / "stuck_legs.json"
        with patch("trading_system.live.live_order_manager._STUCK_LEGS_PATH", str(stuck_path)):
            result = ic.enter(24000, 12.0, 24500, 23500, sr_mgr, "17-APR-2026", 10)

        assert result is False
        # leg 3 (cancel) is not rolled back (fill_qty=0); legs 1+2 are
        rollback_calls = om.place_order.call_args_list[3:]
        assert len(rollback_calls) == 2

    def test_leg2_canceled_partial_fill_halt_reversal_uses_fill_qty(self, mock_md, sr_mgr, tmp_path):
        """CANCELED with fill_qty=300 on leg 2 → reversal is for 300, not 650; stuck JSON written."""
        om = MagicMock()
        om.build_option_symbol.side_effect = lambda inst, exp, s, t: f"NFO|{inst}{exp}{t[0]}{int(s)}"
        om.place_order.side_effect = [
            _complete(qty=650, side="SELL"),  # leg 1 SC — full fill
            _canceled_partial(fill_qty=300, qty=650, side="BUY"),  # leg 2 LC — partial
            # partial-fill halt: reversal for leg 2 (300) then leg 1 (650)
            _make_order("COMPLETE", 300, 300, side="SELL"),
            _complete(qty=650, side="BUY"),
        ]
        om.tracker = None
        om.get_available_margin.return_value = float("inf")

        ic = _make_ic(om, mock_md)
        stuck_path = tmp_path / "stuck_legs.json"
        with patch("trading_system.live.live_order_manager._STUCK_LEGS_PATH", str(stuck_path)):
            result = ic.enter(24000, 12.0, 24500, 23500, sr_mgr, "17-APR-2026", 10)

        assert result is False
        # Leg 2 reversal must be for fill_qty=300, not requested qty=650
        reversal_for_leg2 = om.place_order.call_args_list[2]
        assert reversal_for_leg2.args[2] == 300 or reversal_for_leg2[0][2] == 300


class TestExitStateMachine:
    def test_exit_leg_incomplete_records_stuck_and_stops(self, tmp_path):
        """Exit leg returning fill_qty=0 → stuck leg recorded, remaining legs not attempted."""
        md = MagicMock()
        md.get_ltp.side_effect = cycle([25.0, 25.0, 5.0, 5.0])
        md.get_lot_size.return_value = 65

        om = MagicMock()
        om.build_option_symbol.side_effect = lambda inst, exp, s, t: f"NFO|{inst}{exp}{t[0]}{int(s)}"
        om.place_order.side_effect = [
            _complete(side="SELL"),  # leg 1 SC entry
            _complete(side="BUY"),  # leg 2 LC entry
            _complete(side="SELL"),  # leg 3 SP entry
            _complete(side="BUY"),  # leg 4 LP entry
            # Exit: first close leg (SC BUY) fails with no fill
            _make_order("REJECTED", 0, 650, side="BUY"),
        ]
        om.tracker = None
        om.get_available_margin.return_value = float("inf")

        ic = _make_ic(om, md)
        sr_mgr_m = MagicMock()
        sr_mgr_m.apply_buffer.side_effect = lambda s, h, l, t, step=50, **kw: s

        stuck_path = tmp_path / "stuck_legs.json"
        with patch("trading_system.live.live_order_manager._STUCK_LEGS_PATH", str(stuck_path)):
            entered = ic.enter(24000, 12.0, 24500, 23500, sr_mgr_m, "17-APR-2026", 10)
            assert entered is True, "entry must succeed for exit test to be meaningful"
            ic.exit("test_reason", pnl=0.0)

        # Only 5 place_order calls total: 4 entry + 1 failed exit leg
        assert om.place_order.call_count == 5
        assert len(ic._last_rollback_stuck_legs) == 1
        assert ic._last_rollback_stuck_legs[0]["reason"] == "exit_leg_incomplete"
