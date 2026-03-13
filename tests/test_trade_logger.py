"""Tests for TradeLogger and GoLiveEvaluator."""
import sys, os, csv, shutil
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pandas as pd
from trading_system.core.trade_logger import TradeLogger, TRADE_COLUMNS
from trading_system.paper.go_live_evaluator import GoLiveEvaluator

TEST_DIR = "/tmp/test_trade_logger"


def _make():
    if os.path.isdir(TEST_DIR):
        shutil.rmtree(TEST_DIR)
    return TradeLogger(data_dir=TEST_DIR)


# ══════════════════════════════════════════════════════════════════════
# TradeLogger
# ══════════════════════════════════════════════════════════════════════

def test_logger_creates_csv_header():
    tl = _make()
    assert os.path.exists(tl._trades_path)
    with open(tl._trades_path) as f:
        reader = csv.reader(f)
        header = next(reader)
    assert header == TRADE_COLUMNS


def test_logger_log_trade():
    tl = _make()
    tid = tl.log_trade({"strategy": "A", "pnl": 5000, "net_pnl": 5000, "direction": "SELL"})
    assert tid.endswith("_0001")
    with open(tl._trades_path) as f:
        lines = f.readlines()
    assert len(lines) == 2  # header + 1 trade


def test_logger_increments_trade_id():
    tl = _make()
    t1 = tl.log_trade({"strategy": "A"})
    t2 = tl.log_trade({"strategy": "B"})
    assert t1 != t2
    assert t1.endswith("_0001")
    assert t2.endswith("_0002")


def test_logger_resumes_counter():
    tl = _make()
    tl.log_trade({"strategy": "A"})
    tl.log_trade({"strategy": "B"})
    tl.log_trade({"strategy": "C"})
    # Simulate restart by creating a new TradeLogger pointing at same dir
    tl2 = TradeLogger(data_dir=TEST_DIR)
    t4 = tl2.log_trade({"strategy": "D"})
    assert t4.endswith("_0004")  # resumed from 3


def test_logger_log_signal():
    tl = _make()
    tl.log_signal("Test signal message")
    assert os.path.exists(tl._signals_path)
    with open(tl._signals_path) as f:
        content = f.read()
    assert "Test signal message" in content


def test_logger_log_signal_structured():
    tl = _make()
    tl.log_signal(regime="CALM", day_type="RANGING", action="SKIP")
    with open(tl._signals_path) as f:
        content = f.read()
    assert "CALM" in content
    assert "RANGING" in content


def test_logger_strategy_key_aliases_in_csv():
    """log_trade with strategy-style keys (reason, pnl, entry_time) → CSV has exit_reason, net_pnl, time_entry."""
    tl = _make()
    trade = {
        "strategy": "A",
        "reason": "TARGET_HIT",
        "pnl": 1500.0,
        "entry_time": "10:15:00",
    }
    tl.log_trade(trade)
    with open(tl._trades_path, newline="") as f:
        reader = csv.DictReader(f)
        row = next(reader)
    assert row.get("exit_reason") == "TARGET_HIT"
    assert row.get("net_pnl") == "1500.0"
    assert row.get("time_entry") == "10:15:00"


# ══════════════════════════════════════════════════════════════════════
# GoLiveEvaluator
# ══════════════════════════════════════════════════════════════════════

def _summary(total=25, wins=18, realised=50000, strat_a_wr=62, strat_b_wr=50,
              strat_c_trades=3, strat_c_wr=55, strat_d_trades=5, strat_d_wr=60):
    return {
        "total_trades": total,
        "winning_trades": wins,
        "win_rate_pct": (wins / total * 100) if total else 0,
        "realised_pnl": realised,
        "strategy_stats": {
            "A": {"trades": 10, "win_rate": strat_a_wr, "total_pnl": 20000},
            "B": {"trades": 8, "win_rate": strat_b_wr, "total_pnl": 15000},
            "C": {"trades": strat_c_trades, "win_rate": strat_c_wr, "total_pnl": 5000},
            "D": {"trades": strat_d_trades, "win_rate": strat_d_wr, "total_pnl": 10000},
            "E": {"trades": 2, "win_rate": 50, "total_pnl": 5000},
        },
    }


def _trades_df(n=25, days=6):
    rows = []
    for i in range(n):
        rows.append({
            "date": f"2026-03-{(i % days) + 1:02d}",
            "time_exit": "13:30",
            "net_pnl": 2000 if i % 3 != 0 else -1000,
            "strategy": "A",
            "regime_entry": "CALM",
            "target_hit_today": "False",
        })
    return pd.DataFrame(rows)


def test_evaluator_all_green():
    ev = GoLiveEvaluator()
    result = ev.evaluate(_summary(), _trades_df())
    assert result["score"] > 0
    assert "verdict" in result


def test_evaluator_min_trades_fail():
    ev = GoLiveEvaluator()
    result = ev.evaluate(_summary(total=5, wins=4))
    assert result["checks"]["min_trades"] is False


def test_evaluator_win_rate_fail():
    ev = GoLiveEvaluator()
    result = ev.evaluate(_summary(total=20, wins=8))
    assert result["checks"]["overall_wr"] is False


def test_evaluator_strat_a_wr_fail():
    ev = GoLiveEvaluator()
    result = ev.evaluate(_summary(strat_a_wr=50))
    assert result["checks"]["strat_a_wr"] is False


