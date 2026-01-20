# Backtest Trend Following - Code Review

**Date:** January 16, 2026  
**File:** `backtest_trend_following.py`  
**Status:** ✅ Functional but has issues

---

## 📊 Overall Assessment

**Strengths:**
- Well-structured code with clear separation of concerns
- Comprehensive indicator calculation (EMA, ATR, ADX)
- Good position management and exit logic
- Detailed reporting with P&L tracking
- Handles multiple data files per day

**Issues Found:**
- **1 Critical Bug** (line 492): Position closing uses wrong price
- **2 Major Simplifications**: Regime detection and ATR percentile
- **3 Minor Issues**: Data pre-loading, trailing stop persistence, transaction costs

---

## 🐛 Critical Issues

### 1. **BUG: Closing Positions Uses Entry Price** (Line 492)

**Location:** `run_backtest()` → End-of-backtest position closing

**Current Code:**
```python
# Line 492
last_price = position['entry_price']  # Simplified - in real backtest, use last candle
```

**Problem:**
- Positions closed at end of backtest use `entry_price` instead of last candle close
- Results in **0 P&L** for all positions held until end
- This artificially skews backtest results

**Fix:**
```python
# Should use the last candle's close price from the most recent day
# Store last_processed_candle in class or pass it through
last_price = candles.iloc[-1]['close'] if not candles.empty else position['entry_price']
```

**Impact:** High - Affects all trades held until end of backtest period

---

## ⚠️ Major Simplifications

### 2. **Hardcoded ATR Percentile** (Line 399)

**Location:** `run_backtest()` → Market state construction

**Current Code:**
```python
# Line 399
'atr_percentile': 75.0,  # Simplified - in production this is calculated from history
```

**Problem:**
- ATR percentile is hardcoded to 75.0 instead of calculated from historical ATR data
- Real regime detection requires actual ATR percentile from historical distribution
- This may cause false positives (detecting TREND when ATR% < 50 in reality)

**Fix:**
- Load historical ATR data from `market_data_atr/` directory
- Calculate percentile from ATR history (similar to how IV percentile is calculated)
- Use `RegimeDetector.get_atr_percentile()` if available

**Impact:** Medium - Affects regime detection accuracy

---

### 3. **Simplified Regime Detection** (Lines 393-413)

**Location:** `run_backtest()` → Regime detection logic

**Current Code:**
```python
# Lines 403-413
# Simplified regime detection for backtest
adx = indicators.get('adx_14', 0)
atr_percentile = market_state['atr_percentile']  # Hardcoded 75.0
direction = self.detect_trend_direction(...)

if adx >= 30 and atr_percentile >= 50 and direction:
    market_state['regime'] = 'TREND_CONTINUATION'
```

**Problem:**
- Doesn't use `RegimeDetector.detect_regime()` method
- Missing regime persistence logic (confirmation count = 2)
- Missing EMA structure check with proper lookback
- Inconsistent with production regime detection

**Fix:**
- Use `RegimeDetector.detect_regime()` for consistency
- Or replicate full regime detection logic from production
- Include regime persistence (anti-whipsaw)

**Impact:** Medium - Affects regime detection accuracy

---

## 🔧 Minor Issues & Improvements

### 4. **Data Pre-loading May Be Insufficient** (Lines 337-346)

**Current Code:**
```python
# Line 337
preload_start = start - timedelta(days=5)
```

**Issue:**
- Only pre-loads 5 days of data
- EMA(100) needs 100 candles
- At 15-minute intervals, trading day (9:15 AM - 3:30 PM) = ~25 candles/day
- 5 days ≈ 125 candles (barely enough)
- Weekend gaps may reduce actual candles

**Recommendation:**
- Increase to 7-10 days for safety
- Add validation: `if len(historical_candles) < 100: logger.warning(...)`

**Impact:** Low - May affect early days of backtest

---

### 5. **Trailing Stop Updates Position In-Place** (Lines 302-311)

**Current Code:**
```python
# Lines 306-311
if direction == 'LONG':
    new_trailing_stop = current_price - trailing_stop_atr
    position['current_stop_price'] = max(current_stop, new_trailing_stop)  # Updates dict
```

**Issue:**
- Position dictionary is modified in-place within `check_exit_conditions()`
- This should work since it's a reference, but could cause confusion
- No explicit persistence/logging of trailing stop updates

**Recommendation:**
- Consider returning updated stop price and updating in caller
- Add logging when trailing stop is updated: `logger.debug(f"Trailing stop updated: {old} -> {new}")`

**Impact:** Low - Code works but could be clearer

---

### 6. **No Transaction Costs** (Throughout)

**Issue:**
- Missing slippage modeling (assumes perfect execution at LTP)
- Missing commission/brokerage fees
- Missing exchange charges
- Real trading costs can be 0.05-0.1% per trade

**Recommendation:**
- Add slippage: `execution_price = ltp * (1 + slippage_bps / 10000)` where `slippage_bps = 2-5`
- Add commission: `commission = quantity * price * 0.0002` (0.02% per leg)
- Deduct from P&L: `pnl = raw_pnl - commission`

**Impact:** Medium - Affects profitability estimates

---

### 7. **Check Interval Only at Candle Boundaries** (Line 315)

