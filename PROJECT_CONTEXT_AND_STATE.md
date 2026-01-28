## Project context

- **Name**: `ironcondor`
- **Goal**: Options strategy system with regime detection + strategy selection; includes historical market-data storage and backtesting utilities.
- **Market data layout**: `market_data_YYYYMMDD/raw_data/{futures,options,...}` with per-underlying option CSVs.
- **Backtest**: `backtest_iron_condor.py` runs Iron Condor proposal/exit logic against stored tick data.

## Current state (2026-01-28)

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
