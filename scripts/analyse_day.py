#!/usr/bin/env python3
"""
Analyse a trading day after market close.

Reads active_positions.json (closed trades with exit_time on the given date),
partial_fill_reviews.json (cleanups on that date), and prints a summary.
Usage:
  python scripts/analyse_day.py              # today
  python scripts/analyse_day.py --date 2026-03-02
"""
import argparse
import json
import os
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description="Analyse a trading day")
    parser.add_argument("--date", type=str, default=None, help="Date YYYY-MM-DD (default: today)")
    args = parser.parse_args()
    if args.date:
        try:
            date_str = datetime.strptime(args.date, "%Y-%m-%d").strftime("%Y-%m-%d")
        except ValueError:
            print("Invalid --date; use YYYY-MM-DD")
            return 1
    else:
        date_str = datetime.now().strftime("%Y-%m-%d")
    date_log = date_str.replace("-", "")  # for log filename YYYYMMDD

    os.chdir(ROOT)
    positions_file = ROOT / "active_positions.json"
    if not positions_file.exists():
        print(f"No {positions_file.name} found.")
        return 0

    with open(positions_file, "r") as f:
        all_positions = json.load(f)

    todays_trades = [
        p for p in all_positions
        if p.get("status") == "CLOSED"
        and (p.get("exit_time") or "").startswith(date_str)
    ]

    print("=" * 60)
    print("DAY ANALYSIS")
    print(f"Date: {date_str}")
    print("=" * 60)

    if not todays_trades:
        print("No closed trades on this date.")
    else:
        total_pnl = sum(t.get("final_pnl", 0) for t in todays_trades)
        winning = [t for t in todays_trades if t.get("final_pnl", 0) > 0]
        losing = [t for t in todays_trades if t.get("final_pnl", 0) < 0]
        breakeven = [t for t in todays_trades if t.get("final_pnl", 0) == 0]
        win_rate = (len(winning) / len(todays_trades)) * 100 if todays_trades else 0
        avg_winner = sum(t.get("final_pnl", 0) for t in winning) / len(winning) if winning else 0
        avg_loser = sum(t.get("final_pnl", 0) for t in losing) / len(losing) if losing else 0
        max_profit = max((t.get("final_pnl", 0) for t in todays_trades), default=0)
        max_loss = min((t.get("final_pnl", 0) for t in todays_trades), default=0)

        by_strategy = {}
        for t in todays_trades:
            s = t.get("strategy", "UNKNOWN")
            if s not in by_strategy:
                by_strategy[s] = {"count": 0, "pnl": 0}
            by_strategy[s]["count"] += 1
            by_strategy[s]["pnl"] += t.get("final_pnl", 0)

        by_exit_reason = {}
        for t in todays_trades:
            r = (t.get("exit_reason") or "UNKNOWN").replace("futures_exit_", "")
            if r not in by_exit_reason:
                by_exit_reason[r] = {"count": 0, "pnl": 0}
            by_exit_reason[r]["count"] += 1
            by_exit_reason[r]["pnl"] += t.get("final_pnl", 0)

        print("\nOverall:")
        print(f"  Total trades: {len(todays_trades)}  (W: {len(winning)}, L: {len(losing)}, BE: {len(breakeven)})")
        print(f"  Win rate:     {win_rate:.1f}%")
        print(f"  Total P&L:    ₹{total_pnl:,.2f}")
        print(f"  Avg winner:  ₹{avg_winner:,.2f}" if winning else "  Avg winner:  -")
        print(f"  Avg loser:   ₹{avg_loser:,.2f}" if losing else "  Avg loser:   -")
        print(f"  Max profit:  ₹{max_profit:,.2f}  |  Max loss: ₹{max_loss:,.2f}")

        print("\nBy strategy:")
        for s, st in by_strategy.items():
            print(f"  {s}: {st['count']} trades, ₹{st['pnl']:,.2f}")

        print("\nBy exit reason:")
        for r, st in by_exit_reason.items():
            print(f"  {r}: {st['count']} trades, ₹{st['pnl']:,.2f}")

        print("\nTrades:")
        for t in todays_trades:
            print(f"  {t.get('trade_id', '')[:20]}...  {t.get('strategy')}  {t.get('exit_reason')}  P&L=₹{t.get('final_pnl', 0):.2f}")

    # Partial fill reviews for this date
    review_file = ROOT / "partial_fill_reviews.json"
    if review_file.exists():
        with open(review_file, "r") as f:
            reviews = json.load(f)
        day_reviews = [r for r in reviews if (r.get("timestamp") or "").startswith(date_str)]
        if day_reviews:
            print("\n" + "-" * 60)
            print(f"Partial-fill cleanups on {date_str}: {len(day_reviews)}")
            for r in day_reviews:
                print(f"  {r.get('timestamp')}  {r.get('reason')}  {r.get('message', '')[:60]}")
        else:
            print("\nNo partial-fill cleanups on this date.")
    else:
        print("\nNo partial_fill_reviews.json found.")

    # Log file
    log_path = ROOT / "logs" / f"trading_system_{date_log}.log"
    print("\n" + "-" * 60)
    if log_path.exists():
        print(f"Log file: {log_path}")
    else:
        print(f"Log file (if run): logs/trading_system_{date_log}.log")

    print("=" * 60)
    return 0


if __name__ == "__main__":
    exit(main())
