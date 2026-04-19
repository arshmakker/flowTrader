# Test Scenarios — RegimeTrader

Acceptance scenarios the system must handle correctly before go-live. Target: ~25 scenarios. Below 15 leaves safety gaps; above 30 won't get rehearsed. Scenarios marked **[P0]** are the pre-go-live priority nine — must be green before any real-money run.

For each scenario: trigger, expected behavior, how to verify, and known gaps against current code.

---

## Entry path

### 1. **[P0]** Clean RANGING entry succeeds
- **Trigger:** Day classified RANGING, VIX < 30 and stable for 45 min, net credit ≥ `IC_MIN_CREDIT = 18`, within `TRADE_START`–`TRADE_END`, DTE ≥ 3.
- **Expected:** All 4 legs fill with status `COMPLETE`. `IronCondorStrategy._position` is set to a new `IC_Position`. `PaperPositionTracker._positions` gains 4 per-leg entries keyed by option symbol (each leg is a dict with `symbol`, `qty`, `avg_price`, `side`, `costs` — not an `IC_Position`; see `paper_position_tracker.py:27-65`).
- **Verify:**
  - `data/open_positions.json` contains the 4 leg entries under `tracker_positions` and the full `IC_Position` under `strategies[<key>]`.
  - `data/pnl_snapshot.json` reflects `unrealised_pnl` changes against the open legs (aggregate scalar, not per-instrument).
  - **Not** verifiable via `paper_trades.csv` — entries are never written on open. `TradeLogger.log_trade` is only called from `pnl_engine.record_trade` (`paper_pnl_engine.py:109`), which is only invoked by `monitor()` returns on exit. There are no entry rows at any stage.

### 2. Entry blocked because day is TRENDING
- **Trigger:** `abs_move ≥ 1.5%` and `vwap_dist ≥ 0.3%` at 10:30.
- **Expected:** `day_class.day_type == "TRENDING_*"`, regime gate closed, no entry.
- **Known gap:** `DayClassifier` calls `compute_vwap_value()` with no argument so `vwap` is always 0 and this branch is currently dead. Fix before this scenario is testable. See TECHNICAL_REFERENCE.md §3.1.

### 3. Entry blocked because VIX ≥ 30
- **Trigger:** India VIX > `IC_VIX_MAX = 30.0` at classification or entry gate check.
- **Expected:** `regime.get_regime_gate()` returns False; main loop skips entry block.
- **Verify:** "Entry Gate BLOCKED: VIX ..." log line from `RegimeFilter`; no new `strategies` entries or `tracker_positions` legs appear in `data/open_positions.json` on the next save.

### 4. Entry blocked because VIX unstable
- **Trigger:** VIX history max−min over last 45 min > `IC_VIX_STABLE_BAND = 1.5`, or fewer than 5 samples in window.
- **Expected:** Regime gate closed with stability reason.
- **Verify:** `RegimeFilter._vix_history` contents; gate log reason.

### 5. Entry blocked because credit below minimum
- **Trigger:** Computed `net_credit_unit = (sc + sp) − (lc + lp) < 18`.
- **Expected:** `IronCondorStrategy.enter()` aborts before placing any leg; emits `IC_REJECT reason=CREDIT_BELOW_MIN` log.
- **Verify:** `IC_REJECT reason=CREDIT_BELOW_MIN ...` log line; no new `strategies` entries or `tracker_positions` legs appear in `data/open_positions.json` on the next save (abort happens before any `place_order` call).

### 6. S/R buffer pushes strikes outward
- **Trigger:** Raw short-call strike < `sr_high + 50` or raw short-put strike > `sr_low − 50`.
- **Expected:** `SRManager.apply_buffer` shifts shorts outward to clear the 50-point buffer; long wings recomputed off the new shorts.
- **Verify:** `sc_strike` / `sp_strike` exceed the naive `spot ± otm_distance` values. Read them from `data/open_positions.json::strategies[<instrument>]` (the persisted `IC_Position` dict) or from the `IC … ENTERED:` log line in `logs/ic_system_YYYYMMDD.log`. Not verifiable via `paper_trades.csv` — entry opens are never written there.

