"""
Live order manager — wraps Shoonya API for real-money execution.

Same public interface as PaperOrderManager so IronCondorStrategy is unaware
of which mode it runs in. place_order blocks until the order reaches a
terminal state (COMPLETE / REJECTED / CANCELED) by polling
single_order_history. Transient API errors are retried with exponential
backoff; MAX_POLL_ERRORS consecutive failures raise OrderPollingAbandoned,
which the caller must handle by halting and alerting the operator.
"""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime
from typing import Any, Dict, Optional

from trading_system.config import settings

logger = logging.getLogger(__name__)

_TERMINAL_STATUSES = {"COMPLETE", "REJECTED", "CANCELED"}

_STUCK_LEGS_PATH = os.path.join(
    getattr(settings, "DATA_DIR", "data"), "stuck_legs.json"
)


class OrderPollingAbandoned(Exception):
    """Raised when MAX_POLL_ERRORS consecutive API errors occur while polling."""

    def __init__(self, order_id: str, cause: Exception) -> None:
        super().__init__(f"Polling abandoned for order {order_id}: {cause}")
        self.order_id = order_id
        self.cause = cause


class LiveOrderManager:
    """
    Production order manager for Shoonya/Noren live execution.

    Identical public interface to PaperOrderManager:
      - place_order(symbol, side, qty, ...) -> order dict
      - build_option_symbol(symbol, expiry, strike, opt_type) -> str

    Order dict shape (same as paper):
      order_id, symbol, side, quantity, fill_qty, fill_price,
      status, stt, brokerage, timestamp, paper=False, reason (on failure)
    """

    def __init__(
        self,
        api: Any,
        market_data: Any,
        position_tracker: Any = None,
    ) -> None:
        self.api = api
        self.md = market_data
        self.tracker = position_tracker

    # ── Public interface ────────────────────────────────────────────────

    def place_order(
        self,
        tradingsymbol: str,
        buy_or_sell: str,
        quantity: int,
        price_type: str = "MKT",
        price: float = 0.0,
        track_position: bool = True,
    ) -> Dict:
        """
        Submit an order to Shoonya and block until it reaches a terminal
        state. Returns a normalised order dict with fill_qty set to the
        actual filled quantity (may be less than quantity on CANCELED).
        """
        buy_or_sell = buy_or_sell.upper()
        logger.info(
            "LIVE ORDER SUBMIT %s %s qty=%d price_type=%s",
            buy_or_sell, tradingsymbol, quantity, price_type,
        )

        try:
            resp = self.api.place_order(
                buy_or_sell=buy_or_sell[0],   # Shoonya expects 'B' or 'S'
                product_type="I",             # intraday
                exchange="NFO",
                tradingsymbol=tradingsymbol.split("|")[-1],
                quantity=quantity,
                discloseqty=0,
                price_type=price_type,
                price=price,
                trigger_price=None,
                retention="DAY",
                remarks=f"IC_{datetime.now().strftime('%H%M%S')}",
            )
        except Exception as exc:
            logger.error("LIVE ORDER submit failed for %s: %s", tradingsymbol, exc)
            return self._make_rejected(tradingsymbol, buy_or_sell, quantity, reason=str(exc))

        order_id = resp.get("norenordno") if resp else None
        if not order_id:
            logger.error(
                "LIVE ORDER submit returned no order_id for %s (resp=%s)",
                tradingsymbol, resp,
            )
            return self._make_rejected(tradingsymbol, buy_or_sell, quantity, reason="no_order_id")

        try:
            broker_order = self._await_terminal(order_id)
        except OrderPollingAbandoned as exc:
            logger.error("LIVE ORDER polling abandoned for %s: %s", order_id, exc)
            persist_stuck_legs([{
                "order_id": order_id,
                "symbol": tradingsymbol,
                "side": buy_or_sell,
                "qty": quantity,
                "reason": "polling_abandoned",
            }])
            raise  # propagate — caller (iron_condor.py) must halt

        order = self._normalise(broker_order, tradingsymbol, buy_or_sell, quantity)

        if track_position and self.tracker is not None and order.get("fill_qty", 0) > 0:
            self.tracker.add_position(order)

        logger.info(
            "LIVE ORDER %s %s status=%s fill_qty=%d fill_price=%.2f",
            buy_or_sell, tradingsymbol,
            order["status"], order["fill_qty"], order["fill_price"],
        )
        return order

    def get_available_margin(self) -> float:
        """LIVE-10: query Shoonya ``get_limits()`` and return available margin
        as ``cash - marginused``. Fails closed — any error or malformed
        response returns 0.0, which guarantees the pre-entry margin check
        refuses entry. Safety over continuity (Axiom 3).
        """
        try:
            limits = self.api.get_limits()
        except Exception:
            logger.exception("LIVE margin query get_limits() failed; reporting 0 available")
            return 0.0
        if not isinstance(limits, dict):
            logger.error("LIVE margin query returned non-dict: %r", type(limits).__name__)
            return 0.0
        try:
            cash = float(limits.get("cash", 0) or 0)
            used = float(limits.get("marginused", 0) or 0)
        except (TypeError, ValueError):
            logger.error("LIVE margin response malformed: %r", limits)
            return 0.0
        return max(cash - used, 0.0)

    @staticmethod
    def build_option_symbol(
        symbol: str, expiry: str, strike: float, opt_type: str
    ) -> str:
        """Identical to PaperOrderManager.build_option_symbol."""
        from datetime import datetime as _dt
        if hasattr(expiry, "strftime"):
            exp_str = expiry.strftime("%d%b%y").upper()
        else:
            try:
                d = _dt.strptime(str(expiry)[:11].strip(), "%d-%b-%Y")
                exp_str = d.strftime("%d%b%y").upper()
            except (ValueError, TypeError):
                exp_str = str(expiry).replace("-", "").upper()
        ot = opt_type[0] if opt_type else "C"
        return f"NFO|{symbol}{exp_str}{ot}{int(strike)}"

    # ── Polling ─────────────────────────────────────────────────────────

    def _await_terminal(self, order_id: str) -> Dict:
        """
        Poll single_order_history until status is terminal.
        Transient errors are retried with exponential backoff.
        Raises OrderPollingAbandoned after MAX_POLL_ERRORS consecutive errors.
        """
        consecutive_errors = 0

        while True:
            try:
                history = self.api.single_order_history(orderno=order_id)
                consecutive_errors = 0
            except Exception as exc:
                consecutive_errors += 1
                logger.warning(
                    "LIVE POLL error #%d for order %s: %s",
                    consecutive_errors, order_id, exc,
                )
                if consecutive_errors >= settings.MAX_POLL_ERRORS:
                    raise OrderPollingAbandoned(order_id, exc)
                backoff = min(2 ** (consecutive_errors - 1), 30)
                time.sleep(backoff)
                continue

            # Shoonya returns a list; the last entry is the most recent state.
            record = history[-1] if isinstance(history, list) and history else (history or {})
            status = record.get("status", "").upper()

            if status in _TERMINAL_STATUSES:
                return record

            time.sleep(settings.POLL_INTERVAL_SEC)

    # ── Normalisation ────────────────────────────────────────────────────

    @staticmethod
    def _normalise(
        broker: Dict,
        tradingsymbol: str,
        side: str,
        requested_qty: int,
    ) -> Dict:
        """Convert a Shoonya order record to the shared order dict shape."""
        status = broker.get("status", "").upper()
        fill_qty = int(float(broker.get("fillshares", 0) or 0))
        fill_price = float(broker.get("avgprc", 0) or 0)

        return {
            "order_id": broker.get("norenordno", ""),
            "symbol": tradingsymbol,
            "side": side,
            "quantity": requested_qty,
            "fill_qty": fill_qty,
            "fill_price": fill_price,
            "status": status,
            "stt": 0.0,       # computed post-fill by cost engine (LIVE-12)
            "brokerage": 0.0,  # idem
            "timestamp": broker.get("exch_tm", datetime.now().isoformat()),
            "paper": False,
            "reason": broker.get("rejreason", ""),
        }

    @staticmethod
    def _make_rejected(
        tradingsymbol: str,
        side: str,
        quantity: int,
        reason: str = "",
    ) -> Dict:
        return {
            "order_id": "",
            "symbol": tradingsymbol,
            "side": side,
            "quantity": quantity,
            "fill_qty": 0,
            "fill_price": 0.0,
            "status": "REJECTED",
            "stt": 0.0,
            "brokerage": 0.0,
            "timestamp": datetime.now().isoformat(),
            "paper": False,
            "reason": reason,
        }


# ── Shared utility (used by iron_condor.py too) ──────────────────────────────

def persist_stuck_legs(legs: list) -> None:
    """Append stuck leg records to data/stuck_legs.json for operator review."""
    existing: list = []
    if os.path.exists(_STUCK_LEGS_PATH):
        try:
            with open(_STUCK_LEGS_PATH) as f:
                existing = json.load(f)
        except Exception:
            pass
    existing.extend(legs)
    try:
        os.makedirs(os.path.dirname(_STUCK_LEGS_PATH), exist_ok=True)
        with open(_STUCK_LEGS_PATH, "w") as f:
            json.dump(existing, f, indent=2, default=str)
    except Exception:
        logger.error("Failed to persist stuck legs to %s", _STUCK_LEGS_PATH)
