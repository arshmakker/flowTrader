"""Tests for Strategy entry gates + full lifecycle (A through E)."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from datetime import time
from trading_system.config import settings
from trading_system.core.strategy_a import StrategyA
from trading_system.core.strategy_b import StrategyB
from trading_system.core.strategy_c import StrategyC
from trading_system.core.strategy_d import StrategyD
from trading_system.core.strategy_e import StrategyE


class MockMD:
    def __init__(self, prices=None):
        self._prices = prices or {}
        self._default = 100.0

    def get_ltp(self, sym):
        return self._prices.get(sym, self._default)


class MockOM:
    def __init__(self):
        self.orders = []

    def place_order(self, sym, side, qty, **kw):
        self.orders.append({"symbol": sym, "side": side, "quantity": qty})
        return {"order_id": f"T{len(self.orders)}"}

    @staticmethod
    def build_option_symbol(sym, expiry, strike, opt_type):
        ot = opt_type[0]
        return f"NFO|{sym}01MAR26{ot}{int(strike)}"


# ══════════════════════════════════════════════════════════════════════
# STRATEGY A — SHORT STRANGLE
# ══════════════════════════════════════════════════════════════════════

def test_a_should_enter_calm_range():
    s = StrategyA(MockOM(), MockMD())
    assert s.should_enter("CALM", "RANGE", time(11, 0)) is True

def test_a_should_enter_normal_range():
    s = StrategyA(MockOM(), MockMD())
    assert s.should_enter("NORMAL", "RANGE", time(11, 0)) is True

def test_a_reject_elevated():
    s = StrategyA(MockOM(), MockMD())
    assert s.should_enter("ELEVATED", "RANGE", time(11, 0)) is False

def test_a_reject_bull():
    s = StrategyA(MockOM(), MockMD())
    assert s.should_enter("CALM", "BULL", time(11, 0)) is False

def test_a_reject_before_entry():
    s = StrategyA(MockOM(), MockMD())
    assert s.should_enter("CALM", "RANGE", time(9, 30)) is False

def test_a_reject_after_entry():
    s = StrategyA(MockOM(), MockMD())
    assert s.should_enter("CALM", "RANGE", time(14, 0)) is False

def test_a_reject_when_active():
    s = StrategyA(MockOM(), MockMD())
    s.enter(24500, 1, "01-MAR-2026", "11:00")
    assert s.should_enter("CALM", "RANGE", time(11, 30)) is False

def test_a_get_strikes():
    cs, ps = StrategyA.get_strikes(24500)
    assert cs > 24500  # OTM call
    assert ps < 24500  # OTM put

def test_a_full_lifecycle():
    md = MockMD()
    om = MockOM()
    s = StrategyA(om, md)
    result = s.enter(24500, 1, "01-MAR-2026", "11:00")
    assert result is not None
    assert s.is_active()
    assert len(om.orders) == 2  # sell call + sell put

    # Force exit
    exit_result = s.force_exit()
    assert exit_result is not None
    assert exit_result["reason"] == "HARD_CLOSE"
    assert not s.is_active()
    assert len(om.orders) == 4  # buy back both legs

def test_a_enter_fails_on_zero_ltp():
    md = MockMD()
    md._default = 0.0
    s = StrategyA(MockOM(), md)
    result = s.enter(24500, 1, "01-MAR-2026", "11:00")
    assert result is None


def test_a_monitor_returns_none_when_ltp_zero():
    """LTP=0 on any leg → monitor() skips cycle and returns None."""
    md = MockMD()
    om = MockOM()
    s = StrategyA(om, md)
    s.enter(24500, 1, "01-MAR-2026", "11:00")
    assert s.is_active()
    md._prices[s._position.call_symbol] = 0.0
    md._prices[s._position.put_symbol] = 0.0
    result = s.monitor()
    assert result is None
    assert s.is_active()  # position not closed


def test_a_force_exit_still_exits_when_ltp_zero():
    """force_exit() with LTP=0 still closes position and returns result (P&L may be wrong)."""
    md = MockMD()
    om = MockOM()
    s = StrategyA(om, md)
    s.enter(24500, 1, "01-MAR-2026", "11:00")
    md._prices[s._position.call_symbol] = 0.0
    md._prices[s._position.put_symbol] = 0.0
    result = s.force_exit()
    assert result is not None
    assert result["reason"] == "HARD_CLOSE"
    assert not s.is_active()
    assert len(om.orders) == 4  # buy back both legs


# ══════════════════════════════════════════════════════════════════════
# STRATEGY B — DIRECTIONAL SPREAD
# ══════════════════════════════════════════════════════════════════════

def test_b_should_enter_calm_bull_high_conf():
    s = StrategyB(MockOM(), MockMD())
    assert s.should_enter("CALM", "BULL", 3, time(11, 0)) is True

def test_b_reject_range():
    s = StrategyB(MockOM(), MockMD())
    assert s.should_enter("CALM", "RANGE", 3, time(11, 0)) is False

def test_b_reject_low_confidence():
    s = StrategyB(MockOM(), MockMD())
    assert s.should_enter("CALM", "BULL", 2, time(11, 0)) is False

def test_b_reject_before_1030():
    s = StrategyB(MockOM(), MockMD())
    assert s.should_enter("CALM", "BULL", 3, time(10, 0)) is False

def test_b_reject_elevated():
    s = StrategyB(MockOM(), MockMD())
    assert s.should_enter("ELEVATED", "BULL", 3, time(11, 0)) is False

def test_b_bull_spread_lifecycle():
    md = MockMD()
    om = MockOM()
    s = StrategyB(om, md)
    result = s.enter("BULL", 24500, 1, "01-MAR-2026", "11:00")
    assert result is not None
    assert result["direction"] == "BULL"
    assert s.is_active()
    exit_r = s.force_exit()
    assert exit_r["reason"] == "HARD_CLOSE"
    assert not s.is_active()

def test_b_bear_spread_lifecycle():
    md = MockMD()
    om = MockOM()
    s = StrategyB(om, md)
    result = s.enter("BEAR", 24500, 1, "01-MAR-2026", "11:00")
    assert result is not None
    assert result["direction"] == "BEAR"


def test_b_monitor_returns_none_when_ltp_zero():
    md = MockMD()
    om = MockOM()
    s = StrategyB(om, md)
    s.enter("BULL", 24500, 1, "01-MAR-2026", "11:00")
    md._prices[s._position.buy_symbol] = 0.0
    md._prices[s._position.sell_symbol] = 0.0
    result = s.monitor()
    assert result is None
    assert s.is_active()


def test_b_force_exit_still_exits_when_ltp_zero():
    md = MockMD()
    om = MockOM()
    s = StrategyB(om, md)
    s.enter("BULL", 24500, 1, "01-MAR-2026", "11:00")
    md._prices[s._position.buy_symbol] = 0.0
    md._prices[s._position.sell_symbol] = 0.0
    result = s.force_exit()
    assert result is not None
    assert result["reason"] == "HARD_CLOSE"
    assert not s.is_active()


# ══════════════════════════════════════════════════════════════════════
# STRATEGY C — FUTURES SCALP
# ══════════════════════════════════════════════════════════════════════

def test_c_should_enter_calm_max_confidence():
    s = StrategyC(MockOM(), MockMD())
    active = {"A": False, "B": False, "D": False, "E": False}
    # is_expiry_day is day-dependent, so we test the other conditions
    if not s.is_expiry_day():
        assert s.should_enter("CALM", 4, active) is True

def test_c_reject_not_calm():
    s = StrategyC(MockOM(), MockMD())
    active = {"A": False}
    assert s.should_enter("NORMAL", 4, active) is False

def test_c_reject_low_confidence():
    s = StrategyC(MockOM(), MockMD())
    active = {"A": False}
    assert s.should_enter("CALM", 3, active) is False

def test_c_reject_other_active():
    s = StrategyC(MockOM(), MockMD())
    active = {"A": True}
    assert s.should_enter("CALM", 4, active) is False

def test_c_bull_lifecycle():
    md = MockMD({})
    md._default = 24500
    om = MockOM()
    s = StrategyC(om, md)
    result = s.enter("BULL", "NFO|NIFTY26MAR26F", "11:00")
    assert result is not None
    assert s.is_active()
    assert s._position.target_price == 24500 + settings.SC_TARGET_PTS
    assert s._position.stop_price == 24500 - settings.SC_STOP_PTS
    exit_r = s.force_exit()
    assert exit_r is not None
    assert not s.is_active()

def test_c_bear_lifecycle():
    md = MockMD({})
    md._default = 24500
    om = MockOM()
    s = StrategyC(om, md)
    result = s.enter("BEAR", "NFO|NIFTY26MAR26F", "11:00")
    assert s._position.target_price == 24500 - settings.SC_TARGET_PTS
    assert s._position.stop_price == 24500 + settings.SC_STOP_PTS

def test_c_target_hit():
    md = MockMD({})
    md._default = 24500
    s = StrategyC(MockOM(), md)
    s.enter("BULL", "NFO|NIFTY26MAR26F", "11:00")
    md._default = 24500 + settings.SC_TARGET_PTS + 1
    result = s.monitor()
    assert result is not None
    assert result["reason"] == "TARGET_HIT"

def test_c_stop_hit():
    md = MockMD({})
    md._default = 24500
    s = StrategyC(MockOM(), md)
    s.enter("BULL", "NFO|NIFTY26MAR26F", "11:00")
    md._default = 24500 - settings.SC_STOP_PTS - 1
    result = s.monitor()
    assert result is not None
    assert result["reason"] == "STOP_HIT"


def test_c_monitor_returns_none_when_ltp_zero():
    md = MockMD({})
    md._default = 24500
    om = MockOM()
    s = StrategyC(om, md)
    s.enter("BULL", "NFO|NIFTY26MAR26F", "11:00")
    md._prices[s._position.fut_symbol] = 0.0
    result = s.monitor()
    assert result is None
    assert s.is_active()


def test_c_force_exit_still_exits_when_ltp_zero():
    md = MockMD({})
    md._default = 24500
    om = MockOM()
    s = StrategyC(om, md)
    s.enter("BULL", "NFO|NIFTY26MAR26F", "11:00")
    md._prices[s._position.fut_symbol] = 0.0
    result = s.force_exit()
    assert result is not None
    assert result["reason"] == "HARD_CLOSE"
    assert not s.is_active()


# ══════════════════════════════════════════════════════════════════════
# STRATEGY D — WIDE IRON CONDOR
# ══════════════════════════════════════════════════════════════════════

def test_d_vix_is_stable_true():
    import time as _time
    base = _time.monotonic()
    hist = [(base + i * 60, 18.0 + (i % 3) * 0.3) for i in range(10)]
    assert StrategyD.vix_is_stable(hist) is True

def test_d_vix_is_stable_false_volatile():
    import time as _time
    base = _time.monotonic()
    hist = [(base + i * 60, 15 + i) for i in range(10)]
    assert StrategyD.vix_is_stable(hist) is False

def test_d_vix_is_stable_too_few():
    assert StrategyD.vix_is_stable([(1, 18)]) is False

def test_d_should_enter():
    import time as _time
    base = _time.monotonic()
    hist = [(base + i * 60, 18.5) for i in range(10)]
    s = StrategyD(MockOM(), MockMD())
    assert s.should_enter(18.5, "RANGING", hist, time(10, 30)) is True

def test_d_reject_low_vix():
    import time as _time
    base = _time.monotonic()
    hist = [(base + i * 60, 12.0) for i in range(10)]
    s = StrategyD(MockOM(), MockMD())
    assert s.should_enter(12.0, "RANGING", hist, time(10, 30)) is False

def test_d_reject_trending():
    import time as _time
    base = _time.monotonic()
    hist = [(base + i * 60, 18.5) for i in range(10)]
    s = StrategyD(MockOM(), MockMD())
    assert s.should_enter(18.5, "TRENDING_UP", hist, time(10, 30)) is False

def test_d_get_strikes():
    sc, sp, lc, lp = StrategyD.get_strikes(24500, 18.0)
    assert sc > 24500  # short call OTM
    assert sp < 24500  # short put OTM
    assert lc > sc     # long call further out
    assert lp < sp     # long put further out

def test_d_lifecycle():
    md = MockMD()
    om = MockOM()
    s = StrategyD(om, md)
    result = s.enter(24500, 18, 1, "01-MAR-2026", "10:30")
    assert result is not None
    assert s.is_active()
    assert len(om.orders) == 4  # 4 legs
    exit_r = s.force_exit()
    assert exit_r is not None
    assert not s.is_active()
    assert len(om.orders) == 8  # close 4 legs


def test_d_monitor_returns_none_when_ltp_zero():
    md = MockMD()
    om = MockOM()
    s = StrategyD(om, md)
    s.enter(24500, 18, 1, "01-MAR-2026", "10:30")
    pos = s._position
    md._prices[pos.sc_sym] = 0.0
    md._prices[pos.sp_sym] = 0.0
    md._prices[pos.lc_sym] = 0.0
    md._prices[pos.lp_sym] = 0.0
    result = s.monitor()
    assert result is None
    assert s.is_active()


def test_d_force_exit_still_exits_when_ltp_zero():
    md = MockMD()
    om = MockOM()
    s = StrategyD(om, md)
    s.enter(24500, 18, 1, "01-MAR-2026", "10:30")
    pos = s._position
    for sym in (pos.sc_sym, pos.sp_sym, pos.lc_sym, pos.lp_sym):
        md._prices[sym] = 0.0
    result = s.force_exit()
    assert result is not None
    assert result["reason"] == "HARD_CLOSE"
    assert not s.is_active()


# ══════════════════════════════════════════════════════════════════════
# STRATEGY E — DEEP ITM DIRECTIONAL
# ══════════════════════════════════════════════════════════════════════

def test_e_should_enter_elevated_trending_high():
    s = StrategyE(MockOM(), MockMD())
    assert s.should_enter(18.0, "TRENDING_UP", "HIGH", time(10, 30)) is True

def test_e_reject_low_vix():
    s = StrategyE(MockOM(), MockMD())
    assert s.should_enter(12.0, "TRENDING_UP", "HIGH", time(10, 30)) is False

def test_e_reject_ranging():
    s = StrategyE(MockOM(), MockMD())
    assert s.should_enter(18.0, "RANGING", "HIGH", time(10, 30)) is False

def test_e_reject_low_confidence():
    s = StrategyE(MockOM(), MockMD())
    assert s.should_enter(18.0, "TRENDING_UP", "LOW", time(10, 30)) is False

def test_e_reject_after_deadline():
    s = StrategyE(MockOM(), MockMD())
    assert s.should_enter(18.0, "TRENDING_UP", "HIGH", time(11, 30)) is False

def test_e_find_deep_itm_strike():
    chain = [
        {"strike": 24000, "type": "CE", "delta": 0.80, "symbol": "NFO|S1"},
        {"strike": 24200, "type": "CE", "delta": 0.60, "symbol": "NFO|S2"},
        {"strike": 24600, "type": "CE", "delta": 0.30, "symbol": "NFO|S3"},
    ]
    result = StrategyE.find_deep_itm_strike(24500, "UP", chain)
    assert result is not None
    assert result["strike"] == 24000

def test_e_find_deep_itm_pe():
    chain = [
        {"strike": 25000, "type": "PE", "delta": 0.80, "symbol": "NFO|P1"},
        {"strike": 24800, "type": "PE", "delta": 0.60, "symbol": "NFO|P2"},
    ]
    result = StrategyE.find_deep_itm_strike(24500, "DOWN", chain)
    assert result is not None
    assert result["strike"] == 25000

def test_e_find_no_candidates():
    chain = [
        {"strike": 25000, "type": "CE", "delta": 0.30, "symbol": "NFO|S1"},
    ]
    assert StrategyE.find_deep_itm_strike(24500, "UP", chain) is None

def test_e_lifecycle():
    md = MockMD()
    md._default = 300.0  # option premium
    om = MockOM()
    s = StrategyE(om, md)
    strike_info = {"strike": 24000, "delta": 0.80, "symbol": "NFO|S1"}
    result = s.enter("UP", strike_info, "01-MAR-2026", "10:30")
    assert result is not None
    assert s.is_active()
    assert s._position.target_price == 300 * (1 + settings.SE_TARGET_PCT)
    assert s._position.stop_price == 300 * (1 - settings.SE_STOP_PCT)

    exit_r = s.force_exit()
    assert exit_r is not None
    assert not s.is_active()

def test_e_direction_reversal():
    md = MockMD()
    md._default = 300.0
    s = StrategyE(MockOM(), md)
    s.enter("UP", {"strike": 24000, "delta": 0.80}, "01-MAR-2026", "10:30")
    md._default = 290.0  # not at target/stop
    result = s.monitor(current_day_type="TRENDING_DOWN")
    assert result is not None
    assert result["reason"] == "DIRECTION_REVERSAL"

def test_e_monitor_target_hit():
    md = MockMD()
    md._default = 300.0
    s = StrategyE(MockOM(), md)
    s.enter("UP", {"strike": 24000, "delta": 0.80}, "01-MAR-2026", "10:30")
    md._default = 300 * (1 + settings.SE_TARGET_PCT) + 1
    result = s.monitor()
    assert result is not None
    assert result["reason"] == "TARGET_HIT"


def test_e_monitor_returns_none_when_ltp_zero():
    md = MockMD()
    md._default = 300.0
    om = MockOM()
    s = StrategyE(om, md)
    s.enter("UP", {"strike": 24000, "delta": 0.80, "symbol": "NFO|NIFTY01MAR26C24000"}, "01-MAR-2026", "10:30")
    md._prices[s._position.option_symbol] = 0.0
    result = s.monitor()
    assert result is None
    assert s.is_active()


def test_e_force_exit_still_exits_when_ltp_zero():
    md = MockMD()
    md._default = 300.0
    om = MockOM()
    s = StrategyE(om, md)
    s.enter("UP", {"strike": 24000, "delta": 0.80, "symbol": "NFO|NIFTY01MAR26C24000"}, "01-MAR-2026", "10:30")
    md._prices[s._position.option_symbol] = 0.0
    result = s.force_exit()
    assert result is not None
    assert result["reason"] == "HARD_CLOSE"
    assert not s.is_active()


if __name__ == "__main__":
    passed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            passed += 1
            print(f"  [PASS] {name}")
    print(f"\n✅ {passed} Strategy tests passed")
