"""
Paper order manager (agent.md §16.1).

Identical interface to a real order manager.
Fills at live LTP with realistic slippage and cost simulation.
"""

from __future__ import annotations

import csv
import itertools
import logging
import os
from datetime import datetime
from typing import Any, Dict, Optional

from trading_system.config import settings
from trading_system.core.fees import compute_taxes_and_fees

logger = logging.getLogger(__name__)

# LIVE-12: CSV now carries the full six-component cost stack so
# ops/reconcile.py can diff every charge against the broker contract note
# rather than eating a fat opaque "costs" delta.
_ORDERS_CSV_COLUMNS = [
    "timestamp", "order_id", "symbol", "side", "quantity",
    "fill_price",
    "stt", "brokerage", "exch_txn", "sebi", "stamp", "gst", "taxes_total",
    "status", "reason", "paper",
]

# Zero-cost stub used on REJECTED/CANCELED orders that never filled — no
# charges apply since no trade happened. Kept as a single dict so every
# non-fill path stays consistent.
_ZERO_FEES = {
    "stt": 0.0, "brokerage": 0.0, "exch_txn": 0.0,
    "sebi": 0.0, "stamp": 0.0, "gst": 0.0, "taxes_total": 0.0,
}


class PaperOrderManager:
    """
    Drop-in replacement for a real order manager.
    Strategies call place_order / build_option_symbol without knowing the mode.
    """

    _id_counter = itertools.count(1)

    def __init__(
        self,
        market_data: Any,
        position_tracker: Any = None,
        orders_csv_path: Optional[str] = None,
    ) -> None:
        self.md = market_data
        self.tracker = position_tracker
        self.orders: list[Dict] = []
        self._orders_csv_path = orders_csv_path or os.path.join(
            settings.DATA_DIR, "paper_orders.csv"
        )
        self._ensure_orders_csv_header()

    def _ensure_orders_csv_header(self) -> None:
        if os.path.exists(self._orders_csv_path):
            return
        parent = os.path.dirname(self._orders_csv_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        try:
            with open(self._orders_csv_path, "w", newline="") as f:
                csv.writer(f).writerow(_ORDERS_CSV_COLUMNS)
        except Exception:
            logger.exception("Failed to initialise paper_orders.csv")

    def _append_order_csv(self, order: Dict[str, Any]) -> None:
        row = [order.get(col, "") for col in _ORDERS_CSV_COLUMNS]
        try:
            with open(self._orders_csv_path, "a", newline="") as f:
                csv.writer(f).writerow(row)
        except Exception:
            logger.exception("Failed to append paper order to CSV")

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

    def _limit_not_reached_cancel(
        self, symbol: str, side: str, qty: int, limit_price: float, ltp: float,
    ) -> Dict:
        """LIVE-25 Phase 4: LMT order whose limit was never crossed by the book
        gets CANCELED (no fill). Mirrors Shoonya's behavior when a timeout-IOC
        limit doesn't trade through."""
        canceled = {
            "order_id": self._next_id(),
            "symbol": symbol, "side": side, "quantity": qty,
            "fill_qty": 0, "fill_price": 0.0,
            **_ZERO_FEES,
            "status": "CANCELED",
            "timestamp": datetime.now().isoformat(), "paper": True,
            "reason": "limit_not_reached",
            "limit_price": limit_price,
            "ltp_at_submit": ltp,
        }
        self._append_order_csv(canceled)
        logger.info(
            "PAPER ORDER CANCELED %s %s qty=%d limit=%.2f ltp=%.2f — limit not reached",
            side, symbol, qty, limit_price, ltp,
        )
        return canceled

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
                rejected = {
                    "order_id": self._next_id(),
                    "symbol": tradingsymbol,
                    "side": buy_or_sell,
                    "quantity": quantity,
                    "fill_qty": 0,
                    "fill_price": 0.0,
                    **_ZERO_FEES,
                    "status": "REJECTED",
                    "timestamp": datetime.now().isoformat(),
                    "paper": True,
                    "reason": "missing_ltp",
                }
                self._append_order_csv(rejected)
                return rejected

        if is_option and (ltp < settings.PAPER_OPTION_LTP_MIN or ltp > settings.PAPER_OPTION_LTP_MAX):
            logger.error(
                "Paper order rejected for %s: suspicious option LTP %.2f outside [%.2f, %.2f]",
                tradingsymbol,
                ltp,
                settings.PAPER_OPTION_LTP_MIN,
                settings.PAPER_OPTION_LTP_MAX,
            )
            rejected = {
                "order_id": self._next_id(),
                "symbol": tradingsymbol,
                "side": buy_or_sell,
                "quantity": quantity,
                "fill_qty": 0,
                "fill_price": 0.0,
                **_ZERO_FEES,
                "status": "REJECTED",
                "timestamp": datetime.now().isoformat(),
                "paper": True,
                "reason": "suspicious_option_ltp",
                "ltp": ltp,
            }
            self._append_order_csv(rejected)
            return rejected

        if is_option and ltp < settings.SLIPPAGE_OTM_THRESHOLD:
            slip = max(ltp * settings.SLIPPAGE_PCT * 3, settings.SLIPPAGE_MIN_ABS)
        else:
            slip = max(ltp * settings.SLIPPAGE_PCT, settings.SLIPPAGE_MIN_ABS)

        # LIVE-25: paper-side LMT support. The paper "book" is modeled as
        # ask=ltp+slip, bid=ltp-slip. A BUY LMT fills only if the limit is at
        # or above the ask; a SELL LMT only if at or below the bid. Otherwise
        # the order is CANCELED (paper's equivalent of "timed out without fill").
        # MKT flow is unchanged — ignores price entirely.
        if price_type == "LMT":
            if price <= 0:
                logger.error("Paper LMT order for %s rejected: no price provided", tradingsymbol)
                rejected = {
                    "order_id": self._next_id(),
                    "symbol": tradingsymbol, "side": buy_or_sell, "quantity": quantity,
                    "fill_qty": 0, "fill_price": 0.0,
                    **_ZERO_FEES,
                    "status": "REJECTED",
                    "timestamp": datetime.now().isoformat(), "paper": True,
                    "reason": "limit_price_missing",
                }
                self._append_order_csv(rejected)
                return rejected

            paper_ask = ltp + slip
            paper_bid = ltp - slip
            if buy_or_sell in ("BUY", "B"):
                if price >= paper_ask:
                    fill = min(price, paper_ask)
                else:
                    return self._limit_not_reached_cancel(tradingsymbol, buy_or_sell, quantity, price, ltp)
            else:
                if price <= paper_bid:
                    fill = max(price, paper_bid)
                else:
                    return self._limit_not_reached_cancel(tradingsymbol, buy_or_sell, quantity, price, ltp)
        else:  # MKT (default)
            if buy_or_sell in ("BUY", "B"):
                fill = ltp + slip
            else:
                fill = ltp - slip

        fill = round(round(fill / settings.PRICE_TICK) * settings.PRICE_TICK, 2)

        fees = compute_taxes_and_fees(tradingsymbol, buy_or_sell, fill, quantity)

        order = {
            "order_id": self._next_id(),
            "symbol": tradingsymbol,
            "side": buy_or_sell,
            "quantity": quantity,
            "fill_qty": quantity,
            "fill_price": fill,
            "stt": fees["stt"],
            "brokerage": fees["brokerage"],
            "exch_txn": fees["exch_txn"],
            "sebi": fees["sebi"],
            "stamp": fees["stamp"],
            "gst": fees["gst"],
            "taxes_total": fees["total"],
            "status": "COMPLETE",
            "timestamp": datetime.now().isoformat(),
            "paper": True,
        }
        self.orders.append(order)
        if self.tracker is not None and track_position:
            self.tracker.add_position(order)
        self._append_order_csv(order)
        logger.info(
            "PAPER ORDER %s %s %d @ %.2f (fees=₹%.2f stt=%.2f brok=%.2f exch=%.2f sebi=%.2f stamp=%.2f gst=%.2f)",
            buy_or_sell, tradingsymbol, quantity, fill,
            fees["total"], fees["stt"], fees["brokerage"],
            fees["exch_txn"], fees["sebi"], fees["stamp"], fees["gst"],
        )
        return order
