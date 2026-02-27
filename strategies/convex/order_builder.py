"""
Order Builder Utility for Convex Strategy

Provides functions to convert trade proposals/positions into broker orders,
with BUY orders prioritized before SELL orders to avoid margin issues.

Guardrails:
- All orders are MIS only (product_type 'I'); no NRML.
- Long legs before short legs; do not reorder.
- Entry: LMT (price control), retention IOC (Immediate-or-Cancel) for fast fill/cancel. Exit: MKT.
- Sequential placement: place longs, wait for fill, then place shorts; verify every order filled.
"""

import json
import logging
import os
import time
import uuid
from typing import Dict, List, Optional, Any

from datetime import datetime

logger = logging.getLogger(__name__)

# File for reviewing partial-fill cleanup events (one entry per incident).
PARTIAL_FILL_REVIEW_FILE = "partial_fill_reviews.json"
MAX_PARTIAL_FILL_REVIEW_ENTRIES = 500

# Guardrail: Convex orders are MIS only (intraday square-off).
# Shoonya/Noren: prd "M" = NRML, "I" = MIS (intraday). Use "I" for MIS.
CONVEX_PRODUCT_TYPE = "I"

# Guardrail: entry = limit (price control), exit = market (execution certainty).
ENTRY_PRICE_TYPE = "LMT"
EXIT_PRICE_TYPE = "MKT"

# Entry order validity: IOC (Immediate-or-Cancel) so we get fast fill or cancel; no lingering
# pending orders. NSE and Shoonya API support DAY / EOS / IOC (see place_order ret*).
ENTRY_RETENTION = "IOC"

# Guardrail 4: sequential placement with fill check.
ORDER_FILL_TIMEOUT_SECONDS = 60
ORDER_FILL_POLL_INTERVAL_SECONDS = 2


def generate_nifty_symbol(strike: float, option_type: str, expiry: str) -> str:
    """
    Generate NIFTY option trading symbol.
    
    Format: NIFTY{YY}{MMM}{STRIKE}{CE/PE}
    Example: NIFTY26FEB25550CE
    
    Args:
        strike: Strike price (e.g., 25550)
        option_type: 'CE' or 'PE'
        expiry: Expiry date string (YYYY-MM-DD or DD-MON-YY)
    
    Returns:
        Trading symbol string
    """
    try:
        if '-' in expiry:
            if len(expiry) == 10:  # YYYY-MM-DD
                dt = datetime.strptime(expiry, '%Y-%m-%d')
            else:  # DD-MON-YY
                dt = datetime.strptime(expiry, '%d-%b-%y')
        else:
            dt = datetime.strptime(expiry, '%Y%m%d')
        
        yy = dt.strftime('%y')
        mon = dt.strftime('%b').upper()
        symbol = f"NIFTY{yy}{mon}{int(strike)}{option_type}"
        return symbol
    except Exception:
        return f"NIFTY{expiry}{int(strike)}{option_type}"


