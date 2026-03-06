"""
Paper P&L engine (agent.md §16.3).

Tracks realised P&L, win/loss counts per strategy.
Writes data/paper_summary.json every time a trade closes.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from typing import Any, Dict

logger = logging.getLogger(__name__)

DATA_DIR = "data"


class PaperPnLEngine:
    def __init__(
        self,
        position_tracker: Any,
        market_data: Any,
        trade_logger: Any,
    ) -> None:
        self.pt = position_tracker
        self.md = market_data
        self.tl = trade_logger
        self.realised_pnl: float = 0.0
        self.total_trades: int = 0
        self.winning_trades: int = 0
        self._strategy_stats: Dict[str, Dict] = {
            s: {"trades": 0, "total_pnl": 0.0, "wins": 0}
            for s in ("A", "B", "C", "D", "E")
        }
        os.makedirs(DATA_DIR, exist_ok=True)

    def record_trade(self, strategy: str, pnl: float, trade_data: Dict) -> None:
        self.realised_pnl += pnl
        self.total_trades += 1
        won = pnl > 0
        if won:
            self.winning_trades += 1

        ss = self._strategy_stats.get(strategy, {"trades": 0, "total_pnl": 0.0, "wins": 0})
        ss["trades"] += 1
        ss["total_pnl"] += pnl
        if won:
            ss["wins"] += 1
        self._strategy_stats[strategy] = ss

        self.tl.log_trade(trade_data)
        self._write_summary()

    @property
    def win_rate(self) -> float:
        if self.total_trades == 0:
            return 0.0
        return (self.winning_trades / self.total_trades) * 100

    @property
    def unrealised_pnl(self) -> float:
        return self.pt.get_unrealised_pnl(self.md)

    @property
    def total_pnl(self) -> float:
        return self.realised_pnl + self.unrealised_pnl

    def get_summary(self) -> Dict:
        strat_stats = {}
        for k, v in self._strategy_stats.items():
            wr = (v["wins"] / v["trades"] * 100) if v["trades"] > 0 else 0.0
            strat_stats[k] = {
                "trades": v["trades"],
                "total_pnl": round(v["total_pnl"], 2),
                "win_rate": round(wr, 1),
            }
        return {
            "timestamp": datetime.now().isoformat(),
            "realised_pnl": round(self.realised_pnl, 2),
            "unrealised_pnl": round(self.unrealised_pnl, 2),
            "total_pnl": round(self.total_pnl, 2),
            "total_trades": self.total_trades,
            "winning_trades": self.winning_trades,
            "win_rate_pct": round(self.win_rate, 1),
            "strategy_stats": strat_stats,
        }

    def _write_summary(self) -> None:
        path = os.path.join(DATA_DIR, "paper_summary.json")
        try:
            with open(path, "w") as f:
                json.dump(self.get_summary(), f, indent=2)
        except Exception:
            logger.exception("Failed to write paper summary")

    def reset_daily(self) -> None:
        """Reset for a new day (keeps monthly totals)."""
        pass
