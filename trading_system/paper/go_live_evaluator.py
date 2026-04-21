"""Go-Live evaluator.

Runs a real 15-point readiness check over paper-trading data. Every check
verifies against the trade log, order log, or snapshot — none are hardcoded.
A failing check blocks go-live; the verdict is GO LIVE only if all checks pass.
"""

import logging
from datetime import datetime
from typing import Dict, Optional
import pandas as pd

from trading_system.config import settings

logger = logging.getLogger(__name__)


class GoLiveEvaluator:
    def evaluate(
        self,
        summary: Dict,
        trades_df: Optional[pd.DataFrame] = None,
        orders_df: Optional[pd.DataFrame] = None,
    ) -> Dict:
        if trades_df is None or trades_df.empty:
            trades_df = pd.DataFrame()
        if orders_df is None or orders_df.empty:
            orders_df = pd.DataFrame()

        c: Dict[str, bool] = {}

        c["min_trades"] = summary.get("total_trades", 0) >= settings.GL_MIN_TRADES
        c["overall_wr"] = summary.get("win_rate_pct", 0) > settings.GL_OVERALL_WR
        c["net_pnl_pos"] = summary.get("realised_pnl", 0) > 0
        c["ev_positive"] = self._win_loss_ratio(trades_df) >= settings.GL_WIN_LOSS_RATIO

        harvest_count = (
            int((trades_df["exit_reason"] == "PROFIT_HARVEST").sum())
            if not trades_df.empty and "exit_reason" in trades_df.columns
            else 0
        )
        c["tsl_exits"] = harvest_count >= settings.GL_MIN_TSL_EXITS

        c["no_trending_entries"] = self._no_trending_entries(trades_df)

        vix_viols = (
            int((trades_df["vix_entry"] > settings.IC_VIX_MAX).sum())
            if not trades_df.empty and "vix_entry" in trades_df.columns
            else 0
        )
        c["no_vix_violations"] = vix_viols == 0

        c["credit_rule_compliance"] = self._credit_compliant(trades_df)
        c["hard_exit_respected"] = self._count_late_exits(trades_df) == 0
        c["plausible_win_rate"] = self._plausible_win_rate(summary)

        days_traded = (
            trades_df["date"].nunique()
            if not trades_df.empty and "date" in trades_df.columns
            else 0
        )
        c["min_days"] = days_traded >= settings.GL_MIN_DAYS
        c["max_drawdown_ok"] = summary.get("max_drawdown_pct", 0) < settings.GL_MAX_DD_PCT
        c["slippage_simulated"] = self._slippage_applied(orders_df)
        c["execution_stability"] = not summary.get("unmarked_positions")
        c["strategy_discipline"] = self._ic_instruments_only(trades_df)

        # Pandas-backed checks may produce numpy.bool_; coerce to native bool
        # so callers can compare with `is True`/`is False`.
        c = {k: bool(v) for k, v in c.items()}
        score = sum(c.values())
        total = len(c)
        return {
            "checks": c,
            "score": score,
            "total": total,
            "all_green": score == total,
            "verdict": "GO LIVE" if score == total else f"KEEP PAPER TRADING ({score}/{total})",
        }

    @staticmethod
    def _win_loss_ratio(df: pd.DataFrame) -> float:
        if df.empty or "net_pnl" not in df.columns:
            return 0.0
        pnl = pd.to_numeric(df["net_pnl"], errors="coerce").dropna()
        wins = pnl[pnl > 0]
        losses = pnl[pnl < 0]
        avg_win = wins.mean() if len(wins) > 0 else 0.0
        avg_loss = abs(losses.mean()) if len(losses) > 0 else 0.0
        if avg_loss == 0:
            return float("inf") if avg_win > 0 else 0.0
        return avg_win / avg_loss

    @staticmethod
    def _no_trending_entries(df: pd.DataFrame) -> bool:
        if df.empty or "day_type" not in df.columns:
            return True
        col = df["day_type"].fillna("").astype(str)
        non_blank_non_ranging = col[(col != "") & (col != "RANGING")]
        return len(non_blank_non_ranging) == 0

    @staticmethod
    def _credit_compliant(df: pd.DataFrame) -> bool:
        if df.empty or "entry_credit" not in df.columns:
            return True
        credits = pd.to_numeric(df["entry_credit"], errors="coerce").dropna()
        if credits.empty:
            return True
        return bool((credits >= settings.IC_MIN_CREDIT).all())

    @staticmethod
    def _plausible_win_rate(summary: Dict) -> bool:
        # Paper-layer generosity trap: 100% win rate over a meaningful sample
        # is almost never the strategy — it's the fill model lying. Block
        # go-live until reconciliation proves otherwise.
        trades = summary.get("total_trades", 0)
        wr = summary.get("win_rate_pct", 0)
        if trades < settings.GL_PLAUSIBLE_WR_MIN_TRADES:
            return True
        return wr <= settings.GL_MAX_PLAUSIBLE_WR

    @staticmethod
    def _slippage_applied(orders_df: pd.DataFrame) -> bool:
        if orders_df.empty:
            return False
        if "status" in orders_df.columns:
            complete = orders_df[orders_df["status"] == "COMPLETE"]
        else:
            complete = orders_df
        if complete.empty:
            return False
        stt = pd.to_numeric(complete.get("stt", 0), errors="coerce").fillna(0)
        brok = pd.to_numeric(complete.get("brokerage", 0), errors="coerce").fillna(0)
        return bool((stt > 0).any() or (brok > 0).any())

    @staticmethod
    def _ic_instruments_only(df: pd.DataFrame) -> bool:
        if df.empty or "instrument" not in df.columns:
            return True
        instruments = set(df["instrument"].dropna().astype(str).unique()) - {""}
        return instruments.issubset({"NIFTY", "BANKNIFTY"})

    @staticmethod
    def _count_late_exits(df: pd.DataFrame) -> int:
        if df.empty or "time_exit" not in df.columns:
            return 0
        trade_end = datetime.strptime(settings.TRADE_END, "%H:%M").time()
        count = 0
        for t in df["time_exit"].dropna():
            try:
                exit_t = datetime.strptime(str(t), "%H:%M:%S").time()
                if exit_t > trade_end:
                    count += 1
            except (ValueError, TypeError):
                continue
        return count