def test_evaluator_net_pnl_positive():
    ev = GoLiveEvaluator()
    result = ev.evaluate(_summary(realised=1000))
    assert result["checks"]["net_pnl_pos"] is True


def test_evaluator_net_pnl_negative():
    ev = GoLiveEvaluator()
    result = ev.evaluate(_summary(realised=-5000))
    assert result["checks"]["net_pnl_pos"] is False


def test_evaluator_no_late_positions():
    ev = GoLiveEvaluator()
    df = _trades_df()
    result = ev.evaluate(_summary(), df)
    assert result["checks"]["no_late_pos"] is True


def test_evaluator_late_position_detected():
    ev = GoLiveEvaluator()
    df = _trades_df()
    df.loc[0, "time_exit"] = "14:20"
    result = ev.evaluate(_summary(), df)
    assert result["checks"]["no_late_pos"] is False


def test_evaluator_no_routing_violations():
    ev = GoLiveEvaluator()
    df = _trades_df()
    result = ev.evaluate(_summary(), df)
    assert result["checks"]["no_routing_viol"] is True


def test_evaluator_routing_violation_detected():
    ev = GoLiveEvaluator()
    df = _trades_df()
    df.loc[0, "regime_entry"] = "DANGER"
    df.loc[0, "strategy"] = "A"  # forbidden in DANGER
    result = ev.evaluate(_summary(), df)
    assert result["checks"]["no_routing_viol"] is False


def test_evaluator_min_days():
    ev = GoLiveEvaluator()
    result = ev.evaluate(_summary(), _trades_df(days=6))
    assert result["checks"]["min_days"] is True


def test_evaluator_too_few_days():
    ev = GoLiveEvaluator()
    result = ev.evaluate(_summary(), _trades_df(days=2))
    assert result["checks"]["min_days"] is False


def test_evaluator_empty_trades():
    ev = GoLiveEvaluator()
    result = ev.evaluate(_summary(), pd.DataFrame())
    assert "score" in result


def test_evaluator_strat_c_untriggered_fails():
    ev = GoLiveEvaluator()
    result = ev.evaluate(_summary(strat_c_trades=0))
    assert result["checks"]["strat_c_wr"] is False
    assert any("Strategy C" in w for w in result["warnings"])


def test_evaluator_strat_d_untriggered_fails():
    ev = GoLiveEvaluator()
    result = ev.evaluate(_summary(strat_d_trades=0))
    assert result["checks"]["strat_d_wr"] is False
    assert any("Strategy D" in w for w in result["warnings"])


def test_evaluator_strat_c_triggered_passes():
    ev = GoLiveEvaluator()
    result = ev.evaluate(_summary(strat_c_trades=5, strat_c_wr=55))
    assert result["checks"]["strat_c_wr"] is True


def test_evaluator_regime_specific_breach():
    ev = GoLiveEvaluator()
    rows = []
    for i in range(10):
        rows.append({
            "date": "2026-03-01",
            "time_exit": "13:30",
            "net_pnl": -900,
            "strategy": "A",
            "regime_entry": "DANGER",
            "target_hit_today": "False",
        })
    df = pd.DataFrame(rows)
    # Total loss = -9000, DANGER limit = 7500 → breach
    result = ev.evaluate(_summary(), df)
    assert result["checks"]["no_loss_breach"] is False


def test_evaluator_calm_day_within_limit():
    ev = GoLiveEvaluator()
    rows = []
    for i in range(5):
        rows.append({
            "date": "2026-03-01",
            "time_exit": "13:30",
            "net_pnl": -3000,
            "strategy": "A",
            "regime_entry": "CALM",
            "target_hit_today": "False",
        })
    df = pd.DataFrame(rows)
    # Total loss = -15000, CALM limit = 20000 → within limit
    result = ev.evaluate(_summary(), df)
    assert result["checks"]["no_loss_breach"] is True


def test_evaluator_has_warnings():
    ev = GoLiveEvaluator()
    result = ev.evaluate(_summary(), _trades_df())
    assert "warnings" in result
    assert isinstance(result["warnings"], list)


def test_evaluator_missing_columns_no_crash():
    """evaluate() with trades_df missing key columns does not crash; returns verdict."""
    ev = GoLiveEvaluator()
    df = pd.DataFrame([{"only_col": 1}])  # missing date, net_pnl, regime_entry, etc.
    result = ev.evaluate(_summary(), df)
    assert "verdict" in result
    assert "checks" in result
    assert result["score"] >= 0


def test_evaluator_nan_in_columns_no_crash():
    """evaluate() with NaN in time_exit / regime_entry does not crash."""
    ev = GoLiveEvaluator()
    df = pd.DataFrame([
        {"date": "2026-03-01", "time_exit": None, "net_pnl": 1000, "strategy": "A", "regime_entry": None, "target_hit_today": "False"},
        {"date": "2026-03-01", "time_exit": "14:00", "net_pnl": -500, "strategy": "B", "regime_entry": "CALM", "target_hit_today": "False"},
    ])
    result = ev.evaluate(_summary(), df)
    assert "verdict" in result
    assert "checks" in result


def _cleanup():
    if os.path.isdir(TEST_DIR):
        shutil.rmtree(TEST_DIR)


if __name__ == "__main__":
    passed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            passed += 1
            print(f"  [PASS] {name}")
    _cleanup()
    print(f"\n✅ {passed} TradeLogger+GoLive tests passed")
