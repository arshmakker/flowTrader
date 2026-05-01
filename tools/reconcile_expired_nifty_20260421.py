"""One-off reconciliation: close the 2026-04-21 NIFTY IC at intrinsic.

The IC was never hard-closed on expiry day because its persisted state (from
2026-04-20) pre-dated the `expiry_date` field; `_find_expiring_today` skipped
it. Commit b99bfea fixed the field-inference bug after market close. There is
still no "past-expiry on next startup" reconciliation path, so this script
handles the one stranded position.

NIFTY close on 2026-04-21 = 24,576.60 (user-confirmed; matches the frozen
post-close tick in market_data_20260421/raw_data/others/Nifty 50_20260421.csv).

Run with main.py NOT running — we write open_positions.json directly.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from trading_system.core import position_persistence
from trading_system.core.trade_logger import TradeLogger
from trading_system.paper.paper_pnl_engine import PaperPnLEngine
from trading_system.paper.paper_position_tracker import PaperPositionTracker

SETTLEMENT_SPOT = 24576.60
EXPIRY_STRATEGY = "NIFTY"
EXIT_DATE = "2026-04-21"
EXIT_TIME = "15:30:00"

LEG_INTRINSIC = {
    "NFO|NIFTY21APR26C24350": max(SETTLEMENT_SPOT - 24350, 0.0),
    "NFO|NIFTY21APR26P22400": max(22400 - SETTLEMENT_SPOT, 0.0),
    "NFO|NIFTY21APR26C24450": max(SETTLEMENT_SPOT - 24450, 0.0),
    "NFO|NIFTY21APR26P22300": max(22300 - SETTLEMENT_SPOT, 0.0),
}


class _StubMarketData:
    def get_ltp(self, symbol: str) -> float:  # noqa: ARG002
        return 0.0


def main() -> int:
    with open(position_persistence.STATE_FILE, "r") as f:
        state = json.load(f)

    nifty_strat = state["strategies"].get(EXPIRY_STRATEGY)
    if nifty_strat is None:
        print("No NIFTY strategy in persisted state — nothing to do.")
        return 0

    tracker = PaperPositionTracker()
    tracker.restore_state({"positions": state.get("tracker_positions", {}), "unmarked": []})

    pnl_engine = PaperPnLEngine(tracker, _StubMarketData(), TradeLogger())
    pnl_engine.restore_state(state.get("pnl_state", {}))

    per_leg: dict[str, float] = {}
    total_pnl = 0.0
    for sym, intrinsic in LEG_INTRINSIC.items():
        pos_before = tracker._positions.get(sym)
        if pos_before is None:
            print(f"  {sym}: not in tracker — skipped")
            continue
        leg_pnl = tracker.close_position(sym, intrinsic)
        per_leg[sym] = leg_pnl
        total_pnl += leg_pnl
        print(
            f"  {sym}: qty={pos_before['qty']:+d} avg={pos_before['avg_price']:.2f} "
            f"exit={intrinsic:.2f} net_pnl={leg_pnl:+.2f}"
        )

    trade_data = {
        "instrument": EXPIRY_STRATEGY,
        "date": EXIT_DATE,
        "entry_date": nifty_strat.get("entry_date", ""),
        "time_entry": nifty_strat.get("entry_time", ""),
        "time_exit": EXIT_TIME,
        "sc_strike": nifty_strat["sc_strike"],
        "sp_strike": nifty_strat["sp_strike"],
        "lc_strike": nifty_strat["lc_strike"],
        "lp_strike": nifty_strat["lp_strike"],
        "entry_credit": round(nifty_strat["entry_credit"], 2),
        "exit_price": round(SETTLEMENT_SPOT, 2),
        "gross_pnl": round(total_pnl, 2),
        "net_pnl": round(total_pnl, 2),
        "exit_reason": "EXPIRY_SETTLEMENT",
        "lots": nifty_strat["lots"],
        "peak_pnl": round(nifty_strat.get("peak_pnl", 0.0), 2),
    }

    pnl_engine.record_trade(EXPIRY_STRATEGY, total_pnl, trade_data)

    state["strategies"].pop(EXPIRY_STRATEGY, None)
    state["tracker_positions"] = tracker._positions
    state["pnl_state"] = pnl_engine.save_state()
    state["saved_at"] = datetime.now().isoformat()
    state["last_loop_at"] = state["saved_at"]

    tmp = position_persistence.STATE_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f, indent=2, default=str)
    os.replace(tmp, position_persistence.STATE_FILE)

    pnl_engine.write_snapshot()

    print(f"\nTotal IC net P&L: ₹{total_pnl:,.2f}")
    print(f"New realised total: ₹{pnl_engine.realised_pnl:,.2f} ({pnl_engine.total_trades} trades)")
    print(f"New win rate: {pnl_engine.win_rate:.1f}%")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
