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

    def __init__(self, market_data: Any, position_tracker: Any = None) -> None:
        self.md = market_data
        self.tracker = position_tracker
        self.orders: list[Dict] = []

    def _next_id(self) -> str:
        return f"PAPER_{next(self._id_counter)}"

    @staticmethod
    def _is_option_symbol(tradingsymbol: str) -> bool:
        core = tradingsymbol.split("|")[-1]
        return ("C" in core[-6:]) or ("P" in core[-6:])

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
        turnover = price * qty
        if "FUT" in symbol:
            return turnover * settings.STT_FUTURES
        if side == "SELL" or side == "S":
            return turnover * settings.STT_OPTIONS_SELL
        return 0.0

    def place_order(
        self,
        tradingsymbol: str,
        buy_or_sell: str,
        quantity: int,
        price_type: str = "MKT",
        price: float = 0.0,
        track_position: bool = True,
    ) -> Dict:
        ltp = self.md.get_ltp(tradingsymbol)
        is_option = self._is_option_symbol(tradingsymbol)
        if ltp <= 0:
            if price > 0:
                ltp = price
                logger.warning("Paper LTP=0 for %s; using explicit fallback %.2f", tradingsymbol, ltp)
            else:
                logger.error("Paper order rejected for %s: missing LTP and no fallback price", tradingsymbol)
                return {
                    "order_id": self._next_id(),
                    "symbol": tradingsymbol,
                    "side": buy_or_sell,
                    "quantity": quantity,
                    "fill_price": 0.0,
                    "stt": 0.0,
                    "brokerage": 0.0,
                    "status": "REJECTED",
                    "timestamp": datetime.now().isoformat(),
                    "paper": True,
                    "reason": "missing_ltp",
                }

        if is_option and (ltp < settings.PAPER_OPTION_LTP_MIN or ltp > settings.PAPER_OPTION_LTP_MAX):
            logger.error(
                "Paper order rejected for %s: suspicious option LTP %.2f outside [%.2f, %.2f]",
                tradingsymbol,
                ltp,
                settings.PAPER_OPTION_LTP_MIN,
                settings.PAPER_OPTION_LTP_MAX,
            )
            return {
                "order_id": self._next_id(),
                "symbol": tradingsymbol,
                "side": buy_or_sell,
                "quantity": quantity,
                "fill_price": 0.0,
                "stt": 0.0,
                "brokerage": 0.0,
                "status": "REJECTED",
                "timestamp": datetime.now().isoformat(),
                "paper": True,
                "reason": "suspicious_option_ltp",
                "ltp": ltp,
            }

        if is_option and ltp < settings.SLIPPAGE_OTM_THRESHOLD:
            slip = max(ltp * settings.SLIPPAGE_PCT * 3, settings.SLIPPAGE_MIN_ABS)
        else:
            slip = max(ltp * settings.SLIPPAGE_PCT, settings.SLIPPAGE_MIN_ABS)
        if buy_or_sell in ("BUY", "B"):
            fill = ltp + slip
        else:
            fill = ltp - slip

        fill = round(round(fill / settings.PRICE_TICK) * settings.PRICE_TICK, 2)

        stt = self._calc_stt(tradingsymbol, buy_or_sell, fill, quantity)

        order = {
            "order_id": self._next_id(),
            "symbol": tradingsymbol,
            "side": buy_or_sell,
            "quantity": quantity,
            "fill_price": fill,
            "stt": round(stt, 2),
            "brokerage": settings.BROKERAGE_PER_ORDER,
            "status": "COMPLETE",
            "timestamp": datetime.now().isoformat(),
            "paper": True,
        }
        self.orders.append(order)
        if self.tracker is not None and track_position:
            self.tracker.add_position(order)
        logger.info(
            "PAPER ORDER %s %s %d @ %.2f (stt=%.2f)",
            buy_or_sell, tradingsymbol, quantity, fill, stt,
        )
        return order
