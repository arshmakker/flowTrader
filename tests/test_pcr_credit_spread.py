"""
Unit tests for PCRCreditSpreadStrategy.
All broker/market-data calls are mocked — no API required.
"""

from datetime import date, datetime
from unittest.mock import MagicMock, patch

import pytest

from trading_system.core.pcr_credit_spread import (
    PCRCreditSpreadStrategy,
    PCS_Position,
)

# ── Fixtures ───────────────────────────────────────────────────────────────────


def _make_strat(short_ltp=80.0, long_ltp=30.0, lot_size=65):
    om = MagicMock()
    md = MagicMock()
    om.build_option_symbol.side_effect = lambda inst, exp, strike, otype: f"{inst}{exp}{strike}{otype}"
    md.get_ltp.side_effect = lambda sym: short_ltp if "25100" in sym or "24900" in sym else long_ltp
    md.get_lot_size.return_value = lot_size
    om.place_order.return_value = {"status": "COMPLETE", "fill_price": short_ltp}
    return PCRCreditSpreadStrategy(om, md, "NIFTY")


# ── Tests ──────────────────────────────────────────────────────────────────────


class TestEnterBearCall:
    def test_success(self):
        om = MagicMock()
        md = MagicMock()
        om.build_option_symbol.side_effect = lambda i, e, s, t: f"SYM{s}{t}"
        md.get_ltp.side_effect = lambda sym: 80.0 if "25100" in sym else 30.0
        md.get_lot_size.return_value = 65
        sell_result = {"status": "COMPLETE", "fill_price": 78.0}
        buy_result = {"status": "COMPLETE", "fill_price": 28.0}
        om.place_order.side_effect = [sell_result, buy_result]

        strat = PCRCreditSpreadStrategy(om, md, "NIFTY")
        entered = strat.enter(spot=25000.0, pcr=0.55, expiry=date(2025, 5, 20), lots=1)

        assert entered
        assert strat.pos is not None
        assert strat.pos.signal == "BEAR_CALL"
        assert strat.pos.opt_type == "CE"
        assert strat.pos.short_strike == 25100  # ATM=25000 + 100
        assert strat.pos.long_strike == 25300  # ATM=25000 + 300
        assert strat.pos.entry_credit == pytest.approx(50.0)  # 78 − 28
        assert om.place_order.call_count == 2
        sell_call = om.place_order.call_args_list[0]
        assert sell_call.kwargs["buy_or_sell"] == "S"


class TestEnterBullPut:
    def test_success(self):
        om = MagicMock()
        md = MagicMock()
        om.build_option_symbol.side_effect = lambda i, e, s, t: f"SYM{s}{t}"
        md.get_ltp.side_effect = lambda sym: 70.0 if "24900" in sym else 25.0
        md.get_lot_size.return_value = 65
        sell_result = {"status": "COMPLETE", "fill_price": 69.0}
        buy_result = {"status": "COMPLETE", "fill_price": 24.0}
        om.place_order.side_effect = [sell_result, buy_result]

        strat = PCRCreditSpreadStrategy(om, md, "NIFTY")
        entered = strat.enter(spot=25000.0, pcr=1.45, expiry=date(2025, 5, 20), lots=1)

        assert entered
        assert strat.pos.signal == "BULL_PUT"
        assert strat.pos.opt_type == "PE"
        assert strat.pos.short_strike == 24900  # ATM=25000 − 100
        assert strat.pos.long_strike == 24700  # ATM=25000 − 300
        assert strat.pos.entry_credit == pytest.approx(45.0)


class TestEnterSkipsNeutralPCR:
    def test_no_entry_on_neutral(self):
        strat = _make_strat()
        entered = strat.enter(spot=25000.0, pcr=0.95, expiry=date(2025, 5, 20), lots=1)
        assert not entered
        assert strat.pos is None
        strat.om.place_order.assert_not_called()

    def test_none_pcr_skips(self):
        strat = _make_strat()
        entered = strat.enter(spot=25000.0, pcr=None, expiry=date(2025, 5, 20), lots=1)
        assert not entered


class TestEnterSkipsLowCredit:
    def test_credit_below_minimum(self):
        om = MagicMock()
        md = MagicMock()
        om.build_option_symbol.side_effect = lambda i, e, s, t: f"SYM{s}{t}"
        # short_ltp=25, long_ltp=15 → credit=10 < PCS_MIN_CREDIT=20
        md.get_ltp.side_effect = lambda sym: 25.0 if "25100" in sym else 15.0
        md.get_lot_size.return_value = 65

        strat = PCRCreditSpreadStrategy(om, md, "NIFTY")
        entered = strat.enter(spot=25000.0, pcr=0.55, expiry=date(2025, 5, 20), lots=1)

        assert not entered
        om.place_order.assert_not_called()