**Current Behavior:**
- Checks for entry/exit only at 15-minute candle close times
- If stop loss is hit mid-candle, exit happens at next 15-minute mark
- Real trading would exit immediately when stop is hit

**Impact:**
- Can overstate P&L (if stop hit mid-candle, continue losing until next check)
- Can understate P&L (if stop hit mid-candle but price recovers)

**Recommendation:**
- For exit checks, use tick-by-tick data within candle
- Or acknowledge limitation in documentation

**Impact:** Low-Medium - Affects accuracy of stop loss exits

---

### 8. **Missing Validation Checks**

**Issues:**
- No validation that historical_candles has enough data (line 384-386 only checks if < 100)
- No validation of candle data quality (missing OHLC values)
- No check for market hours (9:15 AM - 3:30 PM IST)
- No handling of holidays/weekends

**Recommendation:**
- Add data quality checks before indicator calculation
- Filter candles by market hours
- Handle gaps (weekends, holidays)

**Impact:** Low - May cause errors with bad data

---

## ✅ What's Working Well

1. **Data Loading:** Handles multiple futures files per day correctly
2. **Candle Aggregation:** Properly resamples to 15-minute candles
3. **Indicator Calculation:** EMA, ATR, ADX calculations are correct
4. **Entry Logic:** Properly checks all conditions before entry
5. **Exit Logic:** Covers all exit scenarios (SL, regime change, EMA break)
6. **Position Tracking:** Maintains detailed position state
7. **Reporting:** Comprehensive P&L and performance metrics

---

## 📈 Backtest Results Analysis

From `backtest_trend_report_20260116_174055.json`:
- **6 trades** (2 LONG, 4 SHORT)
- **66.67% win rate** (4 wins, 2 losses)
- **Total P&L: ₹9,405** (0.94% return)
- **Max Profit: ₹8,410** (regime change exit)
- **Max Loss: ₹15,250** (stop loss hit)

**Observations:**
- Short trades performed worse (avg P&L: ₹226) than long trades (avg P&L: ₹4,250)
- 5 exits due to stop loss, 1 due to regime change
- Stop loss exits had mixed results (some wins, some losses)

---

## 🔧 Recommended Fixes (Priority Order)

### Priority 1: Critical Bug Fix

```python
# In run_backtest(), before closing positions at end:
# Store last candle from the most recent day processed
last_candle_price = candles.iloc[-1]['close'] if not candles.empty else None

# In position closing loop (line 492):
if last_candle_price:
    last_price = last_candle_price
else:
    # Fallback: use last entry price (shouldn't happen)
    last_price = position['entry_price']
    logger.warning(f"Using entry_price for end-of-backtest close: {position['entry_time']}")
```

### Priority 2: Implement Real ATR Percentile Calculation

```python
# Load historical ATR data
atr_history_file = os.path.join('market_data_atr', 'atr_history.json')
if os.path.exists(atr_history_file):
    with open(atr_history_file, 'r') as f:
        atr_history = json.load(f)
    current_atr = indicators.get('atr_14', 0)
    if current_atr and atr_history:
        atr_percentile = self.regime_detector.get_atr_percentile(current_atr, atr_history)
    else:
        atr_percentile = 75.0  # Fallback
else:
    atr_percentile = 75.0  # Fallback
```

### Priority 3: Use Production Regime Detector

```python
# Instead of simplified regime detection (lines 403-413):
market_state_full = {
    'spot_price': current_price,
    'adx_14': indicators.get('adx_14', 0),
    'atr': indicators.get('atr_14', 0),
    'atr_percentile': atr_percentile,  # Calculated above
    'iv_percentile': 50.0,  # May need historical IV data
    'api': None,  # Not available in backtest
    'symbol_manager': None  # Not available in backtest
}

# Use RegimeDetector if possible (may need API/symbol_manager for EMA)
# Or replicate full logic including persistence
regime = self.regime_detector.detect_regime(market_state_full, api=None, symbol_manager=None)
```

### Priority 4: Add Transaction Costs

```python
# In position closing (lines 422-426):
# Add slippage and commission
slippage_bps = 3  # 0.03%
commission_rate = 0.0002  # 0.02%

execution_price = current_price * (1 + (slippage_bps / 10000) * (1 if direction == 'LONG' else -1))
commission = quantity * execution_price * commission_rate

if direction == 'LONG':
    raw_pnl = (execution_price - position['entry_price']) * quantity
else:
    raw_pnl = (position['entry_price'] - execution_price) * quantity

pnl = raw_pnl - commission  # Deduct transaction costs
```

---

## 📝 Additional Recommendations

1. **Add Unit Tests:** Test indicator calculations, entry/exit logic separately
2. **Parameterize Configuration:** Move hardcoded values to config file
3. **Add Data Validation:** Check data completeness before backtest
4. **Improve Logging:** Add more detailed logs for debugging
5. **Add Visualization:** Plot equity curve, trades on price chart
6. **Compare with Production:** Validate that backtest matches production behavior

---

## 🎯 Summary

**Status:** The backtest is functional and produces results, but has one critical bug (closing price) and several simplifications that affect accuracy. The code structure is solid and most logic is correct.

**Immediate Action:** Fix the closing price bug (Priority 1) - this is straightforward and has high impact.

**Future Enhancements:** Implement real ATR percentile calculation and use production regime detector for better accuracy.
