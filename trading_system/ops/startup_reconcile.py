"""LIVE-07: startup broker-vs-engine position reconciliation.

On a cold-start in live mode, the broker — not ``data/open_positions.json``
— is the authoritative source of open exposure. A crash between leg-2
fill and leg-3 send leaves the JSON stale: 0 legs on disk, 2 legs at the
broker. Continuing to trade against the stale JSON violates Axiom 3
("uncertain state halts new entries until trustworthy").

This module is pure: callers pass in the engine's restored positions +
the broker's ``get_positions`` response and get back a structured
report. The ``consistent`` flag drives the halt/resume decision; the
per-symbol diffs drive the alert body and operator log.

Paper mode has no broker counterpart — main.py gates invocation on
``not settings.PAPER_TRADE_MODE``, so this module never runs in paper.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional


def _to_engine_symbol(tsym: Optional[str], exch: Optional[str]) -> Optional[str]:
    """Shoonya returns ``tsym`` without an exchange prefix; the engine keys
    positions as ``EXCH|TSYM``. Skip rows missing either field rather than
    inventing keys — a malformed broker row should surface as a mismatch,
    not quietly reconcile."""
    if not tsym or not exch:
        return None
    return f"{exch}|{tsym}"


def _to_int_qty(value: Any) -> int:
    """Shoonya returns qty as a string. Engine stores it as an int with
    sign (positive = long, negative = short). Coerce defensively."""
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def _engine_qty(pos: Dict[str, Any]) -> int:
    return _to_int_qty(pos.get("qty", 0))


@dataclass
class QtyMismatch:
    symbol: str
    engine_qty: int
    broker_qty: int


@dataclass
class StartupReconciliationReport:
    """Outcome of comparing engine state vs broker state at startup.

    ``consistent=True`` iff every symbol present in either side is also
    present in the other AND the signed quantities match. Any mismatch
    halts startup — there is no "partial trust" middle ground.
    """

    consistent: bool
    engine_symbols: List[str] = field(default_factory=list)
    broker_symbols: List[str] = field(default_factory=list)
    engine_only: List[str] = field(default_factory=list)
    broker_only: List[str] = field(default_factory=list)
    qty_mismatches: List[QtyMismatch] = field(default_factory=list)

    def summary(self) -> str:
        """One-line structured summary safe to log and include in an alert."""
        if self.consistent:
            return (
                f"startup_reconcile=OK "
                f"engine_positions={len(self.engine_symbols)} "
                f"broker_positions={len(self.broker_symbols)}"
            )
        parts = [
            "startup_reconcile=DIVERGENT",
            f"engine_only={self.engine_only}",
            f"broker_only={self.broker_only}",
            f"qty_mismatches={[(m.symbol, m.engine_qty, m.broker_qty) for m in self.qty_mismatches]}",
        ]
        return " ".join(parts)


def reconcile_startup_positions(
    engine_positions: Optional[Dict[str, Dict[str, Any]]],
    broker_positions: Optional[Iterable[Dict[str, Any]]],
) -> StartupReconciliationReport:
    """Compare engine tracker positions to Shoonya ``get_positions()`` output.

    ``engine_positions`` shape matches ``PaperPositionTracker._positions`` —
    ``{symbol: {qty, avg_price, ...}}`` where ``qty`` is signed (positive
    long, negative short).

    ``broker_positions`` shape is Shoonya's list-of-dicts with at minimum
    ``tsym``, ``exch``, and ``netqty`` keys. Zero-qty rows (fully flat
    within the day) are filtered out — Shoonya leaves them in the response
    for audit, but they are not live exposure.
    """
    engine_positions = engine_positions or {}
    broker_positions = broker_positions or []

    # Engine side: only non-zero qty counts as an open position. The tracker
    # deletes flattened rows, but we defend against zombies.
    engine_map: Dict[str, int] = {}
    for sym, pos in engine_positions.items():
        qty = _engine_qty(pos)
        if qty != 0:
            engine_map[sym] = qty

    broker_map: Dict[str, int] = {}
    for row in broker_positions:
        if not isinstance(row, dict):
            continue
        sym = _to_engine_symbol(row.get("tsym"), row.get("exch"))
        if sym is None:
            continue
        qty = _to_int_qty(row.get("netqty"))
        if qty == 0:
            continue
        broker_map[sym] = qty

    engine_symbols = sorted(engine_map)
    broker_symbols = sorted(broker_map)
    engine_only = sorted(set(engine_map) - set(broker_map))
    broker_only = sorted(set(broker_map) - set(engine_map))
    mismatches: List[QtyMismatch] = []
    for sym in sorted(set(engine_map) & set(broker_map)):
        if engine_map[sym] != broker_map[sym]:
            mismatches.append(
                QtyMismatch(
                    symbol=sym,
                    engine_qty=engine_map[sym],
                    broker_qty=broker_map[sym],
                ),
            )

    consistent = not engine_only and not broker_only and not mismatches

    return StartupReconciliationReport(
        consistent=consistent,
        engine_symbols=engine_symbols,
        broker_symbols=broker_symbols,
        engine_only=engine_only,
        broker_only=broker_only,
        qty_mismatches=mismatches,
    )
