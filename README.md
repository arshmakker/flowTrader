# Index Futures Trading System

A Python-based automated trading system for index futures (NIFTY, BANKNIFTY, FINNIFTY) using the Shoonya API. The system implements paper trading with real-time data collection and automated trading strategies.

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

## System Workflow

1. **Initialization**
   - System loads credentials and connects to Shoonya API
   - Initializes logging system
   - Sets up data directories
   - Loads symbol information

2. **Data Collection**
   - Creates date-specific directories for market data
   - Collects real-time tick data for index futures
   - Processes and stores data in raw and processed formats
   - Maintains separate directories for different data types

3. **Trading Operations**
   - Monitors market data in real-time
   - Applies trading strategies
   - Manages paper trading positions
   - Implements risk management rules

4. **Monitoring & Logging**
   - Detailed logging of all system operations
   - Regular system health checks
   - Performance monitoring
   - Position and PnL tracking

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

## Testing

The system includes a comprehensive testing framework:
- Unit tests for API functionality
- Strategy backtesting capabilities
- Paper trading simulation
- Performance analysis tools

## Logging System

1. **System Logs** (`logs/trading_system_YYYYMMDD.log`)
   - Detailed operation logging
   - Error and warning messages
   - System performance metrics
   - API communication logs

2. **Trading Logs**
   - Trade execution details
   - Position management
   - PnL tracking
   - Risk metrics

3. **Market Data**
   - Raw tick data
   - Processed market data
   - Daily data organization
   - Backup and archival

## Development

- Use the test suite for validating changes
- Follow the example files for implementation references
- Monitor logs for system behavior
- Use strategy tester for algorithm validation

## Note

This is a paper trading system designed for testing and development. Always validate strategies thoroughly before live trading. Past performance does not guarantee future results.


