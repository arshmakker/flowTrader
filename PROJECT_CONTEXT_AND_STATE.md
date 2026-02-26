## Project context

- **Name**: `regimetrader`
- **Goal**: Multi-strategy trading system with regime detection that automatically routes to appropriate strategies based on market conditions. **Two-fork (this branch):** Only two regimes — **TRENDING** and **SIDEWAYS**. TRENDING → Convex Backspread; SIDEWAYS → Iron Condor. **Convex and Iron Condor can run and hold positions at the same time** (no mutual exclusion between them). Trend Following Futures and Neutral Calendar are disabled in the main execution path. Includes market data collection, position tracking, and backtesting utilities.
- **Market data layout**: `market_data_YYYYMMDD/raw_data/{futures,options,...}` with per-underlying option CSVs.
- **Backtest**: `backtest_iron_condor.py` (Iron Condor), `backtest_trend_following.py` (Trend Futures with ATR profit target and hybrid trailing) run against stored tick data.

## Current state (2026-02-19)

**Two-fork branch:** Regime detector, strategy runner, main loop, position tracker (Convex exit), backtests, and docs updated for TRENDING/SIDEWAYS only; Trend Futures and Calendar disabled. **Convex and Iron Condor can run and hold positions at the same time** (mutual exclusion relaxed for these two; Calendar/Trend remain exclusive).

### Market hours: 3:30 PM IST (2026-02-06)

All market open/close checks use **India Standard Time (IST, Asia/Kolkata)** so behaviour is correct regardless of server timezone.

- **strategy_runner.py**: `zoneinfo.ZoneInfo("Asia/Kolkata")` (fallback if Python &lt; 3.9). `get_now_ist()` returns current time in IST. `is_market_closed_ist()` true when weekday and IST ≥ 15:30. `is_market_hours()` uses 9:15–15:30 IST. `get_weekly_expiry(date=None)` uses `get_now_ist()` so "after 3:30" is in IST.
- **main.py**: Main loop stops when `is_market_closed_ist()`. Trend futures "exit before close" uses `get_now_ist()` for 15:30 and exit window (IST).
- **web_dashboard.py**: `can_start_system()` and `is_market_hours()` use `get_now_ist` and strategy_runner's `is_market_hours` (IST).

### Regime: two-fork only — TRENDING and SIDEWAYS (2026-01-29)

**`regime_detector.detect_regime()`** returns only two regimes:

1. **TRENDING**: Price structure (ADX ≥ TREND_ADX_MIN, ATR% ≥ TREND_ATR_PCT_MIN, EMA directional bias). When trend conditions pass, regime is TRENDING (with persistence).
2. **SIDEWAYS**: Everything else (no VIX-based CONVEX/INCOME/NEUTRAL). When not trending, regime is SIDEWAYS (with persistence).

**Routing:** TRENDING → Convex Backspread only; SIDEWAYS → Iron Condor only. Trend Following Futures and Neutral Calendar are **disabled** in the two-fork branch (no entry from runner, no position monitoring in main).

### Convex: TRENDING regime only (two-fork, 2026-01-29)

In the two-fork model, **Convex Backspread** is entered only when regime is **TRENDING** (trend conditions: ADX, ATR%, EMA). No VIX-based CONVEX/INCOME routing.

- **strategy_runner**: TRENDING → `_run_convex_backspread_strategy`; SIDEWAYS → `_run_iron_condor_strategy_internal`. India VIX still fetched for logging/daily_metrics.
- **regime_detector**: `classify_regime_from_indicators` returns only `TRENDING` or `SIDEWAYS` (trend check only; VIX/IV no longer used for routing).
- **get_recent_candles lookback**: Production calls pass `lookback_days=20` for rolling_avg_range where needed. Regime detector uses `MIN_CANDLES_FOR_ROLLING = 15`.

### Iron Condor: SIDEWAYS regime only (two-fork, 2026-01-29)

In the two-fork model, **Iron Condor** is entered only when regime is **SIDEWAYS** (non-trending). No separate INCOME/CONVEX/NEUTRAL; SIDEWAYS covers all non-trending conditions.

### Ctrl+C summary (2026-02-19)

On **KeyboardInterrupt** (Ctrl+C), the main loop now prints the same **end-of-day style summary** (today’s closed trades, total P&L, win rate, by strategy, by exit reason) via `generate_daily_trade_summary(logger)`, then lists **open positions** (strategy, trade_id, entry_time) from `active_positions.json`. Cleanup (stop collector, etc.) runs after the summary.

### Convex + Iron Condor coexistence (2026-02-19)

