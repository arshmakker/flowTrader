"""
Daily target tracker (agent.md §14).

Once realised P&L reaches the daily target, no new entries are allowed.
Existing positions continue to be monitored until exit.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


class DailyTarget:
    def __init__(self) -> None:
        self._target: float = 0.0
        self._hit: bool = False

    def set(self, amount: float) -> None:
        self._target = amount
        self._hit = False

    @property
    def target(self) -> float:
        return self._target

    def is_hit(self, current_pnl: float) -> bool:
        if not self._hit and self._target > 0 and current_pnl >= self._target:
            self._hit = True
            logger.info("Daily target ₹%s reached — no new entries", f"{self._target:,.0f}")
        return self._hit

    def progress(self, pnl: float) -> str:
        pct = min(pnl / self._target * 100, 100) if self._target else 0.0
        return f"₹{pnl:,.0f} / ₹{self._target:,.0f}  ({pct:.0f}%)"

    def reset(self) -> None:
        self._target = 0.0
        self._hit = False
