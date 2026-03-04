# Production changes and live-test readiness

Summary of **production** (live-trading) changes and a short readiness checklist for going live.

---

## 1. Production changes (what we changed in the product)

### Entry and order placement

| Change | Where | What |
|--------|--------|------|
| **No Convex entry within 45 min of close** | `strategy_runner.py` | Skip new Convex entry when `get_now_ist()` is within 45 min of 15:30. Avoids opening a position that would immediately hit end-of-day exit. |
| **Net debit cap (live)** | `strategies/convex/call_backspread.py` | `MAX_NET_DEBIT_PCT = 0.0032` (0.32% of spot). Proposals with net debit above this are rejected. |
| **Sequential entry (buy then sell)** | `strategies/convex/order_builder.py` | Buys placed first; after fills, sell orders placed. Not basket. |
| **Refresh sell price before place** | `strategies/convex/order_builder.py` | Before each sell order, `_refresh_sell_order_price()` updates limit from current ask/LTP so sells are not stale. |

### Exits (Convex)

| Change | Where | What |
|--------|--------|------|
| **Time exit only when in loss** | `strategies/iron_condor/position_tracker.py` | TIME_ELAPSED_40PCT triggers only when `current_mtm <= 0`. In profit we do not exit on time; TSL or other exits handle. |
| **Ignore regime change when in profit** | `strategies/iron_condor/position_tracker.py` | If regime flips TRENDING → SIDEWAYS but `current_mtm > 0`, we do not exit on REGIME_CHANGED; regime-change count is reset. Lets winners run; TSL or other exits close. |
| **TSL and peak tracking** | `strategies/iron_condor/position_tracker.py` | TSL activation at +5% MTM or 25% time; trail 15% (10% when time > 40% or ATR% < 30). Peak MTM and TSL profit stored and logged at close; included in `performance_by_regime.json`. |

### Position sync and monitoring

| Change | Where | What |
|--------|--------|------|
| **Periodic position sync** | `main.py` | Every 60s position check calls `position_tracker.sync_from_broker(api, positions_raw=positions_raw)` using the same `get_positions()` result (no extra API call). Tracker OPEN list aligned with broker NFO positions. |
| **Imbalance check** | Removed | Previous imbalance detection and auto-resolve were removed per your request. |

### Broker and robustness

| Change | Where | What |
|--------|--------|------|
| **“Yel is down” timeout** | `strategies/convex/order_builder.py` | On rejection with "SAF:Yel is down", wait 5 minutes before treating order as terminal (gives broker time to recover). |

### Logging and observability

| Change | Where | What |
|--------|--------|------|
| **Unrealized MTM log (peak, TSL)** | `main.py` | Convex unrealized MTM log line includes `peak_profit` and `tsl_active`. |
| **MTM breakdown (broker vs system vs manual)** | `main.py` | In the 60s block, log: `broker_total`, `urmtom`, `rpnl`, `system_unrealized`, `system_realized_today`, `manual/other` so you can see system vs manual P&L. |
| **Peak / TSL profit at close** | `strategies/iron_condor/position_tracker.py` | On Convex close, log and store `peak_profit` and `tsl_profit` (when exit is CONVEX_TSL_HIT); written to performance_by_regime. |

### Not in production (backtest / tools only)

- Backtest report enrichment (IV, ADX, entry_hour, hold_minutes) and `analyze_convex_backtest.py`.
- Backtest-only net debit override (`max_net_debit_pct=0.007` / 0.011) and `--time-exit` override.
- Backtest “no entry within 45 min” (same rule exists in production in strategy_runner).
- Docs: CONVEX_TIME_BASED_EXITS, CONVEX_WINNING_PATTERN, CONVEX_TIME_EXIT_EXPERIMENT, CONVEX_BACKTEST_ANALYSIS.

---

## 2. Are we ready for production?

**Code and behaviour**

- Entry: Convex only in TRENDING; no new Convex entry within 45 min of 15:30; net debit cap 0.32% of spot; sequential buy-then-sell with refreshed sell price.
- Exits: Regime change ignored when in profit; time exit only when in loss; TSL and max loss (-30%) and other Convex exits unchanged.
- Sync: Positions synced from broker every 60s without extra `get_positions()` call.
- Robustness: 5‑minute wait on “Yel is down” before marking order terminal.
- Logging: Unrealized MTM (peak, tsl_active), MTM breakdown (broker / system / manual), peak and TSL profit at close.

**What you should confirm before live test**

1. **Broker and env**  
   API credentials, NFO permissions, margin, and that “no live market data” constraint (if any) does not block the live run.

2. **Capital and size**  
   Convex uses `CONVEX_MAX_LOTS = 1` (from size_config/call_backspread). Confirm lot size and capital are what you want for tomorrow.

3. **Market hours**  
   Main loop and strategy_runner use IST 9:15–15:30; no new Convex entry after 14:45. Confirm this matches your intent.

4. **Logs and files**  
   Check that log level and file logging are set so you get DEBUG/INFO where needed; `performance_by_regime.json` and `active_positions.json` paths are writable.

5. **Backtest caveat**  
   Recent backtest was inconclusive (few trades, no winners, time-exit threshold not differentiated). Live results can differ; treat first day as observation.

---

## 3. Quick reference: main production files

| File | Role |
|------|------|
| `main.py` | Loop, position sync, Convex monitoring, MTM logging, end-of-day handling. |
| `strategy_runner.py` | Regime → strategy routing, Convex/Iron Condor proposals, 45‑min no-entry window. |
| `strategies/iron_condor/position_tracker.py` | Convex exit rules (regime, time, TSL, max loss), sync, peak/TSL tracking. |
| `strategies/convex/order_builder.py` | Place Convex (sequential), refresh sell price, “Yel is down” wait. |
| `strategies/convex/call_backspread.py` | Convex proposal, net debit check (MAX_NET_DEBIT_PCT 0.32%). |

If you want, we can add a one-line “live test checklist” (e.g. in README or this doc) that you can tick off before starting tomorrow.
