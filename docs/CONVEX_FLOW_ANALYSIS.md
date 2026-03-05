# Convex Flow Analysis: Flaws, Fixes, and Coverage

This document summarizes the end-to-end flow analysis, identified flaws, fixes applied, and remaining assumptions.

---

## 1. Startup Flow

- **Init:** `main.py` creates `IronCondorPositionTracker()`, loads `active_positions.json`.
- **First sync:** `sync_from_broker(api)` is called (no `positions_raw`); it calls `api.get_positions()`.
- **If sync fails:** Exception is caught, warning logged, run continues with whatever was in the file (possibly empty or stale). Next successful sync is on the first 60s position check when `get_positions()` succeeds.

**Covered:** Startup sync; failure does not crash. No retry loop; next 60s cycle retries.

---

## 2. Convex Entry Flow

- **Proposal:** `run_strategy_with_regime` → Convex proposal generated.
- **Commit:** For `CALL_BACKSPREAD`, `place_convex_trade(api, prop)` runs first. If `place_result.get('success')` is false, we `continue` (no save, no add_position).
- **On success:** `save_trade_proposal(prop)`, write by-id copy, then `position_tracker.add_position(prop)`. Position has `entry_credit` from proposal (`net_debit_total`), our `trade_id`, etc.
- **60s later:** `sync_from_broker(api, positions_raw=positions_raw)` runs. OPEN list is **replaced** with `open_from_broker` (built from broker NFO rows). So the position we added is replaced by a broker-built one (same legs, new `trade_id` like `broker_sync_10MAR26_...`).

**Implication:** Proposal-derived `entry_credit` is only used until the first sync. After that, `entry_credit` comes from broker avg price (if API returns it) or 0. So broker must return average price (e.g. `avgprc`) for TSL “5% of entry premium” and CONVEX_MAX_LOSS % to work for our own trades too after sync.

**Covered:** We try to read broker avg price in `_build_positions_from_broker_nfo`; we set `days_to_expiry` at sync so time-based exits work for broker-synced positions.

---

## 3. Position Check Loop (Every 60s)

- **get_positions()** → `positions_raw`.
- **sync_from_broker(positions_raw=positions_raw)** → OPEN list = broker-built positions (or closed only if no NFO open).
- **active_positions** = `get_active_positions()` (status OPEN).
- For each position: get spot, option_chain, build `current_prices`, **calculate_current_pnl**, then Convex block:
  - **mtm_for_exit** = broker MTM for this position (C4) when `positions_raw` is not None; else `current_pnl`.
  - **check_convex_exit_conditions(..., current_mtm=mtm_for_exit)** (both when `market_state` present and when None).
  - Trailing stop, profit target, convex exit: close via broker then `close_position(...)`.

**Unmet condition / gap:** If **option_chain is empty** for a position, we `continue` and do not run exit logic that cycle. So we could miss an exit for one or more cycles. Optional improvement: when option_chain is empty but we have `positions_raw`, still run exit using broker MTM and `days_to_expiry` from position (minimal path).

**Covered:** C4 (broker MTM for exit); convex exit when `market_state` is None; unrealized MTM log outside `market_state`; profit target and TSL use correct inputs.

---

## 4. Sync When Broker Has No Open NFO

- **Before fix:** If broker returned no open NFO, we set `active_positions = closed`. Any position that was OPEN (e.g. we closed it but `close_position` was not called, or broker flattened it) was **dropped** from history.
- **After fix:** When `nfo_open` is empty and we had OPEN positions, we mark each of those as CLOSED with `exit_reason="broker_flattened"`, `exit_time=now`, `final_pnl=0`, then set `active_positions = closed` (so they remain in the list as closed). History is preserved.

**Covered:** No more silent drop of OPEN positions when broker shows no NFO.

---

## 5. Broker-Synced Position: Time-Based Exits

- **Before fix:** `days_to_expiry` and `entry_days_to_expiry` were None for broker-built positions. So `time_elapsed_pct` was 0 and TIME_ELAPSED_40PCT, TSL time gate (25%), and NO_ATR_EXPANSION never fired for synced positions.
- **After fix:** In `_build_positions_from_broker_nfo` we set `days_to_expiry` to days to expiry at sync time. In main we pass `entry_days_to_expiry = position.get('days_to_expiry', days_to_expiry)`. So time-based exits and TSL time gate work for broker-synced positions.

**Covered:** Time-based logic works after restart/sync.

---

## 6. Positions in Loss

- **When entry_premium > 0:** CONVEX_MAX_LOSS fires at -30% of entry premium.
- **When entry_premium == 0:** We added CONVEX_MAX_LOSS_ABSOLUTE_INR (e.g. ₹6000); if `current_mtm <= -CONVEX_MAX_LOSS_ABSOLUTE_INR` we exit with CONVEX_MAX_LOSS.

**Covered:** Both percentage and absolute loss cap.

---

## 7. MTM Breakdown and EOD Summary

- **MTM breakdown:** Uses `broker_mtm_for_tracked_positions(positions_raw, active_positions)` so “system_unrealized” is broker MTM for our legs; “manual/other” = broker_total - that - system_realized_today.
- **EOD summary:** Includes “opened today (still open)”; wording “No trades were closed today” / “No positions opened today”; returns dict with `opened_today` when relevant.

**Covered:** Correct attribution and wording.

---

## 8. Final PnL Stored on Close

- We pass `current_pnl` (option-chain) to `close_position(..., final_pnl=current_pnl)`. Exit decision may use broker MTM (C4), but stored `final_pnl` is our calculation. Acceptable; optional improvement would be to store broker MTM as final_pnl when available.

---

## 9. Summary of Fixes Applied in This Pass

| Issue | Fix |
|-------|-----|
| Broker-synced positions had no `days_to_expiry` | Set `days_to_expiry` at build time from expiry vs today so time-based exits and TSL time gate work. |
| OPEN positions dropped when broker had no NFO | Mark those OPEN as CLOSED with `broker_flattened`, then set `active_positions = closed` so history is kept. |

---

## 10. Optional / Future Improvements

- **Exit when option_chain is empty:** Use broker MTM + position `days_to_expiry` to run convex exit (minimal path) so we don’t skip exit for a full cycle.
- **final_pnl from broker:** When closing, if we have broker MTM for that position, optionally set `final_pnl` to that for consistency with broker books.
- **Broker avg price field name:** If your broker uses a different key for average price, add it in `_get_avg_price_from_broker_row` in `position_tracker.py`.