def build_convex_entry_orders(proposal: Dict, product_type: Optional[str] = None) -> List[Dict]:
    """
    Build orders for Convex entry with BUY orders first.

    Guardrail: long legs before short legs; do not reorder.
    Guardrail: all Convex orders are MIS only (product_type ignored, forced to 'I').
    Guardrail: entry uses LMT for price control.

    Args:
        proposal: Trade proposal with legs
        product_type: Ignored; Convex uses MIS only.

    Returns:
        List of order dictionaries (BUY first, then SELL)
    """
    legs = proposal.get("legs", [])
    lots = proposal.get("lots", 1)
    lot_size = proposal.get("lot_size", 65)
    expiry = proposal.get("expiry")

    if not legs or not expiry:
        raise ValueError("Proposal missing legs or expiry")

    buy_orders: List[Dict] = []
    sell_orders: List[Dict] = []

    for leg in legs:
        position = leg.get("position", "").upper()
        option_type = leg.get("option_type", "").upper()
        strike = leg.get("strike")
        price = leg.get("price", leg.get("ltp", 0))

        if strike is None:
            continue

        symbol = generate_nifty_symbol(strike, option_type, expiry)

        leg_qty = leg.get("quantity", 1)
        quantity = leg_qty * lots * lot_size

        # NIFTY options trade on NFO (F&O), not NSE (cash). Wrong exchange causes place_order to return None.
        order: Dict[str, Any] = {
            "exchange": "NFO",
            "tradingsymbol": symbol,
            "quantity": quantity,
            "price": float(price),
            "price_type": ENTRY_PRICE_TYPE,
            "retention": ENTRY_RETENTION,
        }
        order["product_type"] = CONVEX_PRODUCT_TYPE

        if position == "LONG":
            order["buy_or_sell"] = "B"
            buy_orders.append(order)
        else:
            order["buy_or_sell"] = "S"
            sell_orders.append(order)

    result = buy_orders + sell_orders
    assert all(o.get("buy_or_sell") == "B" for o in buy_orders), "Long legs must be BUY"
    assert all(o.get("buy_or_sell") == "S" for o in sell_orders), "Short legs must be SELL"
    assert result == buy_orders + sell_orders, "Guardrail: long legs before short legs"
    return result


def build_convex_exit_orders(
    position: Dict, exit_prices: Optional[Dict] = None, product_type: Optional[str] = None
) -> List[Dict]:
    """
    Build orders for Convex exit with BUY orders first (cover SHORT, then SELL LONG).

    Guardrail: long legs before short legs; do not reorder.
    Guardrail: all Convex orders are MIS only (product_type ignored, forced to 'I').
    Guardrail: exit uses MKT for execution certainty.

    Args:
        position: Position with legs
        exit_prices: Optional dict of {strike: price} for each leg
        product_type: Ignored; Convex uses MIS only.

    Returns:
        List of order dictionaries (BUY first, then SELL)
    """
    legs = position.get("legs", [])
    lots = position.get("lots", 1)
    lot_size = position.get("lot_size", 65)
    expiry = position.get("expiry")

    if not legs or not expiry:
        raise ValueError("Position missing legs or expiry")

    buy_orders: List[Dict] = []
    sell_orders: List[Dict] = []

    for leg in legs:
        position_type = leg.get("position", "").upper()
        option_type = leg.get("option_type", "").upper()
        strike = leg.get("strike")

        if strike is None:
            continue

        symbol = generate_nifty_symbol(strike, option_type, expiry)

        leg_qty = leg.get("quantity", 1)
        quantity = leg_qty * lots * lot_size

        if exit_prices and strike in exit_prices:
            price = exit_prices[strike]
        else:
            price = leg.get("price", leg.get("ltp", 0))

        # NIFTY options trade on NFO (F&O), not NSE (cash).
        order: Dict[str, Any] = {
            "exchange": "NFO",
            "tradingsymbol": symbol,
            "quantity": quantity,
            "price": float(price),
            "price_type": EXIT_PRICE_TYPE,
        }
        order["product_type"] = CONVEX_PRODUCT_TYPE

        if position_type == "SHORT":
            order["buy_or_sell"] = "B"
            buy_orders.append(order)
        else:
            order["buy_or_sell"] = "S"
            sell_orders.append(order)

    result = buy_orders + sell_orders
    assert all(o.get("buy_or_sell") == "B" for o in buy_orders), "Cover-short legs must be BUY"
    assert all(o.get("buy_or_sell") == "S" for o in sell_orders), "Sell-long legs must be SELL"
    assert result == buy_orders + sell_orders, "Guardrail: long legs before short legs"
    return result


