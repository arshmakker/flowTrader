# Available Backtests for Historical Data

## 📊 Available Data

**Date Range:** December 22, 2025 → January 16, 2026 (18 trading days)

**Data Folders:**
- `market_data_20251222` through `market_data_20260116`
- `market_data_atr/` (ATR historical data)
- `market_data_iv/` (IV historical data)

**Data Structure per Day:**
```
market_data_YYYYMMDD/
├── raw_data/
│   ├── cash/          # Equity tick data (NIFTY 50 stocks)
│   ├── futures/       # Futures tick data (NIFTY, BANKNIFTY, FINNIFTY)
│   └── options/       # Options tick data
│       └── NIFTY/
│           ├── ce/    # Call options CSV files
│           └── pe/    # Put options CSV files
```

**Data Fields Available:**
- **Options:** timestamp, symbol, ltp, bid, ask, volume, oi, strike, option_type
- **Futures:** timestamp, symbol, ltp, bid, ask, volume, oi
- **Cash:** timestamp, symbol, ltp, bid, ask, volume

---

## ✅ Existing Backtest Scripts

### 1. **Iron Condor Backtest** (`backtest_iron_condor.py`)

**Status:** ✅ Implemented and tested

**What it tests:**
- Iron Condor strategy entry/exit logic
- Strike selection based on IV percentile
- Position sizing and margin calculation
- Exit rules (profit target, stop loss, time decay)

**Data Requirements:**
- ✅ Options data (CE and PE) for NIFTY
- ✅ Spot prices (from futures as proxy)
- ✅ Historical IV data (for IV percentile calculation)

**Current Results:**
- **0 trades** across all available dates
- **Root cause:** Sparse option chain data (max 2 unique strikes per expiry, needs ≥4 for Iron Condor)

**How to Run:**
```bash
python3 backtest_iron_condor.py
```

**Modifiable Parameters:**
- Date range (start_date, end_date)
- Check interval (default: 2 minutes)
- Initial capital (default: ₹100,000)
- Quote staleness window (default: 10 minutes)

---

## 🚀 Potential New Backtests

### 2. **Convex Backspread Backtest**

**Strategy:** Call Backspread (Sell 1 ATM Call, Buy 2 OTM Calls)

**Data Requirements:**
- ✅ Options data (CE) for NIFTY
- ✅ Spot prices
- ✅ Historical IV data
- ✅ Regime detection (CONVEX regime)

**What to Test:**
- Entry conditions (IV < 40%, ATR% < 25%, compressed range)
- Strike selection (ATM + OTM at ~1% above spot)
- Net debit validation (≤ 0.25% of spot)
- Position sizing (max loss ≤ 1% of capital)
- Exit rules (profit target, stop loss, expiry)

**Implementation Effort:** Medium
- Reuse existing backtest framework
- Adapt for 3-leg strategy (vs 4-leg Iron Condor)
- Add regime detection logic

**Expected Data Quality:** Better than Iron Condor
- Needs only 3 strikes (vs 4 for Iron Condor)
- May work with sparse option chain data

---

### 3. **Neutral Calendar Backtest**

**Strategy:** ATM Call Calendar (Sell near expiry, Buy far expiry)

**Data Requirements:**
- ✅ Options data (CE) for NIFTY
- ✅ Spot prices
- ✅ Multiple expiries (near + far)
- ✅ Regime detection (NEUTRAL_ACTIVE sub-state)

**What to Test:**
- Entry conditions (ADX 18-25, IV 45-55%, ATR% 30-50%)
- Strike selection (ATM for both expiries)
- Position sizing
- Exit rules (time decay, IV expansion, ADX breakout)

**Implementation Effort:** Medium
- Need to handle multiple expiries
- Calendar spread specific exit logic

**Expected Data Quality:** Good
- Needs only 2 strikes (same strike, different expiries)
- Should work with available data

---

### 4. **Futures Trend Following Backtest**

**Strategy:** Directional trend following on NIFTY futures

**Data Requirements:**
- ✅ Futures data (NIFTY futures)
- ✅ 15-minute candle data (for EMA calculation)
- ✅ Historical price data (for ATR/ADX calculation)
- ✅ Regime detection (TREND_CONTINUATION)

**What to Test:**
- Entry conditions (ADX ≥ 30, ATR% ≥ 50, EMA structure aligned)
- Direction detection (LONG: price > EMA(50) > EMA(100), SHORT: opposite)
- Position sizing (1 lot, risk ≤ 0.5% of capital)
- Stop loss (initial: 1.5 × ATR, trailing: 2 × ATR)
- Exit rules (SL hit, EMA structure breaks, regime change)

**Implementation Effort:** Medium-High
- Need to build 15-minute candles from tick data
- EMA calculation on 15-min timeframe
- Trailing stop logic
- Regime detection integration

**Expected Data Quality:** Excellent
- Futures data is more complete than options
- Can aggregate tick data into 15-minute candles
- Should have sufficient data for EMA(100) calculation

---

### 5. **Regime Detection Backtest**

