"""Tests for PaperPnLEngine — record, save/restore, snapshot, summary."""
import sys, os, json
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from trading_system.paper.paper_pnl_engine import PaperPnLEngine
from trading_system.config import settings
from trading_system.paper.paper_position_tracker import PaperPositionTracker
from trading_system.core.trade_logger import TradeLogger


class MockMD:
    def get_ltp(self, sym):
        return 100.0


def _make():
    trk = PaperPositionTracker()
    md = MockMD()
    tl = TradeLogger(data_dir="/tmp/test_pnl_eng")
    return PaperPnLEngine(trk, md, tl, data_dir="/tmp/test_pnl_eng")


def test_initial_state():
    pnl = _make()
    assert pnl.realised_pnl == 0.0
    assert pnl.total_trades == 0
    assert pnl.winning_trades == 0
    assert pnl.win_rate == 0.0
    assert pnl.total_pnl == 0.0


def test_record_trade_winning():
    pnl = _make()
    pnl.record_trade("A", 5000, {"strategy": "A", "pnl": 5000})
    assert pnl.realised_pnl == 5000
    assert pnl.total_trades == 1
    assert pnl.winning_trades == 1
    assert pnl.win_rate == 100.0


def test_record_trade_losing():
    pnl = _make()
    pnl.record_trade("B", -2000, {"strategy": "B", "pnl": -2000})
    assert pnl.realised_pnl == -2000
    assert pnl.total_trades == 1
    assert pnl.winning_trades == 0
    assert pnl.win_rate == 0.0


def test_record_multiple_trades():
    pnl = _make()
    pnl.record_trade("A", 5000, {"strategy": "A"})
    pnl.record_trade("A", 3000, {"strategy": "A"})
    pnl.record_trade("B", -1000, {"strategy": "B"})
    assert pnl.realised_pnl == 7000
    assert pnl.total_trades == 3
    assert pnl.winning_trades == 2
    assert abs(pnl.win_rate - 66.67) < 0.1


def test_strategy_stats():
    pnl = _make()
    pnl.record_trade("A", 5000, {})
    pnl.record_trade("A", -1000, {})
    pnl.record_trade("D", 3000, {})
    assert pnl._strategy_stats["A"]["trades"] == 2
    assert pnl._strategy_stats["A"]["total_pnl"] == 4000
    assert pnl._strategy_stats["A"]["wins"] == 1
    assert pnl._strategy_stats["D"]["trades"] == 1


def test_get_summary():
    pnl = _make()
    pnl.record_trade("A", 5000, {})
    s = pnl.get_summary()
    assert s["realised_pnl"] == 5000
    assert s["total_trades"] == 1
    assert s["winning_trades"] == 1
    assert s["win_rate_pct"] == 100.0
    assert "strategy_stats" in s
    assert s["strategy_stats"]["A"]["trades"] == 1


def test_save_state():
    pnl = _make()
    pnl.record_trade("A", 5000, {})
    pnl.record_trade("D", -2000, {})
    state = pnl.save_state()
    assert state["realised_pnl"] == 3000
    assert state["total_trades"] == 2
    assert state["winning_trades"] == 1
    assert state["strategy_stats"]["A"]["trades"] == 1
    assert state["strategy_stats"]["D"]["trades"] == 1
    assert state["daily_realised_pnl"] == 3000
    assert state["daily_trades"] == 2
    assert "daily_strategy_stats" in state


def test_restore_state():
    pnl = _make()
    state = {
        "realised_pnl": 8000,
        "total_trades": 5,
        "winning_trades": 3,
        "daily_realised_pnl": 1500,
        "daily_trades": 2,
        "daily_wins": 1,
        "strategy_stats": {
            "A": {"trades": 3, "total_pnl": 6000, "wins": 2},
            "B": {"trades": 2, "total_pnl": 2000, "wins": 1},
        },
        "daily_strategy_stats": {
            "A": {"trades": 1, "total_pnl": 1500, "wins": 1},
        },
        "daily_peak_pnl": 2000,
        "daily_max_drawdown": 500,
    }
    pnl.restore_state(state)
    assert pnl.realised_pnl == 8000
    assert pnl.total_trades == 5
    assert pnl.winning_trades == 3
    assert pnl.daily_realised_pnl == 1500
    assert pnl.daily_trades == 2
    assert pnl.daily_wins == 1
    assert pnl._strategy_stats["A"]["trades"] == 3
    assert pnl._strategy_stats["B"]["total_pnl"] == 2000
    assert pnl._daily_strategy_stats["A"]["total_pnl"] == 1500
    assert pnl.daily_max_drawdown == 500


