## Project context

- **Name**: `regimetrader`
- **Goal**: Multi-strategy trading system with regime detection that automatically routes to appropriate strategies based on market conditions. Supports Iron Condor (INCOME), Call Backspread (CONVEX), Trend Following Futures (TREND_CONTINUATION), and Calendar Spreads (NEUTRAL). Includes market data collection, position tracking, and backtesting utilities.
- **Market data layout**: `market_data_YYYYMMDD/raw_data/{futures,options,...}` with per-underlying option CSVs.
- **Backtest**: `backtest_iron_condor.py` (Iron Condor), `backtest_trend_following.py` (Trend Futures with ATR profit target and hybrid trailing) run against stored tick data.

## Current state (2026-01-30)

### CONVEX Regime: India VIX and range only (2026-01-30)

CONVEX regime uses **India VIX < 15** and **range compressed** only (no IV percentile or ATR criteria).

- **strategy_runner**: `get_india_vix(api)` fetches India VIX from NSE (token 26017); `build_market_state_from_chain` adds `india_vix` to market_state; `save_daily_metrics` persists `india_vix` for backtest.
- **regime_detector**: `CONVEX_VIX_MAX = 15`; CONVEX = (india_vix is not None and india_vix < 15) and range_compressed; regime_result includes `india_vix`.
- **Backtests**: Trend and regime backtests load `india_vix` from daily_metrics when present and pass it to `classify_regime_from_indicators`.

### INCOME Regime: India VIX or IV% (2026-01-30)

INCOME regime uses **high vol** (IV% &gt; 60 **or** India VIX ≥ 20) and **low trend** (ADX &lt; 20, ATR% &lt; 50).

- **regime_detector**: `INCOME_VIX_MIN = 20`; INCOME triggers when `(iv_percentile > 60 OR (india_vix is not None and india_vix >= 20))` and ADX &lt; 20 and ATR% &lt; 50. IV percentile remains supported; India VIX allows INCOME to trigger in backtest and live without IV history.
- **Backtest**: Regime backtest passes `india_vix` from daily_metrics (or synthetic 14 when missing); INCOME can trigger on days where `daily_metrics.json` has India VIX ≥ 20 and ADX/ATR conditions are met.

### Regime Detection Backtest (2026-01-30)

**`backtest_regime_detection.py`** now uses **production** regime logic (`classify_regime_from_indicators`) with the same thresholds (CONVEX, INCOME, TREND_CONTINUATION, NEUTRAL). IV source: optional CSV → `daily_metrics.json` → volatility proxy (ATR/close percentile). Accumulates 100 bars across days for EMA/ATR/range; reports regime distribution, transitions, and indicator stats by regime.

**Run**: `python3 backtest_regime_detection.py [start_YYYYMMDD] [end_YYYYMMDD] [iv_csv_path]` (default 20251222–20260116). Report: `backtest_regime_report_YYYYMMDD_HHMMSS.json`.

**CONVEX in regime backtest**: Report includes `convex_triggered` (count of bars where regime was CONVEX), `range_compressed_bars`, and `convex_note` (CONVEX requires India VIX &lt; 15 and range_compressed; note explains why CONVEX did or did not trigger). CONVEX triggers only when both conditions hold; in sample runs with no range compression, CONVEX stays 0.

**Convex backspread backtest (production regime)** (2026-01-30): **`backtest_convex_backspread.py`** now uses **production** regime detection: loads NIFTY futures per date, builds 15m candles, keeps rolling 100 bars, and at each check time classifies regime via `classify_regime_from_indicators` (India VIX from `daily_metrics` or synthetic 14, range_compressed from last_range &lt; 0.6×rolling_avg_range). Convex **entries** occur only when regime is CONVEX (no separate IV/ATR entry filters). Report includes `avg_pnl`; 0-trades case returns all fields (e.g. avg_pnl 0) so the report prints without error.

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

**Default config: trailing only (2026-01-29)**

