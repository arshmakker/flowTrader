# Bugs for Review — Merged

Merged from `bugs_for_review1.md` (repo root) and `docs/bugs_for_review2.md`. Scope is intentionally tight:
- **Current practical defects** — things that are wrong in the runtime you are actually using today.
- **Test infrastructure defects** — things that block trustworthy verification.
- **Secondary cleanup / latent risks** — dead wiring or dormant code paths that are worth tracking, but are not current execution blockers.

Excluded by design: architectural go-live gaps (see `GO_LIVE_CHECKLIST.md`), websocket implementation work, and strategy/economics calibration (harvest threshold, cost-model completeness — tracked separately as model decisions, not defects).

Severity scale:
- **High** — affects current paper/runtime correctness, safety, or blocks reconciliation.
- **Medium** — silent-but-wrong behavior, verification gap, or important runtime coupling issue.
- **Low** — cleanup / dead wiring / efficiency / out-of-scope latent path.

Priority scale:
- **P0** — fix before trusting paper results or adding live execution.
- **P1** — fix after P0; important for robustness, maintainability, or verification.
- **P2** — cleanup / efficiency / dormant-path work; schedule opportunistically.

---

## BUG-01 · VWAP classification is effectively dead
- **Severity:** High
- **Severity reason:** The system's primary day-type classifier is functionally broken, so an intended top-level regime filter is not operating at all.
- **Priority:** P0
- **Priority reason:** This must be fixed before trusting any paper conclusions about regime gating or entry quality.
- **Evidence:**
  - `trading_system/core/day_classifier.py:62` — `vwap = self.se.compute_vwap_value()` called with **no argument**.
  - `trading_system/core/signal_engine.py:53-56` — returns `0.0` when `ohlcv_df` is `None`. The engine is stateless; no caching between calls.
  - `main.py:389-390` — computes VWAP with the correct dataframe but discards the return value.
- **Impact:** `vwap_dist` is always `0.0`, so the trending branch in `DayClassifier.classify()` (requires `vwap_dist ≥ 0.3%`) **never fires**. Every day classifies as `RANGING`. The entire "trending vs ranging" regime gate is doing nothing — the `RegimeFilter` VIX gate carries all real filtering.
- **Suggested fix:** Either pass the OHLCV dataframe into `classify()` and forward it to `compute_vwap_value(ohlcv)`, or cache the last VWAP value on `SignalEngine` so `main.py`'s pre-compute is usable. Add a test asserting a synthesized trending-day classification returns `TRENDING_*`.

## BUG-02 · `force_exit()` returns are discarded — exits not logged
- **Severity:** High
- **Severity reason:** Stop-loss and EOD exits disappear from trade history and realised P&L, which breaks reconciliation and misstates system results.
- **Priority:** P0
- **Priority reason:** Until this is fixed, paper books are incomplete on exactly the days where accurate accounting matters most.
- **Evidence:**
  - `main.py:365-367` — EOD pre-holiday flatten: `for s in strats: if s.is_active(): s.force_exit()`. Return value dropped.
  - `main.py:404-405` — Combined hard stop: same pattern, return dropped.
  - `main.py:399` is the **only** runtime call site of `pnl_engine.record_trade`.
  - `trading_system/core/iron_condor.py:349-353` — `force_exit()` returns a result dict identical in shape to `monitor()` returns.
- **Impact:** Exits via the combined hard stop and the EOD flatten path never reach `paper_trades.csv` or `paper_summary.json`. Realised PnL appears smaller than reality; trade-count statistics are wrong; nightly reconciliation against broker contract notes is impossible during any stop-loss or holiday-flatten day.
- **Suggested fix:** In both `main.py` sites, capture the return: `result = s.force_exit(); if result: pnl_engine.record_trade(s.instrument, result['pnl'], result); risk.update_pnl(result['pnl'])`. Mirror the pattern already used for `monitor()` returns.

