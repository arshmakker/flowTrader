"""
Paper position tracker (agent.md §16.2).

Tracks open positions in memory. Computes unrealised P&L from live LTP.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List

from trading_system.config import settings

logger = logging.getLogger(__name__)


def _round_to_tick(price: float) -> float:
    """Round price to NSE F&O tick size (multiples of 0.05)."""
    return round(round(price / settings.PRICE_TICK) * settings.PRICE_TICK, 2)


class PaperPositionTracker:
    def __init__(self) -> None:
        self._positions: Dict[str, Dict] = {}  # symbol → position dict
        self._unmarked: List[str] = []          # symbols skipped in last mark

    def add_position(self, order: Dict) -> None:
        sym = order["symbol"]
        qty = order["quantity"]
        price = order["fill_price"]
        side = order["side"]
        order_costs = order.get("stt", 0.0) + order.get("brokerage", 0.0)

        if sym in self._positions:
            pos = self._positions[sym]
            old_qty = pos["qty"]

            if side in ("BUY", "B"):
                new_qty = old_qty + qty
            else:
                new_qty = old_qty - qty

            pos["costs"] += order_costs

            if new_qty == 0:
                del self._positions[sym]
                return

            # Recalculate weighted avg price when scaling in the same direction
            same_direction = (old_qty > 0 and side in ("BUY", "B")) or \
                             (old_qty < 0 and side in ("SELL", "S"))
            if same_direction:
                total_value = pos["avg_price"] * abs(old_qty) + price * qty
                pos["avg_price"] = _round_to_tick(total_value / abs(new_qty))

            pos["qty"] = new_qty
            return

        self._positions[sym] = {
            "symbol": sym,
            "qty": qty if side in ("BUY", "B") else -qty,
            "avg_price": _round_to_tick(price),
            "side": side,
            "costs": order_costs,
        }

    def close_position(self, symbol: str, exit_price: float, exit_qty: int = 0) -> float:
        pos = self._positions.pop(symbol, None)
        if pos is None:
            return 0.0

        exit_price = _round_to_tick(exit_price)
        abs_qty = abs(pos["qty"]) if exit_qty == 0 else exit_qty

        if pos["qty"] > 0:
            gross = (exit_price - pos["avg_price"]) * abs_qty
        else:
            gross = (pos["avg_price"] - exit_price) * abs_qty

        exit_stt = PaperPositionTracker._estimate_exit_stt(symbol, pos, exit_price, abs_qty)
        exit_brokerage = settings.BROKERAGE_PER_ORDER
        total_costs = pos["costs"] + exit_stt + exit_brokerage

        return gross - total_costs

    @staticmethod
    def _estimate_exit_stt(symbol: str, pos: Dict, exit_price: float, qty: int) -> float:
        turnover = exit_price * qty
        if "FUT" in symbol:
            return turnover * settings.STT_FUTURES
        exit_side = "SELL" if pos["qty"] > 0 else "BUY"
        if exit_side == "SELL":
            return turnover * settings.STT_OPTIONS_SELL
        return 0.0

    def get_unrealised_pnl(self, market_data: Any) -> float:
        total = 0.0
        self._unmarked = []
        for sym, pos in self._positions.items():
            ltp = market_data.get_ltp(sym)
            if ltp <= 0:
                self._unmarked.append(sym)
                logger.warning(
                    "Unrealised P&L: LTP=0 for %s (qty=%d, avg=%.2f) — excluded from mark",
                    sym, pos["qty"], pos["avg_price"],
                )
                continue
            if pos["qty"] > 0:
                total += (ltp - pos["avg_price"]) * abs(pos["qty"])
            else:
                total += (pos["avg_price"] - ltp) * abs(pos["qty"])
        return total

    @property
    def unmarked_symbols(self) -> List[str]:
        return list(self._unmarked)

    def get_open_positions(self) -> List[Dict]:
        return [
            {**pos, "abs_qty": abs(pos["qty"])}
            for pos in self._positions.values()
            if pos["qty"] != 0
        ]

    def has_open_positions(self) -> bool:
        return len(self._positions) > 0

    def save_state(self) -> Dict:
        return {
            "positions": self._positions,
            "unmarked": self._unmarked
        }

    def restore_state(self, state: Dict) -> None:
        self._positions = state.get("positions", {})
        self._unmarked = state.get("unmarked", [])
        if self._positions:
            logger.info(f"Restored {len(self._positions)} positions from state.")
