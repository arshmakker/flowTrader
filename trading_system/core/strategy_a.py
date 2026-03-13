"""
Strategy A — Short Strangle (agent.md §8).

Condition : Regime CALM or NORMAL + consensus RANGE
Structure : Sell OTM Call + Sell OTM Put (SA_OTM_PCT away from spot)
Entry time: 10:00–13:00 only
Target    : SA_TARGET_PCT × premium collected
Stop      : SA_STOP_MULT × premium collected
Hard exit : 14:15 IST (all legs closed simultaneously)
Size      : min(SA_MAX_LOTS, allowed_lots) × regime size_multiplier
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import time
from typing import Any, Dict, Optional

from trading_system.config import settings

logger = logging.getLogger(__name__)


@dataclass
class StranglePosition:
    call_strike: float = 0.0
    put_strike: float = 0.0
    call_symbol: str = ""
    put_symbol: str = ""
    premium_received: float = 0.0   # combined credit at entry
    lots: int = 0
    entry_time: str = ""

    def to_dict(self) -> Dict:
        return {k: getattr(self, k) for k in self.__dataclass_fields__}

    @classmethod
    def from_dict(cls, d: Dict) -> "StranglePosition":
        return cls(**{k: d[k] for k in cls.__dataclass_fields__ if k in d})


class StrategyA:
    """Short Strangle — sell premium on low-VIX ranging days."""

    @staticmethod
    def _parse_time(s: str) -> time:
        h, m = s.split(":")
        return time(int(h), int(m))

    @property
    def ENTRY_START(self):
        return self._parse_time(settings.SA_ENTRY_START)

    @property
    def ENTRY_END(self):
        return self._parse_time(settings.SA_ENTRY_END)

    def __init__(self, order_manager: Any, market_data: Any):
        self.om = order_manager
        self.md = market_data
        self._position: Optional[StranglePosition] = None

    def is_active(self) -> bool:
        return self._position is not None

    def save_state(self) -> Optional[Dict]:
        return {"strategy": "A", "position": self._position.to_dict()} if self._position else None

    def restore_state(self, state: Dict) -> None:
        if state and state.get("position"):
            self._position = StranglePosition.from_dict(state["position"])
            logger.info("StratA: restored position from disk")

    # ── Strike selection ────────────────────────────────────────────────

    @staticmethod
    def get_strikes(spot: float, instrument: str = "NIFTY") -> tuple[float, float]:
        step = settings.NIFTY_STRIKE_STEP if instrument == "NIFTY" else settings.BANKNIFTY_STRIKE_STEP
        call_strike = round(spot * (1 + settings.SA_OTM_PCT) / step) * step
        put_strike = round(spot * (1 - settings.SA_OTM_PCT) / step) * step
        return call_strike, put_strike

    # ── Entry gate ──────────────────────────────────────────────────────

    def should_enter(self, regime: str, consensus: str, now_time: time) -> bool:
        return (
            regime in ("CALM", "NORMAL")
            and consensus == "RANGE"
            and self.ENTRY_START <= now_time <= self.ENTRY_END
            and not self.is_active()
        )

    def enter(self, spot: float, lots: int, expiry: str, now_str: str) -> Optional[Dict]:
        call_strike, put_strike = self.get_strikes(spot)
        call_sym = self.om.build_option_symbol("NIFTY", expiry, call_strike, "CE")
        put_sym = self.om.build_option_symbol("NIFTY", expiry, put_strike, "PE")

        call_ltp = self.md.get_ltp(call_sym)
        put_ltp = self.md.get_ltp(put_sym)
        if call_ltp <= 0 or put_ltp <= 0:
            logger.warning(
                "StrategyA: cannot get LTP; skipping. CE=%s(%.2f) PE=%s(%.2f)",
                call_sym, call_ltp, put_sym, put_ltp,
            )
            return None

        premium = call_ltp + put_ltp

        self.om.place_order(call_sym, "SELL", lots * settings.NIFTY_LOT_SIZE)
        self.om.place_order(put_sym, "SELL", lots * settings.NIFTY_LOT_SIZE)

        self._position = StranglePosition(
            call_strike=call_strike,
            put_strike=put_strike,
            call_symbol=call_sym,
            put_symbol=put_sym,
            premium_received=premium,
            lots=lots,
            entry_time=now_str,
        )
        logger.info(
            "StratA ENTER: sell %s CE@%.0f + PE@%.0f  prem=%.2f  lots=%d",
            expiry, call_strike, put_strike, premium, lots,
        )
        return {"strategy": "A", "action": "ENTER", "premium": premium, "lots": lots}

    # ── Monitor / exit ──────────────────────────────────────────────────

    def monitor(self) -> Optional[Dict]:
        if not self.is_active():
            return None
        pos = self._position
        call_ltp = self.md.get_ltp(pos.call_symbol)
        put_ltp = self.md.get_ltp(pos.put_symbol)
        if call_ltp <= 0 or put_ltp <= 0:
            logger.warning("StratA monitor: LTP=0 (CE=%.2f PE=%.2f) — skipping cycle", call_ltp, put_ltp)
            return None
        current_combined = call_ltp + put_ltp
        pnl_per_unit = pos.premium_received - current_combined
        qty = pos.lots * settings.NIFTY_LOT_SIZE

        if pnl_per_unit >= pos.premium_received * settings.SA_TARGET_PCT:
            return self.exit("TARGET_HIT", pnl_per_unit * qty)
        if pnl_per_unit <= -pos.premium_received * settings.SA_STOP_MULT:
            return self.exit("STOP_HIT", pnl_per_unit * qty)
        return None

    def exit(self, reason: str, pnl: float = 0.0) -> Dict:
        pos = self._position
        qty = pos.lots * settings.NIFTY_LOT_SIZE
        call_order = self.om.place_order(pos.call_symbol, "BUY", qty, track_position=False)
        put_order = self.om.place_order(pos.put_symbol, "BUY", qty, track_position=False)
        if any(order.get("status") != "COMPLETE" for order in (call_order, put_order)):
            logger.error("StratA EXIT [%s] failed; preserving position for retry", reason)
            return None
        if getattr(self.om, "tracker", None) is not None:
            self.om.tracker.add_position(call_order)
            self.om.tracker.add_position(put_order)
        logger.info("StratA EXIT [%s]: pnl=%.2f  lots=%d", reason, pnl, pos.lots)
        result = {
            "strategy": "A",
            "action": "EXIT",
            "reason": reason,
            "pnl": pnl,
            "premium_received": pos.premium_received,
            "lots": pos.lots,
            "entry_time": pos.entry_time,
        }
        self._position = None
        return result

    def force_exit(self) -> Optional[Dict]:
        if not self.is_active():
            return None
        pos = self._position
        call_ltp = self.md.get_ltp(pos.call_symbol)
        put_ltp = self.md.get_ltp(pos.put_symbol)
        if call_ltp <= 0 or put_ltp <= 0:
            logger.error("StratA force_exit: LTP=0 (CE=%.2f PE=%.2f) — using entry premium as loss estimate", call_ltp, put_ltp)
        pnl_per_unit = pos.premium_received - (call_ltp + put_ltp)
        total_pnl = pnl_per_unit * pos.lots * settings.NIFTY_LOT_SIZE
        return self.exit("HARD_CLOSE", total_pnl)
