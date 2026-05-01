"""
Paper P&L engine (agent.md §16.3).

Tracks realised P&L, win/loss counts per strategy — both daily and cumulative.
Writes data/paper_summary.json every time a trade closes.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from typing import Any, Dict, List, Optional

from trading_system.config import settings

logger = logging.getLogger(__name__)


def _empty_strat_stats() -> Dict[str, Dict]:
    return {s: {"trades": 0, "total_pnl": 0.0, "wins": 0} for s in ("NIFTY", "BANKNIFTY")}


class PaperPnLEngine:
    def __init__(
        self,
        position_tracker: Any,
        market_data: Any,
        trade_logger: Any,
        data_dir: Optional[str] = None,
    ) -> None:
        self.pt = position_tracker
        self.md = market_data
        self.tl = trade_logger
        self._data_dir = data_dir if data_dir is not None else settings.DATA_DIR

        # Cumulative (survives reset_daily, persisted across restarts)
        self.realised_pnl: float = 0.0
        self.total_trades: int = 0
        self.winning_trades: int = 0
        self._strategy_stats: Dict[str, Dict] = _empty_strat_stats()

        # Daily (reset every morning)
        self.daily_realised_pnl: float = 0.0
        self.daily_trades: int = 0
        self.daily_wins: int = 0
        self._daily_strategy_stats: Dict[str, Dict] = _empty_strat_stats()

        # Risk-adjusted tracking
        self._trade_pnls: List[float] = []
        self._peak_pnl: float = 0.0
        self.max_drawdown: float = 0.0
        self._daily_peak_pnl: float = 0.0
        self.daily_max_drawdown: float = 0.0

        os.makedirs(self._data_dir, exist_ok=True)

    def record_trade(self, strategy: str, pnl: float, trade_data: Dict) -> None:
        won = pnl > 0

        # Cumulative
        self.realised_pnl += pnl
        self.total_trades += 1
        if won:
            self.winning_trades += 1

        if strategy not in self._strategy_stats:
            self._strategy_stats[strategy] = {"trades": 0, "total_pnl": 0.0, "wins": 0}

        ss = self._strategy_stats[strategy]
        ss["trades"] += 1
        ss["total_pnl"] += pnl
        if won:
            ss["wins"] += 1

        # Daily
        self.daily_realised_pnl += pnl
        self.daily_trades += 1
        if won:
            self.daily_wins += 1

        if strategy not in self._daily_strategy_stats:
            self._daily_strategy_stats[strategy] = {"trades": 0, "total_pnl": 0.0, "wins": 0}

        ds = self._daily_strategy_stats[strategy]
        ds["trades"] += 1
        ds["total_pnl"] += pnl
        if won:
            ds["wins"] += 1

        # Drawdown tracking
        self._trade_pnls.append(pnl)
        if self.realised_pnl > self._peak_pnl:
            self._peak_pnl = self.realised_pnl
        dd = self._peak_pnl - self.realised_pnl
        if dd > self.max_drawdown:
            self.max_drawdown = dd

        if self.daily_realised_pnl > self._daily_peak_pnl:
            self._daily_peak_pnl = self.daily_realised_pnl
        daily_dd = self._daily_peak_pnl - self.daily_realised_pnl
        if daily_dd > self.daily_max_drawdown:
            self.daily_max_drawdown = daily_dd

        self.tl.log_trade(trade_data)
        self._write_summary()

    @property
    def win_rate(self) -> float:
        if self.total_trades == 0:
            return 0.0
        return (self.winning_trades / self.total_trades) * 100

    @property
    def daily_win_rate(self) -> float:
        if self.daily_trades == 0:
            return 0.0
        return (self.daily_wins / self.daily_trades) * 100

    @property
    def unrealised_pnl(self) -> float:
        return self.pt.get_unrealised_pnl(self.md)

    @property
    def total_pnl(self) -> float:
        return self.realised_pnl + self.unrealised_pnl

    @property
    def profit_factor(self) -> float:
        gross_wins = sum(p for p in self._trade_pnls if p > 0)
        gross_losses = abs(sum(p for p in self._trade_pnls if p < 0))
        if gross_losses == 0:
            return float("inf") if gross_wins > 0 else 0.0
        return gross_wins / gross_losses

    @property
    def avg_win(self) -> float:
        wins = [p for p in self._trade_pnls if p > 0]
        return sum(wins) / len(wins) if wins else 0.0

    @property
    def avg_loss(self) -> float:
        losses = [p for p in self._trade_pnls if p < 0]
        return sum(losses) / len(losses) if losses else 0.0

    @property
    def max_drawdown_pct(self) -> float:
        # Drawdown as a percentage of peak realised P&L. Meaningful only once
        # we've had a positive peak; before that, pct is undefined (return 0).
        if self._peak_pnl <= 0:
            return 0.0
        return (self.max_drawdown / self._peak_pnl) * 100

    def _strat_summary(self, stats: Dict[str, Dict]) -> Dict[str, Dict]:
        out = {}
        for k, v in stats.items():
            wr = (v["wins"] / v["trades"] * 100) if v["trades"] > 0 else 0.0
            out[k] = {
                "trades": v["trades"],
                "total_pnl": v["total_pnl"],
                "win_rate": round(wr, 1),
            }
        return out

    def get_summary(self) -> Dict:
        unmarked = getattr(self.pt, "unmarked_symbols", [])
        return {
            "timestamp": datetime.now().isoformat(),
            "realised_pnl": round(self.realised_pnl, 2),
            "unrealised_pnl": round(self.unrealised_pnl, 2),
            "total_pnl": round(self.total_pnl, 2),
            "total_trades": self.total_trades,
            "winning_trades": self.winning_trades,
            "win_rate_pct": round(self.win_rate, 1),
            "strategy_stats": self._strat_summary(self._strategy_stats),
            "max_drawdown": round(self.max_drawdown, 2),
            "max_drawdown_pct": round(self.max_drawdown_pct, 2),
            "profit_factor": round(self.profit_factor, 2) if self.profit_factor != float("inf") else "inf",
            "avg_win": round(self.avg_win, 2),
            "avg_loss": round(self.avg_loss, 2),
            "unmarked_positions": unmarked if unmarked else [],
            "daily": {
                "realised_pnl": round(self.daily_realised_pnl, 2),
                "trades": self.daily_trades,
                "wins": self.daily_wins,
                "win_rate_pct": round(self.daily_win_rate, 1),
                "max_drawdown": round(self.daily_max_drawdown, 2),
                "strategy_stats": self._strat_summary(self._daily_strategy_stats),
            },
        }

    @staticmethod
    def _atomic_write_json(path: str, payload) -> None:
        """BUG-20: write JSON to a tmp path then os.replace onto the final path,
        mirroring position_persistence. Prevents partial/corrupt files on crash."""
        tmp = path + ".tmp"
        try:
            with open(tmp, "w") as f:
                json.dump(payload, f, indent=2)
            os.replace(tmp, path)
        except Exception:
            logger.exception("Failed to atomically write %s", path)
            # Best-effort cleanup of the tmp file so it doesn't linger.
            try:
                if os.path.exists(tmp):
                    os.remove(tmp)
            except Exception:
                pass

    def write_snapshot(self) -> None:
        """Write current P&L snapshot to disk — called every cycle."""
        self._atomic_write_json(
            os.path.join(self._data_dir, "pnl_snapshot.json"),
            self.get_summary(),
        )

    def _write_summary(self) -> None:
        self._atomic_write_json(
            os.path.join(self._data_dir, "paper_summary.json"),
            self.get_summary(),
        )

    # ── Persistence ──────────────────────────────────────────────────────

    def save_state(self) -> Dict:
        return {
            "realised_pnl": self.realised_pnl,
            "total_trades": self.total_trades,
            "winning_trades": self.winning_trades,
            "strategy_stats": self._strategy_stats,
            "daily_realised_pnl": self.daily_realised_pnl,
            "daily_trades": self.daily_trades,
            "daily_wins": self.daily_wins,
            "daily_strategy_stats": self._daily_strategy_stats,
            "trade_pnls": self._trade_pnls,
            "peak_pnl": self._peak_pnl,
            "max_drawdown": self.max_drawdown,
            "daily_peak_pnl": self._daily_peak_pnl,
            "daily_max_drawdown": self.daily_max_drawdown,
        }

    def restore_state(self, state: Dict, *, reset_daily: bool = False) -> None:
        self.realised_pnl = state.get("realised_pnl", 0.0)
        self.total_trades = state.get("total_trades", 0)
        self.winning_trades = state.get("winning_trades", 0)
        saved_stats = state.get("strategy_stats", {})
        if isinstance(saved_stats, dict):
            # Preserve existing defaults, but allow arbitrary strategy keys from saved state.
            self._strategy_stats = {**self._strategy_stats, **saved_stats}

        self._trade_pnls = state.get("trade_pnls", [])
        self._peak_pnl = state.get("peak_pnl", 0.0)
        self.max_drawdown = state.get("max_drawdown", 0.0)
        if reset_daily:
            self.daily_realised_pnl = 0.0
            self.daily_trades = 0
            self.daily_wins = 0
            # Keep daily keys aligned with strategies seen so far
            keys = set(self._strategy_stats.keys()) | set(self._daily_strategy_stats.keys())
            self._daily_strategy_stats = {k: {"trades": 0, "total_pnl": 0.0, "wins": 0} for k in keys}
            self._daily_peak_pnl = 0.0
            self.daily_max_drawdown = 0.0
        else:
            self.daily_realised_pnl = state.get("daily_realised_pnl", 0.0)
            self.daily_trades = state.get("daily_trades", 0)
            self.daily_wins = state.get("daily_wins", 0)
            saved_daily_stats = state.get("daily_strategy_stats", {})
            if isinstance(saved_daily_stats, dict):
                self._daily_strategy_stats = {**self._daily_strategy_stats, **saved_daily_stats}
            self._daily_peak_pnl = state.get("daily_peak_pnl", 0.0)
            self.daily_max_drawdown = state.get("daily_max_drawdown", 0.0)
        logger.info(
            "Restored P&L state: realised=₹%s day=₹%s trades=%d win_rate=%.0f%% max_dd=₹%s%s",
            f"{self.realised_pnl:,.0f}",
            f"{self.daily_realised_pnl:,.0f}",
            self.total_trades,
            self.win_rate,
            f"{self.max_drawdown:,.0f}",
            " [daily reset]" if reset_daily else "",
        )

    def reset_daily(self) -> None:
        """Reset daily counters for a new trading day. Cumulative stats are untouched."""
        logger.info(
            "Daily reset: day_pnl=₹%s day_trades=%d day_wr=%.0f%%",
            f"{self.daily_realised_pnl:,.0f}",
            self.daily_trades,
            self.daily_win_rate,
        )
        self.daily_realised_pnl = 0.0
        self.daily_trades = 0
        self.daily_wins = 0
        # Keep daily keys aligned with strategies seen so far
        keys = set(self._strategy_stats.keys()) | set(self._daily_strategy_stats.keys())
        self._daily_strategy_stats = {k: {"trades": 0, "total_pnl": 0.0, "wins": 0} for k in keys}
        self._daily_peak_pnl = 0.0
        self.daily_max_drawdown = 0.0
