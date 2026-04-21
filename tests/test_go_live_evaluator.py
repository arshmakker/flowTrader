"""Regression tests for GoLiveEvaluator.

Covers bugs:
  A — total == len(checks); verdict reachable
  B — credit_rule_compliance is a real check, not hardcoded True
  C — slippage_simulated needs non-zero costs in orders_df
  D — execution_stability requires unmarked_positions == []
  E — strategy_discipline requires only NIFTY/BANKNIFTY instruments
  F — max_drawdown_ok reads max_drawdown_pct from summary
  H — no_trending_entries treats blank day_type as unknown, not violation
  I — plausible_win_rate fails when win_rate > GL_MAX_PLAUSIBLE_WR and trades >= min
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pandas as pd

from trading_system.paper.go_live_evaluator import GoLiveEvaluator
from trading_system.config import settings


def _perfect_summary():
    """Summary that passes every summary-level check on its own."""
    return {
        "total_trades": 50,
        "win_rate_pct": 75.0,
        "realised_pnl": 50000.0,
        "max_drawdown_pct": 2.0,
        "unmarked_positions": [],
    }


def _perfect_trades():
    """Trades DataFrame that passes every trade-level check."""
    rows = []
    for i in range(60):  # 60 trades over 15 days
        rows.append({
            "date": f"2026-03-{(i % 15) + 1:02d}",
            "time_exit": "14:00:00",
            "instrument": "NIFTY" if i % 2 else "BANKNIFTY",
            "net_pnl": 1500.0 if i % 4 else -500.0,   # 75% win rate, ratio ~3
            "entry_credit": settings.IC_MIN_CREDIT + 5,
            "exit_reason": "PROFIT_HARVEST" if i < 30 else "TARGET_HIT",
            "vix_entry": 15.0,
            "day_type": "RANGING",
        })
    return pd.DataFrame(rows)


def _perfect_orders():
    return pd.DataFrame([
        {"status": "COMPLETE", "stt": 1.2, "brokerage": 5.0},
        {"status": "COMPLETE", "stt": 0.0, "brokerage": 5.0},
    ])


# ── Bug A — total matches checks, verdict reachable ─────────────────

def test_total_matches_checks_count():
    res = GoLiveEvaluator().evaluate(_perfect_summary(), _perfect_trades(), _perfect_orders())
    assert res["total"] == len(res["checks"]), (
        "total must equal the number of checks performed"
    )


def test_all_green_verdict_is_reachable():
    res = GoLiveEvaluator().evaluate(_perfect_summary(), _perfect_trades(), _perfect_orders())
    assert res["all_green"] is True
    assert res["verdict"] == "GO LIVE"


# ── Bug B — credit_rule_compliance is a real check ──────────────────

def test_credit_rule_compliance_fails_when_any_trade_below_min_credit():
    df = _perfect_trades().copy()
    df.loc[0, "entry_credit"] = settings.IC_MIN_CREDIT - 1  # one violation
    res = GoLiveEvaluator().evaluate(_perfect_summary(), df, _perfect_orders())
    assert res["checks"]["credit_rule_compliance"] is False


def test_credit_rule_compliance_passes_when_all_meet_min():
    res = GoLiveEvaluator().evaluate(_perfect_summary(), _perfect_trades(), _perfect_orders())
    assert res["checks"]["credit_rule_compliance"] is True


# ── Bug C — slippage_simulated looks at orders_df ────────────────────

def test_slippage_simulated_fails_when_orders_df_is_empty():
    res = GoLiveEvaluator().evaluate(_perfect_summary(), _perfect_trades(), None)
    assert res["checks"]["slippage_simulated"] is False


def test_slippage_simulated_fails_when_all_costs_zero():
    orders = pd.DataFrame([{"status": "COMPLETE", "stt": 0.0, "brokerage": 0.0}])
    res = GoLiveEvaluator().evaluate(_perfect_summary(), _perfect_trades(), orders)
    assert res["checks"]["slippage_simulated"] is False


def test_slippage_simulated_passes_when_costs_nonzero():
    res = GoLiveEvaluator().evaluate(_perfect_summary(), _perfect_trades(), _perfect_orders())
    assert res["checks"]["slippage_simulated"] is True


# ── Bug D — execution_stability requires unmarked_positions == [] ───

def test_execution_stability_fails_with_unmarked_positions():
    summary = _perfect_summary()
    summary["unmarked_positions"] = ["NIFTY28APR26C23000"]
    res = GoLiveEvaluator().evaluate(summary, _perfect_trades(), _perfect_orders())
    assert res["checks"]["execution_stability"] is False


def test_execution_stability_passes_when_all_marked():
    res = GoLiveEvaluator().evaluate(_perfect_summary(), _perfect_trades(), _perfect_orders())
    assert res["checks"]["execution_stability"] is True


# ── Bug E — strategy_discipline requires only IC instruments ────────

def test_strategy_discipline_fails_on_unexpected_instrument():
    df = _perfect_trades().copy()
    df.loc[0, "instrument"] = "RELIANCE"
    res = GoLiveEvaluator().evaluate(_perfect_summary(), df, _perfect_orders())
    assert res["checks"]["strategy_discipline"] is False


def test_strategy_discipline_passes_on_ic_instruments_only():
    res = GoLiveEvaluator().evaluate(_perfect_summary(), _perfect_trades(), _perfect_orders())
    assert res["checks"]["strategy_discipline"] is True


# ── Bug F — max_drawdown_ok reads pct from summary ──────────────────

def test_max_drawdown_ok_fails_when_pct_exceeds_limit():
    summary = _perfect_summary()
    summary["max_drawdown_pct"] = settings.GL_MAX_DD_PCT + 1
    res = GoLiveEvaluator().evaluate(summary, _perfect_trades(), _perfect_orders())
    assert res["checks"]["max_drawdown_ok"] is False


def test_max_drawdown_ok_passes_when_pct_under_limit():
    res = GoLiveEvaluator().evaluate(_perfect_summary(), _perfect_trades(), _perfect_orders())
    assert res["checks"]["max_drawdown_ok"] is True


# ── Bug H — blank day_type not counted as trending violation ────────

def test_no_trending_entries_ignores_blank_day_type():
    df = _perfect_trades().copy()
    df.loc[0, "day_type"] = ""     # blank — unknown, should not count
    df.loc[1, "day_type"] = None   # null — same
    res = GoLiveEvaluator().evaluate(_perfect_summary(), df, _perfect_orders())
    assert res["checks"]["no_trending_entries"] is True


def test_no_trending_entries_fails_on_explicit_trending_row():
    df = _perfect_trades().copy()
    df.loc[0, "day_type"] = "TRENDING"
    res = GoLiveEvaluator().evaluate(_perfect_summary(), df, _perfect_orders())
    assert res["checks"]["no_trending_entries"] is False


# ── Bug I — plausible_win_rate blocks implausibly high win rates ────

def test_plausible_win_rate_fails_when_wr_too_high_over_large_sample():
    summary = _perfect_summary()
    summary["total_trades"] = settings.GL_PLAUSIBLE_WR_MIN_TRADES + 10
    summary["win_rate_pct"] = settings.GL_MAX_PLAUSIBLE_WR + 5
    res = GoLiveEvaluator().evaluate(summary, _perfect_trades(), _perfect_orders())
    assert res["checks"]["plausible_win_rate"] is False


def test_plausible_win_rate_skips_check_on_small_sample():
    summary = _perfect_summary()
    summary["total_trades"] = settings.GL_PLAUSIBLE_WR_MIN_TRADES - 1
    summary["win_rate_pct"] = 100.0
    res = GoLiveEvaluator().evaluate(summary, _perfect_trades(), _perfect_orders())
    assert res["checks"]["plausible_win_rate"] is True


def test_plausible_win_rate_passes_at_reasonable_wr():
    res = GoLiveEvaluator().evaluate(_perfect_summary(), _perfect_trades(), _perfect_orders())
    assert res["checks"]["plausible_win_rate"] is True


# ── Sanity — one failing check does not silently green the verdict ──

def test_single_failure_downgrades_verdict():
    summary = _perfect_summary()
    summary["unmarked_positions"] = ["X"]
    res = GoLiveEvaluator().evaluate(summary, _perfect_trades(), _perfect_orders())
    assert res["all_green"] is False
    assert res["verdict"].startswith("KEEP PAPER TRADING")
    assert res["score"] == res["total"] - 1