## BUG-03 · `PaperPositionTracker` never unwound on exit (stale leg accumulation)
- **Severity:** High
- **Severity reason:** Closed positions continue to look open in tracker-derived state and P&L, corrupting dashboards and persistence.
- **Priority:** P0
- **Priority reason:** The tracker is foundational infrastructure; it has to be trustworthy before any higher-level analysis or live work.
- **Evidence:**
  - `trading_system/core/iron_condor.py:320-323` — all four closing legs in `exit()` submitted with `track_position=False`.
  - `trading_system/paper/paper_order_manager.py:152-153` — `place_order` only calls `tracker.add_position(order)` when `track_position=True`.
  - `trading_system/paper/paper_position_tracker.py:27-65` — `add_position` is the only path entries are added; a reverse order against an existing position is not netted because it never reaches the tracker.
- **Impact:** Every exit (harvest, adjustment, force_exit) leaves the original 4 short leg entries in `_positions` forever. Two concrete downstream consequences:
  - `trading_system/core/position_persistence.py:95-96` copies `_positions` directly into `open_positions.json::tracker_positions`, so the persisted state shows phantom legs after any flatten.
  - `PaperPnLEngine.unrealised_pnl` (at `paper_pnl_engine.py:125-126`) reads `tracker.get_unrealised_pnl`, which marks to market **closed** positions as if they were open, distorting `pnl_snapshot.json`.
- **Suggested fix:** Have `IronCondorStrategy.exit()` call `self.om.tracker.close_position(sym, fill_price)` explicitly per leg, or submit closing legs with `track_position=True` so `add_position` nets to zero (see `paper_position_tracker.py:45-47`). Prefer `close_position` — it already handles exit-side cost accounting (`paper_position_tracker.py:67-84`).

## BUG-04 · Rollback does not unwind tracker either
- **Severity:** High (same class as BUG-03, distinct code path)
- **Severity reason:** Failed atomic entries leave ghost legs in state, so one of the system's core safety paths produces incorrect position accounting.
- **Priority:** P0
- **Priority reason:** A broken rollback path invalidates both paper results and any attempt to harden the system for live execution.
- **Evidence:**
  - `trading_system/core/iron_condor.py:218` — normal entry calls `self.om.place_order(symbol, side, qty)` with the default `track_position=True`. Filled legs are added to the tracker.
  - `trading_system/core/iron_condor.py:67` — `_rollback_partial_entry` submits reverse orders with `track_position=False`.
- **Impact:** If an atomic entry fails after legs 1–2 fill, `self._position` is never set (strategy-level clean), but `PaperPositionTracker._positions` retains the 2 leg entries forever. Subsequent unrealised P&L marks, and `open_positions.json::tracker_positions`, show ghosts from a rolled-back entry.
- **Suggested fix:** Same as BUG-03 — use `tracker.close_position(sym, fill)` in `_rollback_partial_entry`, or flip rollback orders to `track_position=True`. Apply the chosen fix consistently to all reverse-order paths.

## BUG-05 · Rollback failure is logged, not escalated
- **Severity:** High
- **Severity reason:** A partial condor can remain open with no halt or alert, creating uncontrolled exposure in the most safety-critical failure mode.
- **Priority:** P0
- **Priority reason:** This is a precondition for any practical live-readiness work and should not be deferred behind cleanup or test polish.
- **Evidence:**
  - `trading_system/core/iron_condor.py:68-77` — when a rollback reverse order itself returns `status != "COMPLETE"`, `_rollback_partial_entry` only emits `logger.error(...)`. No halt, no alert, no quarantine of state.
  - `enter()` then returns `False` (line 231); the main loop continues and will attempt a new entry on the next cycle despite legs 1 or 2 still being open at the broker.
- **Impact:** In live execution this is catastrophic. A partial condor left open with no reverse-order guarantee represents uncontrolled directional exposure. The system currently has no mechanism to detect, alert on, or stop trading after a stuck rollback. Even in paper, the P&L books diverge from reality from that point on.
- **Suggested fix:** On rollback failure, set `risk.halted = True`, record `stop_hit_at`, emit a loud alert (see `GO_LIVE_CHECKLIST.md` item 9), and persist a "stuck legs" record in `open_positions.json` listing which symbols could not be reversed and at what qty/side. Next startup should refuse to trade until those are manually resolved or reconciled against broker positions.

