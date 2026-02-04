# Parity Check: Production vs Backtest (Trend Following Futures)

Comparison of **production** (`main.py` + `strategy_runner.py` + `strategies/trend/`) and **backtest** (`backtest_trend_following.py`) for the TREND_FOLLOW_FUTURE strategy.

---

## 1. Entry conditions

| Check | Production | Backtest | Parity |
|-------|------------|----------|--------|
| Regime | `regime == 'TREND_CONTINUATION'` (from RegimeDetector) | `market_state['regime'] == 'TREND_CONTINUATION'` | ✅ Same |
| ADX | Implicit in regime (classify_regime_from_indicators: ADX ≥ 30 for TREND) | Explicit: `adx_14 >= 30` | ✅ Same |
| ATR percentile | Implicit in regime (ATR% ≥ 50 for TREND) | Explicit: `atr_percentile >= 50` | ✅ Same |
| EMA structure / direction | `get_ema_structure` → LONG if price > EMA50 > EMA100, SHORT if price < EMA50 < EMA100 | `detect_trend_direction(current_price, ema_50, ema_100)` same logic | ✅ Same |
| Days to expiry at entry | Production: skips contract if `days_to_expiry <= EXIT_DAYS_BEFORE_EXPIRY` (2) when selecting futures | Backtest: no per-contract expiry; uses single futures series from data | ⚠️ Backtest does not filter by days-to-expiry at entry |
| Mutual exclusion | `can_enter_strategy(STRATEGY_TREND, position_tracker)` (no open trend position) | `if not self.open_positions` (no open position) | ✅ Same idea |

**Verdict:** Entry logic is aligned except backtest does not apply **EXIT_DAYS_BEFORE_EXPIRY** at entry (no contract roll by expiry in backtest).

---

## 2. Position sizing

| Item | Production | Backtest | Parity |
|------|------------|----------|--------|
| Risk % | `MAX_RISK_PCT_OF_CAPITAL` (0.05) via trend_follow_futures | `_max_risk_pct` from config (same 0.05) | ✅ |
| Max position size | `MAX_POSITION_SIZE` × lot_size (5 lots) | `_max_position_size * lot_size` | ✅ |
| Initial stop | `INITIAL_STOP_LOSS_ATR_MULTIPLIER` (2.5) × ATR | Same from config | ✅ |
| Quantity | min(risk-based, cap), rounded down to lot size | Same | ✅ |

**Verdict:** ✅ Parity.

---

## 3. Exit conditions (order and logic)

Production order: **0** → **1** → **1b** → **1c** → **2** → **2b** → **3** → **4** → trailing update.

| # | Exit | Production | Backtest | Parity |
|---|------|------------|----------|--------|
| **0** | Market close | **Yes.** `EXIT_BEFORE_MARKET_CLOSE` + `MARKET_CLOSE_EXIT_MINUTES` (15). Exit if within 15 min of 15:30. | **Yes.** Same; `current_timestamp` passed to `check_exit_conditions`; no new entry that day after market-close exit. | ✅ Same |
| **1** | Stop loss | Price vs `current_stop_price` (LONG: price ≤ stop; SHORT: price ≥ stop). | Same. | ✅ |
| **1b** | Max intraday loss | **Yes.** `current_pnl <= -MAX_INTRADAY_LOSS_INR` (15000) → `MAX_LOSS_CAP`. | **Yes.** Same. | ✅ Same |
| **1c** | Time in loss | **Yes.** `current_pnl < 0` and `position_age_minutes >= MAX_TIME_IN_LOSS_MINUTES` (150) → `TIME_IN_LOSS`. | **Yes.** Same; uses `entry_time` for position age. | ✅ Same |
| **2** | Regime change | **Yes, with confirmation.** `regime_change_count`; exit only when `regime_change_count >= REGIME_CHANGE_CONFIRMATION_CHECKS` (2). Reset count when regime is TREND again. | **Yes.** Same; position has `regime_change_count`, incremented/reset and exit when ≥ 2. | ✅ Same |
| **2b** | ATR profit target | **Yes**, if `PROFIT_TARGET_ATR_MULTIPLIER` is not None. | **Yes**, same (`_profit_target_atr`). | ✅ |
| **3** | Expiry approaching | **Yes.** `days_to_expiry <= EXIT_DAYS_BEFORE_EXPIRY` (2) → `EXPIRY_APPROACHING`. | **No.** No expiry on position; no exit by days-to-expiry. | ⚠️ N/A (backtest uses single series, no per-contract expiry) |
| **4** | EMA structure break | **Yes, with confirmation and tolerance.** `EMA_BREAK_TOLERANCE_PCT` (0.1%); `ema_break_count`; exit when count ≥ `ema_required_checks` (1 when in loss, 2 when in profit). `PRIORITIZE_TRAILING_STOP_IN_PROFIT`: in profit and far from stop → ignore break. | **Yes.** Same: tolerance, `ema_break_count`, 1/2 checks, in-profit ignore when far from stop. | ✅ Same |
| — | Trailing stop update | Hybrid phases 1–6, breakeven lock ₹300, phases 5/6. | Same phases and breakeven. | ✅ |

