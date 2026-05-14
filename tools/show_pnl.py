#!/usr/bin/env python3
"""Display current PnL from snapshot."""

import csv
import json
from datetime import datetime
from pathlib import Path

SNAPSHOT_PATH = Path(__file__).parent.parent / "data" / "pnl_snapshot.json"
TRADES_CSV_PATH = Path(__file__).parent.parent / "data" / "paper_trades.csv"
OPEN_POS_PATH = Path(__file__).parent.parent / "data" / "open_positions.json"


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
    d_net = fmt_inr(daily.get("realised_pnl"))
    d_trades = str(daily.get("trades", "—"))
    d_winrate = f"{daily.get('win_rate_pct', 0):.0f}%" if daily.get("trades") else "—"

    a_realised = fmt_inr(data["realised_pnl"])
    a_unrealised = fmt_inr(data["unrealised_pnl"])
    a_net = fmt_inr(data["total_pnl"])
    a_trades = str(data["total_trades"])
    a_winrate = f"{data['win_rate_pct']:.0f}%" if data.get("total_trades") else "—"

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

    print_daily_trades()
    print_open_positions(load_open_positions())


def load_todays_trades() -> list[dict]:
    """Return today's trades from paper_trades.csv."""
    today = datetime.now().strftime("%Y-%m-%d")
    trades = []
    if not TRADES_CSV_PATH.exists():
        return trades
    with open(TRADES_CSV_PATH) as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row.get("date") == today:
                trades.append(row)
    return trades


def load_open_positions() -> dict:
    """Return open positions from open_positions.json."""
    if not OPEN_POS_PATH.exists():
        return {}
    with open(OPEN_POS_PATH) as f:
        data = json.load(f)
    return data.get("strategies", {})


def print_open_positions(open_pos: dict) -> None:
    """Print a table of currently open positions, followed by per-leg marks."""
    if not open_pos:
        print("  No open positions.\n")
        return

    active_positions = []
    for instr, strat in open_pos.items():
        pos = strat.get("position")
        if pos:
            active_positions.append(
                {
                    "instrument": instr,
                    "entry_time": pos.get("entry_time", ""),
                    "entry_credit": pos.get("entry_credit", 0),
                    "peak_pnl": pos.get("peak_pnl", 0),
                    "max_profit": pos.get("max_profit", 0),
                    "lots": pos.get("lots", 0),
                    "expiry": pos.get("expiry_date", ""),
                    "leg_marks": strat.get("leg_marks", {}),
                    "sc_sym": pos.get("sc_sym", ""),
                    "sp_sym": pos.get("sp_sym", ""),
                    "lc_sym": pos.get("lc_sym", ""),
                    "lp_sym": pos.get("lp_sym", ""),
                }
            )

    if not active_positions:
        print("  No open positions.\n")
        return

    rows = []
    for p in active_positions:
        entry_time = p["entry_time"][:5] if p["entry_time"] else ""
        credit = p["entry_credit"]
        peak = p["peak_pnl"]
        max_profit = p["max_profit"]
        lots = p["lots"]
        expiry = p["expiry"]
        rows.append(
            (p["instrument"], entry_time, f"₹{credit:,.2f}", f"₹{peak:,.2f}", f"₹{max_profit:,.2f}", f"{lots}", expiry)
        )

    if not rows:
        print("  No open positions.\n")
        return

    header = ("Instrument", "Entry", "Credit", "Peak P&L", "Max Profit", "Lots", "Expiry")
    w1 = max(len(r[0]) for r in rows + [header])
    w2 = max(len(r[1]) for r in rows + [header])
    w3 = max(len(r[2]) for r in rows + [header])
    w4 = max(len(r[3]) for r in rows + [header])
    w5 = max(len(r[4]) for r in rows + [header])
    w6 = max(len(r[5]) for r in rows + [header])
    w7 = max(len(r[6]) for r in rows + [header])

    def _sep(l="├", m="┼", r="┤"):
        return f"  {l}{'─' * (w1+2)}{m}{'─' * (w2+2)}{m}{'─' * (w3+2)}{m}{'─' * (w4+2)}{m}{'─' * (w5+2)}{m}{'─' * (w6+2)}{m}{'─' * (w7+2)}{r}"

    def _row(c1, c2, c3, c4, c5, c6, c7):
        return f"  │ {c1:<{w1}} │ {c2:<{w2}} │ {c3:<{w3}} │ {c4:<{w4}} │ {c5:<{w5}} │ {c6:<{w6}} │ {c7:<{w7}} │"

    print("\n  Open positions:\n")
    print(
        f"  ┌{'─' * (w1+2)}┬{'─' * (w2+2)}┬{'─' * (w3+2)}┬{'─' * (w4+2)}┬{'─' * (w5+2)}┬{'─' * (w6+2)}┬{'─' * (w7+2)}┐"
    )
    print(_row("Instrument", "Entry", "Credit", "Peak P&L", "Max Profit", "Lots", "Expiry"))
    for row in rows:
        print(_sep())
        print(_row(*row))
    print(
        f"  └{'─' * (w1+2)}┴{'─' * (w2+2)}┴{'─' * (w3+2)}┴{'─' * (w4+2)}┴{'─' * (w5+2)}┴{'─' * (w6+2)}┴{'─' * (w7+2)}┘\n"
    )

    print_leg_marks(active_positions)


