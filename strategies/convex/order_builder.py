"""
Order Builder Utility for Convex Strategy

Provides functions to convert trade proposals/positions into broker orders,
with BUY orders prioritized before SELL orders to avoid margin issues.

Guardrails:
- All orders are MIS only (product_type 'I'); no NRML.
- Entry: LMT (price control), retention DAY. Exit: MKT.
- When USE_BASKET_ENTRY: all legs submitted in parallel (basket), then wait for all to fill; reduces imbalance from sequential delay.
- When disabled: sequential placement (longs first, wait for fill, then shorts).
"""

import concurrent.futures
import json
import logging
import os
import time
import uuid
import math
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

# Entry order validity: DAY so orders can rest; if long does not fill within LONG_LEG_FILL_TIMEOUT we drop proposal.
ENTRY_RETENTION = "DAY"

# NIFTY option tick size (price increments) on NSE.
NIFTY_OPTION_TICK_SIZE = 0.05


def _round_price_to_tick(price: float, tick_size: float, side: str) -> float:
    """
    Round price to a valid NIFTY option tick (0.05). Broker rejects with
    "Price X is not a multiple of tick size 0.05" if not exact.

    For fill probability: BUY round up, SELL round down. Result is normalized
    to an exact multiple of tick_size to avoid float representation issues.
    """
    try:
        p = float(price)
        ts = float(tick_size)
        if ts <= 0:
            return round(p, 2)
        ticks = p / ts
        side_u = (side or "").strip().upper()
        if side_u == "B":
            num_ticks = math.ceil(ticks - 1e-9)
        elif side_u == "S":
            num_ticks = math.floor(ticks + 1e-9)
        else:
            num_ticks = round(ticks)
        # Exact multiple: avoid (num_ticks * ts) float noise; 2 decimals for 0.05
        rounded = round(num_ticks * ts, 2)
        return rounded
    except Exception:
        return float(price)

# Guardrail 4: sequential placement with fill check. Long must fill within this window or we drop proposal.
LONG_LEG_FILL_TIMEOUT_SECONDS = 120
ORDER_FILL_POLL_INTERVAL_SECONDS = 2
ORDER_FILL_TIMEOUT_SECONDS = 60  # used for short leg and exit orders

