"""
LIVE-08: per-leg reconciliation between engine orders and broker contract notes.

The engine writes every filled leg to ``data/paper_orders.csv``. In live, the
broker prints a contract note with the real fill price, qty, and cost stack.
This module joins them on ``(symbol, side, time±window)`` and reports per-leg
deltas so the operator can attribute drift (slippage LIVE-04, missing costs
LIVE-12, or a real bug).

The reconciliation function is the core library. ``tools/reconcile_trades.py``
is a thin CLI wrapper; keeping the logic here makes it unit-testable against
fixtures without a live broker.

Pre-LIVE-08 drift is invisible. After LIVE-08, nightly divergence > 2% on any
single leg is a hard signal to pause the proving period and debug.
"""

from __future__ import annotations

import csv
import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

DEFAULT_MATCH_WINDOW_SEC = 60.0
DEFAULT_FLAG_PRICE_PCT = 0.02  # 2% per-leg price drift → flag


@dataclass
class Leg:
    """A normalized per-leg fill record. Both engine and broker rows use this."""

    timestamp: datetime
    symbol: str
    side: str  # canonicalised to 'BUY' / 'SELL'
    quantity: int
    fill_price: float
    costs: float  # sum of whatever cost columns are present
    source_row: Dict = field(default_factory=dict)


@dataclass
class MatchedPair:
    engine: Leg
    broker: Leg

    @property
    def price_delta(self) -> float:
        return self.broker.fill_price - self.engine.fill_price

    @property
    def price_delta_pct(self) -> float:
        """Signed percent drift of broker price vs engine price."""
        if self.engine.fill_price == 0:
            return 0.0
        return self.price_delta / self.engine.fill_price

    @property
    def qty_delta(self) -> int:
        return self.broker.quantity - self.engine.quantity

    @property
    def cost_delta(self) -> float:
        return self.broker.costs - self.engine.costs

    @property
    def time_delta_sec(self) -> float:
        return (self.broker.timestamp - self.engine.timestamp).total_seconds()


@dataclass
class ReconciliationReport:
    date: str
    engine_leg_count: int
    broker_leg_count: int
    matched: List[MatchedPair] = field(default_factory=list)
    unmatched_engine: List[Leg] = field(default_factory=list)
    unmatched_broker: List[Leg] = field(default_factory=list)
    flagged: List[MatchedPair] = field(default_factory=list)

    def to_dict(self) -> Dict:
        def _leg(l: Leg) -> Dict:
            return {
                "timestamp": l.timestamp.isoformat(),
                "symbol": l.symbol,
                "side": l.side,
                "quantity": l.quantity,
                "fill_price": l.fill_price,
                "costs": l.costs,
            }

        def _pair(p: MatchedPair) -> Dict:
            return {
                "symbol": p.engine.symbol,
                "side": p.engine.side,
                "engine": _leg(p.engine),
                "broker": _leg(p.broker),
                "price_delta": round(p.price_delta, 4),
                "price_delta_pct": round(p.price_delta_pct, 6),
                "qty_delta": p.qty_delta,
                "cost_delta": round(p.cost_delta, 4),
                "time_delta_sec": round(p.time_delta_sec, 3),
            }

        return {
            "date": self.date,
            "engine_leg_count": self.engine_leg_count,
            "broker_leg_count": self.broker_leg_count,
            "matched_count": len(self.matched),
            "unmatched_engine_count": len(self.unmatched_engine),
            "unmatched_broker_count": len(self.unmatched_broker),
            "flagged_count": len(self.flagged),
            "matched": [_pair(p) for p in self.matched],
            "unmatched_engine": [_leg(l) for l in self.unmatched_engine],
            "unmatched_broker": [_leg(l) for l in self.unmatched_broker],
            "flagged": [_pair(p) for p in self.flagged],
        }


def _canonical_side(raw: str) -> str:
    """Accepts BUY/B/SELL/S (Shoonya uses single-letter in some responses)."""
    r = str(raw or "").strip().upper()
    if r in ("BUY", "B"):
        return "BUY"
    if r in ("SELL", "S"):
        return "SELL"
    return r  # unknown — surface in the row so the operator sees it