## BUG-06 · `get_open_price()` silent `lp` substitution bypasses LOW-confidence downgrade
- **Severity:** Medium
- **Severity reason:** It does not always break classification, but it silently weakens confidence signalling and biases day-type interpretation.
- **Priority:** P1
- **Priority reason:** Important to fix once the core classifier itself is repaired, but it is secondary to the bigger VWAP and accounting defects.
- **Evidence:**
  - `trading_system/existing/market_data.py:136-143` — `op = float(q.get("o", 0) or q.get("lp", 0))`. When the quote payload is returned but has no/zero `o`, `lp` is used silently and `_open_price_fallback` is never populated.
  - `trading_system/existing/market_data.py:146-154` — the explicit fallback path adds the symbol to `_open_price_fallback` and logs a warning.
  - `trading_system/core/day_classifier.py:74-79` — `is_open_price_reliable()` drives the LOW-confidence downgrade off this flag.
- **Impact:** A quote missing `o` but carrying `lp` lets the classifier run at normal confidence on what is effectively a current-tick price masquerading as "open." `move_pct = (current - open_px) / open_px` becomes close to 0, biasing toward RANGING even on genuinely trending days. Combined with BUG-01, the reliability signalling is non-functional.
- **Suggested fix:** In `get_open_price()`, only accept `q["o"]` as a real open; if missing, fall through to the LTP path (which already flags `_open_price_fallback`). If the substitution is ever kept intentionally, add the symbol to `_open_price_fallback` in that branch and log.

## BUG-07 · Mid-session OAuth recovery is not implemented
- **Severity:** Medium
- **Severity reason:** It creates a real runtime fragility, but only when auth expires mid-session rather than on every normal run.
- **Priority:** P1
- **Priority reason:** Worth addressing before serious live use, but not as urgent as the currently broken paper-accounting paths.
- **Evidence:**
  - `api_helper.py:~270` — per-call OAuth-header → `jKey` fallback exists for individual quote requests.
  - `main.py:218-268` — the orchestrated re-auth loop (`oauth_reauth_attempts`) lives in startup logic only. Nothing equivalent runs inside the active trading loop.
- **Impact:** A hard token expiry during market hours has no dedicated recovery path. If both OAuth and `jKey` fall through, quote requests degrade into ordinary failures. In practice that means quote-dependent paths may start skipping entries or exits, and the session can drift into partial or inconsistent behavior without any explicit auth-recovery event.
- **Suggested fix:** Extract the re-auth loop into a callable helper and invoke it from within the main loop's exception handling when `_last_broker_error` indicates auth failure. Emit a structured alert on each re-auth attempt so silent token refreshes are observable.

## BUG-08 · Cross-instrument regime coupling: BANKNIFTY entries are gated by NIFTY classification
- **Severity:** Medium
- **Severity reason:** It produces wrong-instrument decision-making, but the system still runs; the defect is in decision ownership rather than process integrity.
- **Priority:** P1
- **Priority reason:** Important for strategy validity after the more fundamental classifier and bookkeeping issues are fixed.
- **Evidence:**
  - `trading_system/core/day_classifier.py:51-52` — the classifier uses `get_open_price(settings.NIFTY_SYMBOL)` and `get_ltp(settings.NIFTY_SPOT_KEY)` exclusively.
  - `main.py:391` — `day_class = classifier.classify()` is computed once.
  - `main.py:409-411` — same `day_class.day_type` is passed to `regime.get_regime_gate()` for both NIFTY and BANKNIFTY strategies.
- **Impact:** BANKNIFTY can trend strongly on a day NIFTY is ranging (or vice versa). Today, BANKNIFTY entries are gated on NIFTY's behavior, not its own. This is not a crash-level implementation bug, but it is a real runtime defect in signal ownership: the BANKNIFTY strategy is using the wrong instrument's regime state. Once BUG-01 is fixed and the trending branch actually fires, this coupling becomes more consequential.
- **Suggested fix:** Classify per instrument — either instantiate two `DayClassifier` objects (one per symbol) or parameterize `classify(symbol)`. Update `main.py` to compute `day_class_nifty` / `day_class_banknifty` and pass each to its own strategy's entry gate.