- **Both can run at the same time**: Convex and Iron Condor can each have open positions simultaneously. `strategies/strategy_exclusion.py`: `can_enter_strategy(IRON_CONDOR)` returns True when active strategy is NONE or CONVEX; `can_enter_strategy(CONVEX)` returns True when active is NONE, IRON_CONDOR, or CONVEX. When both have positions, `get_active_strategy_type()` returns CONVEX so both can still add. Calendar and Trend remain mutually exclusive with others.
- **Iron Condor eligibility relaxed**: `strategies/iron_condor/config.py` — `ADX_THRESHOLD` raised from 22 to **45** so Iron Condor can pass eligibility when ADX is in the 22–45 range (e.g. moderate-trend sessions where Convex also runs). IC still requires IV 50–100%, days 3–30, NIFTY WEEKLY/MONTHLY, no major event.

### Regime Detection Backtest (2026-01-30)

**`backtest_regime_detection.py`** uses **production** regime logic (`classify_regime_from_indicators`). Two-fork: only **TRENDING** and **SIDEWAYS**. Report includes `trending_triggered`, `sideways_triggered`, regime distribution, transitions, indicator stats.

**Run**: `python3 backtest_regime_detection.py [start_YYYYMMDD] [end_YYYYMMDD] [iv_csv_path]` (default 20251222–20260116). Report: `backtest_regime_report_YYYYMMDD_HHMMSS.json`.

**Convex backspread backtest (two-fork)** (2026-02-08): **`backtest_convex_backspread.py`** uses production regime (TRENDING/SIDEWAYS). Convex **entries** when regime is **TRENDING** only. Exit: regime flip to SIDEWAYS (after confirmation), time 40%, ATR, re-compression, max loss, TSL. **Backtest-only relaxation**: when the option chain has only one CE strike, a synthetic OTM strike is added. Report includes `avg_pnl` and **entry_diagnostics** (eligible_bars, chain_empty, proposal_none).

### IV for Backtest and Daily Metrics Persistence (2026-01-30)

**Backtest IV when no IV data**:
- Trend backtest (`backtest_trend_following.py`) can test all four regimes (CONVEX, INCOME, TREND, NEUTRAL) even without an IV CSV.
- **Priority**: (1) If `iv_csv_path` is provided → use CSV (date → iv_percentile). (2) Else for each date, if `market_data_YYYYMMDD/daily_metrics.json` exists and has `iv_percentile` → use it. (3) Else **compute IV proxy** from that day’s 15m candles: daily vol = ATR(14)/close, then percentile rank over previous days’ daily vols (same formula as production; &lt;20 samples → 50).
- So CONVEX/INCOME can trigger in backtest using stored IV (future) or a volatility-based proxy (now).

**Persist IV when calculated (production)**:
- When regime is detected in production, IV and regime inputs are written to **daily data** for future backtest use.
- `strategy_runner.save_daily_metrics(metrics)` writes to `market_data_YYYYMMDD/daily_metrics.json` (merge with existing). Called after regime detection with: `date`, `regime`, `iv_percentile`, `adx_14`, `atr_percentile` (only keys that are not None).
- So as and when IV is calculated (e.g. from option chain + `market_data_iv` history), it is stored in that day’s folder; backtests on that date will prefer this column over the proxy.

### Trend Backtest: Profit Target vs Earlier Breakeven Comparison (2026-01-30)

**Implemented**: `backtest_trend_following.py` runs **three scenarios** when executed (`main()` → `run_comparison()`):
1. **Baseline**: 0.1× ATR profit target, 0.5× ATR breakeven threshold (config default).
2. **Lower ATR target**: 0.05× target, 0.5× breakeven — books profit sooner.
3. **Earlier breakeven**: 0.1× target, 0.25× breakeven — moves stop to breakeven sooner.

**Backtester overrides**: `TrendFollowingBacktester(..., profit_target_atr_multiplier=None, hybrid_breakeven_threshold_atr=None, scenario_name=None, max_position_size=None, max_risk_pct_of_capital=None, force_max_lots=False)`. `None` uses config; `force_max_lots=True` always takes max_position_size lots (ignores risk limit for comparison).

**Comparison run (20251222–20260116)** — four scenarios (Total Qty = sum of quantity across trades; lot 65 → 13,650 ≈ 42×5 lots):
| Scenario | Trades | Wins | Net P&L (₹) | Gross P&L (₹) | Charges (₹) | Total Qty | PROFIT_TARGET_ATR |
|----------|--------|------|-------------|---------------|-------------|-----------|-------------------|
| Baseline (0.1× target, 0.5× breakeven) | 43 | 33 | -8,629 | 81,282 | 89,912 | 13,975 | 39 |
| Lower ATR target (0.05× target, 0.5× breakeven) | 42 | 31 | **-6,513** | 81,282 | 87,795 | 13,650 | 39 |
| Earlier breakeven (0.1× target, 0.25× breakeven) | 43 | 33 | -8,629 | 81,282 | 89,912 | 13,975 | 39 |
| 0.05× ATR, 5 lots (0.05× target, force 5 lots) | 42 | 31 | **-6,513** | 81,282 | 87,795 | 13,650 | 39 |

**Best net P&L**: Lower ATR target (0.05×) and 0.05× ATR 5 lots — same outcome; in this period risk-based sizing already allowed 5 lots, so the “force 5 lots” scenario matched “Lower ATR target”.

