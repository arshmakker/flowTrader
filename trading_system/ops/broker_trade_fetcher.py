"""LIVE-08: live broker-fetcher shim over ``api.get_trade_book()``.

The reconcile library (``trading_system.ops.reconcile``) is source-agnostic;
it accepts pre-built ``Leg`` lists. This module produces those Legs from a
live Shoonya session, closing the last open piece of LIVE-08.

Field names follow the documented Shoonya/Noren trade-book response shape.
On day-1 of shakedown the operator must validate this map against a real
response — the constants below are the single point of update if a key
shifts (e.g. ``flqty`` → ``fillshares``). The same convention (``tsym``,
``exch``, ``trantype``) is already used elsewhere in this repo
(``startup_reconcile``, ``api_helper.place_order``) so divergence here would
be an upstream contract change, not a code-side guess.

Cost stack: ``get_trade_book`` returns per-trade fills only. The full cost
breakdown (STT, brokerage, exch_txn, SEBI, stamp, GST) lives on the contract
note (LIVE-12). This shim emits ``costs=0.0``; the reconciliation report's
``cost_delta`` will reflect engine-side costs vs zero until LIVE-12 wires a
separate cost fetch. Price drift (LIVE-04) is unaffected.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Iterable, List, Optional

from trading_system.ops.reconcile import Leg

logger = logging.getLogger(__name__)

# Shoonya trade-book field names — single point of update.
_FIELD_SYMBOL = "tsym"
_FIELD_EXCHANGE = "exch"
_FIELD_SIDE = "trantype"
_FIELD_FILL_QTY = "flqty"
_FIELD_FILL_PRICE = "flprc"
_FIELD_FILL_TIME = "fltm"
_FIELD_ORDER_ID = "norenordno"

# Shoonya fltm: "DD-MM-YYYY HH:MM:SS" (broker-portal style). ISO fallback
# kept narrow — covers the case where an upstream wrapper has already
# normalised timestamps. Anything else is a malformed row and is skipped.
_FLTM_PRIMARY = "%d-%m-%Y %H:%M:%S"


def _parse_fltm(raw: Any) -> Optional[datetime]:
    s = str(raw or "").strip()
    if not s:
        return None
    try:
        return datetime.strptime(s, _FLTM_PRIMARY)
    except ValueError:
        pass
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def _row_to_leg(row: Any) -> Optional[Leg]:
    if not isinstance(row, dict):
        return None
    tsym = str(row.get(_FIELD_SYMBOL, "")).strip()
    exch = str(row.get(_FIELD_EXCHANGE, "")).strip()
    if not tsym or not exch:
        return None
    raw_side = str(row.get(_FIELD_SIDE, "")).strip().upper()
    if raw_side in ("B", "BUY"):
        side = "BUY"
    elif raw_side in ("S", "SELL"):
        side = "SELL"
    else:
        return None
    try:
        qty = int(float(row.get(_FIELD_FILL_QTY, 0) or 0))
        price = float(row.get(_FIELD_FILL_PRICE, 0) or 0)
    except (TypeError, ValueError):
        return None
    if qty <= 0 or price <= 0:
        return None
    ts = _parse_fltm(row.get(_FIELD_FILL_TIME))
    if ts is None:
        return None
    return Leg(
        timestamp=ts,
        symbol=f"{exch}|{tsym}",
        side=side,
        quantity=qty,
        fill_price=price,
        costs=0.0,
        source_row=dict(row),
    )


def normalize_trade_book(
    rows: Optional[Iterable[Any]],
    date_iso: Optional[str] = None,
) -> List[Leg]:
    """Convert a ``get_trade_book()`` response into reconcile ``Leg`` objects.

    ``date_iso`` (YYYY-MM-DD) filters by exchange fill date when provided.
    Malformed rows (missing or unparseable required fields) are skipped
    silently — they have no engine counterpart so they would surface as
    ``unmatched_broker`` if kept, which is no more useful than dropping them.
    """
    legs: List[Leg] = []
    if not rows:
        return legs
    for row in rows:
        leg = _row_to_leg(row)
        if leg is None:
            continue
        if date_iso and leg.timestamp.date().isoformat() != date_iso:
            continue
        legs.append(leg)
    return legs


def fetch_trade_book_legs(api, date_iso: Optional[str] = None) -> List[Leg]:
    """Call ``api.get_trade_book()`` and return normalized Legs.

    The Noren SDK returns ``None`` for the no-trades case (the underlying
    response is a non-list error envelope). Treat that as zero legs, not
    an exception — a flat day is a valid reconciliation outcome.

    Errors from the underlying HTTP call propagate; reconciliation against
    a partial broker set is worse than no reconciliation, so the pipeline
    should halt and surface the failure rather than emit a misleading
    "all engine legs unmatched" report.
    """
    rows = api.get_trade_book()
    if rows is None:
        logger.info("get_trade_book returned no rows for date=%s", date_iso or "*")
        return []
    legs = normalize_trade_book(rows, date_iso=date_iso)
    row_count = len(rows) if hasattr(rows, "__len__") else "?"
    logger.info(
        "get_trade_book: %s row(s) -> %d leg(s) for date=%s",
        row_count,
        len(legs),
        date_iso or "*",
    )
    return legs
