# Futures Trend Following Backtest - Implementation Summary

## ✅ Implementation Complete

Created `backtest_trend_following.py` - A comprehensive backtesting framework for the Futures Trend Following strategy.

## Features

### 1. **Data Loading**
- Loads NIFTY futures tick data from `market_data_YYYYMMDD/raw_data/futures/`
- Handles multiple futures contracts per day
- Filters invalid prices

### 2. **Data Aggregation**
- Converts tick data to 15-minute candles
- Maintains OHLC (Open, High, Low, Close) data
- Preserves volume information

### 3. **Technical Indicators**
- **EMA(50)** and **EMA(100)** on 15-minute candles
- **ATR(14)** calculation using RegimeDetector method
- **ADX(14)** calculation for trend strength
- Maintains rolling window of 100 candles for EMA calculation

### 4. **Regime Detection**
- Simplified TREND_CONTINUATION detection:
  - ADX >= 30
  - ATR percentile >= 50 (currently hardcoded to 75 for backtest)
  - EMA structure aligned (price > EMA50 > EMA100 for LONG, or opposite for SHORT)

### 5. **Entry Logic**
- Checks regime == TREND_CONTINUATION
- Validates ADX >= 30
- Validates ATR percentile >= 50
- Detects trend direction from EMA structure
- Calculates position size based on risk limits:
  - Max risk: 0.5% of capital per trade
  - Max position: 1 lot (50 shares)
  - Initial stop loss: 1.5 × ATR

### 6. **Exit Logic**
- **Stop Loss Hit**: Price crosses initial or trailing stop
- **Regime Change**: Regime != TREND_CONTINUATION
- **EMA Structure Break**: Trend structure no longer aligned
- **Trailing Stop**: Updates dynamically (2 × ATR from current price)

### 7. **Position Management**
- Tracks open positions with:
  - Entry price, direction, quantity
  - Initial and current stop loss prices
  - Entry indicators (ATR, EMA values)
- Updates trailing stops on each check
- Closes positions at end of backtest if still open

### 8. **Performance Reporting**
- Total trades, win rate
- Total P&L, average P&L per trade
- Max profit, max loss
- Performance by direction (LONG vs SHORT)
- Exit reason breakdown
- Detailed trade log saved to JSON

## Usage

```bash
python3 backtest_trend_following.py
```

### Configuration

Modify in `main()` function:
- **Date range**: `backtester.run_backtest('20251222', '20260116', ...)`
- **Check interval**: `check_interval_minutes=15` (default: 15 minutes)
- **Initial capital**: `initial_capital=1000000` (default: ₹10L)

## Data Requirements

### Required Data Structure:
```
market_data_YYYYMMDD/
└── raw_data/
    └── futures/
        └── NIFTY*.csv  (NIFTY futures tick data)
```

### CSV Format:
- `timestamp`: DateTime
- `ltp`: Last traded price (float)
- `bid`: Bid price (optional)
- `ask`: Ask price (optional)
- `volume`: Volume (optional)
- `oi`: Open interest (optional)

## Current Limitations

1. **ATR Percentile**: Currently hardcoded to 75.0 for backtest
   - **Future Enhancement**: Calculate from historical ATR values stored in `market_data_atr/`

2. **Regime Detection**: Simplified version
   - **Future Enhancement**: Use full RegimeDetector with proper ATR history

3. **End-of-Day Positions**: Uses entry price as exit price
   - **Future Enhancement**: Use last candle's close price

4. **Slippage**: Not modeled
   - **Future Enhancement**: Add realistic slippage (0.05-0.1%)

5. **Transaction Costs**: Not included
   - **Future Enhancement**: Add brokerage, taxes, STT

## Expected Output

```
============================================================
FUTURES TREND FOLLOWING BACKTEST REPORT
============================================================
Total Trades: X
Winning Trades: Y
Losing Trades: Z
Win Rate: W%

Total P&L: ₹X.XX
Average P&L per Trade: ₹Y.YY
Max Profit: ₹A.AA
Max Loss: ₹B.BB

Long Trades: X (P&L: ₹Y.YY)
Short Trades: Z (P&L: ₹W.WW)

Initial Capital: ₹1000000.00
Final Capital: ₹XXXXXX.XX
Total Return: X.XX%

Exit Reasons:
  STOP_LOSS_HIT: X trades, P&L: ₹Y.YY
  REGIME_CHANGE: Z trades, P&L: ₹W.WW
  EMA_STRUCTURE_BROKEN: A trades, P&L: ₹B.BB
============================================================
```

## Next Steps

1. **Run Initial Backtest**: Test on available data (Dec 22, 2025 - Jan 16, 2026)
2. **Analyze Results**: Review trade log and performance metrics
3. **Enhance ATR Percentile**: Implement proper historical ATR calculation
4. **Add Transaction Costs**: Include realistic slippage and fees
5. **Optimize Parameters**: Test different check intervals, stop loss multipliers
6. **Compare with Other Strategies**: Run alongside Iron Condor, Convex Backspread backtests

## Files Created

- `backtest_trend_following.py`: Main backtest script
- `BACKTEST_TREND_IMPLEMENTATION.md`: This documentation
