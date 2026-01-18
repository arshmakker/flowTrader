# TREND_CONTINUATION Regime & Futures Trend Following Strategy

## Implementation Summary

Successfully added a new regime and strategy to the existing trading system without modifying existing regimes or strategies.

## 1. New Regime: TREND_CONTINUATION

### Detection Logic
Added to `regime/regime_detector.py`:

**Regime = "TREND_CONTINUATION" if:**
- ADX >= 30 (strong trend)
- ATR percentile >= 50 (expanding volatility)
- Directional bias stable (EMA structure aligned):
  - LONG: price > EMA(50) > EMA(100)
  - SHORT: price < EMA(50) < EMA(100)

### Implementation Details
- Added EMA calculation function to `technical_indicators.py`
- Regime detection checks EMA structure using 100+ candles of historical data
- Does NOT weaken existing regimes (CONVEX, INCOME, NEUTRAL)
- Uses same regime persistence mechanism (N=2 confirmations)

## 2. New Strategy: Trend Following Futures

### File Structure
```
strategies/trend/
├── __init__.py
├── config.py
└── trend_follow_futures.py
```

### Strategy Rules

**Instrument:** NIFTY FUTURE

**Position Limits:**
- Max position: 1 lot
- Risk per trade ≤ 0.5% of capital

**Entry Conditions:**
- Regime == "TREND_CONTINUATION"
- LONG if price > EMA(50) > EMA(100)
- SHORT if price < EMA(50) < EMA(100)

**Stop Loss:**
- Initial SL = 1.5 × ATR(14)
- Trailing SL = 2 × ATR (Chandelier)

**Exit Rules:**
- Stop loss hit
- EMA structure breaks
- Regime != TREND_CONTINUATION

### Risk Management
- Position sizing based on risk limits
- No pyramiding
- No averaging
- No prediction (pure trend following)

## 3. Strategy Routing

Updated `strategy_runner.py`:

```python
IF regime == "INCOME":
    allow Iron Condor

ELIF regime == "NEUTRAL":
    stand aside or optional calendar

ELIF regime == "CONVEX":
    allow Backspread

ELIF regime == "TREND_CONTINUATION":
    allow Futures Trend strategy
```

## 4. Mutual Exclusion

Updated `strategies/strategy_exclusion.py`:
- Added `STRATEGY_TREND` constant
- Updated `get_active_strategy_type()` to detect trend positions
- Updated `can_enter_strategy()` to enforce mutual exclusion
- Priority order: Iron Condor > Convex > Trend > Calendar

## 5. Logging

Trade proposals include:
```json
{
  "strategy": "TREND_FOLLOW_FUTURE",
  "regime": "TREND_CONTINUATION",
  "direction": "LONG" | "SHORT",
  "entry_price": float,
  "stop_loss_price": float,
  "exit_reason": string,
  "ema_50": float,
  "ema_100": float,
  "risk_amount": float,
  "risk_pct_of_capital": float
}
```

## Files Modified

1. **regime/regime_detector.py**
   - Added TREND_CONTINUATION regime detection
   - Added EMA structure validation

2. **technical_indicators.py**
   - Added `calculate_ema()` function

3. **strategies/trend/** (new directory)
   - `__init__.py` - Module initialization
   - `config.py` - Strategy configuration
   - `trend_follow_futures.py` - Main strategy implementation

4. **strategies/strategy_exclusion.py**
   - Added STRATEGY_TREND constant
   - Updated mutual exclusion logic

5. **strategy_runner.py**
   - Added TREND_CONTINUATION routing
   - Added `_run_trend_follow_strategy()` function
   - Updated imports

## Constraints Met

✅ No modifications to existing regimes/strategies  
✅ No pyramiding  
✅ No averaging  
✅ No prediction (pure trend following)  
✅ Mutual exclusion enforced  
✅ Clean, readable Python code  
✅ Proper logging and error handling  

## Testing Recommendations

1. **Regime Detection:**
   - Verify TREND_CONTINUATION is detected when ADX >= 30, ATR% >= 50, EMA aligned
   - Verify it doesn't interfere with CONVEX/INCOME detection

2. **Strategy Entry:**
   - Test LONG entry (price > EMA50 > EMA100)
   - Test SHORT entry (price < EMA50 < EMA100)
   - Verify mutual exclusion blocks entry if other strategies active

3. **Risk Management:**
   - Verify position sizing respects 0.5% risk limit
   - Verify max 1 lot limit
   - Verify stop loss calculation (1.5 × ATR)

4. **Exit Rules:**
   - Test exit on stop loss hit
   - Test exit on EMA structure break
   - Test exit on regime change

## Usage

The strategy will automatically be considered when:
- Regime is detected as TREND_CONTINUATION
- No other strategies have active positions
- EMA structure is aligned (trending direction)
- Risk limits are satisfied

No manual intervention required - fully integrated into existing system flow.
