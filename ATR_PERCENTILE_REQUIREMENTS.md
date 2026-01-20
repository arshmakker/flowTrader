# ATR Percentile Calculation - Requirements Analysis

**Date:** January 18, 2026  
**Purpose:** Understand requirements before implementing ATR percentile in backtest

---

## 📋 Current State

### Production System
- **Location:** `regime/regime_detector.py`
- **Method:** `calculate_atr_percentile(current_atr, historical_atrs=None)`
- **Storage:** `market_data_atr/atr_history.json`
- **Format:** JSON array with entries: `{date, atr, timestamp}`

### Backtest System
- **Location:** `backtest_trend_following.py` (line 399)
- **Current:** Hardcoded `atr_percentile = 75.0`
- **Issue:** Not using real ATR percentile calculation

---

## 🔍 Requirements Analysis

### 1. **Data Requirements**

#### A. Historical ATR Data
- **Minimum:** 10 sessions (for robust calculation)
- **Ideal:** 15-20 sessions
- **Format:** List of ATR values (floats)
- **Source Options:**
  1. **Stored History File** (`market_data_atr/atr_history.json`)
     - ✅ Already exists (2 entries: 2026-01-16, 2026-01-14)
     - ❌ Insufficient (only 2 entries, need 10+)
  2. **Calculate from Backtest Data**
     - ✅ Can calculate ATR for each day in backtest
     - ✅ Backtest has 18 trading days (Dec 22 - Jan 16)
     - ✅ Can build history as we process days

#### B. Current ATR Value
- **Source:** Already calculated in `calculate_indicators()` (line 150)
- **Method:** `regime_detector.calculate_atr(highs, lows, closes, period=14)`
- **Available:** ✅ Yes, in `indicators['atr_14']`

---

### 2. **Calculation Requirements**

#### A. Method Signature
```python
def calculate_atr_percentile(
    current_atr: float, 
    historical_atrs: List[float] = None
) -> Optional[float]
```

#### B. Calculation Logic
1. **Load stored history** from `market_data_atr/atr_history.json`
2. **If stored history has ≥10 entries:**
   - Extract ATR values: `[h['atr'] for h in history if h['atr'] > 0]`
   - Use stored history
3. **Else if provided `historical_atrs` has ≥10 values:**
   - Use provided historical_atrs
4. **Else:**
   - Return `None` (insufficient data)
5. **Calculate percentile:**
   - Sort historical ATRs
   - Count how many are below current ATR
   - Percentile = (count_below / total_count) × 100

#### C. Requirements
- **Minimum 10 historical ATR values** (for robustness)
- **Current ATR must be > 0**
- **Returns:** Percentile (0-100) or `None` if calculation fails

---

### 3. **Backtest Context Requirements**

#### A. Available Data
- ✅ **18 trading days** of historical data (Dec 22, 2025 - Jan 16, 2026)
- ✅ **15-minute candles** aggregated from tick data
- ✅ **ATR calculation** already working (line 150)
- ✅ **RegimeDetector instance** available (`self.regime_detector`)

#### B. Data Flow in Backtest
```
For each day:
  1. Load futures tick data
  2. Aggregate to 15-minute candles
  3. Calculate indicators (including ATR)
  4. Need ATR percentile
  5. Use ATR percentile for regime detection
```

#### C. Challenge
- **Problem:** ATR percentile needs historical ATR values from previous days
- **Solution Options:**
  1. **Pre-calculate ATR for all days** before starting backtest
  2. **Build ATR history as we process days** (rolling window)
  3. **Use existing stored history** + build during backtest

---

### 4. **Implementation Options**

#### Option A: Pre-calculate ATR History (Recommended)
**Approach:**
1. Before backtest starts, calculate ATR for each day in date range
2. Store in list: `[atr_day1, atr_day2, ..., atr_dayN]`
3. During backtest, use rolling window of last 10-20 days
4. Calculate percentile from rolling window

