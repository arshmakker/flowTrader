# Test Coverage Gaps — Status

This document lists code paths and edge cases that were identified as gaps. Each section is marked **Addressed**, **Partially addressed**, or **Open**.

---

## 1. **Strategy LTP=0 behavior (monitor / force_exit)** — **Addressed**

| Gap | Status |
|-----|--------|
| monitor() returns None when LTP=0 | **Addressed.** `test_*_monitor_returns_none_when_ltp_zero` for A, B, C, D, E in `test_strategies.py`. |
| force_exit() with LTP=0 still exits | **Addressed.** `test_*_force_exit_still_exits_when_ltp_zero` for A, B, C, D, E. |

---

## 2. **DayClassifier — open price reliability** — **Addressed**

| Gap | Status |
|-----|--------|
| Confidence downgrade when open is LTP fallback | **Addressed.** `test_confidence_low_when_open_price_unreliable` in `test_day_classifier.py`. |

---

## 3. **PaperOrderManager — OTM option slippage** — **Addressed**

| Gap | Status |
|-----|--------|
| Wider slippage for options below SLIPPAGE_OTM_THRESHOLD | **Addressed.** `test_om_otm_option_slippage` in `test_paper_trading.py`. |

---

## 4. **Position persistence — error and edge paths** — **Addressed**

| Gap | Status |
|-----|--------|
| Corrupt state file → load() returns 0 | **Addressed.** `test_load_corrupt_json_returns_zero`. |
| Unknown strategy key in payload → skipped | **Addressed.** `test_load_unknown_strategy_key_skipped`. |
| load() with pnl_engine=None when payload has pnl_state | **Addressed.** `test_load_with_pnl_state_but_pnl_engine_none_no_crash`. |
| clear() when file missing | **Addressed.** `test_clear_when_file_missing_no_error`. |
| save() failure (e.g. disk full) | **Open.** Not simulated; exception is logged in code. |

---

## 5. **Main loop and orchestration (main.py)** — **Open**

| Gap | Status |
|-----|--------|
| Main loop logic (day reset, hard close, gates, entry, save-after-entry) | **Open.** Only smoke test (main.py compiles). No test runs the loop. |
| Exception handling in monitor/force_exit | **Open.** Not triggered by tests. |
| print_session_summary / _enrich_result | **Open.** Not unit-tested. |
| initialize_api / setup_logging | **Open.** By design (creds); logging could be tested in isolation. |

**Suggestion:** Optional “main loop in a box” test with mocks and one–two iterations.

---

## 6. **MarketData (existing/market_data.py)** — **Partially addressed**

| Gap | Status |
|-----|--------|
| get_open_price() fallback to LTP | **Addressed.** `test_get_open_price_fallback_to_ltp_and_unreliable` in `test_market_data.py`. |
| is_open_price_reliable | **Addressed.** Same file; `test_is_open_price_reliable_true_when_open_from_api`, `test_reset_daily_clears_open_price_fallback`. |
| get_nearest_expiry() | **Open.** Not tested in pytest. |
| get_ohlcv_df() / _fetch_intraday_bars | **Open.** Only in test_all_fixes (non-pytest). |

---

## 7. **DataCollector and SymbolManager** — **Open**

| Gap | Status |
|-----|--------|
| DataCollector | **Open.** No tests. |
| SymbolManager (lot size, symbol resolution) | **Open.** No tests. |

**Suggestion:** Smoke tests: SymbolManager loads NFO; DataCollector construct + start/stop.

---

## 8. **Strategy restore_state with malformed state** — **Addressed**

| Gap | Status |
|-----|--------|
| load() with one strategy state invalid (e.g. position not a dict) | **Addressed.** `test_load_malformed_strategy_state_skipped_others_restored`. |

---

## 9. **TradeLogger — key aliases and CSV edge cases** — **Partially addressed**

| Gap | Status |
|-----|--------|
| log_trade with strategy-style keys only → CSV columns | **Addressed.** `test_logger_strategy_key_aliases_in_csv`. |
| _read_last_counter with no numeric suffix | **Open.** Not tested. |
| CSV with special characters (commas, newlines) | **Open.** Not tested. |

---

## 10. **GoLiveEvaluator — edge cases** — **Addressed**

| Gap | Status |
|-----|--------|
| evaluate() with trades_df missing key columns | **Addressed.** `test_evaluator_missing_columns_no_crash`. |
| evaluate() with NaN in time_exit / regime_entry | **Addressed.** `test_evaluator_nan_in_columns_no_crash`. |

---

## 11. **RegimeFilter — get_vix failure** — **Addressed**

| Gap | Status |
|-----|--------|
| get_quotes returns None or invalid lp | **Addressed.** `test_get_vix_returns_zero_when_api_returns_none`, `test_get_vix_returns_zero_when_lp_invalid`. |

---

## 12. **Strategy D — enter with real option chain** — **Open**

| Gap | Status |
|-----|--------|
| Strategy D enter() with realistic chain DataFrame | **Open.** Lifecycle tests use mocks; optional. |

---

## 13. **test_all_fixes.py not in pytest** — **Open**

| Gap | Status |
|-----|--------|
| test_all_fixes.py runs as script, sys.exit(1) breaks collection | **Open.** Run manually: `python tests/test_all_fixes.py`. Contains one known failure (StratB stop P&L). |

---

## Summary

| Section | Status |
|---------|--------|
| 1 Strategy LTP=0 | Addressed |
| 2 DayClassifier | Addressed |
| 3 PaperOrderManager OTM | Addressed |
| 4 Position persistence | Addressed (save failure open) |
| 5 Main loop | Open |
| 6 MarketData | Partially addressed (expiry, OHLCV open) |
| 7 DataCollector / SymbolManager | Open |
| 8 Malformed restore_state | Addressed |
| 9 TradeLogger | Partially addressed (counter, special chars open) |
| 10 GoLiveEvaluator | Addressed |
| 11 RegimeFilter API | Addressed |
| 12 Strategy D real chain | Open |
| 13 test_all_fixes | Open |

**Remaining open gaps:** Main loop (main.py) not executed by tests; DataCollector and SymbolManager untested; MarketData get_nearest_expiry and get_ohlcv_df; TradeLogger counter edge and special chars; Strategy D real chain (optional); test_all_fixes not in pytest; save() write failure not simulated.