**Higher ATR target sweep (0.15×, 0.20×, 0.25×)** — same period:
| Scenario | Trades | Wins | Net P&L (₹) | Gross P&L (₹) | Charges (₹) | PROFIT_TARGET_ATR | STOP_LOSS_HIT |
|----------|--------|------|-------------|---------------|-------------|-------------------|---------------|
| 0.15× ATR target | 42 | 34 | -6,543 | 81,282 | 87,826 | 36 | 5 |
| 0.20× ATR target | 41 | 35 | -4,425 | 81,282 | 85,707 | 35 | 5 |
| 0.25× ATR target | 40 | 33 | **-2,385** | 81,282 | 83,667 | 33 | 6 |

Higher target → fewer trades, lower total charges, same gross; best net in sweep: **0.25× ATR target** (−₹2,385). Still negative; moving target further (e.g. 0.3×) or reducing trade count could reach profitability.

**Production trend config (aligned with profitable backtest, 2026-01-29)**

- **Config** (`strategies/trend/config.py`): **PROFIT_TARGET_ATR_MULTIPLIER = 0.35** — exit when unrealized profit ≥ 0.35× ATR (backtest with 10 lots + ADX ≥ 35 was profitable). **MIN_ADX_TREND_ENTRY = 35** — trend entry requires ADX ≥ 35 (non–high-vol); when ATR% ≥ 90, **HIGH_VOL_MIN_ADX = 40** still applies. PnL lock at ₹300 and phases 1–6 unchanged.

**Trend trailing: PnL terms (2026-02-09)**

- **Production** (`main.py`) and **backtest** (`backtest_trend_following.py`): Hybrid trailing phase and breakeven lock use **current P&L in ₹** (INR). Config: `HYBRID_BREAKEVEN_THRESHOLD_INR = 300`, then 1000, 2000, 3000, 5000, 7500 for phases 2–6+. When PnL >= ₹300, stop is moved to **lock at least ₹300 profit** (`LOCK_PROFIT_MIN_INR = 300`): LONG stop >= entry + 300/qty, SHORT stop <= entry - 300/qty (not just breakeven). **Never relax once in profit**: once the stop has moved beyond entry (profit locked), the stop is never set back to a level that would lock less than ₹300 — i.e. you never give back the first lock. Legacy ATR constants remain in config for optional backtest override.

**0.5× ATR target — comparison sweep (2026-01-30)**

- **Comparison runs**: `run_comparison()` still uses explicit scenarios (e.g. 0.1×, 0.05×, 0.25× ATR targets). Backtest showed 0.5× was the only net-profitable target in the sweep when a fixed target was used.
- **Backtest profitability (0.5×, 20251222–20260116)**:
  - **Win percentage**: 72.2% (26 winning trades / 36 total)
  - **Total return**: 0.16% on capital
  - **Net P&L**: ₹1,579.26 (gross ₹76,830, charges ₹75,251)
- **ATR percentile**: Backtest uses a fixed `atr_percentile=75` and filter ≥ 50; all entries pass. To see which ATR percentile range would have been profitable would require per-bar ATR percentile from history and P&L bucketed by percentile (not implemented).

Report: `backtest_trend_comparison_YYYYMMDD_HHMMSS.json` (summaries only). Single-run reports: `backtest_trend_report_YYYYMMDD_HHMMSS.json`.

### Trend Strategy: PnL Lock ₹300, No Fixed Profit Target, Phases 5/6 (2026-01-29)

**Config** (`strategies/trend/config.py`):
- **HYBRID_MIN_PNL_LOCK_INR = 300** — Move stop to breakeven as soon as P&L ≥ ₹300 (in addition to phase-based breakeven).
- **PROFIT_TARGET_ATR_MULTIPLIER = 0.35** — Exit when profit ≥ 0.35× ATR (production aligned with profitable backtest).
- **MIN_ADX_TREND_ENTRY = 35** — Trend entry requires ADX ≥ 35 (non–high-vol); high-vol (ATR% ≥ 90) uses HIGH_VOL_MIN_ADX = 40.
- **Phases 5–6**: `HYBRID_PHASE5_THRESHOLD_ATR = 3.0`, `HYBRID_PHASE5_MULTIPLIER = 0.5`; `HYBRID_PHASE6_THRESHOLD_ATR = 4.0`, `HYBRID_PHASE6_MULTIPLIER = 0.25` (incremental tightening beyond 2.5× ATR).

**Live** (`main.py`): Breakeven lock when `trail_phase` is in profit phases **or** `current_pnl >= HYBRID_MIN_PNL_LOCK_INR`. PHASE5/PHASE6 applied; ATR profit-target exit runs only when `PROFIT_TARGET_ATR_MULTIPLIER is not None`.

