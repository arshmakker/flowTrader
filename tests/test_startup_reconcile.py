"""LIVE-07 — startup broker-vs-engine position reconciliation.

Pins the behaviour of ``reconcile_startup_positions``: the purely functional
authority that drives the "halt or resume" decision on cold-start in live
mode. Every scenario here maps to a real live-mode failure mode
(phantom positions, hidden positions, partial-fill qty drift, zombie
zero-qty rows, malformed broker rows).
"""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from trading_system.ops.startup_reconcile import (
    QtyMismatch,
    StartupReconciliationReport,
    reconcile_startup_positions,
)


# ── Helpers to build fake positions in each side's shape ───────────────

def _engine_leg(qty: int, avg_price: float = 50.0) -> dict:
    """Shape of a PaperPositionTracker._positions entry."""
    return {"qty": qty, "avg_price": avg_price, "side": "SELL" if qty < 0 else "BUY", "costs": 0.0}


def _broker_leg(tsym: str, netqty: int, exch: str = "NFO", netavgprc: str = "50.00") -> dict:
    """Shape of a Shoonya get_positions() row. Shoonya returns qty as a string."""
    return {
        "stat": "Ok",
        "exch": exch,
        "tsym": tsym,
        "netqty": str(netqty),
        "netavgprc": netavgprc,
        "prd": "I",
    }


def _ic_engine_positions() -> dict:
    """A representative 10-lot NIFTY IC in the engine: 2 shorts, 2 wings."""
    return {
        "NFO|NIFTY28APR26C24100": _engine_leg(-650),  # SC
        "NFO|NIFTY28APR26P24000": _engine_leg(-650),  # SP
        "NFO|NIFTY28APR26C24200": _engine_leg(650),   # LC wing
        "NFO|NIFTY28APR26P23900": _engine_leg(650),   # LP wing
    }


def _ic_broker_positions() -> list:
    """Same IC on the broker side."""
    return [
        _broker_leg("NIFTY28APR26C24100", -650),
        _broker_leg("NIFTY28APR26P24000", -650),
        _broker_leg("NIFTY28APR26C24200", 650),
        _broker_leg("NIFTY28APR26P23900", 650),
    ]


# ── Happy paths ────────────────────────────────────────────────────────

def test_both_flat_is_consistent():
    """Clean cold boot, no position either side."""
    report = reconcile_startup_positions({}, [])
    assert report.consistent is True
    assert report.engine_only == []
    assert report.broker_only == []
    assert report.qty_mismatches == []


def test_none_inputs_treated_as_empty():
    """None-safety: main.py may pass None if get_positions returns None."""
    report = reconcile_startup_positions(None, None)
    assert report.consistent is True


def test_matching_ic_is_consistent():
    """Engine restored an IC, broker reports the same 4 legs at the same
    signed qty — the common resume case."""
    report = reconcile_startup_positions(_ic_engine_positions(), _ic_broker_positions())
    assert report.consistent is True
    assert len(report.engine_symbols) == 4
    assert len(report.broker_symbols) == 4


# ── Divergence: engine has phantom positions ────────────────────────────

def test_engine_has_position_broker_does_not_is_divergent():
    """Phantom: JSON says we have 4 legs, broker says 0. Broker won overnight
    (force-squared at 15:30, engine didn't see the update). Resuming would
    monitor phantom legs against real LTPs — bad outcome."""
    report = reconcile_startup_positions(_ic_engine_positions(), [])
    assert report.consistent is False
    assert set(report.engine_only) == set(_ic_engine_positions().keys())
    assert report.broker_only == []
    assert report.qty_mismatches == []


def test_engine_has_one_extra_leg():
    """Crash between leg-3 rollback-close and leg-4 rollback-close: engine
    JSON might show 1 extra leg the broker already squared."""
    broker_3_legs = _ic_broker_positions()[:3]  # missing the LP wing
    report = reconcile_startup_positions(_ic_engine_positions(), broker_3_legs)
    assert report.consistent is False
    assert report.engine_only == ["NFO|NIFTY28APR26P23900"]
    assert report.broker_only == []


# ── Divergence: broker has positions engine doesn't ────────────────────

def test_broker_has_hidden_position_is_divergent():
    """Hidden: broker holds legs the engine doesn't know about — a manual
    trade, a prior session's rollback half that didn't close, or a crash
    mid-leg-2. Resuming would leave this exposure unmanaged."""
    report = reconcile_startup_positions({}, _ic_broker_positions())
    assert report.consistent is False
    assert report.engine_only == []
    assert set(report.broker_only) == {leg["symbol"] for leg in []} | set(
        f"NFO|{leg['tsym']}" for leg in _ic_broker_positions()
    )