### 7. 3-DTE rolls to next expiry
- **Trigger:** Nearest expiry has DTE < `IC_DTE_THRESHOLD = 3`.
- **Expected:** `ExpiryManager.get_expiry()` returns the next weekly expiry, not the current one. Legs built against the new expiry.
- **Verify:** Expiry string in leg symbols matches the rolled expiry, not the nearest.

### 8. Entry skipped outside trading window
- **Trigger:** Clock < `TRADE_START = 10:00` or ≥ `TRADE_END = 15:10`.
- **Expected:** Before classification time, main loop simply doesn't enter the entry block. After `TRADE_END`, the EOD branch takes over.
- **Verify:** No entry attempts logged during these windows.

---

## Monitoring & exit

### 9. **[P0]** Profit harvest at 1%
- **Trigger:** `total_pnl ≥ max_profit × IC_HARVEST_PCT = 0.01` during monitoring.
- **Expected:** `monitor()` returns exit dict with `reason=PROFIT_HARVEST`. Main loop calls `pnl_engine.record_trade()`. Trade row written; re-entry allowed on next cycle if conditions still hold.
- **Verify:** `paper_trades.csv` row with `exit_reason=PROFIT_HARVEST`; `paper_summary.json` realised PnL updated.

### 10. Short-strike breach → adjustment exit
- **Trigger:** While position is profitable, spot crosses `sc_strike` above or `sp_strike` below.
- **Expected:** `monitor()` returns `reason=ADJUSTMENT_REQUIRED` exit; position cleared; may re-enter on later cycle if gate re-opens.
- **Verify:** Trade row with `exit_reason=ADJUSTMENT_REQUIRED`.

### 11. Combined hard stop at 3×
- **Trigger:** Sum of unrealized losses across all active condors ≤ `−total_max_profit × IC_STOP_LOSS_MULT = 3.0`, confirmed across 2 consecutive ticks (`IC_HARD_STOP_CONFIRM_TICKS`).
- **Expected:** `risk.halted = True`, `stop_hit_at` set, all active strategies `force_exit()`-ed, no further entries that session.
- **Verify:** Critical log line "COMBINED STOP LOSS HIT"; positions cleared.
- **Known gap:** `force_exit()` returns are discarded today, so these exits will NOT appear in `paper_trades.csv` / `paper_summary.json`. See TECHNICAL_REFERENCE.md §2.3 step 10. Fix before this scenario's verification is meaningful.

### 12. Hard stop ignores invalid quotes
- **Trigger:** At least one active strategy present, but one or more legs has `ltp ≤ 0` in the current cycle.
- **Expected:** `RiskManager.check_combined_stop_loss()` skips the check and resets `_stop_breach_streak = 0`. System does not halt.
- **Verify:** Log line showing skip-on-invalid-quotes; streak counter reset.

### 13. Both instruments active simultaneously
- **Trigger:** NIFTY and BANKNIFTY both have open condors at the same time.
- **Expected:** Combined stop-loss math sums unrealized across both (`RiskManager.check_combined_stop_loss` iterates `active_strategies`); harvest/adjustment evaluated per-instrument; no interference.
- **Verify:**
  - `data/open_positions.json` shows `strategies` entries for both NIFTY and BANKNIFTY keys, and `tracker_positions` contains 8 per-leg entries (4 per instrument).
  - Per-instrument lifecycle logs are independent (`IC NIFTY ...` vs `IC BANKNIFTY ...`).
  - **Do not** expect per-instrument open-position entries in `pnl_snapshot.json`. The snapshot is aggregate: `realised_pnl`, `unrealised_pnl`, `total_pnl` as scalars plus `strategy_stats` (which only tracks realized closed trades, not open positions) — see `paper_pnl_engine.py:162-186`.

---

## End of day

