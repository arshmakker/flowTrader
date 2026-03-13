"""Tests for PaperOrderManager + PaperPositionTracker."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from trading_system.config import settings
from trading_system.paper.paper_order_manager import PaperOrderManager
from trading_system.paper.paper_position_tracker import PaperPositionTracker
from trading_system.core.strategy_a import StrategyA, StranglePosition


class MockMD:
    def __init__(self, ltp=100.0):
        self._ltp = ltp

    def get_ltp(self, sym):
        return self._ltp


# ══════════════════════════════════════════════════════════════════════
# PaperOrderManager
# ══════════════════════════════════════════════════════════════════════

def test_om_place_order_buy():
    md = MockMD(100.0)
    trk = PaperPositionTracker()
    om = PaperOrderManager(md, trk)
    order = om.place_order("NFO|TEST", "BUY", 50)
    assert order["status"] == "COMPLETE"
    assert order["side"] == "BUY"
    assert order["quantity"] == 50
    assert order["fill_price"] > 100.0  # slippage up
    assert order["paper"] is True


def test_om_place_order_sell():
    md = MockMD(100.0)
    trk = PaperPositionTracker()
    om = PaperOrderManager(md, trk)
    order = om.place_order("NFO|TEST", "SELL", 50)
    assert order["fill_price"] < 100.0  # slippage down


def test_om_slippage():
    md = MockMD(200.0)
    om = PaperOrderManager(md)
    order = om.place_order("NFO|TEST", "BUY", 50)
    expected_slip = max(200 * settings.SLIPPAGE_PCT, settings.SLIPPAGE_MIN_ABS)
    assert abs(order["fill_price"] - (200 + expected_slip)) < 0.01


def test_om_otm_option_slippage():
    """Options with LTP below SLIPPAGE_OTM_THRESHOLD get wider slippage (3x rate, min SLIPPAGE_MIN_ABS)."""
    ltp = 30.0  # below 50
    md = MockMD(ltp)
    om = PaperOrderManager(md)
    symbol = "NFO|NIFTY01MAR26C24000"  # CE option; last 6 chars contain C so OTM path used
    order = om.place_order(symbol, "BUY", 50)
    # OTM path: slip = max(ltp * SLIPPAGE_PCT * 3, SLIPPAGE_MIN_ABS)
    expected_slip = max(ltp * settings.SLIPPAGE_PCT * 3, settings.SLIPPAGE_MIN_ABS)
    assert order["fill_price"] == round(ltp + expected_slip, 2)
    assert order["fill_price"] >= ltp + settings.SLIPPAGE_MIN_ABS


def test_om_fill_price_multiple_of_tick():
    """All fill prices must be in multiples of 0.05 (NSE F&O tick size)."""
    md = MockMD(100.33)  # LTP that would yield non-tick-aligned fill without rounding
    om = PaperOrderManager(md)
    order_buy = om.place_order("NFO|TEST", "BUY", 50)
    order_sell = om.place_order("NFO|TEST2", "SELL", 50)
    tick = settings.PRICE_TICK
    assert abs(order_buy["fill_price"] * (1 / tick) - round(order_buy["fill_price"] / tick)) < 1e-9
    assert abs(order_sell["fill_price"] * (1 / tick) - round(order_sell["fill_price"] / tick)) < 1e-9


def test_om_brokerage():
    om = PaperOrderManager(MockMD())
    order = om.place_order("NFO|TEST", "BUY", 50)
    assert order["brokerage"] == 5.0


def test_om_stt_options_sell():
    om = PaperOrderManager(MockMD(100.0))
    order = om.place_order("NFO|NIFTY01MAR26C24500", "SELL", 50)
    expected_stt = order["fill_price"] * 50 * 0.0005
    assert abs(order["stt"] - expected_stt) < 0.1


def test_om_stt_options_buy():
    om = PaperOrderManager(MockMD(100.0))
    order = om.place_order("NFO|NIFTY01MAR26C24500", "BUY", 50)
    assert order["stt"] == 0.0


def test_om_stt_futures():
    om = PaperOrderManager(MockMD(24500.0))
    order = om.place_order("NFO|NIFTY01MAR26FUT", "BUY", 50)
    expected_stt = order["fill_price"] * 50 * 0.0001
    assert abs(order["stt"] - expected_stt) < 1.0


def test_om_unique_order_ids():
    om = PaperOrderManager(MockMD())
    o1 = om.place_order("NFO|A", "BUY", 1)
    o2 = om.place_order("NFO|B", "BUY", 1)
    assert o1["order_id"] != o2["order_id"]


def test_om_wires_tracker():
    md = MockMD()
    trk = PaperPositionTracker()
    om = PaperOrderManager(md, trk)
    om.place_order("NFO|TEST", "BUY", 50)
    assert trk.has_open_positions()


def test_om_no_tracker_doesnt_crash():
    om = PaperOrderManager(MockMD())
    order = om.place_order("NFO|TEST", "BUY", 50)
    assert order["status"] == "COMPLETE"


def test_om_build_option_symbol():
    sym = PaperOrderManager.build_option_symbol("NIFTY", "17-MAR-2026", 24500, "CE")
    assert sym.startswith("NFO|NIFTY")
    assert "C24500" in sym


def test_om_build_option_symbol_pe():
    sym = PaperOrderManager.build_option_symbol("NIFTY", "17-MAR-2026", 24000, "PE")
    assert "P24000" in sym


def test_om_fallback_price():
    md = MockMD(0.0)
    om = PaperOrderManager(md)
    order = om.place_order("NFO|TEST", "BUY", 50, price=150.0)
    assert order["fill_price"] > 0


def test_om_rejects_missing_ltp_without_explicit_price():
    md = MockMD(0.0)
    om = PaperOrderManager(md)
    order = om.place_order("NFO|TEST", "BUY", 50)
    assert order["status"] == "REJECTED"
    assert order["reason"] == "missing_ltp"


def test_strategy_exit_preserves_position_when_close_order_rejected():
    md = MockMD(0.0)
    trk = PaperPositionTracker()
    om = PaperOrderManager(md, trk)
    strat = StrategyA(om, md)
    strat._position = StranglePosition(
        call_strike=24800,
        put_strike=24200,
        call_symbol="NFO|CE",
        put_symbol="NFO|PE",
        premium_received=100.0,
        lots=1,
        entry_time="10:00:00",
    )
    trk._positions = {
        "NFO|CE": {"symbol": "NFO|CE", "qty": -50, "avg_price": 100.0, "side": "SELL", "costs": 5.0},
        "NFO|PE": {"symbol": "NFO|PE", "qty": -50, "avg_price": 100.0, "side": "SELL", "costs": 5.0},
    }
    result = strat.force_exit()
    assert result is None
    assert strat.is_active()
    assert trk._positions["NFO|CE"]["qty"] == -50
    assert trk._positions["NFO|PE"]["qty"] == -50


# ══════════════════════════════════════════════════════════════════════
# PaperPositionTracker
# ══════════════════════════════════════════════════════════════════════

def test_tracker_empty():
    trk = PaperPositionTracker()
    assert not trk.has_open_positions()
    assert trk.get_open_positions() == []


def test_tracker_add_buy():
    trk = PaperPositionTracker()
    trk.add_position({"symbol": "NFO|A", "quantity": 50, "fill_price": 100, "side": "BUY", "stt": 0, "brokerage": 5})
    assert trk.has_open_positions()
    positions = trk.get_open_positions()
    assert len(positions) == 1
    assert positions[0]["qty"] == 50


def test_tracker_add_sell():
    trk = PaperPositionTracker()
    trk.add_position({"symbol": "NFO|A", "quantity": 50, "fill_price": 100, "side": "SELL", "stt": 0, "brokerage": 5})
    positions = trk.get_open_positions()
    assert positions[0]["qty"] == -50


def test_tracker_close_by_opposite():
    trk = PaperPositionTracker()
    trk.add_position({"symbol": "NFO|A", "quantity": 50, "fill_price": 100, "side": "BUY", "stt": 0, "brokerage": 5})
    trk.add_position({"symbol": "NFO|A", "quantity": 50, "fill_price": 110, "side": "SELL", "stt": 0, "brokerage": 5})
    assert not trk.has_open_positions()


def test_tracker_close_position():
    trk = PaperPositionTracker()
    trk.add_position({"symbol": "NFO|A", "quantity": 50, "fill_price": 100, "side": "BUY", "stt": 2, "brokerage": 5})
    pnl = trk.close_position("NFO|A", 110)
    # gross = (110-100)*50 = 500
    # entry costs = 2 + 5 = 7
    # exit costs = sell-side STT (110*50*0.0005=2.75) + brokerage (5) = 7.75
    # net = 500 - 7 - 7.75 = 485.25
    assert abs(pnl - 485.25) < 0.01
    assert not trk.has_open_positions()


def test_tracker_close_short():
    trk = PaperPositionTracker()
    trk.add_position({"symbol": "NFO|A", "quantity": 50, "fill_price": 100, "side": "SELL", "stt": 0, "brokerage": 5})
    pnl = trk.close_position("NFO|A", 90)
    # gross = (100-90)*50 = 500
    # entry costs = 0 + 5 = 5
    # exit costs = buy-side (no STT) + brokerage (5) = 5
    # net = 500 - 5 - 5 = 490
    assert pnl == 490.0


def test_tracker_close_nonexistent():
    trk = PaperPositionTracker()
    assert trk.close_position("NFO|NOPE", 100) == 0.0


def test_tracker_unrealised_pnl():
    md = MockMD(110.0)
    trk = PaperPositionTracker()
    trk.add_position({"symbol": "NFO|A", "quantity": 50, "fill_price": 100, "side": "BUY", "stt": 0, "brokerage": 5})
    unrealised = trk.get_unrealised_pnl(md)
    assert unrealised == (110 - 100) * 50


def test_tracker_unrealised_pnl_short():
    md = MockMD(90.0)
    trk = PaperPositionTracker()
    trk.add_position({"symbol": "NFO|A", "quantity": 50, "fill_price": 100, "side": "SELL", "stt": 0, "brokerage": 5})
    unrealised = trk.get_unrealised_pnl(md)
    assert unrealised == (100 - 90) * 50


def test_tracker_unrealised_zero_ltp():
    md = MockMD(0.0)
    trk = PaperPositionTracker()
    trk.add_position({"symbol": "NFO|A", "quantity": 50, "fill_price": 100, "side": "BUY", "stt": 0, "brokerage": 5})
    assert trk.get_unrealised_pnl(md) == 0.0


def test_tracker_close_includes_exit_brokerage():
    """Exit brokerage (₹5) is always charged."""
    trk = PaperPositionTracker()
    trk.add_position({"symbol": "NFO|A", "quantity": 50, "fill_price": 100, "side": "BUY", "stt": 0, "brokerage": 0})
    pnl = trk.close_position("NFO|A", 100)
    # gross=0, entry_costs=0, exit_stt=100*50*0.0005=2.5, exit_brokerage=5
    assert abs(pnl - (-7.5)) < 0.01


def test_tracker_close_futures_exit_stt():
    """Futures get STT on both sides."""
    trk = PaperPositionTracker()
    trk.add_position({"symbol": "NFO|NIFTYFUT", "quantity": 50, "fill_price": 100, "side": "BUY", "stt": 0.5, "brokerage": 5})
    pnl = trk.close_position("NFO|NIFTYFUT", 110)
    # gross = 500, entry_costs = 5.5, exit_stt = 110*50*0.0001=0.55, exit_brok = 5
    expected = 500 - 5.5 - 0.55 - 5
    assert abs(pnl - expected) < 0.01


def test_tracker_avg_price_recalc_on_scale_in():
    """Scaling into same direction recalculates weighted avg price."""
    trk = PaperPositionTracker()
    trk.add_position({"symbol": "NFO|A", "quantity": 50, "fill_price": 100, "side": "BUY", "stt": 0, "brokerage": 5})
    trk.add_position({"symbol": "NFO|A", "quantity": 50, "fill_price": 120, "side": "BUY", "stt": 0, "brokerage": 5})
    pos = trk.get_open_positions()
    assert len(pos) == 1
    assert pos[0]["qty"] == 100
    assert pos[0]["avg_price"] == 110.0  # (100*50 + 120*50) / 100, rounded to tick
    assert pos[0]["costs"] == 10.0  # 5 + 5


def test_tracker_avg_price_multiple_of_tick():
    """Stored avg_price is in multiples of 0.05; close_position rounds exit_price to tick."""
    trk = PaperPositionTracker()
    trk.add_position({"symbol": "NFO|A", "quantity": 50, "fill_price": 100.07, "side": "BUY", "stt": 0, "brokerage": 5})
    pos = trk._positions["NFO|A"]
    tick = settings.PRICE_TICK
    assert abs(pos["avg_price"] / tick - round(pos["avg_price"] / tick)) < 1e-9
    pnl = trk.close_position("NFO|A", 110.03)
    assert pnl != 0  # sanity


def test_tracker_partial_close_opposite():
    """Opposite side order reduces qty without recalc."""
    trk = PaperPositionTracker()
    trk.add_position({"symbol": "NFO|A", "quantity": 100, "fill_price": 100, "side": "BUY", "stt": 0, "brokerage": 5})
    trk.add_position({"symbol": "NFO|A", "quantity": 50, "fill_price": 110, "side": "SELL", "stt": 2, "brokerage": 5})
    pos = trk.get_open_positions()
    assert len(pos) == 1
    assert pos[0]["qty"] == 50
    assert pos[0]["avg_price"] == 100  # unchanged on partial exit
    assert pos[0]["costs"] == 12.0  # 5 + 2 + 5


def test_tracker_unrealised_warns_on_zero_ltp():
    """LTP=0 now logs a warning and tracks unmarked symbols."""
    md = MockMD(0.0)
    trk = PaperPositionTracker()
    trk.add_position({"symbol": "NFO|A", "quantity": 50, "fill_price": 100, "side": "BUY", "stt": 0, "brokerage": 5})
    unrealised = trk.get_unrealised_pnl(md)
    assert unrealised == 0.0
    assert "NFO|A" in trk.unmarked_symbols


def test_tracker_unmarked_clears_when_ltp_available():
    md = MockMD(110.0)
    trk = PaperPositionTracker()
    trk.add_position({"symbol": "NFO|A", "quantity": 50, "fill_price": 100, "side": "BUY", "stt": 0, "brokerage": 5})
    trk.get_unrealised_pnl(md)
    assert trk.unmarked_symbols == []


if __name__ == "__main__":
    passed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            passed += 1
            print(f"  [PASS] {name}")
    print(f"\n✅ {passed} PaperTrading tests passed")
