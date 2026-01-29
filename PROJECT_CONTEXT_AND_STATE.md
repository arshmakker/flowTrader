## Project context

- **Name**: `regimetrader`
- **Goal**: Multi-strategy trading system with regime detection that automatically routes to appropriate strategies based on market conditions. Supports Iron Condor (INCOME), Call Backspread (CONVEX), Trend Following Futures (TREND_CONTINUATION), and Calendar Spreads (NEUTRAL). Includes market data collection, position tracking, and backtesting utilities.
- **Market data layout**: `market_data_YYYYMMDD/raw_data/{futures,options,...}` with per-underlying option CSVs.
- **Backtest**: `backtest_iron_condor.py` runs Iron Condor proposal/exit logic against stored tick data.

## Current state (2026-01-29)

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
The system detects market regimes and routes to appropriate strategies:
- **CONVEX** → Call Backspread (IV < 40%, ATR% < 25%, range compressed)
- **INCOME** → Iron Condor (IV > 60%, ADX < 20, ATR% < 50%)
- **TREND_CONTINUATION** → Trend Following Futures (ADX >= 30, ATR% >= 50%, EMA aligned)
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
