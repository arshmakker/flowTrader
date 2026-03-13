"""Integration tests — full signal → entry → monitor → exit → P&L pipeline."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from datetime import time
from trading_system.config import settings
from trading_system.paper.paper_order_manager import PaperOrderManager
from trading_system.paper.paper_position_tracker import PaperPositionTracker
from trading_system.paper.paper_pnl_engine import PaperPnLEngine
from trading_system.core.trade_logger import TradeLogger
from trading_system.core.risk_manager import RiskManager
from trading_system.core.daily_target import DailyTarget
from trading_system.core.strategy_a import StrategyA
from trading_system.core.strategy_b import StrategyB
from trading_system.core.strategy_d import StrategyD
from trading_system.core import position_persistence


class MockMD:
    def __init__(self):
        self._prices = {}
        self._default = 100.0

    def get_ltp(self, sym):
        return self._prices.get(sym, self._default)


def _setup():
    md = MockMD()
    trk = PaperPositionTracker()
    om = PaperOrderManager(md, trk)
    tl = TradeLogger(data_dir="/tmp/test_integ")
    pnl = PaperPnLEngine(trk, md, tl)
    risk = RiskManager()
    target = DailyTarget()
    target.set(8000)
    return md, trk, om, tl, pnl, risk, target


# ── Test 1: Strategy A full cycle with P&L recording ─────────────────

def test_strata_full_cycle_pnl():
    md, trk, om, tl, pnl, risk, target = _setup()
    strat_a = StrategyA(om, md)

    assert strat_a.should_enter("CALM", "RANGE", time(11, 0))
    entry = strat_a.enter(24500, 1, "01-MAR-2026", "11:00")
    assert entry is not None
    assert strat_a.is_active()
    assert trk.has_open_positions()

    # Simulate premium decay (target hit)
    pos = strat_a._position
    target_prem = pos.premium_received * (1 - settings.SA_TARGET_PCT) - 0.01
    for sym in (pos.call_symbol, pos.put_symbol):
        md._prices[sym] = target_prem / 2

    result = strat_a.monitor()
    assert result is not None
    assert result["reason"] == "TARGET_HIT"
    assert result["pnl"] > 0

    pnl.record_trade("A", result["pnl"], result)
    risk.update_pnl(result["pnl"])
    assert pnl.realised_pnl > 0
    assert pnl.total_trades == 1
    assert risk.daily_pnl > 0


# ── Test 2: Risk halt stops new entries ───────────────────────────────

def test_risk_halt_blocks_entries():
    md, trk, om, tl, pnl, risk, target = _setup()
    risk.update_loss_limit(5000)
    risk.update_pnl(-6000)
    assert not risk.can_trade()

    strat_a = StrategyA(om, md)
    can_enter = strat_a.should_enter("CALM", "RANGE", time(11, 0)) and risk.can_trade()
    assert can_enter is False


# ── Test 3: Daily target blocks new entries ───────────────────────────

def test_daily_target_blocks_entries():
    md, trk, om, tl, pnl, risk, target = _setup()
    target.set(5000)
    assert not target.is_hit(4999)
    assert target.is_hit(5001)

    strat_b = StrategyB(om, md)
    can_enter = strat_b.should_enter("CALM", "BULL", 3, time(11, 0)) and not target.is_hit(5001)
    assert can_enter is False


def test_daily_target_uses_daily_not_cumulative_pnl():
    md, trk, om, tl, pnl, risk, target = _setup()
    target.set(5000)
    pnl.realised_pnl = 12000
    pnl.daily_realised_pnl = 1000

    assert target.is_hit(pnl.daily_realised_pnl) is False


# ── Test 4: Multiple strategies P&L accumulation ─────────────────────

def test_multi_strategy_pnl():
    md, trk, om, tl, pnl, risk, target = _setup()

    pnl.record_trade("A", 5000, {"strategy": "A"})
    pnl.record_trade("D", -2000, {"strategy": "D"})
    pnl.record_trade("A", 3000, {"strategy": "A"})

    assert pnl.realised_pnl == 6000
    assert pnl.total_trades == 3
    assert pnl.winning_trades == 2
    assert pnl._strategy_stats["A"]["trades"] == 2
    assert pnl._strategy_stats["D"]["trades"] == 1

    risk.update_pnl(5000)
    risk.update_pnl(-2000)
    risk.update_pnl(3000)
    assert risk.daily_pnl == 6000
    assert risk.can_trade()


# ── Test 5: Persistence round-trip with P&L ──────────────────────────

def test_persistence_with_pnl():
    md, trk, om, tl, pnl, risk, target = _setup()
    strat_d = StrategyD(om, md)
    strat_d.enter(24500, 18, 1, "01-MAR-2026", "10:30")

    pnl.record_trade("A", 5000, {"strategy": "A"})

    strats = {"A": StrategyA(om, md), "D": strat_d}

    orig_file = position_persistence.STATE_FILE
    position_persistence.STATE_FILE = "/tmp/test_integ_state.json"
    try:
        position_persistence.save(strats, trk, pnl)

        # Restore into fresh objects
        fresh_md = MockMD()
        fresh_trk = PaperPositionTracker()
        fresh_om = PaperOrderManager(fresh_md, fresh_trk)
        fresh_tl = TradeLogger(data_dir="/tmp/test_integ2")
        fresh_pnl = PaperPnLEngine(fresh_trk, fresh_md, fresh_tl)
        fresh_strats = {
            "A": StrategyA(fresh_om, fresh_md),
            "D": StrategyD(fresh_om, fresh_md),
        }

        meta = position_persistence.load(fresh_strats, fresh_trk, fresh_pnl)
        assert meta["restored_strategies"] == 1  # only D had position
        assert fresh_pnl.realised_pnl == 5000
        assert fresh_pnl.total_trades == 1
        assert fresh_strats["D"].is_active()

        position_persistence.clear()
    finally:
        position_persistence.STATE_FILE = orig_file
        import shutil
        for p in ("/tmp/test_integ", "/tmp/test_integ2", "/tmp/test_integ_state.json"):
            if os.path.isdir(p):
                shutil.rmtree(p)
            elif os.path.exists(p):
                os.remove(p)


# ── Test 6: End-to-end Strategy D iron condor lifecycle ──────────────

def test_strategy_d_end_to_end():
    md = MockMD()
    trk = PaperPositionTracker()
    om = PaperOrderManager(md, trk)
    tl = TradeLogger(data_dir="/tmp/test_integ_d")
    pnl_eng = PaperPnLEngine(trk, md, tl)
    risk = RiskManager()

    strat = StrategyD(om, md)
    entry = strat.enter(24500, 18, 1, "01-MAR-2026", "10:30")
    assert entry is not None
    assert strat.is_active()

    # Simulate favorable premium decay (all short legs drop)
    pos = strat._position
    md._prices[pos.sc_sym] = 30  # short legs shrink
    md._prices[pos.sp_sym] = 25
    md._prices[pos.lc_sym] = 5   # long legs shrink faster
    md._prices[pos.lp_sym] = 3
    current_value = (30 + 25) - (5 + 3)  # = 47
    pnl_per_unit = pos.net_premium - current_value

    # Check if it would hit target
    if pnl_per_unit >= pos.net_premium * settings.SD_TARGET_PCT:
        result = strat.monitor()
        assert result is not None
        assert result["reason"] == "TARGET_HIT"
        pnl_eng.record_trade("D", result["pnl"], result)
        risk.update_pnl(result["pnl"])
        assert pnl_eng.total_trades == 1
    else:
        # Force exit
        result = strat.force_exit()
        assert result is not None
        pnl_eng.record_trade("D", result["pnl"], result)

    assert not strat.is_active()

    import shutil
    if os.path.isdir("/tmp/test_integ_d"):
        shutil.rmtree("/tmp/test_integ_d")


if __name__ == "__main__":
    passed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            passed += 1
            print(f"  [PASS] {name}")
    print(f"\n✅ {passed} Integration tests passed")