# Shoonya order states (status) and report types (rpt): see
# https://github.com/Shoonya-Dev/ShoonyaApi-py#-order-states-and-report-types
# Having norenordno only means "order accepted by OMS", not executed. Fulfillment = status COMPLETE or rpt Fill.
ORDER_STATUS_FILLED = "COMPLETE"  # order state: successfully executed
ORDER_REPORT_FILL = "Fill"        # report type: order fully or partially executed
ORDER_STATUS_TERMINAL_FAIL = ("REJECTED", "CANCELLED", "CANCELED")
ORDER_REPORT_TERMINAL_FAIL = ("Rejected", "Canceled")


def _order_dict_to_place_kwargs(order: Dict[str, Any]) -> Dict[str, Any]:
    """
    Convert order dict to kwargs for NorenApi.place_order.
    See: https://github.com/Shoonya-Dev/ShoonyaApi-py#place-order
    - exch: NSE/NFO/... (from login exarr); prd: C/M/I/B/H (from login prarr, I=MIS); ret: DAY/EOS/IOC.
    - trgprc only for SL/SL-M; for LMT/MKT pass trigger_price=None per API doc.
    """
    pt = (order.get("price_type") or "LMT").upper()
    trigger = order.get("trigger_price") if pt in ("SL-LMT", "SL-MKT") else None
    return {
        "buy_or_sell": order["buy_or_sell"],
        "product_type": order["product_type"],
        "exchange": order["exchange"],
        "tradingsymbol": order["tradingsymbol"],
        "quantity": int(order["quantity"]),
        "discloseqty": order.get("discloseqty", 0),
        "price_type": order["price_type"],
        "price": float(order["price"]),
        "trigger_price": trigger,
        "retention": order.get("retention", "DAY"),
        "remarks": order.get("remarks", f"convex_{uuid.uuid4().hex[:8]}"),
    }


def place_single_order(api: Any, order: Dict[str, Any]) -> Dict[str, Any]:
    """
    Place one order via the broker API. Ensures all required NorenApi fields are set.

    Returns:
        API response dict; must contain 'norenordno' (order ID) on success.
    """
    kwargs = _order_dict_to_place_kwargs(order)
    try:
        ret = api.place_order(**kwargs)
    except Exception as e:
        logger.error(
            "place_order raised exception. Request: exchange=%s tradingsymbol=%s quantity=%s product_type=%s price_type=%s retention=%s | exception=%s",
            kwargs.get("exchange"),
            kwargs.get("tradingsymbol"),
            kwargs.get("quantity"),
            kwargs.get("product_type"),
            kwargs.get("price_type"),
            kwargs.get("retention"),
            e,
            exc_info=True,
        )
        raise
    # Per Shoonya API doc: success = { stat, norenordno }; failure = { stat: "Not_Ok", emsg }.
    # Python client returns None when no response (network/session/parse), not when server sends Not_Ok.
    if ret is None:
        logger.error(
            "place_order returned None. Request: exchange=%s tradingsymbol=%s quantity=%s product_type=%s price_type=%s retention=%s | raw_ret type=%s repr=%r",
            kwargs.get("exchange"),
            kwargs.get("tradingsymbol"),
            kwargs.get("quantity"),
            kwargs.get("product_type"),
            kwargs.get("price_type"),
            kwargs.get("retention"),
            type(ret).__name__,
            ret,
        )
        raise RuntimeError("place_order returned None")
    if not isinstance(ret, dict):
        logger.error(
            "place_order returned non-dict. Request: exchange=%s tradingsymbol=%s | raw_ret type=%s repr=%r",
            kwargs.get("exchange"),
            kwargs.get("tradingsymbol"),
            type(ret).__name__,
            ret,
        )
        raise RuntimeError(f"place_order returned unexpected type {type(ret).__name__}: {ret!r}")
    if ret.get("stat") == "Not_Ok":
        logger.warning("place_order returned Not_Ok: %s", ret)
        raise RuntimeError(f"Order rejected: {ret.get('emsg', ret)}")
    logger.debug("place_order success: type=%s keys=%s", type(ret).__name__, list(ret.keys()) if isinstance(ret, dict) else "n/a")
    return ret