## BUG-09 · Test packaging — targeted pytest invocations fail
- **Severity:** Medium (infrastructure)
- **Severity reason:** It does not affect runtime trading directly, but it materially weakens verification and makes targeted testing unreliable.
- **Priority:** P1
- **Priority reason:** Needed to make bug-fixing and regression testing efficient, especially before adding more sensitive execution logic.
- **Evidence:**
  - No `pyproject.toml`, `setup.py`, or `pytest.ini` at repo root.
  - `tests/conftest.py` sets `collect_ignore` but does not modify `sys.path`.
  - `tests/test_pnl_engine.py:3` and a few other tests self-patch: `sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))`. Most tests do not.
  - `pytest tests/test_ic_strategy.py` fails at collection: `ModuleNotFoundError: No module named 'trading_system'`. The same command with `PYTHONPATH=.` succeeds.
- **Impact:** Running targeted tests fails unpredictably depending on whether the test file self-patches. Once a live order path is added, this will bite harder — you cannot run a single live-order test without an env-var workaround.
- **Suggested fix:** Add a minimal `pyproject.toml` with `[tool.pytest.ini_options] pythonpath = ["."]`, or a `pytest.ini` with the same. Then delete the per-file `sys.path.insert` hacks.

## BUG-10 · Dead-module imports in ignored test files
- **Severity:** Medium (masked by `conftest.py`, rotting)
- **Severity reason:** This is hidden technical debt in the test suite rather than a direct runtime fault, but it leaves useful coverage dead and misleadingly silent.
- **Priority:** P1
- **Priority reason:** Should be cleaned up once test execution is made reliable so the suite reflects reality again.
- **Evidence:**
  - `tests/test_paper_trading.py:11` imports `trading_system.core.strategy_a` — module does not exist.
  - `tests/test_integration.py:12` imports `trading_system.core.daily_target` — module does not exist.
  - `tests/test_position_persistence.py:13` imports `trading_system.core.strategy_a` — module does not exist.
  - `tests/conftest.py:12-39` silently ignores all three via `collect_ignore`, so failures don't surface in `pytest` runs.
- **Impact:** Tests have been dead long enough to rot. Their coverage (paper trading integration, persistence around risk/target state) is not running, and the ignore list hides that fact.
- **Suggested fix:** Decide per file — restore the missing modules if the tests contain useful coverage, or delete the tests and remove them from `collect_ignore`. Don't leave them ignored indefinitely.

## BUG-11 · `test_ic_strategy::test_ic_strategy_entry_success` MagicMock return shape
- **Severity:** Medium
- **Severity reason:** The broken test does not change production behavior, but it defeats a supposedly important happy-path check.
- **Priority:** P1
- **Priority reason:** Fixing this improves confidence in subsequent code changes, but it depends on the broader test execution path being usable.
- **Evidence:** Once BUG-10 is resolved (or `PYTHONPATH=.` is set), the test fails with:
  ```
  IC NIFTY entry aborted: leg SELL NFO|NIFTY19-MAR-2026C22150 rejected
    (status=<MagicMock...>, reason=<MagicMock...>, ltp=<MagicMock...>)
  ```
  `iron_condor.py:220` checks `order.get("status") != "COMPLETE"`. The test's `mock_om.place_order` returns a raw `MagicMock` whose `.get(...)` returns another MagicMock, not `"COMPLETE"`.
- **Impact:** The test supposedly covers the happy-path IC entry. It currently proves nothing — the strategy always aborts on leg 1 during the test.
- **Suggested fix:** Configure `mock_om.place_order.return_value = {"status": "COMPLETE", "fill_price": ..., ...}` so `order.get("status")` resolves to the expected string.

## BUG-18 · Expiry-day close not enforced (Axiom 2 violation)
- **Severity:** Medium
- **Severity reason:** Under Axiom 2, positions must be flat by the IC's expiry-day close. Current EOD logic has no expiry check, so an expired IC can persist into the next trading day. Does not crash, but leaves expired legs in strategy and tracker state.
- **Priority:** P1
- **Priority reason:** Real axiom violation, but rare in practice — the 1% harvest target is usually hit on expiry day due to theta decay, so most ICs self-close before EOD. Fix after foundational correctness bugs are resolved.
- **Evidence:**
  - `main.py:359-367` — EOD force-exit triggers only on `not next_day_is_trading`. No check on whether today is the active IC's expiry date.
  - `trading_system/core/iron_condor.py:19-34` — `IC_Position` stores leg symbols (with expiry embedded in the string) but no explicit expiry-date field; no expiry comparison exists at monitor or EOD.
  - `docs/axioms.md` Axiom 2 — "Positions may carry across weekday trading-day boundaries but must be flat ... by the IC's expiry-day close."
