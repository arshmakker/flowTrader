# Shoonya Trading System

A Python-based trading system for algorithmic trading using the Shoonya API. This system provides functionality for market data collection, paper trading, and strategy testing.

## Features

- Real-time market data collection
- Paper trading with simulated orders
- Symbol management for NSE Cash and F&O markets
- Strategy testing framework
- Comprehensive logging and data storage

## Prerequisites

- Python 3.7+
- Shoonya API credentials
- Required Python packages (install using `pip install -r requirements.txt`):
  - pandas
  - numpy
  - api_helper (Shoonya API Python wrapper)

## Directory Structure

```
├── symbols/
│   ├── NSE.csv       # NSE Cash market symbols
│   └── NFO.csv       # NSE F&O market symbols
├── market_data_YYYYMMDD/
│   ├── raw_data/     # Raw market data
│   ├── processed_data/  # Processed market data
│   └── master_files/    # Daily master files
```

## Setup

1. Install required packages:
```bash
pip install -r requirements.txt
```

2. Place your NSE and NFO symbol files in the `symbols` directory:
   - `symbols/NSE.csv`: NSE Cash market symbols
   - `symbols/NFO.csv`: NSE F&O market symbols

3. Configure your Shoonya API credentials:
```python
from api_helper import ShoonyaApiPy

api = ShoonyaApiPy()
api.set_session('YOUR_SESSION_TOKEN')  # Or use login credentials
```

## Usage

### Basic Usage

```python
from api_helper import ShoonyaApiPy
from strategy_tester import StrategyTester

# Initialize API
api = ShoonyaApiPy()
api.set_session('YOUR_SESSION_TOKEN')

# Create and run strategy
strategy = StrategyTester(api)
strategy.run_strategy()
```

### Data Collection

```python
from data_collector import DataCollector

# Initialize data collector
collector = DataCollector(api)

# Start collecting data for specific symbols
symbols = [
    {'symbol': 'NIFTY-I', 'token': '26000', 'exchange': 'NFO'},
    {'symbol': 'BANKNIFTY-I', 'token': '26009', 'exchange': 'NFO'}
]
collector.start_collection(symbols)

# Stop data collection
collector.stop_collection()
```

### Paper Trading

```python
from paper_trader import PaperTrader

# Initialize paper trader with capital
trader = PaperTrader(capital=100000)

# Place orders
order_id = trader.place_order(
    symbol='NIFTY-I',
    quantity=50,
    side='BUY',
    order_type='MARKET'
)

# Close position
trader.close_position(order_id)

# Get position summary
summary = trader.get_position_summary()
print(summary)
```

## Data Files

### Symbol Files Format (NSE.csv/NFO.csv)
Required columns:
- symbol: Trading symbol
- token: Exchange token
- lotsize: Lot size (for F&O)
- tick_size: Minimum price movement

### Market Data Storage
- Raw market data is stored in CSV format in the `raw_data` directory
- Each symbol has its own file named `SYMBOL_YYYYMMDD.csv`
- Data includes: timestamp, ltp, volume, bid, ask, and open interest

## Logging

The system maintains comprehensive logs for:
- Market data collection
- Paper trading activities
- Symbol management
- Strategy execution

Logs are stored with appropriate timestamps and log levels for easy debugging.

## Error Handling

The system includes robust error handling for:
- API connection issues
- Data collection errors
- Trading errors
- File I/O operations

All errors are logged with appropriate context for troubleshooting.

## Contributing

Feel free to submit issues and enhancement requests!

## License

This project is licensed under the MIT License - see the LICENSE file for details.


