# Live Convex Trading – Gaps and Fixes

Track and fix each gap before / during live trading.

| # | Gap | Priority | Status |
|---|-----|----------|--------|
| 1 | **Partial fills → orphan positions**: Long 1 fills, long 2 times out → broker has open leg, tracker has nothing. No cancel/reconcile. Process crash mid-sequence same. | High | **Done**: On partial completion we close filled legs via offsetting MKT orders and append a review entry to `partial_fill_reviews.json` for every such event. |
| 2 | **No cap on Convex positions**: Can add new backspread every 5 min; no max concurrent Convex positions. | High | Deferred: TSL handles. |
| 3 | **Max loss % doc vs code**: Doc says "1% of capital", code uses 10%. Clarify and align. | Medium | **Done**: Doc updated to 10%. |
| 4 | **Entry LMT at mid**: No slippage buffer; fast markets → no fill or partial fill. | Medium | TODO |
| 5 | **No daily loss limit**: Live path has no "stop new trades if daily P&L < -X". | High | Deferred: TSL handles. |
| 6 | **Profit target disabled**: `check_profit_target()` always False; profit-target exit path is dead code. | Low | TODO |
| 7 | **Market closed close**: On "market closed" we only update tracker, no broker exit; MIS usually auto-squares but mismatch possible. | Medium | TODO |
| 8 | **Regime lag**: 3-confirmation + 5-min check → can hold Convex too long after regime flip. | Low | TODO |
| 9 | **Single expiry**: Convex uses first expiry only; no fallback if illiquid. | Low | TODO |
| 10 | **Capital / sizing**: Capital is fixed (e.g. CAPITAL_8L); no check vs actual broker/risk. | Low | TODO |

---

## IOC orders (Shoonya + NSE)

Shoonya API and NSE support **IOC** (Immediate-or-Cancel) via `place_order(..., retention='IOC')`. See [ShoonyaApi-py](https://github.com/Shoonya-Dev/ShoonyaApi-py): `ret` (retention) can be **DAY / EOS / IOC**. Convex **entry** orders now use `ENTRY_RETENTION = "IOC"` in `strategies/convex/order_builder.py`: we get fast fill or cancel, no lingering pending orders, and quicker feedback when a leg doesn’t fill (helps with gap #1: react sooner to partial completion).

---

## Partial-fill cleanup and review (gap #1)

When entry is only partially complete (e.g. long filled, short cancelled), the code now:

1. **Closes filled legs** – Places offsetting MKT orders for each filled leg so the broker is flat (no orphan).
2. **Saves a review entry** – Appends one record to **`partial_fill_reviews.json`** (project root) with:
   - `timestamp`, `reason`, `message`
   - `filled_legs`: order_id, tradingsymbol, quantity, side
   - `cleanup_results`: per-leg cleanup_order_id, cleanup_ok, error (if any)
   - `proposal_info`: proposal_id, expiry, lots

After every such cleanup you can open `partial_fill_reviews.json` to review what happened and whether each close succeeded. File is capped at 500 entries (oldest dropped). It is listed in `.gitignore`.

---

## Suggested order to address

1. **#3** – Max loss doc vs code — **Done** (doc updated to 10%).
2. **#2** – Max Convex positions — **Deferred** (TSL handles).
3. **#5** – Daily loss limit — **Deferred** (TSL handles).
4. **#1** – Partial fills — **Done** (close filled legs + review file).
5. **#4** – Entry slippage (e.g. LMT at mid + buffer or use MKT for one leg).
6. **#7** – Market-closed: document that MIS auto-squares and optionally add a “reconcile on next run” note.
7. **#6** – Profit target: either implement a real target or remove dead code.
8. **#8, #9, #10** – Tune later (regime lag, expiry choice, capital/sizing).

---

---

## Ghost position: save/add before place (fixed 2026-02-27)

**Issue**: "Saved trade proposal" and "Added position to tracker" were logged *before* `place_convex_trade()` ran. When `place_order` returned None, the tracker still had an OPEN position (ghost) because the Convex strategy was saving and adding inside `_run_convex_backspread_strategy()` before returning the proposal. The main commit loop then called `place_convex_trade()` (which failed) and correctly did *not* add again—but the position had already been added in the strategy.

**Fix**: Removed `save_trade_proposal()` and `position_tracker.add_position()` from `_run_convex_backspread_strategy()`. Only the main commit loop in `run_strategy_with_regime()` now saves and adds, and only *after* `place_convex_trade()` returns success. So no ghost position is added when order placement fails.

**If a ghost was already added** (e.g. trade_id like `2026-02-27T10:26:08.406373_e958f593_078a4be4`): remove that entry from `active_positions.json` (filter out that `trade_id` and keep only positions with `status == 'CLOSED'` or other valid OPEN positions that correspond to real broker positions).

---

## place_order returned None — wrong exchange (fixed 2026-02-27)

**Issue**: Session was valid (prices/quotes were fetched successfully), but `place_order` returned None. Convex entry/exit orders were built with **exchange `NSE`** (cash segment). NIFTY index options (e.g. NIFTY26MAR25550CE) trade on **NFO** (F&O segment). Sending an NFO symbol with exchange NSE causes the API to return None or reject.

**Fix**: In `strategies/convex/order_builder.py`, entry and exit order dicts now use **`"exchange": "NFO"`** for NIFTY option orders instead of `"NSE"`. Applied in `build_convex_entry_orders()` and `build_convex_exit_orders()`.

---

*Created: 2026-02-26*
