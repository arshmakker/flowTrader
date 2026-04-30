"""LIVE-08: per-leg engine↔broker reconciliation tests.

Fixtures seed known deltas so the assertions stay mechanical. Real
Shoonya contract-note shape is mirrored by the broker CSV columns.
"""
import csv
import json
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from trading_system.ops import reconcile as rec


def _write_engine_csv(path, rows):
    cols = ["timestamp", "order_id", "symbol", "side", "quantity",
            "fill_price", "stt", "brokerage", "status", "reason", "paper"]
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for r in rows:
            w.writerow({c: r.get(c, "") for c in cols})


def _write_broker_csv(path, rows):
    cols = ["timestamp", "order_id", "symbol", "side", "quantity",
            "fill_price", "stt", "brokerage", "exch_txn", "sebi", "stamp", "gst"]
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for r in rows:
            w.writerow({c: r.get(c, "") for c in cols})


def _engine_row(ts, sym, side, qty, px, status="COMPLETE"):
    return {
        "timestamp": ts, "order_id": "PAPER_1", "symbol": sym, "side": side,
        "quantity": qty, "fill_price": px, "stt": 0.0, "brokerage": 5.0,
        "status": status, "reason": "", "paper": "True",
    }


def _broker_row(ts, sym, side, qty, px, costs=None):
    costs = costs or {}
    return {
        "timestamp": ts, "order_id": "B1", "symbol": sym, "side": side,
        "quantity": qty, "fill_price": px,
        "stt": costs.get("stt", 0.0),
        "brokerage": costs.get("brokerage", 5.0),
        "exch_txn": costs.get("exch_txn", 1.5),
        "sebi": costs.get("sebi", 0.1),
        "stamp": costs.get("stamp", 0.3),
        "gst": costs.get("gst", 1.0),
    }


def test_matched_pair_within_window(tmp_path):
    """Engine leg @ 10:00:00 and broker leg @ 10:00:03 on the same symbol+side
    are a match — price delta surfaces cleanly."""
    ep = str(tmp_path / "paper_orders.csv")
    bp = str(tmp_path / "broker.csv")
    _write_engine_csv(ep, [_engine_row("2026-04-22T10:00:00", "NFO|NIFTY24APR26C22150", "SELL", 650, 18.0)])
    _write_broker_csv(bp, [_broker_row("2026-04-22T10:00:03", "NFO|NIFTY24APR26C22150", "SELL", 650, 17.8)])

    eng = rec.load_engine_orders(ep, "2026-04-22")
    brk = rec.load_broker_fills(bp, "2026-04-22")
    report = rec.reconcile(eng, brk, date_iso="2026-04-22")

    assert len(report.matched) == 1
    pair = report.matched[0]
    assert round(pair.price_delta, 4) == -0.2
    # 0.2/18 = 1.11% → below 2% default flag.
    assert round(pair.price_delta_pct, 4) == round(-0.2 / 18.0, 4)
    assert len(report.flagged) == 0


def test_price_drift_above_threshold_flags(tmp_path):
    """3% price drift exceeds the default 2% flag."""
    ep = str(tmp_path / "paper_orders.csv")
    bp = str(tmp_path / "broker.csv")
    _write_engine_csv(ep, [_engine_row("2026-04-22T10:00:00", "NFO|NIFTY24APR26C22150", "SELL", 650, 18.0)])
    _write_broker_csv(bp, [_broker_row("2026-04-22T10:00:10", "NFO|NIFTY24APR26C22150", "SELL", 650, 18.6)])

    report = rec.reconcile(
        rec.load_engine_orders(ep, "2026-04-22"),
        rec.load_broker_fills(bp, "2026-04-22"),
        date_iso="2026-04-22",
    )
    assert len(report.flagged) == 1
    assert len(report.matched) == 1  # flagged rows still count as matched