def print_leg_marks(active_positions: list[dict]) -> None:
    """Per-leg LTP, bid/ask, and freshness for each open IC. Pulled from
    monitor()'s last-cycle snapshot (data/open_positions.json -> leg_marks)."""
    leg_order = ("sc", "sp", "lc", "lp")
    leg_label = {"sc": "SC (short call)", "sp": "SP (short put)", "lc": "LC (long call)", "lp": "LP (long put)"}

    rows = []
    any_marks = False
    for ap in active_positions:
        marks = ap.get("leg_marks") or {}
        if not marks:
            continue
        any_marks = True
        # Walk legs in fixed order using the position's own symbol→leg mapping.
        sym_to_leg = {ap[f"{lk}_sym"]: lk for lk in leg_order if ap.get(f"{lk}_sym")}
        for sym, mark in marks.items():
            leg = mark.get("leg") or sym_to_leg.get(sym, "?")
            ltp = mark.get("ltp", 0.0)
            age = mark.get("age_sec", 0.0)
            bid = mark.get("bid", 0.0)
            ask = mark.get("ask", 0.0)
            tradable = mark.get("tradable", False)
            volume = mark.get("volume", 0)
            oi = mark.get("oi", 0)
            age_str = f"{age:.0f}s" + (" STALE" if age > 10 else "")
            book_str = f"{bid:.2f} / {ask:.2f}" if tradable else "—"
            vol_str = f"{volume:,}" if volume else "—"
            oi_str = f"{oi:,}" if oi else "—"
            rows.append(
                (ap["instrument"], leg_label.get(leg, leg), sym, f"₹{ltp:.2f}", age_str, book_str, vol_str, oi_str)
            )

    if not any_marks:
        print("  No per-leg marks available (monitor() hasn't recorded yet).\n")
        return

    header = ("Instrument", "Leg", "Symbol", "LTP", "Age", "Bid / Ask", "Volume", "OI")
    w = tuple(max(len(r[i]) for r in rows + [header]) for i in range(8))

    def _sep(l="├", m="┼", r="┤"):
        return (
            f"  {l}{'─' * (w[0]+2)}{m}{'─' * (w[1]+2)}{m}{'─' * (w[2]+2)}{m}"
            f"{'─' * (w[3]+2)}{m}{'─' * (w[4]+2)}{m}{'─' * (w[5]+2)}{m}"
            f"{'─' * (w[6]+2)}{m}{'─' * (w[7]+2)}{r}"
        )

    def _row(c0, c1, c2, c3, c4, c5, c6, c7):
        return (
            f"  │ {c0:<{w[0]}} │ {c1:<{w[1]}} │ {c2:<{w[2]}} │ "
            f"{c3:>{w[3]}} │ {c4:<{w[4]}} │ {c5:<{w[5]}} │ "
            f"{c6:>{w[6]}} │ {c7:>{w[7]}} │"
        )

    print("\n  Leg marks (last monitor cycle):\n")
    print(_sep("┌", "┬", "┐"))
    print(_row(*header))
    for row in rows:
        print(_sep())
        print(_row(*row))
    print(_sep("└", "┴", "┘") + "\n")


def print_daily_trades(trades: list[dict] | None = None) -> None:
    """Print a table of today's individual trades."""
    if trades is None:
        trades = load_todays_trades()

    if not trades:
        print("  No trades today.\n")
        return

    # Build rows: Trade ID | Entry | Exit | Net P&L | Exit Reason
    rows = []
    for t in trades:
        tid = t.get("trade_id", "")
        entry = t.get("time_entry", "")[:5]  # HH:MM
        exit_ = t.get("time_exit", "")[:5]
        try:
            pnl = float(t.get("net_pnl", 0))
        except (ValueError, TypeError):
            pnl = 0.0
        reason = t.get("exit_reason", "")
        if pnl >= 0:
            pnl_str = f"₹{pnl:,.2f}"
        else:
            pnl_str = f"-₹{abs(pnl):,.2f}"
        rows.append((tid, entry, exit_, pnl_str, reason))

    # Column widths
    w1 = max(len(r[0]) for r in rows + [("Trade ID", "", "", "", "")])
    w2 = max(len(r[1]) for r in rows + [("", "Entry", "", "", "")])
    w3 = max(len(r[2]) for r in rows + [("", "", "Exit", "", "")])
    w4 = max(len(r[3]) for r in rows + [("", "", "", "Net P&L", "")])
    w5 = max(len(r[4]) for r in rows + [("", "", "", "", "Exit Reason")])

    def _sep(l="├", m="┼", r="┤"):
        return f"  {l}{'─' * (w1+2)}{m}{'─' * (w2+2)}{m}{'─' * (w3+2)}{m}{'─' * (w4+2)}{m}{'─' * (w5+2)}{r}"

    def _row(c1, c2, c3, c4, c5):
        return f"  │ {c1:<{w1}} │ {c2:<{w2}} │ {c3:<{w3}} │ {c4:<{w4}} │ {c5:<{w5}} │"

    print("\n  Today's trades:\n")
    print(f"  ┌{'─' * (w1+2)}┬{'─' * (w2+2)}┬{'─' * (w3+2)}┬{'─' * (w4+2)}┬{'─' * (w5+2)}┐")
    print(_row("Trade ID", "Entry", "Exit", "Net P&L", "Exit Reason"))
    for row in rows:
        print(_sep())
        print(_row(*row))
    print(f"  └{'─' * (w1+2)}┴{'─' * (w2+2)}┴{'─' * (w3+2)}┴{'─' * (w4+2)}┴{'─' * (w5+2)}┘\n")


def main():
    if not SNAPSHOT_PATH.exists():
        print(f"Error: Snapshot not found at {SNAPSHOT_PATH}")
        return

    with open(SNAPSHOT_PATH) as f:
        data = json.load(f)

    print_summary(data)


if __name__ == "__main__":
    main()