**Backtest** (`backtest_trend_following.py`): Imports and uses PHASE5/PHASE6 and `HYBRID_MIN_PNL_LOCK_INR`. Profit-target exit only when `_profit_target_atr is not None` (supports config `None` = trailing only). Breakeven lock uses `unrealized_pnl_points > 0 or current_pnl >= HYBRID_MIN_PNL_LOCK_INR`.

### Iron Condor: Trailing PnL Lock (INCOME regime) (2026-01-29)

**Config** (`strategies/iron_condor/exit_rules.py`):
- **MIN_PNL_LOCK_INR = 300** — First lock when PnL ≥ ₹300.
- **PNL_TRAIL_DISTANCE_INR = 200** — Lock trails at (current_pnl − 200); exit when PnL &lt; lock.

**Logic**: Once PnL ≥ ₹300, set locked floor = 300. Thereafter lock = max(lock, current_pnl − 200). Exit when current_pnl &lt; profit_locked_inr (trailing stop hit). Lock is stored per position as `profit_locked_inr` and persisted in `active_positions.json`.

**Live** (`main.py`): For positions with `book == 'INCOME'`, after calculating current PnL we call `position_tracker.update_trailing_lock_and_check(position, current_pnl)`. If it returns True, close with reason `trailing_stop_pnl`. Check runs before profit target, calendar, and convex exits.

**Position tracker** (`strategies/iron_condor/position_tracker.py`): New options positions get `profit_locked_inr: 0`. `update_trailing_lock_and_check(position, current_pnl)` updates lock in place, saves when lock changes, and returns True when PnL drops below lock.

### Trend Strategy: Loss-Control Circuit Breakers & Regime/EMA (2026-02-02)

**Config** (`strategies/trend/config.py`):
- **MAX_INTRADAY_LOSS_INR = 15000** — Exit if unrealized loss exceeds ₹15k (circuit breaker).
- **MAX_TIME_IN_LOSS_MINUTES = 150** — Exit if position has been in loss for 2.5 hours.
- **REGIME_CHANGE_CONFIRMATION_CHECKS = 2** — Require N consecutive regime ≠ TREND_CONTINUATION before REGIME_CHANGE exit (reduces whipsaw).
- **EMA_BREAK_CONFIRMATION_CHECKS_WHEN_IN_LOSS = 1** — When in loss, require only 1 consecutive EMA break (faster exit on trend flip); in profit still uses EMA_BREAK_CONFIRMATION_CHECKS (2).

**Live** (`main.py`):
- **MAX_LOSS_CAP**: If `current_pnl <= -MAX_INTRADAY_LOSS_INR`, exit with reason `MAX_LOSS_CAP`.
- **TIME_IN_LOSS**: If `current_pnl < 0` and `position_age_minutes >= MAX_TIME_IN_LOSS_MINUTES`, exit with reason `TIME_IN_LOSS`.
- **Regime change**: Per-position `regime_change_count` incremented when regime ≠ TREND_CONTINUATION, reset when regime is TREND_CONTINUATION. Exit with `REGIME_CHANGE` only when `regime_change_count >= REGIME_CHANGE_CONFIRMATION_CHECKS`.
- **Regime-change losses (2026-02-06)**: (1) **Trend**: `IGNORE_REGIME_CHANGE_WHEN_IN_PROFIT = True` — do not exit on regime change when `current_pnl > 0`; let trailing stop or other exits handle. Reduces crystallized losses when regime flickers in profit. (2) **Convex**: Regime-change exit is **skipped once `convex_tsl_active` is True**; TSL or CONVEX_MAX_LOSS handle exit. Same logic in `backtest_trend_following.py` for parity.
- **EMA break**: When `current_pnl < 0`, use `ema_required_checks = EMA_BREAK_CONFIRMATION_CHECKS_WHEN_IN_LOSS` (1); otherwise use `EMA_BREAK_CONFIRMATION_CHECKS` (2). Status log shows `EMA_Break_Count=X/Y` with Y = 1 or 2 depending on PnL.

### Parity: Production vs Backtest (Trend) (2026-02-02, sanity check 2026-02-03)

**`PARITY_TREND_BACKTEST_VS_PRODUCTION.md`** (sanity check) compares backtest and production item-by-item: entry (regime, ADX, high-vol ADX, ATR%, EMA, cooldown, max trades/day), exit order and rules, hybrid trailing phases, breakeven lock, position sizing. **Breakeven lock aligned (2026-02-03):** Production previously locked BE when in phase 2 (profit ≥ 0.5× ATR). It now matches backtest: lock only when **profit ≥ 1× ATR** (HYBRID_PHASE2_THRESHOLD_ATR) **or PnL ≥ ₹300**. Re-run the parity doc after any trend entry/exit or config change.

### Trend Backtest: Exit Logic (ATR Profit Target + Hybrid Trail)