# Submit all Convex entry legs in parallel (basket); then wait for all to fill. Reduces imbalance risk from sequential delay.
USE_BASKET_ENTRY = True
BASKET_FILL_TIMEOUT_SECONDS = 120  # all legs must fill within this window
# When buys are filled but short(s) still pending, we cancel short limit and place market; give market this extra time.
BASKET_MKT_FALLBACK_EXTRA_SECONDS = 60


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
        raw_price = leg.get("price", leg.get("ltp", 0))

        if strike is None:
            continue

        symbol = (leg.get("tradingsymbol") or "").strip()
        if not symbol:
            raise ValueError(
                f"Convex entry leg missing tradingsymbol (strike={strike}, option_type={option_type}). "
                "Legs must have tradingsymbol from NFO option chain; no fallback."
            )

        leg_qty = leg.get("quantity", 1)
        quantity = leg_qty * lots * lot_size

        # Determine side and apply tick-size rounding to entry price.
        side = "B" if position == "LONG" else "S"
        price = _round_price_to_tick(raw_price, NIFTY_OPTION_TICK_SIZE, side)

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

        order["buy_or_sell"] = side
        if side == "B":
            buy_orders.append(order)
        else:
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

        symbol = (leg.get("tradingsymbol") or "").strip()
        if not symbol:
            raise ValueError(
                f"Convex exit leg missing tradingsymbol (strike={strike}, option_type={option_type}). "
                "Legs must have tradingsymbol from NFO; no fallback."
            )

        leg_qty = leg.get("quantity", 1)
        quantity = leg_qty * lots * lot_size

        # MKT orders must have price 0; broker rejects "nonzero Price for Market order!"
        price = 0.0

        # NIFTY options trade on NFO (F&O), not NSE (cash).
        order: Dict[str, Any] = {
            "exchange": "NFO",
            "tradingsymbol": symbol,
            "quantity": quantity,
            "price": price,
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

# When broker reports "Yel is down" (SAF:Yel), wait this long before treating as terminal (gives broker time to recover).
YEL_DOWN_WAIT_SECONDS = 300  # 5 minutes


def _order_dict_to_place_kwargs(order: Dict[str, Any]) -> Dict[str, Any]:
    """
    Convert order dict to kwargs for NorenApi.place_order.
    See: https://github.com/Shoonya-Dev/ShoonyaApi-py#place-order
    - exch: NSE/NFO/... (from login exarr); prd: C/M/I/B/H (from login prarr, I=MIS); ret: DAY/EOS/IOC.
    - trgprc only for SL/SL-M; for LMT/MKT pass trigger_price=None per API doc.
    """
    pt = (order.get("price_type") or "LMT").upper()
    trigger = order.get("trigger_price") if pt in ("SL-LMT", "SL-MKT") else None
    # MKT orders must have price 0 (broker rejects nonzero price for market order)
    if pt == "MKT":
        price = 0.0
    else:
        price = round(float(order["price"]), 2)
    return {
        "buy_or_sell": order["buy_or_sell"],
        "product_type": order["product_type"],
        "exchange": order["exchange"],
        "tradingsymbol": order["tradingsymbol"],
        "quantity": int(order["quantity"]),
        "discloseqty": order.get("discloseqty", 0),
        "price_type": order["price_type"],
        "price": price,
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
            "place_order returned None. Request: exchange=%s tradingsymbol=%s quantity=%s product_type=%s price_type=%s retention=%s | raw_ret type=%s repr=%r. "
            "Check: (1) login exarr contains NFO and prarr contains I (MIS); (2) run with logging DEBUG to see HTTP; (3) try 1 lot to rule out quantity limits.",
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


def _is_yel_down_rejection(record: Dict[str, Any]) -> bool:
    """True if rejection reason indicates broker 'Yel' service is down (transient)."""
    msg = (
        (record.get("rejreason") or "")
        + " "
        + (record.get("emsg") or "")
    ).strip().upper()
    return "YEL" in msg and ("DOWN" in msg or "SAF" in msg)


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
                if _is_yel_down_rejection(current):
                    logger.warning(
                        "Order %s rejected (Yel is down): waiting %s s before treating as terminal. rejreason=%s",
                        order_id, YEL_DOWN_WAIT_SECONDS, current.get("rejreason") or current.get("emsg"),
                    )
                    time.sleep(YEL_DOWN_WAIT_SECONDS)
                else:
                    logger.warning(
                        "Order %s terminal non-fill: status=%s rpt=%s emsg=%s full_record=%r",
                        order_id,
                        current.get("status"),
                        current.get("rpt"),
                        current.get("emsg") or current.get("rejreason"),
                        current,
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


def _place_convex_basket(api: Any, orders: List[Dict[str, Any]]) -> tuple:
    """
    Submit all Convex entry orders in parallel (basket), then wait for all to fill.
    Returns (order_ids: List[str], success: bool). On success all legs filled; on failure
    caller should run cleanup (cancel unfilled, close filled).
    """
    order_ids: List[str] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        futures = [executor.submit(place_single_order, api, o) for o in orders]
        results: List[Optional[Dict[str, Any]]] = []
        for f in futures:
            try:
                results.append(f.result())
            except Exception as e:
                logger.warning("Basket order submit failed for one leg: %s", e)
                results.append(None)
    # Check all submitted successfully
    for i, r in enumerate(results):
        if r is None:
            logger.error("Basket: order %d failed to submit; cancelling any that were placed", i + 1)
            for oid in order_ids:
                try:
                    api.cancel_order(orderno=oid)
                    logger.info("Cancelled basket order %s after submit failure", oid)
                except Exception as ce:
                    logger.warning("Cancel failed for %s: %s", oid, ce)
            return (order_ids, False)
        oid = (r.get("norenordno") or r.get("order_id") or "").__str__()
        if not oid:
            logger.error("Basket: order %d returned no order ID; cancelling any that were placed", i + 1)
            for oid_existing in order_ids:
                try:
                    api.cancel_order(orderno=oid_existing)
                except Exception:
                    pass
            return (order_ids, False)
        order_ids.append(oid)
    # Order of order_ids matches orders: first all buys, then all sells.
    n_buy = sum(1 for o in orders if (o.get("buy_or_sell") or "").strip().upper() == "B")
    sell_upgraded: set = set()  # indices of order_ids we've replaced with market orders
    deadline = time.monotonic() + BASKET_FILL_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        filled_indices: set = set()
        terminal_fail_indices: set = set()
        all_filled = True
        for i, oid in enumerate(order_ids):
            try:
                hist = api.single_order_history(orderno=oid)
            except Exception:
                all_filled = False
                break
            records = hist if isinstance(hist, list) else [hist]
            current = records[0] if records else None
            if not isinstance(current, dict):
                all_filled = False
                break
            if _is_order_filled(current):
                filled_indices.add(i)
                continue
            if _is_order_terminal_fail(current):
                if _is_yel_down_rejection(current):
                    logger.warning(
                        "Basket order %s rejected (Yel is down): waiting %s s before treating as terminal. rejreason=%s",
                        oid, YEL_DOWN_WAIT_SECONDS, current.get("rejreason") or current.get("emsg"),
                    )
                    time.sleep(YEL_DOWN_WAIT_SECONDS)
                else:
                    logger.warning("Basket order %s terminal non-fill: %s", oid, current.get("rejreason") or current.get("status"))
                return (order_ids, False)
            all_filled = False
        if all_filled:
            logger.info("Basket: all %d leg(s) filled within timeout", len(order_ids))
            return (order_ids, True)
        # If all buy orders are filled but some short(s) still pending, replace those short limits with market.
        buy_indices = set(range(n_buy))
        sell_indices = set(range(n_buy, len(orders)))
        pending_sells = sell_indices - filled_indices - terminal_fail_indices - sell_upgraded
        if buy_indices <= filled_indices and pending_sells:
            deadline = max(deadline, time.monotonic() + BASKET_MKT_FALLBACK_EXTRA_SECONDS)
            for j in pending_sells:
                try:
                    api.cancel_order(orderno=order_ids[j])
                    logger.info("Basket: cancelled short limit %s, placing market order for same leg", order_ids[j])
                except Exception as ce:
                    logger.warning("Basket: cancel short %s failed: %s", order_ids[j], ce)
                mkt_order = {
                    **orders[j],
                    "price_type": "MKT",
                    "price": 0.0,
                    "trigger_price": None,
                    "remarks": (orders[j].get("remarks") or "convex") + "_mkt",
                }
                try:
                    ret = place_single_order(api, mkt_order)
                    new_oid = (ret.get("norenordno") or ret.get("order_id") or "").__str__()
                    if new_oid:
                        order_ids[j] = new_oid
                        sell_upgraded.add(j)
                        logger.info("Basket: placed short market order %s for %s", new_oid, orders[j].get("tradingsymbol"))
                    else:
                        logger.warning("Basket: market order for short leg returned no order ID")
                except Exception as e:
                    logger.exception("Basket: place short market failed: %s", e)
        time.sleep(ORDER_FILL_POLL_INTERVAL_SECONDS)
    logger.warning("Basket: not all orders filled within timeout")
    return (order_ids, False)


def place_convex_trade(api: Any, proposal: Dict, product_type: Optional[str] = None) -> Dict:
    """
    Place Convex trade: either basket (all legs submitted in parallel) or sequential (longs first, then shorts).
    Returns success only when all legs are filled.
    """
    orders = build_convex_entry_orders(proposal, product_type)
    buy_orders = [o for o in orders if o.get("buy_or_sell") == "B"]
    sell_orders = [o for o in orders if o.get("buy_or_sell") == "S"]

    logger.info(
        "=== Convex entry (%s): %d long(s), %d short(s) ===",
        "basket" if USE_BASKET_ENTRY else "sequential",
        len(buy_orders),
        len(sell_orders),
    )
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

    if USE_BASKET_ENTRY:
        order_ids_out, success = _place_convex_basket(api, orders)
        order_ids.extend(order_ids_out)
        if success:
            return {
                "success": True,
                "orders": orders,
                "order_ids": order_ids,
                "result": order_ids,
            }
        msg = "basket: not all legs filled within timeout" if order_ids else "basket: submit failure"
        return _cleanup_and_return_failure("basket_fill_timeout", msg)

    # Phase 1: place longs and wait for fill each (sequential path)
    for i, order in enumerate(buy_orders):
        try:
            ret = place_single_order(api, order)
            oid = ret.get("norenordno") or ret.get("order_id") or ""
            if not oid:
                logger.error("Place long order %d: no order ID in response %s", i + 1, ret)
                return _cleanup_and_return_failure("no_order_id_long", "no order ID", result=ret)
            order_ids.append(str(oid))
            if not wait_for_order_fill(api, str(oid), timeout_seconds=LONG_LEG_FILL_TIMEOUT_SECONDS):
                logger.error(
                    "Long order %s did not fill within %s s. Cancelling and dropping proposal; short leg(s) not placed.",
                    oid,
                    LONG_LEG_FILL_TIMEOUT_SECONDS,
                )
                try:
                    api.cancel_order(orderno=str(oid))
                    logger.info("Cancelled unfilled long order %s", oid)
                except Exception as cancel_err:
                    logger.warning("Failed to cancel unfilled long order %s: %s", oid, cancel_err)
                # No filled legs to close; do not call _cleanup_and_return_failure (would wrongly offset unfilled order).
                return {"success": False, "orders": orders, "order_ids": order_ids, "message": "long leg fill timeout"}
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
                logger.warning("Short order %s did not fill within timeout", oid)
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