class TestEnterRollbackPartialFill:
    def test_long_leg_failure_rolls_back_short(self):
        om = MagicMock()
        md = MagicMock()
        om.build_option_symbol.side_effect = lambda i, e, s, t: f"SYM{s}{t}"
        md.get_ltp.side_effect = lambda sym: 80.0 if "25100" in sym else 30.0
        md.get_lot_size.return_value = 65
        sell_ok = {"status": "COMPLETE", "fill_price": 80.0}
        buy_fail = {"status": "REJECTED", "fill_price": 0.0}
        rollback = {"status": "COMPLETE", "fill_price": 80.0}
        om.place_order.side_effect = [sell_ok, buy_fail, rollback]

        strat = PCRCreditSpreadStrategy(om, md, "NIFTY")
        entered = strat.enter(spot=25000.0, pcr=0.55, expiry=date(2025, 5, 20), lots=1)

        assert not entered
        assert strat.pos is None
        # 3rd call must be the rollback buy of the short leg
        assert om.place_order.call_count == 3
        rollback_call = om.place_order.call_args_list[2]
        assert rollback_call.kwargs["buy_or_sell"] == "B"


class TestMonitorStopLoss:
    def _strat_with_position(self, entry_credit=50.0):
        strat = _make_strat()
        strat.pos = PCS_Position(
            instrument="NIFTY",
            signal="BEAR_CALL",
            short_strike=25100,
            long_strike=25300,
            opt_type="CE",
            short_sym="SHORT_SYM",
            long_sym="LONG_SYM",
            entry_credit=entry_credit,
            lots=1,
            lot_size=65,
            expiry=date(2099, 1, 1).isoformat(),
            entry_time=datetime.now().isoformat(),
            entry_pcr=0.55,
        )
        return strat

    def test_stop_triggered_when_spread_doubles(self):
        strat = self._strat_with_position(entry_credit=50.0)
        # short=160, long=5 → spread=155, mtm_loss=105 > stop_threshold=100
        strat.md.get_ltp_with_age.side_effect = lambda sym: (160.0, 1.0) if sym == "SHORT_SYM" else (5.0, 1.0)

        result = strat.monitor()

        assert result is not None
        assert result["reason"] == "PCS_STOP"

    def test_no_stop_below_threshold(self):
        strat = self._strat_with_position(entry_credit=50.0)
        # spread = 90, loss = 40 < 100
        strat.md.get_ltp_with_age.side_effect = lambda sym: (90.0, 1.0) if sym == "SHORT_SYM" else (0.0, 1.0)

        result = strat.monitor()

        assert result is None


class TestMonitorExpiryForceExit:
    def test_expiry_day_past_exit_time(self):
        strat = _make_strat()
        today = date.today()
        strat.pos = PCS_Position(
            instrument="NIFTY",
            signal="BULL_PUT",
            short_strike=24900,
            long_strike=24700,
            opt_type="PE",
            short_sym="SHORT_SYM",
            long_sym="LONG_SYM",
            entry_credit=40.0,
            lots=1,
            lot_size=65,
            expiry=today.isoformat(),
            entry_time=datetime.now().isoformat(),
            entry_pcr=1.45,
        )

        import pytz

        IST = pytz.timezone("Asia/Kolkata")
        fake_now = datetime.now(IST).replace(hour=15, minute=0, second=0)
        with patch("trading_system.core.pcr_credit_spread.datetime") as mock_dt:
            mock_dt.now.return_value = fake_now
            mock_dt.fromisoformat = datetime.fromisoformat
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            result = strat.monitor()

        assert result is not None
        assert result["reason"] == "EXPIRY_CLOSE"


class TestEnterAlreadyActive:
    def test_returns_false_when_position_exists(self):
        strat = _make_strat()
        strat.pos = PCS_Position(
            instrument="NIFTY",
            signal="BEAR_CALL",
            short_strike=25100,
            long_strike=25300,
            opt_type="CE",
            short_sym="S",
            long_sym="L",
            entry_credit=50.0,
            lots=1,
            lot_size=65,
            expiry=date(2099, 1, 1).isoformat(),
            entry_time=datetime.now().isoformat(),
            entry_pcr=0.55,
        )
        entered = strat.enter(spot=25000.0, pcr=0.55, expiry=date(2099, 1, 1), lots=1)
        assert not entered
        strat.om.place_order.assert_not_called()