- **Impact:** If an IC reaches its expiry day still open (not harvested, not stopped) and the next calendar day is a trading day, current code carries it overnight. In paper mode, the tracker marks expired legs against stale or zero LTPs, distorting unrealised P&L and leaving phantom entries in `open_positions.json`. In live, the broker auto-settles with fees the engine does not capture, and reconciliation becomes impossible. Either way, an expired position persists across a day boundary — direct contradiction of Axiom 2.
- **Suggested fix:** Add an `expiry_date` (date type, not the symbol-embedded string) to `IC_Position` at entry. In the main loop's EOD block, force-exit any active strategy whose `expiry_date == today`, independent of the next-day-is-trading check. Add a test: construct an IC with `expiry_date = today`, advance clock to TRADE_END with a non-holiday next day, assert `force_exit` is called and `_position is None` afterward.

---

## Secondary Cleanup / Latent Risks

These items are real, but they are not current execution blockers for the runtime you are actually using today.

## BUG-12 · `EOW_EXIT_TIME` is dead config
- **Severity:** Low
- **Severity reason:** It is misleading but does not currently cause incorrect runtime behavior by itself.
- **Priority:** P2
- **Priority reason:** Safe to defer until after correctness and verification issues are resolved.
- **Evidence:**
  - `trading_system/config/settings.py:29` — `EOW_EXIT_TIME = "15:15"`.
  - No references anywhere else in the runtime. EOD logic in `main.py:359-367` uses only `TRADE_END` plus the next-day-is-trading check.
- **Impact:** Misleading. Readers of `settings.py` and older docs (e.g. `AGENTS.md`) may believe a "flat every Thursday" rule is wired. It is not.
- **Suggested fix:** Delete the constant. If the "flat every Thursday" rule is actually wanted, wire it into `main.py`; otherwise remove the dead setting.

## BUG-13 · `GoLiveEvaluator` instantiated but unused
- **Severity:** Low
- **Severity reason:** Dead wiring causes confusion but not incorrect trading behavior.
- **Priority:** P2
- **Priority reason:** Cleanup only; no need to spend time here before fixing real correctness problems.
- **Evidence:**
  - `main.py:34` imports `GoLiveEvaluator`.
  - `main.py:321` — `evaluator = GoLiveEvaluator()` — no further uses in the file.
- **Impact:** Dead object instantiated every startup. A reader of `main.py` assumes the class participates in go-live decisions; it does not.
- **Suggested fix:** Remove the import and instantiation until a real caller exists. Or wire it to evaluate `paper_summary.json` thresholds at end of each session.

## BUG-14 · `paper_signals.log` writer supported but never called in runtime
- **Severity:** Low
- **Severity reason:** This leaves an empty observability feature, but does not compromise trade execution or P&L correctness.
- **Priority:** P2
- **Priority reason:** Useful cleanup or enhancement later, not a blocker.
- **Evidence:**
  - `trading_system/core/trade_logger.py:75-82` — `log_signal()` is defined.
  - Only callers are `tests/test_trade_logger.py:62, 71`.
  - `trading_system/dashboard/web_dashboard.py:240` and `trading_system/dashboard/terminal_dashboard.py:32` read the file, but nothing writes it during a real session.
- **Impact:** The file will either not exist or be empty under normal operation. Dashboards display "no signals" permanently. The writer looks functional but is effectively unreachable.
- **Suggested fix:** Wire `log_signal()` into the regime/classification/gate decision points in `main.py` and `RegimeFilter` (call on each blocked/allowed decision with a structured message). Alternatively, remove the writer and dashboard readers if the signal log is not actually useful.

## BUG-15 · `RiskManager.can_enter_recovery()` never called
- **Severity:** Low (unfinished feature)
- **Severity reason:** Unused recovery logic is misleading, but since no recovery flow is advertised as active behavior, this is feature incompleteness more than a live bug.
- **Priority:** P2
- **Priority reason:** Either wire it or remove it later; it should not displace work on active-path defects.
- **Evidence:**
  - `trading_system/core/risk_manager.py:88-110` — 22-line method with 5 conditions.
  - No callers in `main.py` or elsewhere in the runtime.
