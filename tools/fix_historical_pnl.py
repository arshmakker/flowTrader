#!/usr/bin/env python3
"""Reset PnL to start fresh - historical data was inflated (no costs)."""

import json
from datetime import datetime
from pathlib import Path

DATA_DIR = Path(__file__).parent.parent / "data"


def main():
    print("Resetting PnL - historical data was without transaction costs")
    print("Starting fresh from today with proper cost tracking")

    snapshot = {
        "timestamp": datetime.now().isoformat(),
        "realised_pnl": 0.0,
        "unrealised_pnl": 0.0,
        "total_pnl": 0.0,
        "total_trades": 0,
        "winning_trades": 0,
        "win_rate_pct": 0.0,
        "strategy_stats": {
            "NIFTY": {"trades": 0, "total_pnl": 0.0, "win_rate": 0.0},
            "BANKNIFTY": {"trades": 0, "total_pnl": 0.0, "win_rate": 0.0},
        },
        "max_drawdown": 0.0,
        "max_drawdown_pct": 0.0,
        "profit_factor": "inf",
        "avg_win": 0.0,
        "avg_loss": 0.0,
        "unmarked_positions": [],
        "daily": {
            "realised_pnl": 0.0,
            "trades": 0,
            "wins": 0,
            "win_rate_pct": 0.0,
            "max_drawdown": 0.0,
            "strategy_stats": {
                "NIFTY": {"trades": 0, "total_pnl": 0.0, "win_rate": 0.0},
                "BANKNIFTY": {"trades": 0, "total_pnl": 0.0, "win_rate": 0.0},
            },
        },
    }

    with open(DATA_DIR / "pnl_snapshot.json", "w") as f:
        json.dump(snapshot, f, indent=2)

    print("Reset pnl_snapshot.json")
    print("\nNote: paper_trades.csv保留了历史数据 (含 inflation).")
    print("Going forward, PnL将正确扣除成本.")


if __name__ == "__main__":
    main()