### 14. **[P0]** Carry overnight on a normal weekday
- **Trigger:** Clock ≥ `TRADE_END = 15:10`, next calendar day passes `is_trading_day_ist`.
- **Expected:** No force-exit; `pnl_engine.write_snapshot()` called; `position_persistence.save()` with `session_status=SESSION_ACTIVE`; loop sleeps until next session.
- **Verify:** `open_positions.json` still contains the 4 legs; log "Positions carried overnight."

### 15. **[P0]** Force-flatten before holiday or weekend
- **Trigger:** Clock ≥ `TRADE_END`, next calendar day is Saturday/Sunday or in `TRADING_HOLIDAYS_IST`.
- **Expected:** All active strategies `force_exit()`-ed; collector stopped; `session_status=SESSION_FLAT` if all positions closed; log "Pre-holiday/weekend close: force-exited all positions."
- **Verify:**
  - Strategy-level state is cleared: `data/open_positions.json::strategies` has no entries for active instruments.
  - Log shows the pre-holiday close message.
- **Known gap — two distinct staleness problems that currently break this scenario's books:**
  1. `force_exit()` return value is discarded (main.py:365-367), so no row is written to `paper_trades.csv` and `paper_summary.json` realised PnL is not updated for these exits.
  2. `exit()` submits closing legs with `track_position=False` (iron_condor.py:320-323), so `PaperPositionTracker._positions` is never unwound. `position_persistence.save` copies `_positions` directly (`position_persistence.py:95-96`), meaning `data/open_positions.json::tracker_positions` can still show the 4 short legs even after strategy-level flatten. Any reconciliation must treat both artifacts with caution until these are fixed.

### 16. Thursday before a Friday holiday
- **Trigger:** Thursday `TRADE_END` with Friday in `TRADING_HOLIDAYS_IST`.
- **Expected:** Same as scenario 15 (multi-day gap triggers flatten). No "one more trading day" exception.
- **Verify:** Flat by Thursday 15:10 log.

---

## Broker / data failure

### 17. **[P0]** Option LTP outside sanity band
- **Trigger:** Broker returns option quote with `lp = 7500` or `lp = 0.02` (outside `[0.05, 5000]`).
- **Expected:** `MarketData.get_ltp()` returns last valid cached LTP, or 0.0 if none. `PaperOrderManager.place_order()` rejects with `reason=suspicious_option_ltp` if no override supplied.
- **Verify:** Warn log; order rejection payload; no filled row for this leg.

### 18. Transient quote failure with backoff
- **Trigger:** `api.get_quotes()` raises network error or returns empty body on first call; succeeds on retry.
- **Expected:** `get_quotes_safe()` retries with backoff. Strategy path sees eventual success. Collector (low-priority lane) backs off on DNS failures by 5 s.
- **Verify:** Retry log entries; final successful quote used by strategy.

### 19. OAuth token expiry mid-session
- **Trigger:** Cached token in `cred.yml` becomes invalid at T+4h during an active session.
- **Expected today (partial):** The quote path in `api_helper.py` has a per-call OAuth-header → `jKey` fallback for individual quote requests, which can mask brief auth blips. That's it.
- **Known gap:** There is **no orchestrated mid-session re-auth flow**. `oauth_reauth_attempts` and the re-auth loop live in the startup path (`main.py:218-268`); once the main trading loop is running, a hard token expiry has no dedicated recovery mechanism. If both OAuth and `jKey` fall through, quote failures will surface as transient errors, not as a guaranteed session-continuation path. Build a real mid-session re-auth before relying on this scenario.
- **Verify (when built):** Auth refresh log entry emitted during market hours; trade cycle resumes in the same process without restart.

### 20. **[P0]** Broker rejects leg during atomic entry
- **Trigger:** Legs 1 and 2 fill; leg 3 returns `status != "COMPLETE"`.
- **Expected:** `IronCondorStrategy.enter()` aborts; `_rollback_partial_entry()` sends reverse orders for legs 1–2; no active strategy state is created (`self._position` stays `None`).
- **Verify:**
  - Log "IC entry aborted: leg ... rejected" plus rollback order log entries.
  - Strategy-level clean: `self._position is None`; no entry in `data/open_positions.json::strategies`.