**Logic** (aligned with live `main.py`):
- **ATR profit target** (optional): If `PROFIT_TARGET_ATR_MULTIPLIER` is not None, exit when unrealized profit in points ≥ multiplier × ATR (`PROFIT_TARGET_ATR`). Config is **None** (trailing only).
- **Hybrid trailing stop**: Phases 1–6 (breakeven, 1×, 2×, 2.5×, 3×, 4× ATR thresholds with 2×, 1.5×, 1×, 0.75×, 0.5×, 0.25× trail). Breakeven also when P&L ≥ ₹300.

### Backtest: Transaction Charges (Futures) (2026-01-30)

**Implemented**: Trend backtest now deducts brokerage/transaction charges from each trade P&L (backtest only). Per your brokerage: **Brokerage** min(0.03%, ₹5) per order; **STT/CTT** 0.02% on sell; **Transaction charges** 0.00173%; **SEBI** ₹10/crore; **GST** 18% on (brokerage + SEBI + transaction charges); **IPFT** ₹0.10 per lakh turnover. Applied per round-trip (entry + exit + IPFT). Report shows **Total P&L (net of charges)**, **Gross P&L**, and **Total charges**; each trade has `pnl` (net), `pnl_gross`, and `charges`.

### NIFTY Lot Size from NFO (2026-01-30)

**Change**: NIFTY futures quantity per lot is no longer hardcoded. It is read from `symbols/NFO.csv` (first row with Symbol=NIFTY, Instrument=FUTIDX → LotSize). **Backtest**: `backtest_trend_following.py` and `backtest_trailing_stop_comparison.py` use `get_nifty_lot_size_from_nfo()` (fallback 50 if file missing). **Live**: `strategies/trend/trend_follow_futures.py` uses lot_size from `futures_info` (populated by `symbol_manager.get_index_futures_all_expiries('NIFTY')`, which reads NFO.csv); if missing, tries symbol_manager contracts; else fallback 50.

### Trend Strategy: Initial Stop 2.5× ATR (2026-01-30)

**Change**: `INITIAL_STOP_LOSS_ATR_MULTIPLIER` reduced from 3.0 to **2.5** in `strategies/trend/config.py`. With ATR ~260 and lot size 65, 3× ATR gave risk per lot ~₹51k (over 5% cap) so position size was 0 on high-vol days. 2.5× keeps risk per lot under ₹50k so 1 lot is allowed while keeping the 5% capital rule. Trade-off: slightly closer stop, possible more stop-outs; 2.5× ATR remains reasonable for trend following.

### Regime Confirmation: 3 Consecutive Detections (2026-01-30)

**Change**: In `regime/regime_detector.py`, the default `confirmation_count` was increased from **2** to **3**. The reported regime now flips only after the new regime is detected **3 consecutive times** (reduces whipsaw / flickering regime exits).

### Logging: logs directory and startup path (2026-01-29)

**main.py** `setup_logging()`: Ensures `logs/` exists with `os.makedirs(log_dir, exist_ok=True)`, uses a single `log_path` for the daily file `logs/trading_system_YYYYMMDD.log`, and logs one line at startup: `Log file: <abs path>` so the file is created immediately and the path is visible in console and in the log file.

### Convex: PnL formula and TSL-only exit (2026-02-05 / 2026-02-06)

**PnL fix** (`strategies/iron_condor/position_tracker.py`): For Convex backspread, `current_value` already equals total P&L (entry credit + mark-to-market). The formula was incorrectly adding `entry_credit` again; it now uses `pnl = current_value` for Convex (Iron Condor unchanged: `pnl = entry_credit - current_value`).

**Historical correction (2026-02-05)**: Three Convex trades closed before the PnL fix had inflated `final_pnl` (double-counted entry credit). Corrected in `active_positions.json` and `performance_by_regime.json`: trade 2026-02-05T12:42:43 → 14.63 (was 4293.25), 2026-02-05T12:48:16 → -6.5 (was 2515.50), 2026-02-05T12:53:01 → 42.25 (was 2640.62).

**TSL only, no hardcoded profit targets (2026-02-06)**: Convex and Iron Condor exit by **trailing stop only**; no fixed profit target. (1) `strategy_runner` no longer sets `profit_target_inr` for Convex. (2) Iron Condor proposal has `profit_target_margin: None`. (3) `check_profit_target()` always returns False. (4) Main loop logs "TSL only, no exit" for options positions. Convex uses MTM-based TSL (activate at +20% or 25% time; trail 35%/25% from peak; max loss -30%). Iron Condor uses trailing PnL lock (MIN_PNL_LOCK_INR ₹300, PNL_TRAIL_DISTANCE_INR ₹200). **Backtests**: `backtest_iron_condor.py` uses TSL only (trailing PnL lock ₹300/₹200). **Convex backtest aligned with production (2026-02-08)**: `backtest_convex_backspread.py` now uses the same exit logic as `check_convex_exit_conditions`: regime change (3 confirmations, skipped when TSL active), time >40%, no ATR expansion, re-compression, CONVEX_MAX_LOSS (-30%), Convex TSL (activate +20% MTM or 25% time, trail 35%/25% from peak). MTM estimated via same simplified formula as _close_position. Trend backtest default remains trailing only.