def test_restore_state_with_daily_reset():
    pnl = _make()
    state = {
        "realised_pnl": 8000,
        "total_trades": 5,
        "winning_trades": 3,
        "daily_realised_pnl": 1500,
        "daily_trades": 2,
        "daily_wins": 1,
        "strategy_stats": {
            "A": {"trades": 3, "total_pnl": 6000, "wins": 2},
        },
        "daily_strategy_stats": {
            "A": {"trades": 1, "total_pnl": 1500, "wins": 1},
        },
        "trade_pnls": [5000, -1000, 4000],
        "peak_pnl": 8000,
        "max_drawdown": 500,
        "daily_peak_pnl": 2000,
        "daily_max_drawdown": 500,
    }
    pnl.restore_state(state, reset_daily=True)
    assert pnl.realised_pnl == 8000
    assert pnl.total_trades == 5
    assert pnl.winning_trades == 3
    assert pnl.daily_realised_pnl == 0.0
    assert pnl.daily_trades == 0
    assert pnl.daily_wins == 0
    assert pnl._strategy_stats["A"]["trades"] == 3
    assert pnl._daily_strategy_stats["A"]["trades"] == 0
    assert pnl.max_drawdown == 500
    assert pnl.daily_max_drawdown == 0.0


def test_save_restore_roundtrip():
    pnl1 = _make()
    pnl1.record_trade("A", 5000, {})
    pnl1.record_trade("D", -1200, {})
    pnl1.record_trade("A", 3000, {})

    state = pnl1.save_state()
    pnl2 = _make()
    pnl2.restore_state(state)

    assert pnl2.realised_pnl == pnl1.realised_pnl
    assert pnl2.total_trades == pnl1.total_trades
    assert pnl2.winning_trades == pnl1.winning_trades
    assert pnl2.win_rate == pnl1.win_rate


def test_write_snapshot():
    pnl = _make()
    pnl.record_trade("A", 1500, {})
    pnl.write_snapshot()
    snap_path = os.path.join(pnl._data_dir, "pnl_snapshot.json")
    assert os.path.exists(snap_path)
    with open(snap_path) as f:
        snap = json.load(f)
    assert snap["realised_pnl"] == 1500
    assert snap["total_trades"] == 1
    os.remove(snap_path)


def test_daily_counters():
    pnl = _make()
    pnl.record_trade("A", 5000, {})
    pnl.record_trade("B", -1000, {})
    assert pnl.daily_realised_pnl == 4000
    assert pnl.daily_trades == 2
    assert pnl.daily_wins == 1
    assert abs(pnl.daily_win_rate - 50.0) < 0.1


def test_reset_daily():
    pnl = _make()
    pnl.record_trade("A", 5000, {})
    pnl.record_trade("B", -1000, {})
    pnl.reset_daily()
    assert pnl.daily_realised_pnl == 0.0
    assert pnl.daily_trades == 0
    assert pnl.daily_wins == 0
    # Cumulative should be untouched
    assert pnl.realised_pnl == 4000
    assert pnl.total_trades == 2
    assert pnl.winning_trades == 1


def test_daily_strategy_stats_reset():
    pnl = _make()
    pnl.record_trade("A", 5000, {})
    pnl.reset_daily()
    assert pnl._daily_strategy_stats["A"]["trades"] == 0
    assert pnl._strategy_stats["A"]["trades"] == 1


def test_max_drawdown():
    pnl = _make()
    pnl.record_trade("A", 5000, {})
    pnl.record_trade("B", -3000, {})
    pnl.record_trade("C", -4000, {})
    # Peak was 5000, now at -2000, drawdown = 7000
    assert pnl.max_drawdown == 7000


def test_profit_factor():
    pnl = _make()
    pnl.record_trade("A", 5000, {})
    pnl.record_trade("B", 3000, {})
    pnl.record_trade("C", -2000, {})
    # PF = (5000+3000) / 2000 = 4.0
    assert abs(pnl.profit_factor - 4.0) < 0.01


def test_profit_factor_no_losses():
    pnl = _make()
    pnl.record_trade("A", 5000, {})
    assert pnl.profit_factor == float("inf")


def test_profit_factor_no_trades():
    pnl = _make()
    assert pnl.profit_factor == 0.0


def test_avg_win_loss():
    pnl = _make()
    pnl.record_trade("A", 6000, {})
    pnl.record_trade("A", 4000, {})
    pnl.record_trade("B", -2000, {})
    pnl.record_trade("B", -1000, {})
    assert pnl.avg_win == 5000.0
    assert pnl.avg_loss == -1500.0


def test_summary_has_daily_and_metrics():
    pnl = _make()
    pnl.record_trade("A", 5000, {})
    pnl.record_trade("B", -1000, {})
    s = pnl.get_summary()
    assert "daily" in s
    assert s["daily"]["trades"] == 2
    assert s["daily"]["realised_pnl"] == 4000
    assert "max_drawdown" in s
    assert "profit_factor" in s
    assert "avg_win" in s
    assert "avg_loss" in s


def test_save_restore_drawdown():
    pnl1 = _make()
    pnl1.record_trade("A", 5000, {})
    pnl1.record_trade("B", -3000, {})
    state = pnl1.save_state()
    pnl2 = _make()
    pnl2.restore_state(state)
    assert pnl2.max_drawdown == pnl1.max_drawdown
    assert pnl2._trade_pnls == pnl1._trade_pnls


def _cleanup():
    import shutil
    if os.path.isdir("/tmp/test_pnl_eng"):
        shutil.rmtree("/tmp/test_pnl_eng")


if __name__ == "__main__":
    passed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            passed += 1
            print(f"  [PASS] {name}")
    _cleanup()
    print(f"\n✅ {passed} PnLEngine tests passed")