def _parse_ts(raw: str) -> Optional[datetime]:
    s = str(raw or "").strip()
    if not s:
        return None
    # ISO-8601 with or without timezone; strip Z if present.
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        # Fallback for 'YYYY-MM-DD HH:MM:SS'
        try:
            return datetime.strptime(s[:19], "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return None


def _row_to_leg(
    row: Dict,
    ts_col: str,
    symbol_col: str,
    side_col: str,
    qty_col: str,
    price_col: str,
    cost_cols: Tuple[str, ...],
) -> Optional[Leg]:
    ts = _parse_ts(row.get(ts_col, ""))
    if ts is None:
        return None
    try:
        qty = int(float(row.get(qty_col, 0) or 0))
        price = float(row.get(price_col, 0) or 0)
    except (TypeError, ValueError):
        return None
    if qty <= 0 or price <= 0:
        return None
    costs = 0.0
    for c in cost_cols:
        try:
            costs += float(row.get(c, 0) or 0)
        except (TypeError, ValueError):
            pass
    return Leg(
        timestamp=ts,
        symbol=str(row.get(symbol_col, "")).strip(),
        side=_canonical_side(row.get(side_col, "")),
        quantity=qty,
        fill_price=price,
        costs=costs,
        source_row=dict(row),
    )


def load_engine_orders(path: str, date_iso: Optional[str] = None) -> List[Leg]:
    """Read ``paper_orders.csv`` and return legs. Filters to ``date_iso`` (YYYY-MM-DD)
    if provided. Skips REJECTED / CANCELED-with-no-fill rows."""
    if not os.path.exists(path):
        raise FileNotFoundError(f"engine orders file not found: {path}")
    legs: List[Leg] = []
    with open(path, newline="") as f:
        for row in csv.DictReader(f):
            # Only legs that actually filled contribute to broker reconciliation.
            status = str(row.get("status", "")).strip().upper()
            if status and status not in ("COMPLETE",):
                continue
            leg = _row_to_leg(
                row,
                ts_col="timestamp",
                symbol_col="symbol",
                side_col="side",
                qty_col="quantity",
                price_col="fill_price",
                cost_cols=("stt", "brokerage"),
            )
            if leg is None:
                continue
            if date_iso and leg.timestamp.date().isoformat() != date_iso:
                continue
            legs.append(leg)
    return legs


def load_broker_fills(path: str, date_iso: Optional[str] = None) -> List[Leg]:
    """Read a broker-contract-note CSV and return legs. Column names mirror
    ``paper_orders.csv`` for symmetry; if the live adapter produces different
    field names, normalise to this shape before calling."""
    if not os.path.exists(path):
        raise FileNotFoundError(f"broker trades file not found: {path}")
    legs: List[Leg] = []
    with open(path, newline="") as f:
        for row in csv.DictReader(f):
            leg = _row_to_leg(
                row,
                ts_col="timestamp",
                symbol_col="symbol",
                side_col="side",
                qty_col="quantity",
                price_col="fill_price",
                # Broker rows include the full cost stack (LIVE-12).
                cost_cols=("stt", "brokerage", "exch_txn", "sebi", "stamp", "gst"),
            )
            if leg is None:
                continue
            if date_iso and leg.timestamp.date().isoformat() != date_iso:
                continue
            legs.append(leg)
    return legs


def reconcile(
    engine_legs: List[Leg],
    broker_legs: List[Leg],
    *,
    window_sec: float = DEFAULT_MATCH_WINDOW_SEC,
    flag_pct: float = DEFAULT_FLAG_PRICE_PCT,
    date_iso: str = "",
) -> ReconciliationReport:
    """Join engine legs to broker legs on (symbol, side, time±window_sec).

    Matching strategy: for each engine leg, pick the closest-in-time broker leg
    with matching (symbol, side) that hasn't been claimed yet. Broker legs not
    claimed by any engine leg become ``unmatched_broker``. Engine legs with no
    candidate become ``unmatched_engine``.
    """
    report = ReconciliationReport(
        date=date_iso,
        engine_leg_count=len(engine_legs),
        broker_leg_count=len(broker_legs),
    )

    broker_by_key: Dict[Tuple[str, str], List[Leg]] = {}
    for b in broker_legs:
        broker_by_key.setdefault((b.symbol, b.side), []).append(b)

    consumed_broker_ids: set = set()

    for e in engine_legs:
        candidates = broker_by_key.get((e.symbol, e.side), [])
        best: Optional[Leg] = None
        best_dt = window_sec + 1
        for b in candidates:
            if id(b) in consumed_broker_ids:
                continue
            dt = abs((b.timestamp - e.timestamp).total_seconds())
            if dt <= window_sec and dt < best_dt:
                best = b
                best_dt = dt
        if best is None:
            report.unmatched_engine.append(e)
            continue
        consumed_broker_ids.add(id(best))
        pair = MatchedPair(engine=e, broker=best)
        report.matched.append(pair)
        if abs(pair.price_delta_pct) > flag_pct:
            report.flagged.append(pair)

    for b in broker_legs:
        if id(b) not in consumed_broker_ids:
            report.unmatched_broker.append(b)

    return report


def write_report(report: ReconciliationReport, path: str) -> None:
    """Atomic write so a concurrent reader never sees a truncated file."""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(report.to_dict(), f, indent=2, default=str)
    os.replace(tmp, path)


def load_reconciliation_reports(data_dir: str) -> List[Dict]:
    """Discover and load every ``reconciliation_YYYYMMDD.json`` in ``data_dir``.

    Consumed by the go-live evaluator (LIVE-21). Unreadable/corrupt files are
    skipped with a warning rather than aborting, because a single bad report
    shouldn't prevent the evaluator from running over the rest.
    """
    import glob

    pattern = os.path.join(data_dir, "reconciliation_*.json")
    reports: List[Dict] = []
    for path in sorted(glob.glob(pattern)):
        try:
            with open(path) as f:
                reports.append(json.load(f))
        except (OSError, ValueError) as e:
            logger.warning("skipping unreadable reconciliation report %s: %s", path, e)
    return reports