**Convex regime-change confirmation (2026-02-05)**: To avoid exiting on brief regime flicker, Convex now requires **CONVEX_REGIME_CHANGE_CONFIRMATION_CHECKS = 3** consecutive monitoring cycles where regime ≠ CONVEX before exiting with REGIME_CHANGED. Per-position `convex_regime_change_count` is incremented when regime ≠ CONVEX and reset to 0 when regime is CONVEX; position is persisted so the count survives across runs. Other Convex exit conditions (time 40%, no ATR expansion, re-compression) still fire immediately.

**Convex exit on regime change from entry (2026-02-05)**: Convex was exiting when regime ≠ CONVEX, so positions opened as **NEUTRAL fallback** (regime NEUTRAL) were closed after 3 checks because NEUTRAL ≠ CONVEX. Now: (1) **Actual regime at entry** is stored: `_run_convex_backspread_strategy(..., regime='CONVEX')` when called from CONVEX regime, and `regime='NEUTRAL'` when called from NEUTRAL fallback; `trade_proposal['regime_at_entry']` is set accordingly. (2) **Exit condition** in `check_convex_exit_conditions` is **current_regime != regime_at_entry** (with same 3-step confirmation and reset when current_regime == regime_at_entry). So Convex opened in NEUTRAL only exits when regime actually leaves NEUTRAL (e.g. to TREND or INCOME), not merely because it is not CONVEX.

**Convex trailing stop loss (MTM-based) (2026-02-05)**: In `check_convex_exit_conditions` (and `add_position` for Convex), Convex-only state: `convex_tsl_active` (bool, default False), `convex_peak_mtm` (float, init at entry MTM). **Activation**: TSL activates when mtm ≥ +20% of entry premium (abs(entry_credit)) OR time_elapsed_pct ≥ 25%. **Peak**: Once active, `convex_peak_mtm = max(convex_peak_mtm, current_mtm)` each evaluation. **Trailing exit**: Base drawdown 35% from peak; tighten to 25% when time_elapsed_pct > 40% or ATR percentile < 30; exit when `current_mtm <= peak * (1 - trailing_pct)` → reason `CONVEX_TSL_HIT`. **Absolute protection**: Exit when `current_mtm <= -30%` of entry premium → reason `CONVEX_MAX_LOSS` (evaluated regardless of TSL state). Main passes `current_pnl` as `current_mtm`. Exit reasons surface as `convex_exit_CONVEX_TSL_HIT` and `convex_exit_CONVEX_MAX_LOSS`.

### Contract Rollover: Log Once per Process (2026-01-30)

**Change**: In `technical_indicators.py`, "Contract rollover detected: …" is now logged at most once per (old_contract, new_contract) per process. A module-level set `_logged_rollovers` tracks which rollover pairs have been logged; subsequent 15m data loads still apply the same adjustment but do not repeat the INFO message (avoids spam every few minutes).

### Trend Strategy: No-Trade Reason Logging (2026-01-30)

**Implemented**: When the trend strategy returns no trade (NO_VALID_TRADE), the exact reason is now logged at INFO level so logs show why. Reason codes: `REGIME_NOT_TREND`, `SPOT_OR_ATR_INVALID`, `API_OR_SYMBOL_MANAGER_MISSING`, `NO_NIFTY_FUTURES`, `ALL_CONTRACTS_NEAR_EXPIRY`, `NO_FUTURES_QUOTE`, `FUTURES_SELECT_ERROR`, `FUTURES_PRICE_INVALID`, `NO_EMA_STRUCTURE`, `DIRECTION_NONE` (15m EMA not aligned), `POSITION_SIZE_ZERO`, `EXCEPTION`. Search logs for `Trend strategy no trade: reason=` to see the cause.

### Trend Strategy: Entry Circuit Breakers — Cooldown, High-Vol ADX, Max Trades/Day (2026-02-03)

**Config** (`strategies/trend/config.py`):
- **REENTRY_COOLDOWN_MINUTES = 30** — After a trend position is closed with STOP_LOSS_HIT, block new trend entries for 30 minutes.
- **HIGH_VOL_ATR_PERCENTILE_THRESHOLD = 90** — When ATR percentile ≥ this, require stronger trend (ADX ≥ HIGH_VOL_MIN_ADX).
- **HIGH_VOL_MIN_ADX = 40** — On high-vol days (ATR% ≥ 90), require ADX ≥ 40 to allow trend entry (else ADX ≥ 30).
- **MAX_TREND_TRADES_PER_DAY = 3** — Maximum trend entries per calendar day (circuit breaker).