class TestEnterLTPUnavailable:
    def test_zero_ltp_skips_entry(self):
        om = MagicMock()
        md = MagicMock()
        om.build_option_symbol.side_effect = lambda i, e, s, t: f"SYM{s}{t}"
        md.get_ltp.return_value = 0.0
        md.get_lot_size.return_value = 65

        strat = PCRCreditSpreadStrategy(om, md, "NIFTY")
        entered = strat.enter(spot=25000.0, pcr=0.55, expiry=date(2025, 5, 20), lots=1)

        assert not entered
        om.place_order.assert_not_called()

    def test_none_ltp_skips_entry(self):
        om = MagicMock()
        md = MagicMock()
        om.build_option_symbol.side_effect = lambda i, e, s, t: f"SYM{s}{t}"
        md.get_ltp.return_value = None
        md.get_lot_size.return_value = 65

        strat = PCRCreditSpreadStrategy(om, md, "NIFTY")
        entered = strat.enter(spot=25000.0, pcr=0.55, expiry=date(2025, 5, 20), lots=1)

        assert not entered
        om.place_order.assert_not_called()


class TestEnterShortLegFails:
    def test_sell_rejected_returns_false_no_rollback(self):
        om = MagicMock()
        md = MagicMock()
        om.build_option_symbol.side_effect = lambda i, e, s, t: f"SYM{s}{t}"
        md.get_ltp.side_effect = lambda sym: 80.0 if "25100" in sym else 30.0
        md.get_lot_size.return_value = 65
        om.place_order.return_value = {"status": "REJECTED", "fill_price": 0.0}

        strat = PCRCreditSpreadStrategy(om, md, "NIFTY")
        entered = strat.enter(spot=25000.0, pcr=0.55, expiry=date(2025, 5, 20), lots=1)

        assert not entered
        assert strat.pos is None
        assert om.place_order.call_count == 1


class TestMonitorNoPosReturnsNone:
    def test_returns_none_without_position(self):
        strat = _make_strat()
        assert strat.pos is None
        result = strat.monitor()
        assert result is None


class TestMonitorStaleLTP:
    def test_stale_short_leg_skips(self):
        strat = _make_strat()
        strat.pos = PCS_Position(
            instrument="NIFTY",
            signal="BEAR_CALL",
            short_strike=25100,
            long_strike=25300,
            opt_type="CE",
            short_sym="SHORT_SYM",
            long_sym="LONG_SYM",
            entry_credit=50.0,
            lots=1,
            lot_size=65,
            expiry=date(2099, 1, 1).isoformat(),
            entry_time=datetime.now().isoformat(),
            entry_pcr=0.55,
        )
        # age=15.0 exceeds IC_FRESH_LTP_MAX_AGE_SEC=10.0
        strat.md.get_ltp_with_age.return_value = (80.0, 15.0)

        result = strat.monitor()
        assert result is None

    def test_stale_long_leg_skips(self):
        strat = _make_strat()
        strat.pos = PCS_Position(
            instrument="NIFTY",
            signal="BEAR_CALL",
            short_strike=25100,
            long_strike=25300,
            opt_type="CE",
            short_sym="SHORT_SYM",
            long_sym="LONG_SYM",
            entry_credit=50.0,
            lots=1,
            lot_size=65,
            expiry=date(2099, 1, 1).isoformat(),
            entry_time=datetime.now().isoformat(),
            entry_pcr=0.55,
        )
        strat.md.get_ltp_with_age.side_effect = lambda sym: (80.0, 1.0) if sym == "SHORT_SYM" else (30.0, 15.0)

        result = strat.monitor()
        assert result is None


class TestMonitorZeroLTPAfterFreshness:
    def test_zero_ltp_returns_none(self):
        strat = _make_strat()
        strat.pos = PCS_Position(
            instrument="NIFTY",
            signal="BEAR_CALL",
            short_strike=25100,
            long_strike=25300,
            opt_type="CE",
            short_sym="SHORT_SYM",
            long_sym="LONG_SYM",
            entry_credit=50.0,
            lots=1,
            lot_size=65,
            expiry=date(2099, 1, 1).isoformat(),
            entry_time=datetime.now().isoformat(),
            entry_pcr=0.55,
        )
        strat.md.get_ltp_with_age.return_value = (0.0, 1.0)

        result = strat.monitor()
        assert result is None


