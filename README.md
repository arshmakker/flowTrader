# Index Futures Trading System

A Python-based automated trading system for index futures (NIFTY, BANKNIFTY, FINNIFTY) using the Shoonya API.

## Features

- Real-time data collection for index futures
- Paper trading with realistic slippage and margin requirements
- Momentum-based trading strategy with volume confirmation
- Risk management with trailing stops and partial profit booking
- Proper position sizing based on available capital
- Trading time restrictions (9:30-11:30 and 13:30-15:15)

## System Requirements

- Python 3.8+
- Required Python packages (install via pip):
  - pandas
  - numpy
  - requests
  - pyyaml
  - psutil

## Installation

1. Clone the repository:
```bash
git clone <repository-url>
cd shoonyapythonmod
```

2. Install required packages:
```bash
pip install -r requirements.txt
```

3. Create a `cred.yml` file with your Shoonya API credentials:
```yaml
user: "YOUR_USER_ID"
pwd: "YOUR_PASSWORD"
factor2: "YOUR_2FA"
vc: "YOUR_VENDOR_CODE"
apikey: "YOUR_API_KEY"
imei: "YOUR_IMEI"
```

## Trading Parameters

### NIFTY
- Lot Size: 50
- Minimum Movement: 5 points
- Initial Stop: 8 points
- Target 1: 8 points (Exit 40%)
- Target 2: 12 points (Exit 30%)
- Trailing Stop: 3 points
- Margin per Lot: ₹23,000
- Maximum Lots: 3

### BANKNIFTY
- Lot Size: 15
- Minimum Movement: 12 points
- Initial Stop: 15 points
- Target 1: 20 points (Exit 40%)
- Target 2: 30 points (Exit 30%)
- Trailing Stop: 6 points
- Margin per Lot: ₹49,000
- Maximum Lots: 2

### FINNIFTY
- Lot Size: 40
- Minimum Movement: 8 points
- Initial Stop: 12 points
- Target 1: 15 points (Exit 40%)
- Target 2: 22 points (Exit 30%)
- Trailing Stop: 4 points
- Margin per Lot: ₹23,000
- Maximum Lots: 3

## Usage

1. Start the trading system:
```bash
python main.py
```

2. Monitor the logs:
- Trading logs: `logs/trading_system_YYYYMMDD.log`
- Paper trade logs: `paper_trades_YYYYMMDD.csv`

## Directory Structure

```
shoonyapythonmod/
├── main.py                 # Main entry point
├── data_collector.py       # Market data collection
├── paper_trader.py         # Paper trading implementation
├── symbol_manager.py       # Symbol and contract management
├── api_helper.py           # Shoonya API wrapper
├── cred.yml               # API credentials (create this)
├── requirements.txt       # Python dependencies
├── logs/                  # Log files
└── market_data_YYYYMMDD/  # Collected market data
    ├── raw_data/         # Raw tick data
    └── processed_data/   # Processed data
```

## Risk Management

1. Capital Protection:
   - Maximum 2% daily loss limit
   - Maximum 3 trades per day
   - No new trades after 2 consecutive losses

2. Position Management:
   - Initial entry with 60% of intended position size
   - First target exits 40% of position
   - Second target exits 30% of position
   - Trailing stop on remaining 30%

3. Margin Requirements:
   - Maximum 30% capital per trade
   - Proper lot size calculation based on available margin

## Data Collection

The system collects real-time market data for:
- Current month index futures
- Tick-by-tick data including LTP, volume, and bid-ask
- Data saved in CSV format for analysis

## Logging

1. System Logs:
   - Trading decisions and executions
   - Position management actions
   - Error and warning messages
   - System performance metrics

2. Trade Logs:
   - Entry and exit prices
   - Position sizes and partial exits
   - PnL calculations
   - Reason for exits

## Note

This is a paper trading system. Always test thoroughly before using with real money. Past performance does not guarantee future results.