**Backtest** (`backtest_trend_following.py`):
- After STOP_LOSS_HIT exit, set `_cooldown_until = exit_timestamp + REENTRY_COOLDOWN_MINUTES`; no new entry until timestamp ≥ cooldown_until.
- Entry: `check_entry_conditions` uses min_adx = 40 when atr_percentile ≥ 90, else 30.
- Per-day counter `_trades_entered_today` reset each date; entry allowed only if `_trades_entered_today < MAX_TREND_TRADES_PER_DAY`; incremented on each new position.
- Single-day 1-lot 20260203: **3 trades** (capped by max/day), all STOP_LOSS_HIT; Net P&L **₹-15,143** (gross ₹-12,363, charges ₹2,780). Without cap there were 7 trades; cap reduces churn.

**Production**:
- **`strategies/trend/entry_state.py`**: `can_enter_trend(now)` → (allowed, reason); `record_stop_loss_exit(exit_time)`; `record_trend_entry(entry_date)`. State in **`trend_state.json`** (cooldown_until_iso, trades_entered_today, last_trade_date); file in project root, listed in .gitignore.
- **`strategy_runner._run_trend_follow_strategy`**: At start, if not `can_enter_trend(now)` → log and return None. After `position_tracker.add_position(trade_proposal)` call `record_trend_entry(now.date())`.
- **`main.py`**: When closing a trend (futures) position with `exit_reason == 'STOP_LOSS_HIT'`, after `position_tracker.close_position(...)` call `record_stop_loss_exit(datetime.now())`.
- **`strategies/trend/trend_follow_futures.generate_trend_follow_trade`**: After regime check, ADX filter: if atr_percentile ≥ 90 require adx ≥ 40, else adx ≥ 30; else return None with reason ADX_TOO_LOW.

### Trend Strategy: At Least 1 Lot When Regime Allows (2026-02-03)

**Change**: When the regime allows a trend trade (TREND_FOLLOW_FUTURE) and all other checks pass (EMA structure, direction, etc.), but risk-based position sizing returns **0 lots** (e.g. high ATR so risk per lot &gt; max risk per trade), the system no longer skips the trade. It now takes **at least 1 lot** for that trade and **ignores the max-risk cap** for that single trade.

**Live** (`strategies/trend/trend_follow_futures.py` → `generate_trend_follow_trade()`): After `calculate_position_size()`, if `position_info['quantity'] == 0`, we override to 1 lot: set `lots=1`, `quantity=lot_size`, and recompute `risk_amount` for 1 lot (lot_size × stop_loss_atr). A log line records: *"Trend strategy: regime allowed trade but risk limits gave 0 lots; taking 1 lot (ignoring max risk). Risk for this trade: ₹X"*. Stop loss and rest of the trade are unchanged.

**Backtest** (`backtest_trend_following.py`): Same logic: after `calculate_position_size()`, if `position_info['quantity'] == 0` we override to 1 lot (lots=1, quantity=lot_size, recompute risk and stop_loss_price). Log: *"Backtest: regime allowed trade but risk gave 0 lots; taking 1 lot (ignoring max risk)."* Run single-day 1-lot PnL: `python3 backtest_trend_following.py --date 20260203 --force-1-lot`.

**Trailing-stop breakeven lock (backtest aligned with production)**: Backtest now uses the same `lock_be` condition as main.py: lock breakeven only when **Phase 2+** (unrealized profit in points ≥ 1× ATR) or PnL ≥ ₹300. Previously the backtest locked on any profit (`unrealized_pnl_points > 0`), causing more whipsaw exits at breakeven.

**Stop loss as limit order (backtest)**: Production places a **limit order** at the stop price, not a market order. Backtest now matches this: stop is "hit" when the bar's **low** (LONG) or **high** (SHORT) trades at or through the stop level (`bar_low <= current_stop` / `bar_high >= current_stop`). Exit price for STOP_LOSS_HIT is the **stop price** (limit filled at limit), not the bar close. When bar low/high are not provided, fallback remains close-based (market-order semantics).

### Trend Strategy: ATR Profit Target & Tighter Trail (2026-01-29)