**Pros:**
- ✅ Simple and straightforward
- ✅ Accurate (uses actual historical data from backtest period)
- ✅ No dependency on external files

**Cons:**
- ⚠️ Need to process all days first (adds setup time)

**Implementation:**
```python
def build_atr_history(self, start_date, end_date):
    """Pre-calculate ATR for all days in backtest period"""
    atr_history = []
    current_date = start_date
    while current_date <= end_date:
        # Load data, calculate ATR, store
        atr_history.append(atr_value)
    return atr_history
```

---

#### Option B: Use Stored History + Build During Backtest
**Approach:**
1. Load existing `atr_history.json` (if exists)
2. As we process each day, calculate ATR and add to history
3. Use rolling window of last 10-20 ATR values
4. Calculate percentile from combined history

**Pros:**
- ✅ Can use existing stored history (if available)
- ✅ Builds history incrementally
- ✅ Works even if stored history is incomplete

**Cons:**
- ⚠️ More complex logic
- ⚠️ Need to handle date matching

**Implementation:**
```python
def get_atr_percentile(self, current_atr, current_date_str):
    # Load stored history
    stored = self.regime_detector.load_atr_history()
    
    # Build history from backtest days processed so far
    backtest_history = self.atr_history_by_date
    
    # Combine and use last 10-20
    combined = combine_histories(stored, backtest_history)
    return self.regime_detector.calculate_atr_percentile(current_atr, combined)
```

---

#### Option C: Calculate from Historical Candles
**Approach:**
1. For each day, calculate ATR from historical candles (last 14+ periods)
2. Store ATR value for that day
3. Use rolling window of last 10-20 days' ATR values
4. Calculate percentile

**Pros:**
- ✅ Uses actual backtest data
- ✅ No external dependencies

**Cons:**
- ⚠️ Need to track ATR per day
- ⚠️ Similar to Option A but more granular

---

### 5. **Recommended Approach: Option A (Pre-calculate)**

#### Why Option A?
1. **Simplest:** Clear separation of setup vs. execution
2. **Accurate:** Uses actual historical data from backtest period
3. **Robust:** No dependency on external files
4. **Efficient:** Calculate once, use many times

#### Implementation Plan

**Step 1: Pre-calculate ATR History**
```python
def build_atr_history(self, start_date, end_date):
    """Calculate ATR for each day in backtest period"""
    atr_by_date = {}
    
    # Process each day
    for date in date_range:
        tick_data = self.load_futures_data(date)
        candles = self.aggregate_to_15min_candles(tick_data)
        
        if len(candles) >= 15:  # Need at least 15 for ATR(14)
            highs = candles['high'].tolist()
            lows = candles['low'].tolist()
            closes = candles['close'].tolist()
            
            atr = self.regime_detector.calculate_atr(highs, lows, closes, period=14)
            if atr:
                atr_by_date[date] = atr
    
    return atr_by_date
```

**Step 2: Use Rolling Window During Backtest**
```python
def get_atr_percentile_for_date(self, current_atr, current_date_str):
    """Get ATR percentile using rolling window of last 10-20 days"""
    # Get ATR values for last 20 days (or available)
    date_obj = datetime.strptime(current_date_str, '%Y%m%d').date()
    lookback_start = date_obj - timedelta(days=30)  # Look back 30 days
    
    historical_atrs = []
    for date_str, atr_value in self.atr_by_date.items():
        date_check = datetime.strptime(date_str, '%Y%m%d').date()
        if lookback_start <= date_check < date_obj:
            historical_atrs.append(atr_value)
    
    # Need at least 10 for robust calculation
    if len(historical_atrs) >= 10:
        return self.regime_detector.calculate_atr_percentile(current_atr, historical_atrs)
    else:
        logger.warning(f"Insufficient ATR history: {len(historical_atrs)} days (need 10+)")
        return None  # Fallback to hardcoded 75.0
```