- **Config**: `PROFIT_TARGET_ATR_MULTIPLIER = None` in `strategies/trend/config.py` — no fixed profit target; exit only on trailing stop, stop loss, regime change, or EMA break. PnL lock at ₹300 and phases 1–6 (including 3× and 4× ATR tightening).

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
- **PROFIT_TARGET_ATR_MULTIPLIER = None** — No fixed profit target; exit only on trailing stop, stop loss, regime change, or EMA break.
- **Phases 5–6**: `HYBRID_PHASE5_THRESHOLD_ATR = 3.0`, `HYBRID_PHASE5_MULTIPLIER = 0.5`; `HYBRID_PHASE6_THRESHOLD_ATR = 4.0`, `HYBRID_PHASE6_MULTIPLIER = 0.25` (incremental tightening beyond 2.5× ATR).

**Live** (`main.py`): Breakeven lock when `trail_phase` is in profit phases **or** `current_pnl >= HYBRID_MIN_PNL_LOCK_INR`. PHASE5/PHASE6 applied; ATR profit-target exit runs only when `PROFIT_TARGET_ATR_MULTIPLIER is not None`.

**Backtest** (`backtest_trend_following.py`): Imports and uses PHASE5/PHASE6 and `HYBRID_MIN_PNL_LOCK_INR`. Profit-target exit only when `_profit_target_atr is not None` (supports config `None` = trailing only). Breakeven lock uses `unrealized_pnl_points > 0 or current_pnl >= HYBRID_MIN_PNL_LOCK_INR`.

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

### Contract Rollover: Log Once per Process (2026-01-30)

**Change**: In `technical_indicators.py`, "Contract rollover detected: …" is now logged at most once per (old_contract, new_contract) per process. A module-level set `_logged_rollovers` tracks which rollover pairs have been logged; subsequent 15m data loads still apply the same adjustment but do not repeat the INFO message (avoids spam every few minutes).

### Trend Strategy: No-Trade Reason Logging (2026-01-30)

**Implemented**: When the trend strategy returns no trade (NO_VALID_TRADE), the exact reason is now logged at INFO level so logs show why. Reason codes: `REGIME_NOT_TREND`, `SPOT_OR_ATR_INVALID`, `API_OR_SYMBOL_MANAGER_MISSING`, `NO_NIFTY_FUTURES`, `ALL_CONTRACTS_NEAR_EXPIRY`, `NO_FUTURES_QUOTE`, `FUTURES_SELECT_ERROR`, `FUTURES_PRICE_INVALID`, `NO_EMA_STRUCTURE`, `DIRECTION_NONE` (15m EMA not aligned), `POSITION_SIZE_ZERO`, `EXCEPTION`. Search logs for `Trend strategy no trade: reason=` to see the cause.

### Trend Strategy: ATR Profit Target & Tighter Trail (2026-01-29)

**Implemented**:
1. **ATR profit target (#2)** – Exit when unrealized profit in points ≥ `PROFIT_TARGET_ATR_MULTIPLIER × ATR` (0.25× ATR). Books gains proactively instead of relying only on trailing stop and regime change. Config: `strategies/trend/config.py` → `PROFIT_TARGET_ATR_MULTIPLIER = 0.25`. Exit reason logged as `PROFIT_TARGET_ATR`.
2. **Tighter trail in very large profit (#4)** – Added Phase 5: when profit ≥ 2.5× ATR, trailing stop uses 0.75× ATR (Phase 4 remains 1× ATR at 2× ATR profit). Config: `HYBRID_PHASE4_THRESHOLD_ATR = 2.5`, `HYBRID_PHASE4_MULTIPLIER = 0.75`. Phase names: `PHASE4_VERY_TIGHT`, `PHASE5_VERY_LARGE_PROFIT`.

**Context**: On 2026-01-29 a SHORT trend trade reached ~₹6,467 profit (~0.39× ATR) then reversed; regime-change exit later closed at -₹1,930. A 0.5× ATR profit target would have locked ~₹8,231 had price reached it; a 0.25× target would have locked ~₹4,115 at peak. Regime-change exit already exits at current P&L (#1); no code change for that.

### Live Trading Status
- **Active strategies**: Trend Following Futures (TREND_CONTINUATION regime)
- **Total trades since Jan 19**: 29 (all TREND_FOLLOW_FUTURE)
- **Net P&L**: ~₹51,000 profit
- **Current regime**: TREND_CONTINUATION (SHORT direction) after rollover fix

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
- **NEUTRAL** → Calendar (NEUTRAL_ACTIVE) or no trade (NEUTRAL_PASSIVE)

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