- **Impact:** The recovery-mode feature described in `TECHNICAL_REFERENCE.md §4.8` is not actually available — the gating logic exists but nothing executes a recovery trade. Documentation implies a feature that doesn't ship.
- **Suggested fix:** Either wire the recovery-trade flow in `main.py` after a hard stop (and add a `RiskManager.enter_recovery()` execution path), or delete `can_enter_recovery`, `recovery_mode`, `recovery_side`, and related state to reflect what's actually implemented.

## BUG-16 · Unused `SignalEngine` methods
- **Severity:** Low
- **Severity reason:** Extra unused methods add cognitive overhead but do not break the active trading path.
- **Priority:** P2
- **Priority reason:** Pure cleanup.
- **Evidence:**
  - `trading_system/core/signal_engine.py` exposes VWAP bias, Wilder RSI, Put-Call Ratio, Max Pain, and a majority-vote consensus engine.
  - Only `compute_vwap_value()` is called from `main.py` (and even that call is currently useless — see BUG-01).
- **Impact:** Misleading surface area. Code-readers assume these are part of the active strategy. Keeping unused indicator code next to active code invites someone to wire them in later without validation.
- **Suggested fix:** Either integrate them (e.g. use PCR/Max Pain to tighten the regime gate, with a proper backtest) or delete them.

## BUG-17 · `SRManager.get_20day_high_low()` re-scans disk on every entry attempt
- **Severity:** Low (efficiency, not correctness)
- **Severity reason:** Wasteful I/O hurts efficiency, but outputs remain correct.
- **Priority:** P2
- **Priority reason:** Performance tuning should come after correctness and accounting are repaired.
- **Evidence:**
  - `trading_system/core/sr_manager.py:42-100` — on every call, globs `market_data_*`, opens up to 20 daily CSVs, computes min/max.
  - Called from `main.py:415` inside the entry loop.
- **Impact:** Each entry attempt pays disk I/O for up to 20 daily CSVs. No correctness issue, but wasteful on tight cycles.
- **Suggested fix:** Cache the result per `(instrument, date)` key. Invalidate only when `mtime` of the latest `market_data_*/raw_data/futures/` directory changes. Typical cycle cost drops from hundreds of ms to near-zero.

---

## Suggested fix order

Prioritized for pre-go-live safety, not by bug ID:

1. **BUG-01** (VWAP) — unblocks any claim about regime classification being operational.
2. **BUG-02** (force_exit logging) — unblocks reconciliation during stop-loss and EOD flatten days.
3. **BUG-03 + BUG-04** (tracker unwind on exit and rollback) — together, the paper tracker becomes trustworthy for the first time. Fixes downstream distortion in `open_positions.json` and `pnl_snapshot.json`.
4. **BUG-05** (rollback escalation) — safety blocker before live.
5. **BUG-06** (open-price substitution) — small, easy, unblocks reliability signalling.
6. **BUG-07 + BUG-08 + BUG-18** — correctness/safety layer 2: mid-session auth recovery, per-instrument classification, and expiry-day close enforcement.
7. **BUG-09 + BUG-10 + BUG-11** (test infrastructure) — required before adding live-code tests on top.
8. **BUG-12 through BUG-17** — cleanup, schedule opportunistically.

After steps 1–5, the three known gaps in `TECHNICAL_REFERENCE.md` (§2.3 step 10, §3.1) and the verification gaps in `TEST_SCENARIOS.md` scenarios 1, 11, 13, 15, 20 all resolve — those docs can then be simplified.

## Out of scope for this document

These were tracked in `bugs_for_review1.md` but are strategy/economics decisions, not code defects. They belong in a model-calibration note, not a bug list:

- `IC_HARVEST_PCT = 0.01` (1%) may be net-negative at 10 lots once real Shoonya cost stack is applied. Calibration exercise.
- Paper cost model in `paper_order_manager.py:137-146` excludes GST, exchange transaction charges, and SEBI fees. An intentional simplification; completing it is an economics decision driven by live-vs-paper reconciliation tolerance.

See the tier-2 items in the improvement discussion (and `GO_LIVE_CHECKLIST.md` item 12) for how to approach those.
