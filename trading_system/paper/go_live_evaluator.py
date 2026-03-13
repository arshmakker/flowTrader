"""
Go-Live evaluator (agent.md §20).

All 15 criteria must be green before flipping PAPER_TRADE_MODE = False.
Returns a dict with individual check results, score, and verdict.
"""

from __future__ import annotations

import logging
from datetime import time as dtime, timedelta, datetime as _dt
from typing import Any, Dict, List, Optional

import pandas as pd

from trading_system.config import settings

logger = logging.getLogger(__name__)

REGIME_LOSS_LIMITS = {
    "CALM": settings.LOSS_LIMIT_CALM,
    "NORMAL": settings.LOSS_LIMIT_NORMAL,
    "ELEVATED": settings.LOSS_LIMIT_ELEVATED,
    "DANGER": settings.LOSS_LIMIT_HIGH_VIX,
    "HIGH_VIX": settings.LOSS_LIMIT_HIGH_VIX,
}


class GoLiveEvaluator:
    def evaluate(self, summary: Dict, trades_df: Optional[pd.DataFrame] = None) -> Dict:
        if trades_df is None or trades_df.empty:
            trades_df = pd.DataFrame()

        s = summary.get("strategy_stats", {})
        c: Dict[str, bool] = {}
        warnings: List[str] = []

        c["min_trades"] = summary.get("total_trades", 0) >= settings.GL_MIN_TRADES
        c["overall_wr"] = summary.get("win_rate_pct", 0) > settings.GL_OVERALL_WR
        c["strat_a_wr"] = (
            s.get("A", {}).get("win_rate", 0) > settings.GL_STRAT_A_WR
            if s.get("A", {}).get("trades", 0) > 0
            else False
        )
        c["strat_b_wr"] = (
            s.get("B", {}).get("win_rate", 0) > settings.GL_STRAT_B_WR
            if s.get("B", {}).get("trades", 0) > 0
            else False
        )

        # C and D: pass if triggered with acceptable WR, FAIL if untriggered (not tested)
        c_trades = s.get("C", {}).get("trades", 0)
        if c_trades > 0:
            c["strat_c_wr"] = s["C"].get("win_rate", 0) > settings.GL_STRAT_C_WR
        else:
            c["strat_c_wr"] = False
            warnings.append("Strategy C never triggered during paper period — untested")

        d_trades = s.get("D", {}).get("trades", 0)
        if d_trades > 0:
            c["strat_d_wr"] = s["D"].get("win_rate", 0) > settings.GL_STRAT_D_WR
        else:
            c["strat_d_wr"] = False
            warnings.append("Strategy D never triggered during paper period — untested")

        c["strat_e_covered"] = (
            s.get("E", {}).get("trades", 0) > 0
            or self._no_danger_day(trades_df)
        )
        if s.get("E", {}).get("trades", 0) == 0 and self._no_danger_day(trades_df):
            warnings.append("Strategy E auto-passed (no elevated VIX days seen) — not actually tested")

        c["net_pnl_pos"] = summary.get("realised_pnl", 0) > 0
        c["ev_positive"] = self._win_loss_ratio(trades_df) >= settings.GL_WIN_LOSS_RATIO
        c["no_loss_breach"] = self._no_daily_breach(trades_df)
        c["no_late_pos"] = self._no_late_positions(trades_df)
        c["no_routing_viol"] = self._no_routing_violations(trades_df)
        c["target_respected"] = self._no_overtrade(trades_df)
        c["min_days"] = self._count_days(trades_df) >= settings.GL_MIN_DAYS
        c["wr_trend"] = self._wr_trend_ok(trades_df)

        score = sum(c.values())
        return {
            "checks": c,
            "score": score,
            "total": 15,
            "all_green": score == 15,
            "verdict": "GO LIVE" if score == 15 else f"KEEP PAPER TRADING ({score}/15)",
            "warnings": warnings,
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
        """Check that no day had cumulative loss exceeding its regime-specific limit."""
        if df.empty or "net_pnl" not in df.columns or "date" not in df.columns:
            return True
        for _, day_group in df.groupby("date"):
            cum = day_group["net_pnl"].astype(float).sum()
            if cum >= 0:
                continue
            regime = "CALM"
            if "regime_entry" in day_group.columns:
                regimes = day_group["regime_entry"].dropna()
                if not regimes.empty:
                    regime = regimes.iloc[0]
            limit = REGIME_LOSS_LIMITS.get(regime, settings.LOSS_LIMIT_CALM)
            if cum < -limit:
                return False
        return True

    @staticmethod
    def _no_late_positions(df: pd.DataFrame) -> bool:
        if df.empty or "time_exit" not in df.columns:
            return True
        h, m = (int(x) for x in settings.TRADE_END.split(":"))
        trade_end_dt = _dt(2000, 1, 1, h, m)
        late_dt = trade_end_dt + timedelta(minutes=settings.GL_LATE_BUFFER_MIN)
        late_limit = dtime(late_dt.hour, late_dt.minute)

        for t in df["time_exit"].dropna():
            try:
                parts = str(t).split(":")
                exit_t = dtime(int(parts[0]), int(parts[1]))
                if exit_t > late_limit:
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
            return True
        last10 = df.tail(10)
        wins = (last10["net_pnl"].astype(float) > 0).sum()
        return wins >= settings.GL_WR_TREND_FLOOR
