# Market Data Collection System

A Python-based market data collection system for NIFTY 50, BANKNIFTY, and FINNIFTY stocks and their index derivatives using the Shoonya API. The system collects real-time tick data for analysis purposes.

## System Architecture

### Core Components

1. **API Integration** (`api_helper.py`)
   - Wrapper for Shoonya API
   - Handles authentication and API communication
   - Manages market data subscriptions

2. **Symbol Management** (`symbol_manager.py`)
   - Manages trading symbols and contracts
   - Handles expiry calculations
   - Maintains symbol mappings for NFO and NSE

3. **Data Collection** (`data_collector.py`)
   - Real-time market data collection
   - Tick-by-tick data processing
   - Data storage in structured format
   - Automatic directory management

4. **Paper Trading** (`paper_trader.py`)
   - Simulated trading environment
   - Position management
   - Risk management
   - PnL tracking

5. **Strategy Testing** (`strategy_tester.py`)
   - Backtesting framework
   - Strategy performance analysis
   - Parameter optimization

## System Requirements

- Python 3.8+
- Required Python packages (install via pip):
  ```bash
  pandas>=1.3.0
  numpy>=1.21.0
  python-dateutil>=2.8.2
  pytz>=2021.1
  requests>=2.26.0
  PyYAML>=5.4.1
  psutil>=5.8.0
  colorama>=0.4.4
  NorenRestApi-0.0.30
  ```

## Installation & Setup

1. Clone the repository:
```bash
git clone <repository-url>
cd shoonyapythonmod
```

2. Install required packages:
```bash
pip install -r requirements.txt
```

3. Create `cred.yml` from template:
```bash
cp cred.yml.template cred.yml
```

4. Edit `cred.yml` with your credentials:
```yaml
user: "YOUR_USER_ID"
pwd: "YOUR_PASSWORD"
factor2: "YOUR_2FA"
vc: "YOUR_VENDOR_CODE"
apikey: "YOUR_API_KEY"
imei: "YOUR_IMEI"
```

## Data Collection Configuration

### Symbols Being Collected

**Cash/Equity Stocks:**
- NIFTY 50: All 50 constituent stocks
- BANKNIFTY: 12 banking stocks (HDFCBANK, ICICIBANK, KOTAKBANK, SBIN, AXISBANK, INDUSINDBK, BANKBARODA, PNB, FEDERALBNK, IDFCFIRSTB, BANDHANBNK, AUBANK)
- FINNIFTY: 20 financial sector stocks (banks + NBFCs including BAJFINANCE, BAJAJFINSV, SBILIFE, HDFCLIFE, ICICIGI, etc.)

**Index Derivatives:**
- NIFTY, BANKNIFTY, FINNIFTY
- Current month futures
- ATM options (5 strikes above and below current price)

### Data Storage Structure
Data is stored in `market_data_YYYYMMDD/` directories:
- `raw_data/cash/` - Equity tick data
- `raw_data/futures/` - Futures tick data  
- `raw_data/options/` - Options tick data (organized by underlying)

## System Workflow

1. **Initialization**
   - System loads credentials and connects to Shoonya API
   - Initializes logging system
   - Sets up data directories
   - Loads symbol information from master files

2. **Data Collection**
   - Creates date-specific directories for market data
   - Collects real-time tick data every second
   - Stores data in structured CSV format
   - Maintains separate directories for different data types

3. **Monitoring & Logging**
   - Detailed logging of all system operations
   - Regular system health checks
   - Data collection statistics

## Directory Structure

```
shoonyapythonmod/
├── main.py                 # Main entry point
├── api_helper.py           # Shoonya API wrapper
├── symbol_manager.py       # Symbol management
├── data_collector.py       # Market data collection
├── paper_trader.py         # Paper trading system
├── strategy_tester.py      # Strategy testing framework
├── example_orders.py       # Order examples
├── example_market.py       # Market data examples
├── tests/                  # Test suite
│   └── test_api.py        # API tests
├── market_data_YYYYMMDD/   # Daily market data
│   ├── raw_data/          # Raw tick data
│   └── processed_data/    # Processed market data
├── logs/                   # System logs
├── data/                   # Additional data files
├── symbols/               # Symbol information
│   ├── NFO.csv           # NFO symbols
│   └── NSE.csv           # NSE symbols
├── cred.yml              # API credentials
├── cred.yml.template     # Credentials template
└── requirements.txt      # Dependencies

## Data Collection Features

- **Real-time tick data collection** at 1-second intervals
- **Comprehensive symbol coverage** for NIFTY 50, BANKNIFTY, and FINNIFTY constituents
- **Structured data storage** organized by instrument type and date
- **Automatic directory management** with daily folders
- **Logging system** for monitoring and debugging

## Logging System

1. **System Logs** (`logs/trading_system_YYYYMMDD.log`)
   - Detailed operation logging
   - Error and warning messages
   - System performance metrics
   - API communication logs
   - Data collection statistics

2. **Market Data**
   - Raw tick data stored in CSV format
   - Automatic daily data organization
   - Separate directories for cash, futures, and options

## Development

- Use the test suite for validating changes
- Follow the example files for implementation references
- Monitor logs for system behavior
- Check data files for collection quality

## Note

This is a data collection system designed for market analysis. The collected data can be used for backtesting, research, and strategy development. Always validate data quality before using for analysis.