def test_broker_has_partial_leg_engine_thinks_full():
    """Partial fill on entry: broker filled 300 of 650 on one leg before
    cancellation. Engine — which today assumes synchronous full fill —
    recorded the full 650. Qty mismatch surfaces it."""
    engine = _ic_engine_positions()
    broker = _ic_broker_positions()
    broker[0]["netqty"] = "-300"  # SC filled only 300/650

    report = reconcile_startup_positions(engine, broker)
    assert report.consistent is False
    assert report.qty_mismatches == [
        QtyMismatch(symbol="NFO|NIFTY28APR26C24100", engine_qty=-650, broker_qty=-300),
    ]
    assert report.engine_only == []
    assert report.broker_only == []


def test_multiple_discrepancies_all_reported():
    """Report aggregates every discrepancy — the operator needs to see
    the full picture, not just the first failure."""
    engine = _ic_engine_positions()
    broker = [
        _broker_leg("NIFTY28APR26C24100", -650),   # match
        _broker_leg("NIFTY28APR26P24000", -300),   # qty mismatch
        # LC wing (24200) missing — engine_only
        _broker_leg("NIFTY28APR26C25000", 650),    # broker_only (mystery)
        _broker_leg("NIFTY28APR26P23900", 650),    # match
    ]
    report = reconcile_startup_positions(engine, broker)
    assert report.consistent is False
    assert report.engine_only == ["NFO|NIFTY28APR26C24200"]
    assert report.broker_only == ["NFO|NIFTY28APR26C25000"]
    assert len(report.qty_mismatches) == 1
    assert report.qty_mismatches[0].symbol == "NFO|NIFTY28APR26P24000"


# ── Zero-qty / malformed rows ──────────────────────────────────────────

def test_zero_netqty_broker_rows_are_filtered():
    """Shoonya leaves flat-within-day positions in the response with
    netqty=0. They're audit rows, not exposure. Engine flat + broker rows
    all zero must reconcile as consistent."""
    broker = [
        _broker_leg("NIFTY28APR26C24100", 0),   # day-flat
        _broker_leg("NIFTY28APR26P24000", 0),   # day-flat
    ]
    report = reconcile_startup_positions({}, broker)
    assert report.consistent is True
    assert report.broker_symbols == []


def test_zero_qty_engine_entries_are_filtered():
    """Defence against a tracker zombie: positions dict entries with qty=0
    aren't live. Don't flag them as divergent when broker has no row."""
    engine = {"NFO|NIFTY28APR26C24100": _engine_leg(0)}
    report = reconcile_startup_positions(engine, [])
    assert report.consistent is True


def test_malformed_broker_rows_skipped_not_crashed():
    """A Shoonya row missing tsym or exch, or carrying a non-numeric
    netqty, must be skipped defensively. The caller sees it as a
    consistent empty broker side if that was the only row — not crash."""
    malformed = [
        {"stat": "Ok"},                                     # no tsym/exch
        {"exch": "NFO", "tsym": "NIFTY28APR26C24100"},     # no netqty
        {"exch": "NFO", "tsym": "NIFTY28APR26C24100", "netqty": "not-a-number"},
        "a-string-not-a-dict",
    ]
    report = reconcile_startup_positions({}, malformed)
    assert report.consistent is True
    assert report.broker_symbols == []


# ── Symbol normalization — engine uses EXCH|TSYM, broker splits them ───

def test_symbol_normalization_matches_engine_format():
    """Broker returns tsym + exch as separate fields; engine stores them
    joined with a pipe. Reconciler must normalise the broker side to
    match — otherwise every position looks 'broker_only'."""
    engine = {"NFO|NIFTY28APR26C24100": _engine_leg(-650)}
    broker = [_broker_leg("NIFTY28APR26C24100", -650)]
    report = reconcile_startup_positions(engine, broker)
    assert report.consistent is True


def test_summary_line_is_structured_on_ok():
    report = reconcile_startup_positions(_ic_engine_positions(), _ic_broker_positions())
    assert "startup_reconcile=OK" in report.summary()
    assert "engine_positions=4" in report.summary()
    assert "broker_positions=4" in report.summary()


def test_summary_line_is_structured_on_divergence():
    """The divergent summary feeds both the error log AND the ntfy alert
    body, so all three discrepancy kinds must appear and be machine-greppable."""
    engine = _ic_engine_positions()
    broker = [
        _broker_leg("NIFTY28APR26C24100", -300),   # qty drift
        _broker_leg("NIFTY28APR26C25000", 650),    # broker_only
    ]
    summary = reconcile_startup_positions(engine, broker).summary()
    assert "startup_reconcile=DIVERGENT" in summary
    assert "engine_only=" in summary
    assert "broker_only=" in summary
    assert "qty_mismatches=" in summary
