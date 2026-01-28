# RegimeTrader

A Python-based multi-strategy trading system with regime detection for NIFTY derivatives using the Shoonya API. The system automatically detects market regimes and routes to appropriate strategies.

## Trading Strategies

| Regime | Strategy | Conditions |
|--------|----------|------------|
| **INCOME** | Iron Condor | IV > 60%, ADX < 20, ATR% < 50% |
| **CONVEX** | Call Backspread | IV < 40%, ATR% < 25%, Range compressed |
| **TREND_CONTINUATION** | Trend Following Futures | ADX >= 30, ATR% >= 50%, EMA aligned |
| **NEUTRAL_ACTIVE** | Calendar Spread | IV 40-60%, ADX 18-25 |
| **NEUTRAL_PASSIVE** | No trade | Conditions don't match any strategy |

## Key Features

- **Regime Detection**: Automatically identifies market conditions using IV percentile, ADX, ATR percentile, and EMA structure
- **Multi-Strategy Routing**: Routes to appropriate strategy based on detected regime
- **Contract Rollover Handling**: Adjusts for futures contract price discontinuities
- **Position Management**: Tracks positions, trailing stops, and exit conditions
- **Risk Management**: Per-trade risk limits, mutual exclusion between strategies
- **Market Data Collection**: Real-time tick data for equities, futures, and options

## System Architecture

### Core Components

1. **Regime Detection** (`regime/regime_detector.py`)
   - Detects market conditions using IV, ADX, ATR, EMA structure
   - Implements regime persistence (anti-whipsaw)
   - Handles contract rollover adjustment for accurate EMA calculation

2. **Strategy Runner** (`strategy_runner.py`)
   - Routes to appropriate strategy based on detected regime
   - Enforces mutual exclusion between strategies
   - Logs all decisions for auditability

3. **Strategies** (`strategies/`)
   - `iron_condor/` - Iron Condor for high IV, low movement markets
   - `convex/` - Call Backspread for low IV, compressed range
   - `trend/` - Trend Following Futures for trending markets
   - `neutral/` - Calendar Spread for range-bound neutral markets

4. **API Integration** (`api_helper.py`)
   - Wrapper for Shoonya API
   - Handles authentication and API communication
   - Manages market data subscriptions

5. **Symbol Management** (`symbol_manager.py`)
   - Manages trading symbols and contracts
   - Handles expiry calculations
   - Maintains symbol mappings for NFO and NSE

6. **Data Collection** (`data_collector.py`)
   - Real-time market data collection
   - Tick-by-tick data processing
   - Data storage in structured format
   - Automatic directory management

7. **Position Tracking** (`strategies/iron_condor/position_tracker.py`)
   - Tracks all open positions across strategies
   - Monitors exit conditions (stop loss, trailing stop, regime change)
   - Records performance by regime

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
cd regimetrader
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
regimetrader/
├── main.py                 # Main entry point
├── strategy_runner.py      # Strategy routing with regime detection
├── api_helper.py           # Shoonya API wrapper
├── symbol_manager.py       # Symbol management
├── data_collector.py       # Market data collection
├── technical_indicators.py # IV, ADX, ATR, EMA calculations
├── regime/                 # Regime detection
│   └── regime_detector.py  # Market regime detection logic
├── strategies/             # Trading strategies
│   ├── iron_condor/       # Iron Condor strategy
│   ├── convex/            # Call Backspread strategy
│   ├── trend/             # Trend Following Futures
│   └── neutral/           # Calendar Spread strategy
├── diagnostics/           # System diagnostics and validation
├── tests/                 # Test suite
├── market_data_YYYYMMDD/  # Daily market data
│   └── raw_data/          # Raw tick data (cash, futures, options)
├── market_data_iv/        # Historical IV data
├── market_data_atr/       # ATR history
├── logs/                  # System logs
├── symbols/               # Symbol information (NFO.csv, NSE.csv)
├── active_positions.json  # Current open positions
├── strategy_decisions.json # Strategy decision log
├── performance_by_regime.json # Performance tracking
├── cred.yml               # API credentials (from template)
└── requirements.txt       # Dependencies

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

## Web Dashboard

A simple web dashboard to monitor your trading system from your mobile device.

### Quick Start

```bash
# Install Flask (if not already installed)
pip install flask

# Run the dashboard
python web_dashboard.py
```

Then access from your mobile browser at `http://YOUR_COMPUTER_IP:5000`

See [`WEB_DASHBOARD_README.md`](WEB_DASHBOARD_README.md) for detailed instructions.

## Note

This is a data collection system designed for market analysis. The collected data can be used for backtesting, research, and strategy development. Always validate data quality before using for analysis.


