"""
Go-Live evaluator (agent.md §20).

All 15 criteria must be green before flipping PAPER_TRADE_MODE = False.
Returns a dict with individual check results, score, and verdict.
"""

from __future__ import annotations

import logging
from datetime import time as dtime
from typing import Any, Dict, Optional

import pandas as pd

logger = logging.getLogger(__name__)


class GoLiveEvaluator:
    def evaluate(self, summary: Dict, trades_df: Optional[pd.DataFrame] = None) -> Dict:
        if trades_df is None or trades_df.empty:
            trades_df = pd.DataFrame()

        s = summary.get("strategy_stats", {})
        c: Dict[str, bool] = {}

        c["min_trades"] = summary.get("total_trades", 0) >= 20
        c["overall_wr"] = summary.get("win_rate_pct", 0) > 60.0
        c["strat_a_wr"] = (
            s.get("A", {}).get("win_rate", 0) > 58.0
            if s.get("A", {}).get("trades", 0) > 0
            else False
        )
        c["strat_b_wr"] = (
            s.get("B", {}).get("win_rate", 0) > 45.0
            if s.get("B", {}).get("trades", 0) > 0
            else False
        )
        c["strat_c_wr"] = (
            s.get("C", {}).get("win_rate", 0) > 48.0
            if s.get("C", {}).get("trades", 0) > 0
            else True  # pass if never triggered
        )
        c["strat_d_wr"] = (
            s.get("D", {}).get("win_rate", 0) > 45.0
            if s.get("D", {}).get("trades", 0) > 0
            else True
        )
        c["strat_e_covered"] = (
            s.get("E", {}).get("trades", 0) > 0
            or self._no_danger_day(trades_df)
        )
        c["net_pnl_pos"] = summary.get("realised_pnl", 0) > 0
        c["ev_positive"] = self._win_loss_ratio(trades_df) >= 1.3
        c["no_loss_breach"] = self._no_daily_breach(trades_df)
        c["no_late_pos"] = self._no_late_positions(trades_df)
        c["no_routing_viol"] = self._no_routing_violations(trades_df)
        c["target_respected"] = self._no_overtrade(trades_df)
        c["min_days"] = self._count_days(trades_df) >= 5
        c["wr_trend"] = self._wr_trend_ok(trades_df)

        score = sum(c.values())
        return {
            "checks": c,
            "score": score,
            "total": 15,
            "all_green": score == 15,
            "verdict": "GO LIVE" if score == 15 else f"KEEP PAPER TRADING ({score}/15)",
        }

    # ── Helper checks ───────────────────────────────────────────────────

    @staticmethod
    def _no_danger_day(df: pd.DataFrame) -> bool:
        if df.empty or "regime_entry" not in df.columns:
            return True
        return "DANGER" not in df["regime_entry"].values

    @staticmethod
    def _win_loss_ratio(df: pd.DataFrame) -> float:
        if df.empty or "net_pnl" not in df.columns:
            return 0.0
        wins = df[df["net_pnl"] > 0]["net_pnl"]
        losses = df[df["net_pnl"] < 0]["net_pnl"]
        avg_win = wins.mean() if len(wins) > 0 else 0.0
        avg_loss = abs(losses.mean()) if len(losses) > 0 else 1.0
        return avg_win / avg_loss if avg_loss > 0 else float("inf")

    @staticmethod
    def _no_daily_breach(df: pd.DataFrame) -> bool:
        """Check that no day had cumulative loss exceeding its regime limit."""
        if df.empty or "net_pnl" not in df.columns or "date" not in df.columns:
            return True
        for _, day_group in df.groupby("date"):
            cum = day_group["net_pnl"].astype(float).sum()
            if cum < -20_000:  # worst-case CALM limit
                return False
        return True

    @staticmethod
    def _no_late_positions(df: pd.DataFrame) -> bool:
        if df.empty or "time_exit" not in df.columns:
            return True
        for t in df["time_exit"].dropna():
            try:
                parts = str(t).split(":")
                exit_t = dtime(int(parts[0]), int(parts[1]))
                if exit_t > dtime(14, 16):
                    return False
            except (ValueError, IndexError):
                continue
        return True

    @staticmethod
    def _no_routing_violations(df: pd.DataFrame) -> bool:
        if df.empty:
            return True
        if "regime_entry" not in df.columns or "strategy" not in df.columns:
            return True
        danger_trades = df[df["regime_entry"] == "DANGER"]
        forbidden_on_danger = danger_trades[danger_trades["strategy"].isin(["A", "B"])]
        return len(forbidden_on_danger) == 0

    @staticmethod
    def _no_overtrade(df: pd.DataFrame) -> bool:
        if df.empty or "target_hit_today" not in df.columns:
            return True
        return not any(
            str(v).lower() == "true"
            for v in df["target_hit_today"].dropna()
        )

    @staticmethod
    def _count_days(df: pd.DataFrame) -> int:
        if df.empty or "date" not in df.columns:
            return 0
        return df["date"].nunique()

    @staticmethod
    def _wr_trend_ok(df: pd.DataFrame) -> bool:
        if df.empty or "net_pnl" not in df.columns or len(df) < 10:
            return True  # not enough data to judge
        last10 = df.tail(10)
        wins = (last10["net_pnl"].astype(float) > 0).sum()
        return wins >= 4  # at least 40% in last 10 = not deteriorating
