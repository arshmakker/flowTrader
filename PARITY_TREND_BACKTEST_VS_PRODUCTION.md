# Trend Strategy: Backtest vs Production Parity Check

Sanity check that backtest (`backtest_trend_following.py`) and production (`main.py` + `strategies/trend/`) use the same logic. Both pull config from `strategies/trend/config.py`.

---

## 1. Entry conditions

| Check | Backtest | Production | Match |
|-------|----------|------------|-------|
| Regime | `regime == 'TREND_CONTINUATION'` | Same in `generate_trend_follow_trade()` | ✅ |
| ADX (base) | `adx >= 30` | Same (implicit in regime + explicit filter) | ✅ |
| ADX (high vol) | When `atr_percentile >= 90` → require `adx >= 40` | Same in `generate_trend_follow_trade()` | ✅ |
| ATR percentile | `atr_percentile >= 50` (explicit) | Implicit via regime (detector requires ATR% ≥ 50 for TREND) | ✅ |
| EMA structure | `detect_trend_direction()`: price > EMA50 > EMA100 (LONG) or price < EMA50 < EMA100 (SHORT) | Same via `get_ema_structure()` → direction | ✅ |
| Cooldown after STOP_LOSS | `_cooldown_until`; no entry if `timestamp < _cooldown_until` (30 min) | `can_enter_trend()` → `REENTRY_COOLDOWN_MINUTES`; `trend_state.json` | ✅ |
| Max trades per day | `_trades_entered_today < MAX_TREND_TRADES_PER_DAY` (3) | `can_enter_trend()` → same config; state in `trend_state.json` | ✅ |

**Config used:** `REENTRY_COOLDOWN_MINUTES`, `HIGH_VOL_ATR_PERCENTILE_THRESHOLD`, `HIGH_VOL_MIN_ADX`, `MAX_TREND_TRADES_PER_DAY`.

---

## 2. Exit conditions (order and logic)

| Exit | Backtest | Production | Match |
|------|----------|------------|-------|
| Market close | `EXIT_BEFORE_MARKET_CLOSE`, `MARKET_CLOSE_EXIT_MINUTES`; no new entries that day | Same | ✅ |
| Stop loss | **Limit at stop:** LONG `bar_low <= current_stop`, SHORT `bar_high >= current_stop`; exit price = `current_stop_price` (fill at stop). Else close-based fallback. | main.py: LONG `current_futures_price <= current_stop`, SHORT `>=` (tick; no bar). **When placing exit order:** use **limit order at `current_stop_price`** (not market) so fill is at or better than stop; matches backtest and avoids slippage. | ✅ |
| Max loss cap | `current_pnl <= -MAX_INTRADAY_LOSS_INR` | Same | ✅ |
| Time in loss | `current_pnl < 0` and `position_age_minutes >= MAX_TIME_IN_LOSS_MINUTES` | Same | ✅ |
| Regime change | `REGIME_CHANGE_CONFIRMATION_CHECKS`; count reset when regime restored | Same | ✅ |
| ATR profit target | Only if `_profit_target_atr is not None` (config = None) | Only if `PROFIT_TARGET_ATR_MULTIPLIER is not None` | ✅ |
| EMA break | `EMA_BREAK_TOLERANCE_PCT`, confirmation 1 when in loss / 2 in profit, `PRIORITIZE_TRAILING_STOP_IN_PROFIT`, `TRAILING_STOP_PRIORITY_DISTANCE_ATR` | Same | ✅ |

---

## 3. Trailing stop (hybrid phases 1–6)

| Item | Backtest | Production | Match |
|------|----------|------------|-------|
| Phase thresholds | HYBRID_BREAKEVEN_THRESHOLD_ATR, PHASE2–6 thresholds | Same | ✅ |
| Phase multipliers | HYBRID_PHASE1–6_MULTIPLIER | Same | ✅ |
| **Breakeven lock** | `lock_be` when `unrealized_pnl_points >= atr * HYBRID_PHASE2_THRESHOLD_ATR` (1× ATR) **or** `current_pnl >= HYBRID_MIN_PNL_LOCK_INR` (₹300) | Was: `lock_be` when `trail_phase in [PHASE2_BREAKEVEN, ...]` (phase 2 = profit in [0.5×, 1×) ATR) or PnL ≥ 300. **Fixed:** use same condition as backtest (≥ 1× ATR or ≥ ₹300) | ✅ (after fix) |

---

## 3b. Stop loss order type (limit needed)

| | Backtest | Production |
|---|----------|------------|
| **Intent** | Stop is simulated as a **limit order at the stop price**: "hit" when price trades at or through the stop (`bar_low <= stop` for LONG, `bar_high >= stop` for SHORT); exit price for P&L = stop price (fill at limit). | When the system places the actual broker order to close on STOP_LOSS_HIT, it should place a **limit order at `current_stop_price`** (not a market order). |
| **Why limit** | Ensures fill at or better than stop; avoids assuming worse fill (e.g. bar close) in backtest. | Ensures fill at or better than stop; avoids slippage in fast markets. Matches backtest semantics. |
| **Backtest** | `exit_price = position['current_stop_price']` when `exit_reason == 'STOP_LOSS_HIT'` (line ~858). | N/A (backtest has no broker). |
| **Production** | N/A. | Use **LMT** at `current_stop_price` when executing the exit for STOP_LOSS_HIT (and for any exit where we want "at stop" semantics). Market order would diverge from backtest and can fill worse. |

---

## 4. Position sizing

| Item | Backtest | Production | Match |
|------|----------|------------|-------|
| Initial stop | `INITIAL_STOP_LOSS_ATR_MULTIPLIER * atr` | Same in `calculate_position_size()` | ✅ |
| Risk cap | `min(max_risk_by_risk, max_position_size * lot_size)` | Same | ✅ |
| At least 1 lot | When regime allows but risk gives 0 lots → override to 1 lot | Same in `generate_trend_follow_trade()` | ✅ |

---

## 5. Cooldown & max trades implementation

| Item | Backtest | Production |
|------|----------|------------|
| Cooldown set | On exit with `STOP_LOSS_HIT`: `_cooldown_until = timestamp + timedelta(minutes=REENTRY_COOLDOWN_MINUTES)` | On close with `exit_reason == 'STOP_LOSS_HIT'`: `record_stop_loss_exit(datetime.now())` → writes `cooldown_until_iso` to `trend_state.json` |
| Cooldown check | Before entry: `timestamp < _cooldown_until` → skip | Before `generate_trend_follow_trade`: `can_enter_trend(now)` → False if `now < cooldown_until` |
| Trades today | `_trades_entered_today` reset per date; increment on entry; block if `>= MAX_TREND_TRADES_PER_DAY` | `record_trend_entry(entry_date)`; `can_enter_trend()` reads state, resets count when `last_trade_date != today` |

Same effective behavior; production persists state in `trend_state.json`.

---

## 6. Summary

- **Aligned:** Entry (regime, ADX, high-vol ADX, ATR%, EMA), exit order and rules, hybrid phases, position sizing, cooldown and max trades per day.
- **Fixed:** Production breakeven lock was “phase 2+” (lock from 0.5× ATR). Updated to match backtest: lock only when profit ≥ 1× ATR or PnL ≥ ₹300.

Run this sanity check after any change to trend entry/exit or config.
