"""
Order Builder Utility for Convex Strategy

Provides functions to convert trade proposals/positions into broker orders,
with BUY orders prioritized before SELL orders to avoid margin issues.

Guardrails:
- All orders are MIS only (product_type 'M'); no NRML.
- Long legs before short legs; do not reorder.
- Entry: LMT (price control). Exit: MKT (execution certainty).
- Sequential placement: place longs, wait for fill, then place shorts; verify every order filled.
"""

import logging
import time
import uuid
from typing import Dict, List, Optional, Any

from datetime import datetime

logger = logging.getLogger(__name__)

# Guardrail: Convex orders are MIS only (intraday square-off).
CONVEX_PRODUCT_TYPE = "M"

# Guardrail: entry = limit (price control), exit = market (execution certainty).
ENTRY_PRICE_TYPE = "LMT"
EXIT_PRICE_TYPE = "MKT"

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
    Guardrail: all Convex orders are MIS only (product_type ignored, forced to 'M').
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

        order: Dict[str, Any] = {
            "exchange": "NSE",
            "tradingsymbol": symbol,
            "quantity": quantity,
            "price": float(price),
            "price_type": ENTRY_PRICE_TYPE,
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
    Guardrail: all Convex orders are MIS only (product_type ignored, forced to 'M').
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

        order: Dict[str, Any] = {
            "exchange": "NSE",
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
    """Convert order dict from builder to kwargs for NorenApi.place_order (discloseqty, retention, remarks)."""
    return {
        "buy_or_sell": order["buy_or_sell"],
        "product_type": order["product_type"],
        "exchange": order["exchange"],
        "tradingsymbol": order["tradingsymbol"],
        "quantity": int(order["quantity"]),
        "discloseqty": order.get("discloseqty", 0),
        "price_type": order["price_type"],
        "price": float(order["price"]),
        "trigger_price": order.get("trigger_price") or 0.0,
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
    ret = api.place_order(**kwargs)
    if ret is None:
        raise RuntimeError("place_order returned None")
    if isinstance(ret, dict) and ret.get("stat") == "Not_Ok":
        raise RuntimeError(f"Order rejected: {ret.get('emsg', ret)}")
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
    # Phase 1: place longs and wait for fill each
    for i, order in enumerate(buy_orders):
        try:
            ret = place_single_order(api, order)
            oid = ret.get("norenordno") or ret.get("order_id") or ""
            if not oid:
                logger.error("Place long order %d: no order ID in response %s", i + 1, ret)
                return {"success": False, "orders": orders, "order_ids": order_ids, "result": ret, "message": "no order ID"}
            order_ids.append(str(oid))
            if not wait_for_order_fill(api, str(oid)):
                logger.error("Long order %s did not fill within timeout; not placing shorts", oid)
                return {"success": False, "orders": orders, "order_ids": order_ids, "message": "long leg fill timeout"}
        except Exception as e:
            logger.exception("Place long order %d failed: %s", i + 1, e)
            return {"success": False, "orders": orders, "order_ids": order_ids, "message": str(e)}

    # Phase 2: place shorts only after all longs filled
    for i, order in enumerate(sell_orders):
        try:
            ret = place_single_order(api, order)
            oid = ret.get("norenordno") or ret.get("order_id") or ""
            if not oid:
                logger.error("Place short order %d: no order ID in response %s", i + 1, ret)
                return {"success": False, "orders": orders, "order_ids": order_ids, "result": ret, "message": "no order ID"}
            order_ids.append(str(oid))
            if not wait_for_order_fill(api, str(oid)):
                logger.error("Short order %s did not fill within timeout", oid)
                return {"success": False, "orders": orders, "order_ids": order_ids, "message": "short leg fill timeout"}
        except Exception as e:
            logger.exception("Place short order %d failed: %s", i + 1, e)
            return {"success": False, "orders": orders, "order_ids": order_ids, "message": str(e)}

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
