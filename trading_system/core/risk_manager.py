"""
Risk manager (agent.md §13).

Tracks daily/monthly P&L and enforces loss limits.
Halts trading when daily limit is breached.
Halves position size when monthly drawdown limit is breached.
"""

from __future__ import annotations

import logging
from typing import Any

from trading_system.config import settings

logger = logging.getLogger(__name__)

_MAX_LOTS = {
    "A": settings.SA_MAX_LOTS,
    "B": settings.SB_MAX_SPREADS,
    "C": settings.SC_MAX_LOTS,
    "D": settings.SD_MAX_LOTS,
    "E": settings.SE_MAX_LOTS,
}


class RiskManager:
    def __init__(self) -> None:
        self.daily_pnl: float = 0.0
        self.monthly_pnl: float = 0.0
        self.trades_today: int = 0
        self.halted: bool = False
        self._daily_loss_limit: float = float(settings.LOSS_LIMIT_CALM)

    def update_loss_limit(self, limit: float) -> None:
        self._daily_loss_limit = limit

    def update_pnl(self, pnl: float) -> None:
        self.daily_pnl += pnl
        self.monthly_pnl += pnl
        self.trades_today += 1
        if self.daily_pnl <= -self._daily_loss_limit:
            self.halted = True
            logger.critical(
                "DAILY LOSS LIMIT ₹%s HIT (daily_pnl=₹%s) — HALTED",
                f"{self._daily_loss_limit:,.0f}",
                f"{self.daily_pnl:,.0f}",
            )

    def can_trade(self) -> bool:
        return not self.halted

    def allowed_lots(self, strategy: str, size_mult: float) -> int:
        base = _MAX_LOTS.get(strategy, 1)
        mult = size_mult
        if self.monthly_pnl <= -settings.MONTHLY_DD_LIMIT:
            mult *= 0.5
            logger.warning(
                "Monthly DD limit hit (₹%s) — halving size multiplier",
                f"{self.monthly_pnl:,.0f}",
            )
        return max(1, int(base * mult))

    def per_trade_risk(self) -> float:
        return settings.CAPITAL * settings.MAX_RISK_PCT_TRADE

    def reset_daily(self) -> None:
        self.daily_pnl = 0.0
        self.trades_today = 0
        self.halted = False