**Implemented**:
1. **ATR profit target (#2)** – Exit when unrealized profit in points ≥ `PROFIT_TARGET_ATR_MULTIPLIER × ATR` (0.25× ATR). Books gains proactively instead of relying only on trailing stop and regime change. Config: `strategies/trend/config.py` → `PROFIT_TARGET_ATR_MULTIPLIER = 0.25`. Exit reason logged as `PROFIT_TARGET_ATR`.
2. **Tighter trail in very large profit (#4)** – Added Phase 5: when profit ≥ 2.5× ATR, trailing stop uses 0.75× ATR (Phase 4 remains 1× ATR at 2× ATR profit). Config: `HYBRID_PHASE4_THRESHOLD_ATR = 2.5`, `HYBRID_PHASE4_MULTIPLIER = 0.75`. Phase names: `PHASE4_VERY_TIGHT`, `PHASE5_VERY_LARGE_PROFIT`.

**Context**: On 2026-01-29 a SHORT trend trade reached ~₹6,467 profit (~0.39× ATR) then reversed; regime-change exit later closed at -₹1,930. A 0.5× ATR profit target would have locked ~₹8,231 had price reached it; a 0.25× target would have locked ~₹4,115 at peak. Regime-change exit already exits at current P&L (#1); no code change for that.

### Live Trading Status
- **Active strategies**: Convex-only live (Iron Condor disabled). Convex Backspread entries via `place_convex_trade()` in strategy_runner; exits via `close_convex_position()` for convex-exit conditions and EOD; **profit-target exit** now also calls `close_convex_position()` before updating tracker (fixed 2026-02-26).
- **Live trades analysis**: See `docs/LIVE_TRADES_ANALYSIS.md` for full entry/exit flow, timing, state persistence, and pre-live checklist.
- **Live trades gaps and fixes**: See `docs/LIVE_TRADES_GAPS_AND_FIXES.md` for tracked gaps (partial fills, position cap, daily loss limit, etc.) and suggested fix order.
- **Partial-fill cleanup**: On Convex entry partial completion (e.g. long filled, short not), we close filled legs via offsetting MKT and append a review entry to `partial_fill_reviews.json` for every such event so you can review the situation after each cleanup.
- **Previous**: Trend Following Futures (TREND_CONTINUATION regime); total trades since Jan 19: 29; net P&L ~₹51,000.

### Regime Detection
The system detects market regimes and routes to appropriate strategies. **Detection sequence (priority order)** in `regime/regime_detector.py` → `detect_regime()`:

1. **CONVEX** (checked first) – India VIX &lt; 15 and range compressed (&lt; 60% of rolling avg) only (no IV percentile or ATR).
2. **INCOME** (checked second) – IV &gt; 60%, ADX &lt; 20, ATR% &lt; 50%. If all met → INCOME.
3. **TREND_CONTINUATION** (checked third) – ADX ≥ 30, ATR% ≥ 50; then EMA structure (price vs EMA50 vs EMA100). If ADX/ATR met and EMA aligned → TREND_CONTINUATION; else → NEUTRAL.
4. **NEUTRAL** (fallback) – If none of the above match, or TREND criteria met but EMA not stable → NEUTRAL.

After the raw `detected_regime` is chosen, **regime persistence** is applied: the reported regime flips only after the new regime is detected **3 consecutive times** (anti-whipsaw).

Strategy routing:
- **CONVEX** → Call Backspread
- **INCOME** → Iron Condor
- **TREND_CONTINUATION** → Trend Following Futures
- **NEUTRAL** → Calendar (NEUTRAL_ACTIVE) or no trade (NEUTRAL_PASSIVE). **NEUTRAL fallback (2026-02-05)**: When regime is NEUTRAL and Calendar returns no valid trade, the runner now tries other strategies in order: Trend Following Futures, then Convex Backspread, then Iron Condor (each subject to mutual exclusion). If any produces a valid proposal, that trade is returned; otherwise NO_VALID_TRADE is logged.

### Recent Fix: Contract Rollover Adjustment (2026-01-28)

**Problem identified**: EMA calculation was using mixed data from different futures contracts (e.g., NIFTY27JAN26F and NIFTY24FEB26F) without adjusting for the price discontinuity at contract rollover. This caused:
- ~293 point artificial price jump when switching from Jan to Feb contract
- Distorted EMA values leading to incorrect trend structure detection
- System incorrectly staying in NEUTRAL when true structure was SHORT aligned

**Fix implemented** in `technical_indicators.py` → `get_15min_candle_data()`:
- Detects contract changes between trading days
- Calculates rollover gap (new contract first price - old contract last price)
- Applies cumulative back-adjustment to older data to create continuous price series
- Logs rollover adjustments for transparency

**Impact**:
| Metric | Before Fix | After Fix |
|--------|------------|-----------|
| EMA50 | 25,270.90 | 25,449.51 |
| EMA100 | 25,287.90 | 25,513.95 |
| EMA Structure | Mixed (not aligned) | SHORT aligned |
| Regime | NEUTRAL | TREND_CONTINUATION |
| Trade Eligibility | None | SHORT futures |

### ATR History
- Currently 9 daily ATR values stored (since Jan 14)
- Warning displayed when < 10 values (reduced statistical robustness)
- Will auto-resolve as more trading days pass

### Historical Notes

#### 2026-01-14 (Initial backtest)
- **Synthetic regime tests**: Passing (4/4 scenarios).
- **Backtest dataset available**: 17 daily folders from **20251222 → 20260114**.
- **Backtest outcome (Iron Condor)**: **0 trades across all available dates** → P&L **₹0**, capital unchanged.
- Root cause: Stored NIFTY option snapshots did not provide enough simultaneous strikes per expiry to build a 4-leg iron condor.

### Configuration Files
- `strategies/trend/config.py` - Trend following parameters
- `strategies/neutral/config.py` - Calendar strategy parameters (ADX 18-25, IV 40-60%)
- `strategies/iron_condor/config.py` - Iron Condor parameters
- `regime/regime_detector.py` - Regime detection thresholds