class TestForceExitNoPosition:
    def test_returns_none_when_no_pos(self):
        strat = _make_strat()
        result = strat.force_exit("TEST")
        assert result is None
        strat.om.place_order.assert_not_called()


class TestForceExit:
    def _strat_with_position(self, entry_credit=50.0):
        om = MagicMock()
        md = MagicMock()
        om.build_option_symbol.side_effect = lambda i, e, s, t: f"{i}{s}{t}"
        md.get_lot_size.return_value = 65
        strat = PCRCreditSpreadStrategy(om, md, "NIFTY")
        strat.pos = PCS_Position(
            instrument="NIFTY",
            signal="BEAR_CALL",
            short_strike=25100,
            long_strike=25300,
            opt_type="CE",
            short_sym="SHORT_SYM",
            long_sym="LONG_SYM",
            entry_credit=entry_credit,
            lots=1,
            lot_size=65,
            expiry=date(2099, 1, 1).isoformat(),
            entry_time=datetime.now().isoformat(),
            entry_pcr=0.55,
        )
        return strat

    def test_places_two_mkt_close_orders(self):
        strat = self._strat_with_position(entry_credit=50.0)
        # buy-back short at 60, sell long at 5 → exit_spread=55, pnl_pts=-5, gross_pnl=-325
        strat.om.place_order.side_effect = [
            {"status": "COMPLETE", "fill_price": 60.0},
            {"status": "COMPLETE", "fill_price": 5.0},
        ]

        strat.force_exit("EOD")

        assert strat.om.place_order.call_count == 2
        buy_call, sell_call = strat.om.place_order.call_args_list
        assert buy_call.kwargs["buy_or_sell"] == "B"
        assert buy_call.kwargs["price_type"] == "MKT"
        assert sell_call.kwargs["buy_or_sell"] == "S"
        assert sell_call.kwargs["price_type"] == "MKT"

    def test_returns_record_with_gross_pnl(self):
        strat = self._strat_with_position(entry_credit=50.0)
        # credit=50, exit_spread=30 → pnl_pts=20, gross_pnl=20*1*65=1300
        strat.om.place_order.side_effect = [
            {"status": "COMPLETE", "fill_price": 40.0},
            {"status": "COMPLETE", "fill_price": 10.0},
        ]

        result = strat.force_exit("EOD")

        assert result is not None
        assert "gross_pnl" in result
        assert "pnl_pts" in result
        assert "exit_spread" in result
        assert result["exit_spread"] == pytest.approx(30.0)  # 40 − 10
        assert result["pnl_pts"] == pytest.approx(20.0)  # 50 − 30
        assert result["gross_pnl"] == pytest.approx(1300.0)  # 20 × 1 × 65
        assert result["reason"] == "EOD"

    def test_clears_position_after_exit(self):
        strat = self._strat_with_position(entry_credit=50.0)
        strat.om.place_order.return_value = {"status": "COMPLETE", "fill_price": 25.0}

        strat.force_exit("FORCE_EXIT")

        assert strat.pos is None

    def test_instrument_and_signal_in_record(self):
        strat = self._strat_with_position(entry_credit=50.0)
        strat.om.place_order.return_value = {"status": "COMPLETE", "fill_price": 25.0}

        result = strat.force_exit("FORCE_EXIT")

        assert result["instrument"] == "NIFTY"
        assert result["signal"] == "BEAR_CALL"
        assert result["entry_credit"] == pytest.approx(50.0)


class TestSaveRestoreState:
    def test_round_trip(self, tmp_path):
        strat = _make_strat()
        strat.pos = PCS_Position(
            instrument="NIFTY",
            signal="BEAR_CALL",
            short_strike=25100,
            long_strike=25300,
            opt_type="CE",
            short_sym="SYM25100CE",
            long_sym="SYM25300CE",
            entry_credit=55.0,
            lots=2,
            lot_size=65,
            expiry="2025-05-20",
            entry_time="2025-05-19T09:25:00",
            entry_pcr=0.62,
        )

        state = strat.save_state()
        strat2 = _make_strat()
        strat2.restore_state(state)

        assert strat2.pos is not None
        assert strat2.pos.signal == "BEAR_CALL"
        assert strat2.pos.entry_credit == pytest.approx(55.0)
        assert strat2.pos.lots == 2
        assert strat2.pos.expiry == "2025-05-20"

    def test_missing_pos_restores_none(self):
        strat = _make_strat()
        strat.restore_state({"pos": None})
        assert strat.pos is None
