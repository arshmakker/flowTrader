"""
Paper order manager (agent.md §16.1).

Identical interface to a real order manager.
Fills at live LTP with realistic slippage and cost simulation.
"""

from __future__ import annotations

import itertools
import logging
from datetime import datetime
from typing import Any, Dict

from trading_system.config import settings

logger = logging.getLogger(__name__)


class PaperOrderManager:
    """
    Drop-in replacement for a real order manager.
    Strategies call place_order / build_option_symbol without knowing the mode.
    """

    _id_counter = itertools.count(1)

    def __init__(self, market_data: Any) -> None:
        self.md = market_data
        self.orders: list[Dict] = []

    def _next_id(self) -> str:
        return f"PAPER_{next(self._id_counter)}"

    @staticmethod
    def build_option_symbol(
        symbol: str, expiry: str, strike: float, opt_type: str
    ) -> str:
        """
        Build a Shoonya-style trading symbol.
        Format: NIFTY17MAR26C23850  (DDMMMYYtypeSTRIKE)
        opt_type 'CE' → 'C', 'PE' → 'P'
        expiry can be a date object or string like '17-MAR-2026'.
        """
        from datetime import datetime as _dt
        if hasattr(expiry, "strftime"):
            exp_str = expiry.strftime("%d%b%y").upper()
        else:
            try:
                d = _dt.strptime(str(expiry)[:11].strip(), "%d-%b-%Y")
                exp_str = d.strftime("%d%b%y").upper()
            except (ValueError, TypeError):
                exp_str = str(expiry).replace("-", "").upper()
        ot = opt_type[0] if opt_type else "C"  # CE→C, PE→P
        return f"NFO|{symbol}{exp_str}{ot}{int(strike)}"

    @staticmethod
    def _calc_stt(symbol: str, side: str, price: float, qty: int) -> float:
        """STT estimate: 0.05% sell-side options, 0.01% futures both sides."""
        turnover = price * qty
        if "FUT" in symbol:
            return turnover * 0.0001  # 0.01%
        if side == "SELL" or side == "S":
            return turnover * 0.0005  # 0.05% options sell
        return 0.0

    def place_order(
        self,
        tradingsymbol: str,
        buy_or_sell: str,
        quantity: int,
        price_type: str = "MKT",
        price: float = 0.0,
    ) -> Dict:
        ltp = self.md.get_ltp(tradingsymbol)
        if ltp <= 0:
            ltp = price if price > 0 else 1.0
            logger.warning("Paper LTP=0 for %s; using fallback %.2f", tradingsymbol, ltp)

        slip = ltp * 0.0005
        if buy_or_sell in ("BUY", "B"):
            fill = ltp + slip
        else:
            fill = ltp - slip

        stt = self._calc_stt(tradingsymbol, buy_or_sell, fill, quantity)

        order = {
            "order_id": self._next_id(),
            "symbol": tradingsymbol,
            "side": buy_or_sell,
            "quantity": quantity,
            "fill_price": round(fill, 2),
            "stt": round(stt, 2),
            "brokerage": 20.0,
            "status": "COMPLETE",
            "timestamp": datetime.now().isoformat(),
            "paper": True,
        }
        self.orders.append(order)
        logger.info(
            "PAPER ORDER %s %s %d @ %.2f (stt=%.2f)",
            buy_or_sell, tradingsymbol, quantity, fill, stt,
        )
        return order
