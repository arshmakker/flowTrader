"""
Strategy B — Directional Spread (agent.md §9).

Condition : Regime CALM or NORMAL + consensus BULL or BEAR + confidence >= 3
Structure : Bull: Buy ATM CE + Sell OTM CE  |  Bear: Buy ATM PE + Sell OTM PE
Entry time: 10:30–13:00 only (avoid first 30 mins)
Target    : SB_TARGET_PCT of max profit (spread width − debit paid)
Stop      : SB_STOP_DEBIT_PCT loss of debit paid
Hard exit : 14:15 IST
Size      : min(SB_MAX_SPREADS, allowed) × size_multiplier
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import time
from typing import Any, Dict, Optional

from trading_system.config import settings

logger = logging.getLogger(__name__)


@dataclass
class SpreadPosition:
    direction: str = ""         # BULL | BEAR
    buy_strike: float = 0.0
    sell_strike: float = 0.0
    buy_symbol: str = ""
    sell_symbol: str = ""
    opt_type: str = ""          # CE | PE
    debit_paid: float = 0.0
    max_profit: float = 0.0
    lots: int = 0
    entry_time: str = ""

    def to_dict(self) -> Dict:
        return {k: getattr(self, k) for k in self.__dataclass_fields__}

    @classmethod
    def from_dict(cls, d: Dict) -> "SpreadPosition":
        return cls(**{k: d[k] for k in cls.__dataclass_fields__ if k in d})


class StrategyB:
    """Directional (bull/bear) debit spread."""

    @staticmethod
    def _parse_time(s: str) -> time:
        h, m = s.split(":")
        return time(int(h), int(m))

    @property
    def ENTRY_START(self):
        return self._parse_time(settings.SB_ENTRY_START)

    @property
    def ENTRY_END(self):
        return self._parse_time(settings.SB_ENTRY_END)

    def __init__(self, order_manager: Any, market_data: Any):
        self.om = order_manager
        self.md = market_data
        self._position: Optional[SpreadPosition] = None

    def is_active(self) -> bool:
        return self._position is not None

    def save_state(self) -> Optional[Dict]:
        return {"strategy": "B", "position": self._position.to_dict()} if self._position else None

    def restore_state(self, state: Dict) -> None:
        if state and state.get("position"):
            self._position = SpreadPosition.from_dict(state["position"])
            logger.info("StratB: restored position from disk")

    # ── Entry gate ──────────────────────────────────────────────────────

    def should_enter(
        self, regime: str, consensus: str, confidence: int, now_time: time
    ) -> bool:
        return (
            regime in ("CALM", "NORMAL")
            and consensus in ("BULL", "BEAR")
            and confidence >= 3
            and self.ENTRY_START <= now_time <= self.ENTRY_END
            and not self.is_active()
        )

    def enter(
        self, direction: str, spot: float, lots: int, expiry: str, now_str: str
    ) -> Optional[Dict]:
        step = settings.NIFTY_STRIKE_STEP
        atm = round(spot / step) * step

        if direction == "BULL":
            buy_strike = atm
            sell_strike = round(spot * (1 + settings.SB_OTM_PCT) / step) * step
            opt_type = "CE"
        else:
            buy_strike = atm
            sell_strike = round(spot * (1 - settings.SB_OTM_PCT) / step) * step
            opt_type = "PE"

        buy_sym = self.om.build_option_symbol("NIFTY", expiry, buy_strike, opt_type)
        sell_sym = self.om.build_option_symbol("NIFTY", expiry, sell_strike, opt_type)

        buy_ltp = self.md.get_ltp(buy_sym)
        sell_ltp = self.md.get_ltp(sell_sym)
        if buy_ltp <= 0 or sell_ltp <= 0:
            logger.warning(
                "StrategyB: cannot get LTP; skipping. BUY=%s(%.2f) SELL=%s(%.2f)",
                buy_sym, buy_ltp, sell_sym, sell_ltp,
            )
            return None

        debit = buy_ltp - sell_ltp
        spread_width = abs(sell_strike - buy_strike)
        max_profit = spread_width - debit if debit > 0 else spread_width

        qty = lots * settings.NIFTY_LOT_SIZE
        self.om.place_order(buy_sym, "BUY", qty)
        self.om.place_order(sell_sym, "SELL", qty)

        self._position = SpreadPosition(
            direction=direction,
            buy_strike=buy_strike,
            sell_strike=sell_strike,
            buy_symbol=buy_sym,
            sell_symbol=sell_sym,
            opt_type=opt_type,
            debit_paid=debit,
            max_profit=max_profit,
            lots=lots,
            entry_time=now_str,
        )
        logger.info(
            "StratB ENTER %s: buy %s@%.0f sell %s@%.0f debit=%.2f lots=%d",
            direction, opt_type, buy_strike, opt_type, sell_strike, debit, lots,
        )
        return {"strategy": "B", "action": "ENTER", "direction": direction, "debit": debit, "lots": lots}

    # ── Monitor / exit ──────────────────────────────────────────────────

    def monitor(self) -> Optional[Dict]:
        if not self.is_active():
            return None
        pos = self._position
        buy_ltp = self.md.get_ltp(pos.buy_symbol)
        sell_ltp = self.md.get_ltp(pos.sell_symbol)
        if buy_ltp <= 0 or sell_ltp <= 0:
            logger.warning("StratB monitor: LTP=0 (BUY=%.2f SELL=%.2f) — skipping cycle", buy_ltp, sell_ltp)
            return None
        current_value = buy_ltp - sell_ltp
        pnl_per_unit = current_value - pos.debit_paid
        qty = pos.lots * settings.NIFTY_LOT_SIZE

        if pnl_per_unit >= pos.max_profit * settings.SB_TARGET_PCT:
            return self.exit("TARGET_HIT", pnl_per_unit * qty)
        if current_value <= pos.debit_paid * (1 - settings.SB_STOP_DEBIT_PCT):
            return self.exit("STOP_HIT", pnl_per_unit * qty)
        return None

    def exit(self, reason: str, pnl: float = 0.0) -> Dict:
        pos = self._position
        qty = pos.lots * settings.NIFTY_LOT_SIZE
        buy_order = self.om.place_order(pos.buy_symbol, "SELL", qty, track_position=False)
        sell_order = self.om.place_order(pos.sell_symbol, "BUY", qty, track_position=False)
        if any(order.get("status") != "COMPLETE" for order in (buy_order, sell_order)):
            logger.error("StratB EXIT [%s] failed; preserving position for retry", reason)
            return None
        if getattr(self.om, "tracker", None) is not None:
            self.om.tracker.add_position(buy_order)
            self.om.tracker.add_position(sell_order)
        logger.info("StratB EXIT [%s]: pnl=%.2f  lots=%d", reason, pnl, pos.lots)
        result = {
            "strategy": "B",
            "action": "EXIT",
            "reason": reason,
            "pnl": pnl,
            "debit_paid": pos.debit_paid,
            "max_profit": pos.max_profit,
            "direction": pos.direction,
            "lots": pos.lots,
            "entry_time": pos.entry_time,
        }
        self._position = None
        return result

    def force_exit(self) -> Optional[Dict]:
        if not self.is_active():
            return None
        pos = self._position
        buy_ltp = self.md.get_ltp(pos.buy_symbol)
        sell_ltp = self.md.get_ltp(pos.sell_symbol)
        if buy_ltp <= 0 or sell_ltp <= 0:
            logger.error("StratB force_exit: LTP=0 (BUY=%.2f SELL=%.2f) — P&L may be inaccurate", buy_ltp, sell_ltp)
        pnl_per_unit = (buy_ltp - sell_ltp) - pos.debit_paid
        total_pnl = pnl_per_unit * pos.lots * settings.NIFTY_LOT_SIZE
        return self.exit("HARD_CLOSE", total_pnl)
