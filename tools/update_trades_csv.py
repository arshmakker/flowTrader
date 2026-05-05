#!/usr/bin/env python3
"""Update paper_trades.csv with transaction costs."""

import csv
from pathlib import Path

DATA_DIR = Path(__file__).parent.parent / "data"


def main():
    print("Updating paper_trades.csv with transaction costs...")

    with open(DATA_DIR / "paper_trades.csv") as f:
        reader = csv.DictReader(f)
        trades = list(reader)

    updated = 0
    for trade in trades:
        if trade["date"] != "2026-05-04":
            continue

        instrument = trade["instrument"]
        _lots = int(trade["lots"])

        original_pnl = float(trade["net_pnl"])
        cost_per_trade = 280 if instrument == "NIFTY" else 400
        real_pnl = original_pnl - cost_per_trade

        trade["net_pnl"] = str(round(real_pnl, 2))
        trade["gross_pnl"] = str(round(real_pnl, 2))
        updated += 1

    fieldnames = [
        "trade_id",
        "date",
        "entry_date",
        "time_entry",
        "time_exit",
        "instrument",
        "sc_strike",
        "sp_strike",
        "lc_strike",
        "lp_strike",
        "entry_credit",
        "exit_price",
        "gross_pnl",
        "net_pnl",
        "exit_reason",
        "lots",
        "peak_pnl",
        "vix_entry",
        "day_type",
        "paper",
    ]

    with open(DATA_DIR / "paper_trades.csv", "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(trades)

    print(f"Updated {updated} trades with costs")
    print("Done! You can now run the system.")


if __name__ == "__main__":
    main()
