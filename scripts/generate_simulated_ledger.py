#!/usr/bin/env python3
import json
import csv
from datetime import datetime
from pathlib import Path

"""
Generate a simulated per-leg fill ledger from finalized active_positions.json.

Output:
 - ../ledger.json (list of fill events)
 - ../ledger_summary.csv (per-trade aggregated cashflow & implied pnl)

Rules:
 - For each trade (record in active_positions.json) we emit ENTRY_FILL and EXIT_FILL
   per leg using entry price (from record.entry_prices or leg.price) and exit price
   (from leg.ltp if available, else use entry price).
 - Effective quantity = lots * leg.quantity * lot_size (if lot_size present).
 - Cash flow signs:
     * SHORT: entry +price*qty (inflow), exit -price*qty (outflow)
     * LONG:  entry -price*qty (outflow), exit +price*qty (inflow)
 - The script writes `is_final` trades only (expects active_positions.json to be finalized).
"""

ROOT = Path(__file__).resolve().parents[1]
AP = ROOT / "active_positions.json"
LEDGER_OUT = ROOT / "ledger.json"
SUMMARY_OUT = ROOT / "ledger_summary.csv"

def parse_price_map(m):
    # entry_prices keys like "CE25700" -> price
    return m or {}

def to_ts(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(s)
    except Exception:
        try:
            return datetime.fromisoformat(s.split('+')[0])
        except Exception:
            return None

def main():
    data = json.loads(AP.read_text())
    ledger = []
    summary_rows = []

    for rec in data:
        if not rec.get("is_final"):
            # skip non-final records
            continue
        trade_id = rec.get("trade_id")
        lots = rec.get("lots", 1) or 1
        lot_size = rec.get("lot_size", 1) or 1
        entry_time = rec.get("entry_time")
        exit_time = rec.get("exit_time")
        entry_prices = parse_price_map(rec.get("entry_prices", {}))
        legs = rec.get("legs", [])

        trade_cashflow = 0.0
        per_leg_cash = []

        for idx, leg in enumerate(legs):
            pos = leg.get("position", "LONG")  # SHORT or LONG
            opt = leg.get("option_type")
            strike = leg.get("strike")
            leg_qty_per = leg.get("quantity", 1) or 1
            total_qty = lots * leg_qty_per * lot_size

            # determine entry price: prefer entry_prices map, fallback to leg.price
            key = f"{opt}{int(strike)}" if (opt is not None and strike is not None) else None
            entry_price = None
            if key and key in entry_prices:
                entry_price = float(entry_prices[key])
            else:
                entry_price = float(leg.get("price") or 0.0)

            # determine exit price: prefer leg.ltp, else use entry_price
            exit_price = float(leg.get("ltp") if leg.get("ltp") is not None else entry_price)

            # compute cashflow for entry / exit
            if pos.upper() == "SHORT":
                entry_cash = + entry_price * total_qty
                exit_cash = - exit_price * total_qty
            else:
                entry_cash = - entry_price * total_qty
                exit_cash = + exit_price * total_qty

            event_entry = {
                "event_id": f"{trade_id}|leg{idx}|ENTRY",
                "trade_id": trade_id,
                "event_type": "ENTRY_FILL",
                "timestamp": entry_time,
                "position": pos,
                "option_type": opt,
                "strike": strike,
                "quantity": total_qty,
                "price": entry_price,
                "cash_flow": entry_cash,
                "source": "SIMULATOR"
            }
            event_exit = {
                "event_id": f"{trade_id}|leg{idx}|EXIT",
                "trade_id": trade_id,
                "event_type": "EXIT_FILL",
                "timestamp": exit_time,
                "position": pos,
                "option_type": opt,
                "strike": strike,
                "quantity": total_qty,
                "price": exit_price,
                "cash_flow": exit_cash,
                "source": "SIMULATOR"
            }

            ledger.append(event_entry)
            ledger.append(event_exit)

            trade_cashflow += entry_cash + exit_cash
            per_leg_cash.append({
                "leg_index": idx,
                "entry_cash": entry_cash,
                "exit_cash": exit_cash,
                "leg_pnl": entry_cash + exit_cash
            })

        # collect summary per trade
        summary_rows.append({
            "trade_id": trade_id,
            "strategy": rec.get("strategy"),
            "book": rec.get("book"),
            "entry_time": entry_time,
            "exit_time": exit_time,
            "lots": lots,
            "lot_size": lot_size,
            "calculated_pnl": round(trade_cashflow, 2),
            "reported_final_pnl": round(float(rec.get("final_pnl", 0)), 2)
        })

    # write ledger
    LEDGER_OUT.write_text(json.dumps(ledger, indent=2))

    # write summary CSV
    with open(SUMMARY_OUT, "w", newline='') as csvf:
        writer = csv.DictWriter(csvf, fieldnames=[
            "trade_id","strategy","book","entry_time","exit_time","lots","lot_size","calculated_pnl","reported_final_pnl"
        ])
        writer.writeheader()
        for row in summary_rows:
            writer.writerow(row)

    print(f"Wrote ledger {LEDGER_OUT} with {len(ledger)} events")
    print(f"Wrote summary {SUMMARY_OUT} with {len(summary_rows)} trades")

if __name__ == '__main__':
    main()

