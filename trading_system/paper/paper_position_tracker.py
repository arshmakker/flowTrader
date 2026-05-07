"""
Paper position tracker (agent.md §16.2).

Tracks open positions in memory. Computes unrealised P&L from live LTP.
"""

import logging
from typing import Any, Dict, List

from trading_system.config import settings
from trading_system.core.fees import compute_taxes_and_fees

logger = logging.getLogger(__name__)


def _round_to_tick(price: float) -> float:
    """Round price to NSE F&O tick size (multiples of 0.05)."""
    return round(round(price / settings.PRICE_TICK) * settings.PRICE_TICK, 2)


class PaperPositionTracker:
    def __init__(self) -> None:
        self._positions: Dict[str, Dict] = {}  # symbol → position dict
        self._unmarked: List[str] = []  # symbols skipped in last mark

    def add_position(self, order: Dict) -> None:
        sym = order["symbol"]
        qty = order["quantity"]
        price = order["fill_price"]
        side = order["side"]
        # LIVE-12: order dicts now carry the full six-component cost stack
        # (taxes_total). Prefer that; fall back to stt+brokerage only for
        # legacy orders produced before LIVE-12 landed.
        order_costs = order.get(
            "taxes_total",
            order.get("stt", 0.0) + order.get("brokerage", 0.0),
        )

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
            same_direction = (old_qty > 0 and side in ("BUY", "B")) or (old_qty < 0 and side in ("SELL", "S"))
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

        # LIVE-12: exit side pays the full six-component cost stack, not just
        # STT + flat brokerage. Long-leg exits (SELL) pay STT but no stamp;
        # short-leg exits (BUY) pay stamp but no STT — delegated to the fee
        # engine so asymmetry stays correct.
        exit_side = "SELL" if pos["qty"] > 0 else "BUY"
        exit_fees = compute_taxes_and_fees(symbol, exit_side, exit_price, abs_qty)
        total_costs = pos["costs"] + exit_fees["total"]

        return gross - total_costs

    def get_unrealised_pnl(self, market_data: Any) -> float:
        total = 0.0
        self._unmarked = []
        for sym, pos in self._positions.items():
            ltp = market_data.get_ltp(sym)
            if ltp <= 0:
                # LTP stale or unavailable — try quote-book mid so risk checks see
                # both real losses and real profits, not just zero.
                qb = market_data.get_quote_book(sym) if hasattr(market_data, "get_quote_book") else None
                if qb is not None and qb.is_tradable:
                    ltp = (qb.bid + qb.ask) / 2.0
                    logger.info(
                        "Unrealised P&L: LTP stale for %s — using quote-book mid %.2f",
                        sym,
                        ltp,
                    )
            if ltp <= 0:
                self._unmarked.append(sym)
                logger.warning(
                    "Unrealised P&L: no price for %s (qty=%d, avg=%.2f) — excluded from mark",
                    sym,
                    pos["qty"],
                    pos["avg_price"],
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
        return [{**pos, "abs_qty": abs(pos["qty"])} for pos in self._positions.values() if pos["qty"] != 0]

    def has_open_positions(self) -> bool:
        return len(self._positions) > 0

    def save_state(self) -> Dict:
        return {"positions": self._positions, "unmarked": self._unmarked}

    def restore_state(self, state: Dict) -> None:
        self._positions = state.get("positions", {})
        self._unmarked = state.get("unmarked", [])
        if self._positions:
            logger.info(f"Restored {len(self._positions)} positions from state.")
