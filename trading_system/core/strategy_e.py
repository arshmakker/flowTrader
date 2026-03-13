"""
Strategy E — Deep ITM Directional (agent.md §12).

Used on high VIX trending days only.
Buys deep ITM option (delta >= 0.70) for near-futures participation
with hard-capped max loss. Must enter before SE_ENTRY_DEADLINE or fall back to D.

Condition : VIX >= 17 + day TRENDING + confidence HIGH/MEDIUM + time <= 10:45
Structure : Buy 1 Deep ITM CE (delta >= 0.70) if UP  |  PE if DOWN
Target    : SE_TARGET_PCT (50%) gain on premium paid
Stop      : SE_STOP_PCT (40%) loss on premium paid
Hard exit : 14:15 IST; also exit on day_type reversal
Size      : SE_MAX_LOTS = 1 only — never scale up

Note: ~45-50% win rate by design. Winning trades compensate for losses.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import time
from typing import Any, Dict, List, Optional

from trading_system.config import settings

logger = logging.getLogger(__name__)


@dataclass
class DeepITMPosition:
    direction: str = ""         # UP | DOWN
    option_symbol: str = ""
    strike: float = 0.0
    opt_type: str = ""          # CE | PE
    entry_price: float = 0.0
    target_price: float = 0.0
    stop_price: float = 0.0
    lots: int = 1
    entry_time: str = ""

    def to_dict(self) -> Dict:
        return {k: getattr(self, k) for k in self.__dataclass_fields__}

    @classmethod
    def from_dict(cls, d: Dict) -> "DeepITMPosition":
        return cls(**{k: d[k] for k in cls.__dataclass_fields__ if k in d})


class StrategyE:
    """Deep ITM directional — limited-risk trend capture in high-VIX environments."""

    def __init__(self, order_manager: Any, market_data: Any):
        self.om = order_manager
        self.md = market_data
        self._position: Optional[DeepITMPosition] = None

    def is_active(self) -> bool:
        return self._position is not None

    def save_state(self) -> Optional[Dict]:
        return {"strategy": "E", "position": self._position.to_dict()} if self._position else None

    def restore_state(self, state: Dict) -> None:
        if state and state.get("position"):
            self._position = DeepITMPosition.from_dict(state["position"])
            logger.info("StratE: restored position from disk")

    @staticmethod
    def _parse_time(s: str) -> time:
        h, m = s.split(":")
        return time(int(h), int(m))

    # ── Strike selection ────────────────────────────────────────────────

    @staticmethod
    def find_deep_itm_strike(
        spot: float, direction: str, chain: List[Dict]
    ) -> Optional[Dict]:
        """
        Pick the strike closest to SE_DELTA_TARGET (0.70) from deep ITM candidates.
        chain entries: {'strike': float, 'type': str, 'delta': float, 'symbol': str, ...}
        CE ITM = strike < spot.  PE ITM = strike > spot.
        """
        opt_type = "CE" if direction == "UP" else "PE"
        candidates = [
            o for o in chain
            if o.get("type") == opt_type
            and abs(o.get("delta", 0)) >= settings.SE_DELTA_FILTER
            and (
                (o["strike"] < spot) if opt_type == "CE" else (o["strike"] > spot)
            )
        ]
        if not candidates:
            return None
        return min(candidates, key=lambda o: abs(abs(o["delta"]) - settings.SE_DELTA_TARGET))

    # ── Entry gate ──────────────────────────────────────────────────────

    def should_enter(
        self, vix: float, day_type: str, confidence: str, now_time: time
    ) -> bool:
        deadline = self._parse_time(settings.SE_ENTRY_DEADLINE)
        return (
            vix >= settings.VIX_NORMAL_HIGH
            and day_type in ("TRENDING_UP", "TRENDING_DOWN")
            and confidence in ("HIGH", "MEDIUM")
            and now_time <= deadline
            and not self.is_active()
        )

    def enter(
        self, direction: str, strike_info: Dict, expiry: str, now_str: str
    ) -> Optional[Dict]:
        opt_type = "CE" if direction == "UP" else "PE"
        strike = strike_info["strike"]
        symbol = self.om.build_option_symbol("NIFTY", expiry, strike, opt_type)
        ltp = self.md.get_ltp(symbol)
        if ltp <= 0:
            logger.warning("StrategyE: cannot get LTP for %s; skipping entry", symbol)
            return None

        target = ltp * (1 + settings.SE_TARGET_PCT)
        stop = ltp * (1 - settings.SE_STOP_PCT)

        qty = settings.SE_MAX_LOTS * settings.NIFTY_LOT_SIZE
        self.om.place_order(symbol, "BUY", qty)

        self._position = DeepITMPosition(
            direction=direction,
            option_symbol=symbol,
            strike=strike,
            opt_type=opt_type,
            entry_price=ltp,
            target_price=target,
            stop_price=stop,
            lots=settings.SE_MAX_LOTS,
            entry_time=now_str,
        )
        logger.info(
            "StratE ENTER %s: %s %s@%.0f  entry=%.2f T=%.2f S=%.2f",
            direction, opt_type, symbol, strike, ltp, target, stop,
        )
        return {"strategy": "E", "action": "ENTER", "direction": direction, "entry_price": ltp}

    # ── Monitor / exit ──────────────────────────────────────────────────

    def monitor(self, current_day_type: Optional[str] = None) -> Optional[Dict]:
        if not self.is_active():
            return None
        pos = self._position
        ltp = self.md.get_ltp(pos.option_symbol)
        if ltp <= 0:
            logger.warning("StratE monitor: LTP=0 for %s — skipping cycle", pos.option_symbol)
            return None
        pnl = (ltp - pos.entry_price) * pos.lots * settings.NIFTY_LOT_SIZE

        if ltp >= pos.target_price:
            return self.exit("TARGET_HIT", pnl)
        if ltp <= pos.stop_price:
            return self.exit("STOP_HIT", pnl)

        if current_day_type is not None:
            expected = "TRENDING_UP" if pos.direction == "UP" else "TRENDING_DOWN"
            if current_day_type != expected and current_day_type != "RANGING":
                return self.exit("DIRECTION_REVERSAL", pnl)
        return None

    def exit(self, reason: str, pnl: float = 0.0) -> Dict:
        pos = self._position
        qty = pos.lots * settings.NIFTY_LOT_SIZE
        order = self.om.place_order(pos.option_symbol, "SELL", qty, track_position=False)
        if order.get("status") != "COMPLETE":
            logger.error("StratE EXIT [%s] failed; preserving position for retry", reason)
            return None
        if getattr(self.om, "tracker", None) is not None:
            self.om.tracker.add_position(order)
        logger.info("StratE EXIT [%s]: pnl=%.2f", reason, pnl)
        result = {
            "strategy": "E",
            "action": "EXIT",
            "reason": reason,
            "pnl": pnl,
            "direction": pos.direction,
            "entry_price": pos.entry_price,
            "lots": pos.lots,
            "entry_time": pos.entry_time,
        }
        self._position = None
        return result

    def force_exit(self) -> Optional[Dict]:
        if not self.is_active():
            return None
        pos = self._position
        ltp = self.md.get_ltp(pos.option_symbol)
        if ltp <= 0:
            logger.error("StratE force_exit: LTP=0 for %s — P&L may be inaccurate", pos.option_symbol)
        pnl = (ltp - pos.entry_price) * pos.lots * settings.NIFTY_LOT_SIZE
        return self.exit("HARD_CLOSE", pnl)
