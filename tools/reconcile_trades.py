"""
LIVE-08: per-leg reconciliation between engine orders and broker contract notes.

Usage:
    python tools/reconcile_trades.py \\
        --date 2026-04-22 \\
        --broker data/broker_trades_20260422.csv

Produces ``data/reconciliation_YYYYMMDD.json`` with per-leg matched pairs,
unmatched sets, and flagged deltas where the price drift exceeds 2%.

The broker CSV must use the same columns as ``paper_orders.csv`` plus the
live cost stack fields (``exch_txn``, ``sebi``, ``stamp``, ``gst``) which the
engine doesn't currently model (LIVE-12). In live mode the producer of that
CSV is a broker-fetcher shim around ``api.get_trade_book()``; in the proving
period it's a CSV exported from Shoonya's backoffice.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from trading_system.config import settings
from trading_system.ops import reconcile as rec


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Reconcile engine orders vs broker fills")
    parser.add_argument("--date", required=True, help="Session date as YYYY-MM-DD")
    parser.add_argument(
        "--engine",
        default=os.path.join(_REPO_ROOT, settings.DATA_DIR, "paper_orders.csv"),
        help="Path to engine paper_orders.csv",
    )
    parser.add_argument(
        "--broker", required=True,
        help="Path to broker contract-note CSV for the given date",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Output JSON path; defaults to data/reconciliation_<date>.json",
    )
    parser.add_argument(
        "--window-sec", type=float, default=rec.DEFAULT_MATCH_WINDOW_SEC,
        help=f"Match window in seconds (default {rec.DEFAULT_MATCH_WINDOW_SEC}).",
    )
    parser.add_argument(
        "--flag-pct", type=float, default=rec.DEFAULT_FLAG_PRICE_PCT,
        help=f"Price drift threshold to flag (default {rec.DEFAULT_FLAG_PRICE_PCT}).",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s [reconcile] %(message)s",
    )
    log = logging.getLogger("reconcile")

    if args.output is None:
        flat_date = args.date.replace("-", "")
        args.output = os.path.join(_REPO_ROOT, settings.DATA_DIR, f"reconciliation_{flat_date}.json")

    try:
        engine = rec.load_engine_orders(args.engine, date_iso=args.date)
        broker = rec.load_broker_fills(args.broker, date_iso=args.date)
    except FileNotFoundError as e:
        log.error(str(e))
        return 2

    report = rec.reconcile(
        engine, broker,
        window_sec=args.window_sec,
        flag_pct=args.flag_pct,
        date_iso=args.date,
    )
    rec.write_report(report, args.output)

    log.info(
        "reconcile date=%s engine=%d broker=%d matched=%d unmatched_engine=%d "
        "unmatched_broker=%d flagged=%d -> %s",
        args.date, report.engine_leg_count, report.broker_leg_count,
        len(report.matched), len(report.unmatched_engine),
        len(report.unmatched_broker), len(report.flagged), args.output,
    )

    # Non-zero exit when there's operator-actionable drift, so this can run
    # from cron and trigger an alert without the operator parsing the JSON.
    if report.flagged or report.unmatched_engine or report.unmatched_broker:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
