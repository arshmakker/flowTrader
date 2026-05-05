#!/usr/bin/env python3
"""Display current PnL from snapshot."""

import json
from pathlib import Path

SNAPSHOT_PATH = Path(__file__).parent.parent / "data" / "pnl_snapshot.json"


def main():
    if not SNAPSHOT_PATH.exists():
        print(f"Error: Snapshot not found at {SNAPSHOT_PATH}")
        return

    with open(SNAPSHOT_PATH) as f:
        data = json.load(f)

    print(f"Snapshot: {data['timestamp']}")
    print(f"Realised PnL:  ₹{data['realised_pnl']:,.2f}")
    print(f"Unrealised PnL: ₹{data['unrealised_pnl']:,.2f}")
    print(f"Net Total:      ₹{data['total_pnl']:,.2f}")
    print(f"Trades: {data['total_trades']} | Win rate: {data['win_rate_pct']}%")
    print()
    print("By Instrument:")
    for inst, stats in data.get("strategy_stats", {}).items():
        print(f"  {inst}: {stats['trades']} trades, ₹{stats['total_pnl']:,.2f}, {stats['win_rate']}% win")


if __name__ == "__main__":
    main()
