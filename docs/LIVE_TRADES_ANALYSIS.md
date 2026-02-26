# Live Trades Code Analysis (for Trading from Tomorrow)

This document analyses the code path for **live Convex trading** so you can run real trades from tomorrow. There is no "live from tomorrow" date gate in the code—as soon as `main.py` runs during market hours, it will place live orders if conditions are met.

---

## 1. Entry Flow (New Trades)

| Step | Location | What happens |
|------|----------|--------------|
| 1 | `main.py` | Main loop runs. Every **5 minutes** (`STRATEGY_CHECK_INTERVAL = 300`), during market hours, it calls `run_strategy_with_regime(api, symbol_manager, position_tracker, capital=CAPITAL_8L)`. |
| 2 | `strategy_runner.py` → `run_strategy_with_regime()` | Gets NIFTY spot, expiries, option chain, builds `market_state`, runs `RegimeDetector().detect_regime()`. Routes to **Convex only** (Iron Condor disabled). |
| 3 | `strategy_runner.py` → `_run_convex_backspread_strategy()` | Calls Convex generator; if regime is TRENDING it can propose; also runs when SIDEWAYS for concurrent execution support. |
| 4 | `strategies/convex/call_backspread.py` → `generate_nifty_call_backspread()` | Builds proposal: 1 short ATM call, 2 long OTM calls, net debit ≤ 0.25% spot, max loss ≤ 10% capital, MIN_DAYS_TO_EXPIRY ≥ 2. |
| 5 | `strategy_runner.py` (commit block) | For `CALL_BACKSPREAD`: **Live orders** via `place_convex_trade(api, prop)`. On success, proposal is saved, added to `position_tracker`, and `order_ids` stored. |
| 6 | `strategies/convex/order_builder.py` → `place_convex_trade()` | Builds entry orders (LMT, MIS). Places **longs first**, waits for fill (60s timeout per order), then places **shorts**. Uses `api.place_order()` (Shoonya). |

**Important**: A proposal is **only** added to the tracker and persisted if `place_convex_trade()` returns `success=True`. Partial fills leave you with broker positions but no tracker entry; the code does not place shorts if a long fails to fill.

---

## 2. Exit Flows (Position Monitoring)

Position checks run every **1 minute** (`POSITION_CHECK_INTERVAL = 60`) in `main.py`. For each **open** Convex position (`book == 'CONVEX'` or `'BACKSPREAD'` in strategy):

| Exit type | Broker orders placed? | Code path |
|-----------|------------------------|-----------|
| **Convex exit conditions** (TSL, regime change, max loss, etc.) | ✅ Yes | `close_convex_position(api, position)` → then `position_tracker.close_position(...)`. |
| **End-of-day liquidation** (15 min before 3:30 PM) | ✅ Yes | Same: `close_convex_position(api, position)` then `close_position`. |
| **Profit target** | ❌ **No** (bug) | Only `position_tracker.close_position(...)` is called; **no** `close_convex_position(api, position)`. So the position is marked closed in the app but **remains open at the broker**. |

**Bug**: When profit target is hit for a Convex position, the code does not place exit orders. Only the tracker is updated. This must be fixed before live trading so that profit-target exits also call `close_convex_position(api, position)` before `position_tracker.close_position(...)`.

---

## 3. Timing and Guards

- **Strategy check**: Every 5 minutes during market hours.
- **No new trades**: Last **60 minutes** before market close (no entries after 2:30 PM IST) — `NO_NEW_TRADES_BEFORE_CLOSE_MINUTES = 60`.
- **End-of-day liquidation**: **15 minutes** before 3:30 PM (3:15 PM IST) — `CLOSE_POSITIONS_BEFORE_CLOSE_MINUTES = 15`; all positions are closed via broker then process can stop.
- **Market closed**: At 3:30 PM IST the loop exits after closing any remaining positions (with broker exit for Convex).

---

## 4. State and Restarts

- **Active positions**: Stored in `active_positions.json`. Only entries with `status == 'OPEN'` are returned by `get_active_positions()`.
- **On startup**: `IronCondorPositionTracker()` loads from `active_positions.json`. So if you restart the process "tomorrow", any OPEN positions from today will still be monitored and exited via the same logic (convex exit, profit target, or EOD).
- **Trade proposals**: Also saved under `trade_proposals_by_id/<YYYYMMDD>/<trade_id>.json` and via `save_trade_proposal()`.

---

## 5. Order Execution Details

- **Product**: MIS only (`CONVEX_PRODUCT_TYPE = "M"`).
- **Entry**: Limit (LMT) for price control.
- **Exit**: Market (MKT) for execution certainty.
- **Sequence**: Longs first, wait for fill (poll every 2s, timeout 60s per order), then shorts; same for exit (cover shorts then sell longs).
- **API**: `api.place_order(**kwargs)` in `order_builder.place_single_order()`.

---

## 6. What to Fix Before “Live from Tomorrow”

1. **Profit-target exit for Convex**  
   In `main.py`, when `position.get('book') == 'CONVEX'` (or Convex backspread) and `check_profit_target(position, current_pnl)` is True, call `close_convex_position(api, position)` first; only then call `position_tracker.close_position(...)`. Handle failure (e.g. log and retry or alert) so tracker and broker stay in sync.

2. **Optional**  
   - Add a small guard (e.g. env `LIVE_TRADING_ENABLED=true`) if you want to disable live orders until a chosen date.  
   - Confirm risk limits (max lots, daily loss cap) are enforced as in `strategies/size_config.py` and any Convex-specific caps.

---

## 7. Summary

- **Entry**: Live Convex orders are placed in `strategy_runner` via `place_convex_trade()`; only successful full fills are committed to the tracker.
- **Exit**: Convex exit conditions and EOD liquidation correctly call `close_convex_position()`. **Profit-target exit for Convex does not**—fix this so broker and tracker stay aligned.
- **No “tomorrow” date in code**: The system will trade live whenever `main.py` runs in market hours; use config or env if you want to enable live only from a specific day.

*Last updated: 2026-02-26*
