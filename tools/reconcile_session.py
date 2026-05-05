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

logger = logging.getLogger("reconcile_session")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Reconcile engine vs broker trades for a session")
    parser.add_argument("--date", required=True, help="Session date YYYY-MM-DD")
    parser.add_argument(
        "--broker-csv",
        required=True,
        help="Path to broker contract-note CSV for the given date",
    )
    parser.add_argument(
        "--engine",
        default=os.path.join(_REPO_ROOT, settings.DATA_DIR, "paper_trades.csv"),
        help="Path to engine paper_trades.csv",
    )
    parser.add_argument(
        "--output-dir",
        default=None,
        help="Output directory; defaults to settings.RECONCILIATION_DIR",
    )
    parser.add_argument(
        "--window-sec",
        type=float,
        default=rec.DEFAULT_MATCH_WINDOW_SEC,
        help=f"Match window in seconds (default {rec.DEFAULT_MATCH_WINDOW_SEC})",
    )
    parser.add_argument(
        "--flag-pct",
        type=float,
        default=rec.DEFAULT_FLAG_PRICE_PCT,
        help=f"Price drift threshold to flag (default {rec.DEFAULT_FLAG_PRICE_PCT})",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s [reconcile_session] %(message)s",
    )

    output_dir = args.output_dir or settings.RECONCILIATION_DIR
    os.makedirs(output_dir, exist_ok=True)
    flat_date = args.date.replace("-", "")
    output_path = os.path.join(output_dir, f"reconciliation_{flat_date}.json")

    if not os.path.exists(args.engine):
        logger.error("Engine trades file not found: %s", args.engine)
        return 2
    if not os.path.exists(args.broker_csv):
        logger.error("Broker CSV not found: %s", args.broker_csv)
        return 2

    engine_legs = rec.load_engine_orders(args.engine, date_iso=args.date)
    logger.info("Loaded %d engine legs for %s", len(engine_legs), args.date)

    broker_legs = rec.load_broker_fills(args.broker_csv, date_iso=args.date)
    logger.info("Loaded %d broker legs for %s", len(broker_legs), args.date)

    report = rec.reconcile(
        engine_legs,
        broker_legs,
        window_sec=args.window_sec,
        flag_pct=args.flag_pct,
        date_iso=args.date,
    )
    rec.write_report(report, output_path)

    logger.info(
        "Reconciliation date=%s engine=%d broker=%d matched=%d unmatched_engine=%d unmatched_broker=%d flagged=%d -> %s",
        args.date,
        report.engine_leg_count,
        report.broker_leg_count,
        len(report.matched),
        len(report.unmatched_engine),
        len(report.unmatched_broker),
        len(report.flagged),
        output_path,
    )

    if report.flagged or report.unmatched_engine or report.unmatched_broker:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