- **Known gap — tracker not unwound by rollback:** Normal entry legs go through `place_order(symbol, side, qty)` with default `track_position=True` (iron_condor.py:218), so legs 1 and 2 are added to `PaperPositionTracker._positions`. Rollback orders are submitted with `track_position=False` (iron_condor.py:67), so the tracker is **not** rolled back. Result: the 2 stale leg entries persist in the tracker and will be written to `data/open_positions.json::tracker_positions` on the next save, even though the strategy reports clean. Fix before treating "tracker clean" as a verification criterion.

### 21. **[P0]** Rollback itself fails
- **Trigger:** Partial entry (legs 1–2 filled), leg 3 rejected, reverse order for leg 1 also fails.
- **Expected:** System halts and alerts (item 4 of `GO_LIVE_CHECKLIST.md`) rather than continuing with a stuck half-condor.
- **Known gap:** Not implemented today — current code logs rollback failure but does not escalate. Build before go-live.

---

## Operational / lifecycle

### 22. **[P0]** Cold start with no prior state
- **Trigger:** Fresh launch; `data/open_positions.json` missing or empty.
- **Expected:** `position_persistence.load()` returns clean state; strategies initialized inactive; system waits for market hours.
- **Verify:** No restore logs; `risk.halted = False`; tracker empty.

### 23. **[P0]** Warm start with open positions
- **Trigger:** Launch after a crash where `open_positions.json` contains 4 open legs.
- **Expected today (paper):** `position_persistence.load()` rebuilds strategy state, tracker, P&L engine, risk manager from the JSON. Monitoring picks up on next cycle.
- **Expected for live (from checklist item 2):** additionally reconcile against broker `get_positions` — broker is truth, JSON is hint.
- **Verify:** Restore log entries; active instrument count matches the JSON; no duplicate entries on next entry cycle.

### 24. Kill switch flattens and exits
- **Trigger:** Operator runs `touch data/HALT` mid-session.
- **Expected:** Top-of-loop check detects the file, force-exits all positions, exits process.
- **Known gap:** Not implemented — item 7 of `GO_LIVE_CHECKLIST.md`. Needed before go-live.

### 25. Double-launch is refused
- **Trigger:** `./start.sh` invoked while another instance is already running.
- **Expected:** PID-file check in `main.py` startup sees live PID and refuses to start; prints clear message.
- **Known gap:** Not implemented — item 16 of `GO_LIVE_CHECKLIST.md`. Critical before live to avoid duplicate orders.

---

## Priority summary

| Priority | Scenarios | Gate |
|---|---|---|
| **[P0]** — must pass before first live trade | 1, 9, 14, 15, 17, 20, 21, 22, 23 | Go-live gate |
| Secondary — validate during 2-week single-instrument proving period | 10, 11, 12, 13, 18, 19, 24, 25 | Scale-up gate |
| Deferred / blocked on code fixes | 2 (VWAP bug), 11 & 15 verification (force-exit logging), 16, 21 (rollback escalation not built) | Fix then test |

## Notes on scenarios vs. tests

- Scenarios are acceptance-level — each may require multiple unit tests plus a dry-run against Shoonya sandbox.
- Most scenarios 1–13 map cleanly to pytest tests using `MockMD` / mocked `PaperOrderManager`.
- Scenarios 14–16 need a time-mocked fixture that advances the clock.
- Scenarios 17–21 need either broker sandbox or deliberately-injected errors into the API wrapper.
- Scenarios 22–25 need a full process launch, not just a unit test.

## Three scenarios deliberately not on the list

- **Backtesting accuracy** — out of scope; this system is forward-running only.
- **Live recovery mode after hard stop** — the gating logic exists in `RiskManager.can_enter_recovery()`, but `main.py` does not wire an execution flow. Not a scenario until the wiring exists.
- **Multi-strategy router** — referenced in older README but the active system is IC-only for NIFTY + BANKNIFTY.
