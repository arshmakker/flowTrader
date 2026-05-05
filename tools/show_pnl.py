#!/usr/bin/env python3
"""Display current PnL from snapshot."""

import json
from datetime import datetime
from pathlib import Path

SNAPSHOT_PATH = Path(__file__).parent.parent / "data" / "pnl_snapshot.json"


def fmt_inr(val) -> str:
    if val is None:
        return "—"
    return f"₹{val:,.2f}"


def _row(col1: str, col2: str, col3: str, widths: tuple) -> str:
    w1, w2, w3 = widths
    return f"  │ {col1:<{w1}} │ {col2:<{w2}} │ {col3:<{w3}} │"


def _sep(widths: tuple, left="├", mid="┼", right="┤") -> str:
    w1, w2, w3 = widths
    return f"  {left}{'─' * (w1 + 2)}{mid}{'─' * (w2 + 2)}{mid}{'─' * (w3 + 2)}{right}"


def _top(widths: tuple) -> str:
    return _sep(widths, "┌", "┬", "┐")


def _bot(widths: tuple) -> str:
    return _sep(widths, "└", "┴", "┘")


def print_summary(data: dict) -> None:
    ts = datetime.fromisoformat(data["timestamp"])
    ist_time = ts.strftime("%H:%M IST")

    daily = data.get("daily", {})
    d_realised = fmt_inr(daily.get("realised_pnl"))
    d_unrealised = "—"
    d_net = ""
    d_trades = str(daily.get("trades", "—"))
    d_winrate = f"{daily.get('win_rate_pct', 0):.0f}%" if daily.get("trades") else "—"

    a_realised = fmt_inr(data["realised_pnl"])
    a_unrealised = fmt_inr(data["unrealised_pnl"])
    a_net = fmt_inr(data["total_pnl"])
    a_trades = str(data["total_trades"])
    a_winrate = f"{data['win_rate_pct']:.0f}%"

    rows = [
        ("Realised PnL", d_realised, a_realised),
        ("Unrealised PnL", d_unrealised, a_unrealised),
        ("Net total", d_net, a_net),
        ("Trades", d_trades, a_trades),
        ("Win rate", d_winrate, a_winrate),
    ]

    w1 = max(len(r[0]) for r in rows)
    w2 = max(len(r[1]) for r in rows + [("", "Daily", "")])
    w3 = max(len(r[2]) for r in rows + [("", "", "All-time")])
    widths = (w1, w2, w3)

    print(f"\n  Today's P&L (snapshot @ {ist_time})\n")
    print(_top(widths))
    print(_row("", "Daily", "All-time", widths))
    for row in rows:
        print(_sep(widths))
        print(_row(*row, widths))
    print(_bot(widths))

    # Per-instrument table
    daily_stats = daily.get("strategy_stats", {})
    all_stats = data.get("strategy_stats", {})
    instruments = list(all_stats.keys())

    if instruments:
        print("\n  Today by instrument:\n")
        i_rows = [
            (
                inst,
                str(daily_stats.get(inst, {}).get("trades", 0)),
                fmt_inr(daily_stats.get(inst, {}).get("total_pnl", 0.0)),
            )
            for inst in instruments
        ]
        iw1 = max(len(r[0]) for r in i_rows + [("Instrument", "", "")])
        iw2 = max(len(r[1]) for r in i_rows + [("", "Trades", "")])
        iw3 = max(len(r[2]) for r in i_rows + [("", "", "PnL")])
        iw = (iw1, iw2, iw3)
        print(_top(iw))
        print(_row("Instrument", "Trades", "PnL", iw))
        for row in i_rows:
            print(_sep(iw))
            print(_row(*row, iw))
        print(_bot(iw))

    dd = data.get("max_drawdown", 0)
    dd_pct = data.get("max_drawdown_pct", 0)
    print(f"\n  Max drawdown all-time: ₹{dd:,.2f} ({dd_pct:.2f}%)\n")


def main():
    if not SNAPSHOT_PATH.exists():
        print(f"Error: Snapshot not found at {SNAPSHOT_PATH}")
        return

    with open(SNAPSHOT_PATH) as f:
        data = json.load(f)

    print_summary(data)


if __name__ == "__main__":
    main()