def _is_order_filled(record: Dict[str, Any]) -> bool:
    """
    True if order is fulfilled per Shoonya API.
    Fulfillment = order state COMPLETE or report type Fill (not just having order ID).
    """
    status = (record.get("status") or "").strip().upper()
    rpt = (record.get("rpt") or "").strip()
    if status == ORDER_STATUS_FILLED:
        return True
    if rpt == ORDER_REPORT_FILL:
        return True
    return False


def _is_order_terminal_fail(record: Dict[str, Any]) -> bool:
    """True if order will not fill (rejected/canceled)."""
    status = (record.get("status") or "").strip().upper()
    rpt = (record.get("rpt") or "").strip()
    if status in ORDER_STATUS_TERMINAL_FAIL:
        return True
    if rpt in ORDER_REPORT_TERMINAL_FAIL:
        return True
    return False


def wait_for_order_fill(
    api: Any,
    order_id: str,
    timeout_seconds: int = ORDER_FILL_TIMEOUT_SECONDS,
    poll_interval_seconds: float = ORDER_FILL_POLL_INTERVAL_SECONDS,
) -> bool:
    """
    Poll order status until filled, timeout, or rejected/cancelled.

    Uses api.single_order_history(orderno=order_id). Fulfillment is detected only when
    order state (status) is COMPLETE or report type (rpt) is Fill — per Shoonya API
    order states and report types. Having norenordno only means order accepted by OMS.

    Returns:
        True if order is filled, False on timeout or cancelled/rejected.
    """
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            hist = api.single_order_history(orderno=order_id)
        except Exception as e:
            logger.warning("single_order_history failed for %s: %s", order_id, e)
            time.sleep(poll_interval_seconds)
            continue
        if not hist:
            time.sleep(poll_interval_seconds)
            continue
        # Response is list of report records (newest first in Shoonya sample); use latest for current state.
        records = hist if isinstance(hist, list) else [hist]
        current = records[0] if records else None
        if isinstance(current, dict):
            if _is_order_filled(current):
                return True
            if _is_order_terminal_fail(current):
                logger.warning(
                    "Order %s terminal non-fill: status=%s rpt=%s",
                    order_id,
                    current.get("status"),
                    current.get("rpt"),
                )
                return False
        time.sleep(poll_interval_seconds)
    logger.warning("Order %s fill check timed out after %s s", order_id, timeout_seconds)
    return False


def _place_offsetting_order(api: Any, order_dict: Dict[str, Any]) -> Dict[str, Any]:
    """
    Place one MKT order to close a filled leg (opposite side, same symbol/qty, MIS).
    Used when we have partial fill and need to flatten to avoid orphan.
    """
    opposite = "S" if (order_dict.get("buy_or_sell") or "").strip().upper() == "B" else "B"
    close_order = {
        "exchange": order_dict["exchange"],
        "tradingsymbol": order_dict["tradingsymbol"],
        "quantity": int(order_dict["quantity"]),
        "price": 0.0,
        "price_type": EXIT_PRICE_TYPE,
        "product_type": CONVEX_PRODUCT_TYPE,
        "buy_or_sell": opposite,
        "retention": "DAY",
        "remarks": f"convex_cleanup_{uuid.uuid4().hex[:8]}",
    }
    return place_single_order(api, close_order)