**Step 3: Update Backtest Logic**
```python
# In run_backtest(), before processing days:
atr_by_date = self.build_atr_history(start, end)
self.atr_by_date = atr_by_date

# In candle processing loop (line 399):
atr_percentile = self.get_atr_percentile_for_date(
    indicators.get('atr_14', 0),
    date_str
)
if atr_percentile is None:
    atr_percentile = 75.0  # Fallback
```

---

### 6. **Data Availability Check**

#### Current Stored History
- **File:** `market_data_atr/atr_history.json`
- **Entries:** 2 (2026-01-16, 2026-01-14)
- **Status:** ❌ Insufficient (need 10+)

#### Backtest Data
- **Date Range:** Dec 22, 2025 - Jan 16, 2026
- **Trading Days:** ~18 days (excluding weekends/holidays)
- **Status:** ✅ Sufficient (can calculate ATR for each day)

#### Conclusion
- **Cannot rely on stored history alone** (only 2 entries)
- **Must calculate ATR from backtest data** (18 days available)
- **Can combine both** (stored + calculated) for more robust history

---

### 7. **Edge Cases to Handle**

1. **Insufficient History (< 10 days)**
   - **Action:** Return `None`, fallback to hardcoded 75.0
   - **Log:** Warning message

2. **Missing Data for a Day**
   - **Action:** Skip that day, continue with available days
   - **Log:** Debug message

3. **ATR Calculation Fails**
   - **Action:** Skip that day, continue
   - **Log:** Warning message

4. **First Few Days of Backtest**
   - **Action:** Use stored history + available backtest days
   - **Fallback:** If still < 10, use hardcoded 75.0

5. **Weekend/Holiday Gaps**
   - **Action:** Only process trading days (already handled)
   - **Note:** ATR history should only include trading days

---

### 8. **Testing Requirements**

1. **Unit Test:** ATR percentile calculation with known values
2. **Integration Test:** Full backtest with ATR percentile
3. **Validation:** Compare results with/without ATR percentile
4. **Edge Cases:** Test with insufficient history, missing data

---

## ✅ Summary

### Requirements Checklist

- [x] **Understand calculation method** (`RegimeDetector.calculate_atr_percentile`)
- [x] **Understand data format** (JSON with date, atr, timestamp)
- [x] **Understand minimum requirements** (10+ historical ATR values)
- [x] **Understand backtest context** (18 days available)
- [x] **Choose implementation approach** (Option A: Pre-calculate)
- [x] **Identify edge cases** (insufficient data, missing days)
- [x] **Plan fallback strategy** (hardcoded 75.0 if insufficient)

### Next Steps

1. **Implement Option A:** Pre-calculate ATR history before backtest
2. **Add rolling window logic:** Use last 10-20 days for percentile
3. **Update backtest:** Replace hardcoded 75.0 with calculated percentile
4. **Add fallback:** Use 75.0 if insufficient history
5. **Test:** Run backtest and validate results

---

## 📝 Implementation Notes

### Key Decisions
- **Approach:** Pre-calculate ATR for all days before backtest starts
- **Window Size:** Use last 20 days (or available) for percentile calculation
- **Fallback:** Use 75.0 if < 10 days available
- **Storage:** Keep ATR by date in memory during backtest

### Code Structure
```python
class TrendFollowingBacktester:
    def __init__(self):
        self.atr_by_date = {}  # Store ATR by date
    
    def build_atr_history(self, start_date, end_date):
        # Pre-calculate ATR for all days
        pass
    
    def get_atr_percentile_for_date(self, current_atr, date_str):
        # Get percentile using rolling window
        pass
    
    def run_backtest(self, start_date, end_date):
        # Build ATR history first
        self.atr_by_date = self.build_atr_history(start, end)
        # Then process days...
```

---

**Status:** ✅ Requirements understood, ready for implementation