**Purpose:** Validate regime detection accuracy on historical data

**What to Test:**
- Regime detection accuracy (CONVEX, INCOME, NEUTRAL, TREND_CONTINUATION)
- Regime persistence (confirmation count logic)
- Indicator calculation accuracy (IV%, ADX, ATR%, Range state)
- Regime transitions and timing

**Data Requirements:**
- ✅ All market data (options, futures, cash)
- ✅ Historical IV data
- ✅ Historical ATR data

**Implementation Effort:** Low-Medium
- Can reuse existing regime detector
- Add logging/validation layer
- Compare detected regimes with expected conditions

**Output:**
- Regime detection accuracy report
- Regime transition timeline
- Indicator value distributions by regime

---

## 📈 Combined Strategy Backtest

### 6. **Multi-Strategy Portfolio Backtest**

**Purpose:** Test all strategies together with regime-based routing

**What to Test:**
- Strategy routing based on detected regime
- Mutual exclusion between strategies
- Portfolio-level risk management
- Overall performance across all regimes

**Data Requirements:**
- ✅ All data types (options, futures, cash)
- ✅ Historical IV/ATR data
- ✅ Regime detection

**Implementation Effort:** High
- Integrate all strategy backtests
- Add portfolio-level tracking
- Implement mutual exclusion logic
- Aggregate performance metrics

**Expected Output:**
- Performance by regime
- Strategy win rates
- Portfolio-level risk metrics
- Capital allocation efficiency

---

## 🔧 Backtest Framework Enhancements

### Current Limitations:
1. **Sparse Option Chain Data:** Max 2 unique strikes per expiry (Iron Condor needs ≥4)
2. **Quote Staleness:** Using 10-minute window to compensate
3. **No Intraday Regime Changes:** Backtest checks at fixed intervals

### Potential Improvements:
1. **Data Quality Checks:** Validate data completeness before backtest
2. **Multiple Timeframes:** Test different check intervals (1min, 5min, 15min)
3. **Slippage Modeling:** Add realistic slippage for order execution
4. **Commission/Taxes:** Include transaction costs
5. **Partial Fills:** Model realistic order execution
6. **Market Hours:** Respect trading hours (9:15 AM - 3:30 PM IST)

---

## 📋 Recommended Backtest Priority

1. **Futures Trend Following** ⭐ (Highest Priority)
   - Best data quality (futures data is complete)
   - Simpler strategy (single instrument)
   - Can validate regime detection

2. **Neutral Calendar** ⭐⭐
   - Good data quality (needs only 2 strikes)
   - Validates NEUTRAL regime detection
   - Tests calendar spread logic

3. **Convex Backspread** ⭐⭐⭐
   - Better than Iron Condor (needs 3 vs 4 strikes)
   - Validates CONVEX regime detection
   - Tests debit spread logic

4. **Regime Detection Validation** ⭐⭐⭐⭐
   - Low effort, high value
   - Validates core system logic
   - Foundation for all strategy backtests

5. **Iron Condor** (Re-test after data quality improves)
   - Currently blocked by sparse data
   - Re-test if more option strikes become available

---

## 🛠️ Implementation Guide

### To Create a New Backtest:

1. **Copy `backtest_iron_condor.py` as template**
2. **Modify data loading** for your strategy's needs
3. **Implement strategy-specific logic:**
   - Entry conditions
   - Position sizing
   - Exit rules
4. **Add regime detection** (if needed)
5. **Generate performance report**

### Example Structure:
```python
class StrategyBacktester:
    def __init__(self, initial_capital=100000):
        # Initialize
        
    def load_historical_data(self, date_str):
        # Load required data
        
    def check_entry_conditions(self, timestamp, market_state):
        # Strategy-specific entry logic
        
    def calculate_position_size(self, trade_proposal, capital):
        # Position sizing
        
    def check_exit_conditions(self, position, timestamp, market_state):
        # Exit logic
        
    def run_backtest(self, start_date, end_date):
        # Main backtest loop
        
    def generate_report(self):
        # Performance metrics
```

---

## 📊 Data Quality Summary

| Strategy | Data Needs | Data Quality | Feasibility |
|----------|-----------|--------------|-------------|
| Iron Condor | 4 strikes, 1 expiry | ⚠️ Poor (max 2 strikes) | ❌ Blocked |
| Convex Backspread | 3 strikes, 1 expiry | ⚠️ Fair (may work) | ⚠️ Possible |
| Neutral Calendar | 2 strikes, 2 expiries | ✅ Good | ✅ Feasible |
| Futures Trend | Futures ticks | ✅ Excellent | ✅ Highly Feasible |
| Regime Detection | All data types | ✅ Good | ✅ Feasible |

---

## 📝 Notes

- **Market Hours:** Data collected during trading hours (9:15 AM - 3:30 PM IST)
- **Data Frequency:** Tick-by-tick data (collected every second)
- **Missing Data:** Some days may have incomplete data (holidays, system issues)
- **Data Validation:** Always check data completeness before running backtests