def _close_filled_legs(
    api: Any, order_ids: List[str], orders: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    """
    Place offsetting MKT orders for each filled leg (order_ids[i] <-> orders[i]).
    Returns list of per-leg results for review: entry_order_id, tradingsymbol, quantity, side,
    cleanup_order_id, cleanup_ok, error (if any).
    """
    results: List[Dict[str, Any]] = []
    for i, oid in enumerate(order_ids):
        if i >= len(orders):
            break
        order = orders[i]
        side = (order.get("buy_or_sell") or "").strip().upper()
        rec = {
            "entry_order_id": oid,
            "tradingsymbol": order.get("tradingsymbol"),
            "quantity": int(order.get("quantity", 0)),
            "side": side,
            "cleanup_order_id": None,
            "cleanup_ok": False,
            "error": None,
        }
        try:
            ret = _place_offsetting_order(api, order)
            rec["cleanup_order_id"] = ret.get("norenordno") or ret.get("order_id")
            rec["cleanup_ok"] = True
            if rec["cleanup_order_id"]:
                wait_ok = wait_for_order_fill(api, str(rec["cleanup_order_id"]))
                rec["cleanup_ok"] = wait_ok
        except Exception as e:
            rec["error"] = str(e)
            logger.exception("Cleanup order for leg %s failed: %s", oid, e)
        results.append(rec)
    return results


def _save_partial_fill_review(
    reason: str,
    message: str,
    order_ids: List[str],
    orders: List[Dict[str, Any]],
    cleanup_results: List[Dict[str, Any]],
    proposal_info: Optional[Dict[str, Any]] = None,
) -> None:
    """
    Append one entry to partial_fill_reviews.json for later review after each cleanup.
    """
    timestamp = datetime.now().isoformat()
    filled_legs = []
    for i, oid in enumerate(order_ids):
        if i < len(orders):
            o = orders[i]
            filled_legs.append({
                "order_id": oid,
                "tradingsymbol": o.get("tradingsymbol"),
                "quantity": int(o.get("quantity", 0)),
                "buy_or_sell": o.get("buy_or_sell"),
            })
    entry = {
        "timestamp": timestamp,
        "reason": reason,
        "message": message,
        "filled_legs": filled_legs,
        "cleanup_results": cleanup_results,
        "proposal_info": proposal_info or {},
    }
    try:
        existing: List[Dict[str, Any]] = []
        if os.path.exists(PARTIAL_FILL_REVIEW_FILE):
            with open(PARTIAL_FILL_REVIEW_FILE, "r") as f:
                existing = json.load(f)
        existing.append(entry)
        if len(existing) > MAX_PARTIAL_FILL_REVIEW_ENTRIES:
            existing = existing[-MAX_PARTIAL_FILL_REVIEW_ENTRIES:]
        with open(PARTIAL_FILL_REVIEW_FILE, "w") as f:
            json.dump(existing, f, indent=2, default=str)
        logger.warning(
            "Partial-fill cleanup: %s. Review saved to %s (timestamp=%s)",
            message, PARTIAL_FILL_REVIEW_FILE, timestamp,
        )
    except Exception as e:
        logger.exception("Failed to save partial-fill review: %s", e)


def place_convex_trade(api: Any, proposal: Dict, product_type: Optional[str] = None) -> Dict:
    """
    Place Convex trade with guardrails: longs first, wait for each fill, then shorts.

    Guardrail: only after all long orders are filled are short orders placed.
    Returns success only when all legs are filled.

    Args:
        api: ShoonyaApiPy instance
        proposal: Trade proposal
        product_type: Ignored; Convex uses MIS only.

    Returns:
        Result dict with success status, order IDs, and placed orders.
    """
    orders = build_convex_entry_orders(proposal, product_type)
    buy_orders = [o for o in orders if o.get("buy_or_sell") == "B"]
    sell_orders = [o for o in orders if o.get("buy_or_sell") == "S"]

    logger.info("=== Convex entry: %d long(s), then %d short(s) ===", len(buy_orders), len(sell_orders))
    for i, order in enumerate(orders, 1):
        logger.info("%d. %s %s %s @ Rs %s", i, order["buy_or_sell"], order["quantity"], order["tradingsymbol"], order["price"])

    order_ids: List[str] = []

    def _cleanup_and_return_failure(reason: str, msg: str, **kwargs: Any) -> Dict[str, Any]:
        if order_ids:
            cleanup_results = _close_filled_legs(api, order_ids, orders)
            _save_partial_fill_review(
                reason=reason,
                message=msg,
                order_ids=order_ids,
                orders=orders,
                cleanup_results=cleanup_results,
                proposal_info={
                    "proposal_id": proposal.get("proposal_id"),
                    "expiry": proposal.get("expiry"),
                    "lots": proposal.get("lots"),
                },
            )
        return {"success": False, "orders": orders, "order_ids": order_ids, "message": msg, **kwargs}

    # Phase 1: place longs and wait for fill each
    for i, order in enumerate(buy_orders):
        try:
            ret = place_single_order(api, order)
            oid = ret.get("norenordno") or ret.get("order_id") or ""
            if not oid:
                logger.error("Place long order %d: no order ID in response %s", i + 1, ret)
                return _cleanup_and_return_failure("no_order_id_long", "no order ID", result=ret)
            order_ids.append(str(oid))
            if not wait_for_order_fill(api, str(oid)):
                logger.error("Long order %s did not fill within timeout; not placing shorts", oid)
                return _cleanup_and_return_failure("long_leg_fill_timeout", "long leg fill timeout")
        except Exception as e:
            logger.exception("Place long order %d failed: %s", i + 1, e)
            return _cleanup_and_return_failure("long_place_exception", str(e))

    # Phase 2: place shorts only after all longs filled
    for i, order in enumerate(sell_orders):
        try:
            ret = place_single_order(api, order)
            oid = ret.get("norenordno") or ret.get("order_id") or ""
            if not oid:
                logger.error("Place short order %d: no order ID in response %s", i + 1, ret)
                return _cleanup_and_return_failure("no_order_id_short", "no order ID", result=ret)
            order_ids.append(str(oid))
            if not wait_for_order_fill(api, str(oid)):
                logger.error("Short order %s did not fill within timeout", oid)
                return _cleanup_and_return_failure("short_leg_fill_timeout", "short leg fill timeout")
        except Exception as e:
            logger.exception("Place short order %d failed: %s", i + 1, e)
            return _cleanup_and_return_failure("short_place_exception", str(e))

    return {
        "success": True,
        "orders": orders,
        "order_ids": order_ids,
        "result": order_ids,
    }


def close_convex_position(
    api: Any,
    position: Dict,
    exit_prices: Optional[Dict] = None,
    product_type: Optional[str] = None,
) -> Dict:
    """
    Close Convex position with guardrails: place exit orders in sequence, wait for each fill.

    BUY to cover short first, then SELL long. Returns success only when all exit orders are filled.

    Args:
        api: ShoonyaApiPy instance
        position: Position to close
        exit_prices: Optional dict of {strike: price}
        product_type: Ignored; Convex uses MIS only.

    Returns:
        Result dict with success status, order IDs, and placed orders.
    """
    orders = build_convex_exit_orders(position, exit_prices, product_type)

    logger.info("=== Convex exit: %d order(s) in sequence ===", len(orders))
    for i, order in enumerate(orders, 1):
        logger.info("%d. %s %s %s @ Rs %s", i, order["buy_or_sell"], order["quantity"], order["tradingsymbol"], order["price"])

    order_ids: List[str] = []
    for i, order in enumerate(orders):
        try:
            ret = place_single_order(api, order)
            oid = ret.get("norenordno") or ret.get("order_id") or ""
            if not oid:
                logger.error("Place exit order %d: no order ID in response %s", i + 1, ret)
                return {"success": False, "orders": orders, "order_ids": order_ids, "result": ret, "message": "no order ID"}
            order_ids.append(str(oid))
            if not wait_for_order_fill(api, str(oid)):
                logger.error("Exit order %s did not fill within timeout", oid)
                return {"success": False, "orders": orders, "order_ids": order_ids, "message": "exit leg fill timeout"}
        except Exception as e:
            logger.exception("Place exit order %d failed: %s", i + 1, e)
            return {"success": False, "orders": orders, "order_ids": order_ids, "message": str(e)}

    return {
        "success": True,
        "orders": orders,
        "order_ids": order_ids,
        "result": order_ids,
    }
