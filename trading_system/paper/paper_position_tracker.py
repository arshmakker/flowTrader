"""
Paper position tracker (agent.md §16.2).

Tracks open positions in memory. Computes unrealised P&L from live LTP.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List

logger = logging.getLogger(__name__)


class PaperPositionTracker:
    def __init__(self) -> None:
        self._positions: Dict[str, Dict] = {}  # symbol → position dict

    def add_position(self, order: Dict) -> None:
        sym = order["symbol"]
        qty = order["quantity"]
        price = order["fill_price"]
        side = order["side"]

        if sym in self._positions:
            pos = self._positions[sym]
            if side in ("BUY", "B"):
                pos["qty"] += qty
            else:
                pos["qty"] -= qty
            if pos["qty"] == 0:
                del self._positions[sym]
            return

        self._positions[sym] = {
            "symbol": sym,
            "qty": qty if side in ("BUY", "B") else -qty,
            "avg_price": price,
            "side": side,
            "costs": order.get("stt", 0.0) + order.get("brokerage", 0.0),
        }

    def close_position(self, symbol: str, exit_price: float) -> float:
        pos = self._positions.pop(symbol, None)
        if pos is None:
            return 0.0
        if pos["qty"] > 0:
            gross = (exit_price - pos["avg_price"]) * abs(pos["qty"])
        else:
            gross = (pos["avg_price"] - exit_price) * abs(pos["qty"])
        return gross - pos["costs"]

    def get_unrealised_pnl(self, market_data: Any) -> float:
        total = 0.0
        for sym, pos in self._positions.items():
            ltp = market_data.get_ltp(sym)
            if ltp <= 0:
                continue
            if pos["qty"] > 0:
                total += (ltp - pos["avg_price"]) * abs(pos["qty"])
            else:
                total += (pos["avg_price"] - ltp) * abs(pos["qty"])
        return total

    def get_open_positions(self) -> List[Dict]:
        return [
            {**pos, "abs_qty": abs(pos["qty"])}
            for pos in self._positions.values()
            if pos["qty"] != 0
        ]

    def has_open_positions(self) -> bool:
        return len(self._positions) > 0