**Verdict:** Backtest now matches production for **market close**, **max loss cap**, **time in loss**, **regime confirmation**, and **EMA confirmation + tolerance + in-profit ignore**. Only **expiry exit** remains N/A (backtest does not track per-contract expiry).

---

## 4. EMA structure check (detail)

| Aspect | Production | Backtest | Parity |
|--------|------------|----------|--------|
| Tolerance | LONG: `price > ema_50 * (1 - 0.1%)` and `ema_50 > ema_100 * (1 - 0.1%)`. SHORT: `price < ema_50 * (1 + 0.1%)` and `ema_50 < ema_100 * (1 + 0.1%)`. | Same: `EMA_BREAK_TOLERANCE_PCT` (0.1%) applied to LONG/SHORT structure checks. | ✅ Same |
| Confirmation | 2 consecutive breaks (or 1 when in loss). Reset when structure valid again. | Same: `ema_required_checks` = 1 when in loss, 2 when in profit; reset when structure valid. | ✅ Same |
| In profit | If `PRIORITIZE_TRAILING_STOP_IN_PROFIT` and in profit and far from stop → ignore EMA break, reset count. | Same: in profit and distance to stop > `TRAILING_STOP_PRIORITY_DISTANCE_ATR` → ignore break, reset count. | ✅ Same |

---

## 5. Config used

| Config | Production (main.py / trend) | Backtest (imports) |
|--------|-----------------------------|--------------------|
| MAX_RISK_PCT_OF_CAPITAL, MAX_POSITION_SIZE, INITIAL_STOP_LOSS_ATR_MULTIPLIER, TRAILING_*, EMA_*, PROFIT_TARGET_ATR_MULTIPLIER | Yes | Yes |
| USE_HYBRID_TRAILING_STOP, HYBRID_* (phases, breakeven, ₹300) | Yes | Yes |
| EXIT_ON_REGIME_CHANGE, REGIME_CHANGE_CONFIRMATION_CHECKS | Yes | Yes |
| EXIT_ON_EMA_BREAK, EMA_BREAK_CONFIRMATION_CHECKS, EMA_BREAK_CONFIRMATION_CHECKS_WHEN_IN_LOSS, EMA_BREAK_TOLERANCE_PCT | Yes | Yes |
| PRIORITIZE_TRAILING_STOP_IN_PROFIT, TRAILING_STOP_PRIORITY_DISTANCE_ATR | Yes | Yes |
| EXIT_BEFORE_MARKET_CLOSE, MARKET_CLOSE_EXIT_MINUTES | Yes | Yes |
| MAX_INTRADAY_LOSS_INR, MAX_TIME_IN_LOSS_MINUTES | Yes | Yes |
| EXIT_DAYS_BEFORE_EXPIRY | Yes | N/A (backtest does not track per-contract expiry) |

---

## 6. End-of-backtest

| Item | Production | Backtest | Parity |
|------|------------|----------|--------|
| Remaining positions | N/A (daily market close exits). | Closed at **end of backtest** with `last_processed_price`, reason `end_of_backtest`. | ⚠️ Backtest has no intraday “market close”, so positions can run across days until stop/regime/EMA or end of run. |

---

## 7. Summary: backtest parity (implemented)

All of the following are **implemented** in `backtest_trend_following.py`:

1. **Market close:** Before other exits, if time is within `MARKET_CLOSE_EXIT_MINUTES` of 15:30 (weekday), exit with `MARKET_CLOSE_APPROACHING` and do not open new position that day (`_market_close_exit_dates`).
2. **Max loss cap:** If `current_pnl <= -MAX_INTRADAY_LOSS_INR`, exit with `MAX_LOSS_CAP`.
3. **Time in loss:** If `current_pnl < 0` and position age ≥ `MAX_TIME_IN_LOSS_MINUTES`, exit with `TIME_IN_LOSS`. Uses `entry_time` on position.
4. **Regime change confirmation:** `regime_change_count` on position; increment when regime ≠ TREND_CONTINUATION, reset when TREND; exit with `REGIME_CHANGE` only when count ≥ `REGIME_CHANGE_CONFIRMATION_CHECKS`.
5. **Expiry exit:** Not applicable (backtest uses single futures series; no per-contract expiry).
6. **EMA break:** `EMA_BREAK_TOLERANCE_PCT` for structure check; `ema_break_count`; `ema_required_checks` (1 when in loss, 2 when in profit); when in profit and far from stop and `PRIORITIZE_TRAILING_STOP_IN_PROFIT`, ignore break and reset count.

---

## 8. File references

- **Production:** `main.py` (futures position monitoring ~347–970), `strategies/trend/config.py`, `strategies/trend/trend_follow_futures.py`, `strategy_runner.py` (regime + trend branch).
- **Backtest:** `backtest_trend_following.py` (`check_entry_conditions`, `check_exit_conditions`, `run_backtest` loop, config imports at top).
