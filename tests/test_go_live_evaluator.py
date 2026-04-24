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
  LIVE-21 — live_reconciled_trades gates the verdict on N days of broker
            reconciliation reports with per-leg drift within threshold.
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


def _clean_report(date: str, pairs: int = 4):
    """A LIVE-08 reconciliation report with ``pairs`` matched legs, zero drift,
    no unmatched legs — i.e. a clean day for the LIVE-21 gate."""
    return {
        "date": date,
        "engine_leg_count": pairs,
        "broker_leg_count": pairs,
        "matched_count": pairs,
        "unmatched_engine_count": 0,
        "unmatched_broker_count": 0,
        "flagged_count": 0,
        "matched": [
            {"symbol": f"LEG{i}", "side": "SELL", "price_delta_pct": 0.0}
            for i in range(pairs)
        ],
        "unmatched_engine": [],
        "unmatched_broker": [],
        "flagged": [],
    }


def _perfect_reports():
    """GL_MIN_RECONCILED_DAYS worth of clean reports — the all-green baseline."""
    return [
        _clean_report(f"2026-03-{d + 1:02d}")
        for d in range(settings.GL_MIN_RECONCILED_DAYS)
    ]


# ── Bug A — total matches checks, verdict reachable ─────────────────

def test_total_matches_checks_count():
    res = GoLiveEvaluator().evaluate(_perfect_summary(), _perfect_trades(), _perfect_orders())
    assert res["total"] == len(res["checks"]), (
        "total must equal the number of checks performed"
    )


def test_all_green_verdict_is_reachable():
    res = GoLiveEvaluator().evaluate(
        _perfect_summary(), _perfect_trades(), _perfect_orders(),
        reconciliation_reports=_perfect_reports(),
    )
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
    res = GoLiveEvaluator().evaluate(
        summary, _perfect_trades(), _perfect_orders(),
        reconciliation_reports=_perfect_reports(),
    )
    assert res["all_green"] is False
    assert res["verdict"].startswith("KEEP PAPER TRADING")
    assert res["score"] == res["total"] - 1


# ── LIVE-21 — verdict gated on engine-vs-broker reconciliation artefacts ──

def test_live_reconciled_trades_fails_without_reports_even_on_perfect_paper():
    """The meta-trap: perfect paper numbers must NOT verdict GO LIVE until
    at least GL_MIN_RECONCILED_DAYS of reconciliation reports exist."""
    res = GoLiveEvaluator().evaluate(
        _perfect_summary(), _perfect_trades(), _perfect_orders(),
    )  # reconciliation_reports defaults to None
    assert res["checks"]["live_reconciled_trades"] is False
    assert res["all_green"] is False
    assert res["verdict"].startswith("KEEP PAPER TRADING")


def test_live_reconciled_trades_fails_with_empty_list():
    res = GoLiveEvaluator().evaluate(
        _perfect_summary(), _perfect_trades(), _perfect_orders(),
        reconciliation_reports=[],
    )
    assert res["checks"]["live_reconciled_trades"] is False


def test_live_reconciled_trades_fails_below_min_days():
    """Boundary: N-1 clean days is not enough — the proving period must cover
    GL_MIN_RECONCILED_DAYS distinct days."""
    reports = _perfect_reports()[:-1]
    res = GoLiveEvaluator().evaluate(
        _perfect_summary(), _perfect_trades(), _perfect_orders(),
        reconciliation_reports=reports,
    )
    assert len(reports) == settings.GL_MIN_RECONCILED_DAYS - 1
    assert res["checks"]["live_reconciled_trades"] is False


def test_live_reconciled_trades_passes_at_exactly_min_days():
    res = GoLiveEvaluator().evaluate(
        _perfect_summary(), _perfect_trades(), _perfect_orders(),
        reconciliation_reports=_perfect_reports(),
    )
    assert res["checks"]["live_reconciled_trades"] is True


def test_live_reconciled_trades_fails_on_per_leg_drift_over_threshold():
    """One report with a single leg drifted past the pinned threshold should
    disqualify that day — even if aggregate PnL nets to zero across legs."""
    reports = _perfect_reports()
    over = settings.GL_RECONCILED_PRICE_DRIFT_PCT + 0.001
    reports[0]["matched"][0]["price_delta_pct"] = over
    res = GoLiveEvaluator().evaluate(
        _perfect_summary(), _perfect_trades(), _perfect_orders(),
        reconciliation_reports=reports,
    )
    # one day knocked out — back to N-1 clean days
    assert res["checks"]["live_reconciled_trades"] is False


def test_live_reconciled_trades_per_leg_stricter_than_aggregate():
    """In an IC, opposing legs can cancel at the aggregate level. Our gate is
    per-leg, so +3% on one leg and -3% on another still disqualifies the day
    even though the aggregate nets to zero."""
    reports = _perfect_reports()
    reports[0]["matched"][0]["price_delta_pct"] = 0.03
    reports[0]["matched"][1]["price_delta_pct"] = -0.03
    res = GoLiveEvaluator().evaluate(
        _perfect_summary(), _perfect_trades(), _perfect_orders(),
        reconciliation_reports=reports,
    )
    assert res["checks"]["live_reconciled_trades"] is False


def test_live_reconciled_trades_fails_with_unmatched_engine_legs():
    """An engine leg without a broker counterpart = a phantom fill on the
    paper side. That day doesn't count as clean."""
    reports = _perfect_reports()
    reports[0]["unmatched_engine_count"] = 1
    res = GoLiveEvaluator().evaluate(
        _perfect_summary(), _perfect_trades(), _perfect_orders(),
        reconciliation_reports=reports,
    )
    assert res["checks"]["live_reconciled_trades"] is False


def test_live_reconciled_trades_fails_with_unmatched_broker_legs():
    """A broker leg without an engine counterpart = silent fill the engine
    never booked. That day doesn't count as clean either."""
    reports = _perfect_reports()
    reports[0]["unmatched_broker_count"] = 1
    res = GoLiveEvaluator().evaluate(
        _perfect_summary(), _perfect_trades(), _perfect_orders(),
        reconciliation_reports=reports,
    )
    assert res["checks"]["live_reconciled_trades"] is False


def test_live_reconciled_trades_rejects_empty_matched_list():
    """A report with zero matched legs is not a clean day — it's a no-data day."""
    reports = _perfect_reports()
    reports[0]["matched"] = []
    reports[0]["matched_count"] = 0
    res = GoLiveEvaluator().evaluate(
        _perfect_summary(), _perfect_trades(), _perfect_orders(),
        reconciliation_reports=reports,
    )
    assert res["checks"]["live_reconciled_trades"] is False


def test_live_reconciled_trades_passes_with_surplus_days():
    """More than the minimum is fine — we only gate on the floor."""
    reports = _perfect_reports() + [_clean_report("2026-03-20")]
    res = GoLiveEvaluator().evaluate(
        _perfect_summary(), _perfect_trades(), _perfect_orders(),
        reconciliation_reports=reports,
    )
    assert res["checks"]["live_reconciled_trades"] is True
