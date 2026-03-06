"""
Strategy C — Futures Scalp (agent.md §10).

Condition : Regime CALM only + confidence == 4 (all signals agree) +
            NOT expiry day + no other active strategy
Structure : 1 lot Nifty Futures, direction per consensus
Target    : SC_TARGET_PTS from entry
Stop      : SC_STOP_PTS from entry (HARD, no exceptions)
Hard exit : 14:15 IST
Size      : SC_MAX_LOTS = 1 only
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, time
from typing import Any, Dict, Optional

from trading_system.config import settings

logger = logging.getLogger(__name__)


@dataclass
class FuturesPosition:
    direction: str = ""         # BULL | BEAR
    fut_symbol: str = ""
    entry_price: float = 0.0
    target_price: float = 0.0
    stop_price: float = 0.0
    lots: int = 1
    entry_time: str = ""


class StrategyC:
    """Nifty Futures scalp — ultra-selective, only on CALM + full signal agreement."""

    def __init__(self, order_manager: Any, market_data: Any):
        self.om = order_manager
        self.md = market_data
        self._position: Optional[FuturesPosition] = None

    def is_active(self) -> bool:
        return self._position is not None

    @staticmethod
    def is_expiry_day() -> bool:
        return datetime.today().weekday() == 3  # Thursday

    def should_enter(
        self, regime: str, confidence: int, active_strategies: Dict[str, bool]
    ) -> bool:
        return (
            regime == "CALM"
            and confidence == 4
            and not self.is_expiry_day()
            and not any(active_strategies.values())
            and not self.is_active()
        )

    def enter(
        self, direction: str, fut_symbol: str, now_str: str
    ) -> Optional[Dict]:
        ltp = self.md.get_ltp(fut_symbol)
        if ltp <= 0:
            logger.warning("StrategyC: cannot get futures LTP; skipping")
            return None

        if direction == "BULL":
            target = ltp + settings.SC_TARGET_PTS
            stop = ltp - settings.SC_STOP_PTS
            side = "BUY"
        else:
            target = ltp - settings.SC_TARGET_PTS
            stop = ltp + settings.SC_STOP_PTS
            side = "SELL"

        qty = settings.SC_MAX_LOTS * settings.NIFTY_LOT_SIZE
        self.om.place_order(fut_symbol, side, qty)

        self._position = FuturesPosition(
            direction=direction,
            fut_symbol=fut_symbol,
            entry_price=ltp,
            target_price=target,
            stop_price=stop,
            lots=settings.SC_MAX_LOTS,
            entry_time=now_str,
        )
        logger.info(
            "StratC ENTER %s: %s @ %.2f  T=%.2f S=%.2f",
            direction, fut_symbol, ltp, target, stop,
        )
        return {"strategy": "C", "action": "ENTER", "direction": direction, "entry_price": ltp}

    def monitor(self) -> Optional[Dict]:
        if not self.is_active():
            return None
        pos = self._position
        ltp = self.md.get_ltp(pos.fut_symbol)

        if pos.direction == "BULL":
            pnl = (ltp - pos.entry_price) * pos.lots * settings.NIFTY_LOT_SIZE
            if ltp >= pos.target_price:
                return self.exit("TARGET_HIT", pnl)
            if ltp <= pos.stop_price:
                return self.exit("STOP_HIT", pnl)
        else:
            pnl = (pos.entry_price - ltp) * pos.lots * settings.NIFTY_LOT_SIZE
            if ltp <= pos.target_price:
                return self.exit("TARGET_HIT", pnl)
            if ltp >= pos.stop_price:
                return self.exit("STOP_HIT", pnl)
        return None

    def exit(self, reason: str, pnl: float = 0.0) -> Dict:
        pos = self._position
        side = "SELL" if pos.direction == "BULL" else "BUY"
        qty = pos.lots * settings.NIFTY_LOT_SIZE
        self.om.place_order(pos.fut_symbol, side, qty)
        logger.info("StratC EXIT [%s]: pnl=%.2f", reason, pnl)
        result = {
            "strategy": "C",
            "action": "EXIT",
            "reason": reason,
            "pnl": pnl,
            "direction": pos.direction,
            "entry_price": pos.entry_price,
            "entry_time": pos.entry_time,
        }
        self._position = None
        return result

    def force_exit(self) -> Optional[Dict]:
        if not self.is_active():
            return None
        pos = self._position
        ltp = self.md.get_ltp(pos.fut_symbol)
        if pos.direction == "BULL":
            pnl = (ltp - pos.entry_price) * pos.lots * settings.NIFTY_LOT_SIZE
        else:
            pnl = (pos.entry_price - ltp) * pos.lots * settings.NIFTY_LOT_SIZE
        return self.exit("HARD_CLOSE", pnl)