def test_unmatched_engine_and_broker(tmp_path):
    """Engine has a leg the broker never reports (phantom engine fill) and
    broker has a leg the engine doesn't know about (rogue broker fill or
    mid-entry crash between fill and persist). Both surface separately."""
    ep = str(tmp_path / "paper_orders.csv")
    bp = str(tmp_path / "broker.csv")
    _write_engine_csv(ep, [
        _engine_row("2026-04-22T10:00:00", "NFO|NIFTY24APR26C22150", "SELL", 650, 18.0),
        _engine_row("2026-04-22T10:00:05", "NFO|NIFTY24APR26P21850", "SELL", 650, 18.0),
    ])
    _write_broker_csv(bp, [
        # Only the first leg reported by broker. Second engine leg is phantom.
        _broker_row("2026-04-22T10:00:02", "NFO|NIFTY24APR26C22150", "SELL", 650, 17.9),
        # Mystery broker fill the engine never placed.
        _broker_row("2026-04-22T10:00:07", "NFO|NIFTY24APR26P21800", "BUY", 650, 5.0),
    ])

    report = rec.reconcile(
        rec.load_engine_orders(ep, "2026-04-22"),
        rec.load_broker_fills(bp, "2026-04-22"),
        date_iso="2026-04-22",
    )
    assert len(report.matched) == 1
    assert len(report.unmatched_engine) == 1
    assert report.unmatched_engine[0].symbol == "NFO|NIFTY24APR26P21850"
    assert len(report.unmatched_broker) == 1
    assert report.unmatched_broker[0].symbol == "NFO|NIFTY24APR26P21800"


def test_window_boundary_beyond_default_is_unmatched(tmp_path):
    """Broker leg 90s after engine leg is outside the default 60s window."""
    ep = str(tmp_path / "paper_orders.csv")
    bp = str(tmp_path / "broker.csv")
    _write_engine_csv(ep, [_engine_row("2026-04-22T10:00:00", "NFO|X", "SELL", 100, 10.0)])
    _write_broker_csv(bp, [_broker_row("2026-04-22T10:01:30", "NFO|X", "SELL", 100, 10.0)])

    report = rec.reconcile(
        rec.load_engine_orders(ep, "2026-04-22"),
        rec.load_broker_fills(bp, "2026-04-22"),
        date_iso="2026-04-22",
    )
    assert len(report.matched) == 0
    assert len(report.unmatched_engine) == 1
    assert len(report.unmatched_broker) == 1


def test_widened_window_catches_late_fill(tmp_path):
    """Same rows, but caller widens the window — match should succeed."""
    ep = str(tmp_path / "paper_orders.csv")
    bp = str(tmp_path / "broker.csv")
    _write_engine_csv(ep, [_engine_row("2026-04-22T10:00:00", "NFO|X", "SELL", 100, 10.0)])
    _write_broker_csv(bp, [_broker_row("2026-04-22T10:01:30", "NFO|X", "SELL", 100, 10.0)])

    report = rec.reconcile(
        rec.load_engine_orders(ep, "2026-04-22"),
        rec.load_broker_fills(bp, "2026-04-22"),
        window_sec=120.0,
        date_iso="2026-04-22",
    )
    assert len(report.matched) == 1


def test_date_filter_excludes_wrong_day(tmp_path):
    """Prior-day rows in the CSV must be silently filtered out."""
    ep = str(tmp_path / "paper_orders.csv")
    bp = str(tmp_path / "broker.csv")
    _write_engine_csv(ep, [
        _engine_row("2026-04-21T10:00:00", "NFO|X", "SELL", 100, 10.0),
        _engine_row("2026-04-22T10:00:00", "NFO|Y", "SELL", 100, 10.0),
    ])
    _write_broker_csv(bp, [_broker_row("2026-04-22T10:00:02", "NFO|Y", "SELL", 100, 10.0)])

    report = rec.reconcile(
        rec.load_engine_orders(ep, "2026-04-22"),
        rec.load_broker_fills(bp, "2026-04-22"),
        date_iso="2026-04-22",
    )
    assert report.engine_leg_count == 1
    assert len(report.matched) == 1


def test_rejected_engine_orders_are_ignored(tmp_path):
    """REJECTED / CANCELED rows never hit the broker, so they must not become
    unmatched_engine noise — they're filtered at load time."""
    ep = str(tmp_path / "paper_orders.csv")
    bp = str(tmp_path / "broker.csv")
    _write_engine_csv(ep, [
        _engine_row("2026-04-22T10:00:00", "NFO|X", "SELL", 100, 10.0, status="REJECTED"),
        _engine_row("2026-04-22T10:00:01", "NFO|Y", "SELL", 100, 10.0, status="COMPLETE"),
    ])
    _write_broker_csv(bp, [_broker_row("2026-04-22T10:00:02", "NFO|Y", "SELL", 100, 10.0)])

    report = rec.reconcile(
        rec.load_engine_orders(ep, "2026-04-22"),
        rec.load_broker_fills(bp, "2026-04-22"),
        date_iso="2026-04-22",
    )
    assert report.engine_leg_count == 1
    assert len(report.matched) == 1
    assert len(report.unmatched_engine) == 0


def test_cost_delta_surfaces_missing_cost_stack(tmp_path):
    """Engine costs = STT + brokerage only. Broker costs include the full
    cost stack (LIVE-12). cost_delta must make this visible so the operator
    can attribute P&L drift to missing fees rather than slippage."""
    ep = str(tmp_path / "paper_orders.csv")
    bp = str(tmp_path / "broker.csv")
    _write_engine_csv(ep, [_engine_row("2026-04-22T10:00:00", "NFO|X", "SELL", 100, 10.0)])
    _write_broker_csv(bp, [_broker_row(
        "2026-04-22T10:00:02", "NFO|X", "SELL", 100, 10.0,
        costs={"stt": 0.0, "brokerage": 5.0, "exch_txn": 1.5, "sebi": 0.1, "stamp": 0.3, "gst": 1.0},
    )])

    report = rec.reconcile(
        rec.load_engine_orders(ep, "2026-04-22"),
        rec.load_broker_fills(bp, "2026-04-22"),
        date_iso="2026-04-22",
    )
    assert len(report.matched) == 1
    # Engine captures 0+5=5; broker captures 0+5+1.5+0.1+0.3+1.0=7.9; delta 2.9.
    assert round(report.matched[0].cost_delta, 2) == 2.9


def test_write_report_produces_readable_json(tmp_path):
    """Report JSON must deserialize cleanly and include the summary counts."""
    report = rec.ReconciliationReport(
        date="2026-04-22",
        engine_leg_count=0,
        broker_leg_count=0,
    )
    out = tmp_path / "rec.json"
    rec.write_report(report, str(out))
    data = json.loads(out.read_text())
    assert data["date"] == "2026-04-22"
    assert data["matched_count"] == 0
    assert data["unmatched_engine_count"] == 0


def test_closest_broker_leg_wins_on_multiple_candidates(tmp_path):
    """If two broker legs both match (symbol, side) within the window, the
    closest-in-time one must win — otherwise the pairing is non-deterministic."""
    ep = str(tmp_path / "paper_orders.csv")
    bp = str(tmp_path / "broker.csv")
    _write_engine_csv(ep, [_engine_row("2026-04-22T10:00:00", "NFO|X", "SELL", 100, 10.0)])
    _write_broker_csv(bp, [
        _broker_row("2026-04-22T10:00:30", "NFO|X", "SELL", 100, 10.5),  # further
        _broker_row("2026-04-22T10:00:02", "NFO|X", "SELL", 100, 10.1),  # closer
    ])

    report = rec.reconcile(
        rec.load_engine_orders(ep, "2026-04-22"),
        rec.load_broker_fills(bp, "2026-04-22"),
        date_iso="2026-04-22",
    )
    assert len(report.matched) == 1
    # Closer broker row (10.1, 2s away) wins over the farther one (10.5, 30s away).
    assert report.matched[0].broker.fill_price == 10.1
    # The other one is unmatched broker-side.
    assert len(report.unmatched_broker) == 1
    assert report.unmatched_broker[0].fill_price == 10.5


# ── LIVE-21 loader helper — feeds the go-live evaluator ────────────────

def test_load_reconciliation_reports_discovers_and_sorts_by_date(tmp_path):
    # Two valid reports and one sibling file that should be ignored.
    (tmp_path / "reconciliation_20260422.json").write_text(
        json.dumps({"date": "2026-04-22", "matched_count": 4})
    )
    (tmp_path / "reconciliation_20260421.json").write_text(
        json.dumps({"date": "2026-04-21", "matched_count": 3})
    )
    (tmp_path / "paper_trades.csv").write_text("not-a-report")

    reports = rec.load_reconciliation_reports(str(tmp_path))

    assert len(reports) == 2
    # Glob order == lexical == chronological for YYYYMMDD-named files.
    assert [r["date"] for r in reports] == ["2026-04-21", "2026-04-22"]


def test_load_reconciliation_reports_skips_corrupt_files_without_raising(tmp_path):
    (tmp_path / "reconciliation_20260422.json").write_text('{"date": "2026-04-22"}')
    (tmp_path / "reconciliation_20260423.json").write_text("{not valid json")

    reports = rec.load_reconciliation_reports(str(tmp_path))

    # Corrupt file is skipped, not fatal — evaluator can still run on the rest.
    assert len(reports) == 1
    assert reports[0]["date"] == "2026-04-22"


def test_load_reconciliation_reports_empty_on_missing_dir(tmp_path):
    missing = tmp_path / "nope"
    assert rec.load_reconciliation_reports(str(missing)) == []
