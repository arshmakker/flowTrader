# Iron Condor Trading System - Complete Logic and Flow Documentation

## 📋 System Overview

An automated Iron Condor options trading system that:
- Collects real-time market data
- Analyzes market conditions using technical indicators
- Generates trade recommendations based on rule-based strategy
- Tracks positions and manages exits automatically

---

## 🏗️ System Architecture

### Core Components

```
main.py (Entry Point)
├── API Initialization (ShoonyaApiPy)
├── Symbol Manager (SymbolManager)
├── Data Collector (DataCollector)
├── Position Tracker (IronCondorPositionTracker)
└── Strategy Runner (run_iron_condor_strategy)
    └── Iron Condor Strategy Module
        ├── Eligibility Check
        ├── Strike Selection
        ├── Payoff Validation
        ├── Position Sizing
        └── Margin Calculation
```

---

## 🔄 Main Execution Flow

### 1. System Initialization (`main.py`)

```python
main()
├── setup_logging() → Initialize logging system
├── load_credentials() → Load cred.yml
├── initialize_api() → Login to Shoonya API (requires 2FA)
├── SymbolManager() → Load symbol files (NFO.csv, NSE.csv)
├── DataCollector() → Start data collection
└── IronCondorPositionTracker() → Initialize position tracking
```

**Timing Configuration:**
- **Strategy checks**: Every 5 minutes (300 seconds)
- **Position monitoring**: Every 1 minute (60 seconds)
- **IV calculation**: Every 2 minutes (120 seconds)

---

### 2. Main Loop (`main.py` lines 198-370)

```
WHILE True:
    ├── Check market close (3:30 PM) → Exit if closed
    │
    ├── IV Calculation (every 2 min)
    │   ├── Get NIFTY spot price
    │   ├── Get nearest expiry
    │   ├── Fetch option chain (10 strikes)
    │   └── calculate_iv_percentile() → Save to historical data
    │
    ├── Strategy Check (every 5 min)
    │   └── run_iron_condor_strategy()
    │
    └── Position Monitoring (every 1 min)
        ├── Get active positions
        ├── Fetch current prices
        ├── Calculate P&L
        └── Check profit target (1% of margin)
    
    Sleep(1 second)
```

---

## 🎯 Strategy Execution Flow

### 3. Strategy Runner (`strategy_runner.py`)

```python
run_iron_condor_strategy(api, symbol_manager, position_tracker)
│
├── Step 1: Get NIFTY Spot Price
│   └── get_nifty_spot_price(api, symbol_manager)
│
├── Step 2: Get Available Expiries
│   └── get_all_eligible_expiries(symbol_manager, max_expiries_to_check=7)
│       └── Returns: List of expiry dates (sorted by proximity)
│
├── Step 3: Loop Through Expiries
│   FOR each expiry_date:
│       ├── Fetch Option Chain
│       │   └── get_option_chain_data(api, symbol_manager, spot_price, expiry_date, count=50)
│       │       └── Returns: DataFrame with option chain data
│       │
│       ├── Build Market State
│       │   └── build_market_state_from_chain(option_chain, spot_price, expiry_date)
│       │       ├── Calculate IV Percentile
│       │       ├── Calculate ADX(14)
│       │       ├── Calculate Days to Expiry
│       │       ├── Check for major events
│       │       └── Returns: market_state dict
│       │
│       ├── Generate Trade Proposal
│       │   └── generate_iron_condor_trade(market_state, option_chain, api, userid, symbol_manager)
│       │
│       └── IF trade_proposal found:
│           ├── save_trade_proposal() → Save to JSON file
│           ├── position_tracker.add_position() → Track position
│           └── RETURN trade_proposal
│
└── RETURN None (if no valid trade found)
```

---

## 📊 Iron Condor Trade Generation

### 4. Trade Generation (`strategies/iron_condor/strategy.py`)

```python
generate_iron_condor_trade(market_state, option_chain, api, userid, symbol_manager)
│
├── Step 1: Eligibility Check
│   └── is_market_eligible(market_state)
│       ├── IV Percentile: 50-100% ✓
│       ├── Days to Expiry: 3-30 days ✓
│       ├── ADX(14) < 22 ✓
│       ├── No major events in next 48h ✓
│       └── Instrument = NIFTY (WEEKLY/MONTHLY) ✓
│
├── Step 2: Strike Selection
│   └── select_strikes(option_chain, spot_price)
│       ├── Find Short Call (Delta: 0.15-0.20)
│       ├── Find Short Put (Delta: -0.20 to -0.15)
│       ├── Find Long Call (Wing width: 100-150 points above short call)
│       └── Find Long Put (Wing width: 100-150 points below short put)
│
├── Step 3: Payoff Validation
│   └── validate_payoff(legs, spot_price, days_to_expiry, iv, option_chain)
│       ├── Calculate Net Credit (must be ₹30-110 per lot)
│       ├── Calculate Max Loss (must be < ₹1500 per lot)
│       ├── Calculate Reward-to-Risk (must be ≥ 0.9)
│       └── Calculate Probability of Profit
│
├── Step 4: Position Sizing
│   └── calculate_lots(max_loss_per_lot)
│       └── Based on MAX_PER_TRADE_RISK (₹30,000)
│
├── Step 5: Get Lot Size
│   └── From option_chain or symbol_manager.nse_fo
│
├── Step 6: Calculate Margin
│   └── calculate_iron_condor_margin(api, legs, lots, expiry_date, ...)
│       ├── Use Shoonya SPAN Calculator API
│       ├── Calculate premium paid for long legs
│       └── Total Margin = SPAN Margin + Premium Paid
│
└── Step 7: Build Trade Proposal
    └── Returns: Dictionary with all trade details
```

---

## 📈 Market State Building

### 5. Market State (`strategy_runner.py`)

```python
build_market_state_from_chain(option_chain, spot_price, expiry_date)
│
├── Calculate IV Percentile
│   └── calculate_iv_percentile(option_chain, spot_price, days_to_expiry)
│       ├── Get ATM IV using Shoonya API (iterative search)
│       ├── Compare with historical IV data
│       └── Return percentile (0-100%)
│
├── Calculate ADX(14)
│   └── calculate_adx(spot_price, period=14)
│       └── Uses historical price data
│
├── Calculate Days to Expiry
│   └── (expiry_date - current_date).days
│
├── Check Major Events
│   └── check_major_events() → Returns False (placeholder)
│
└── Returns: {
    'iv_percentile': float,
    'adx_14': float,
    'days_to_expiry': int,
    'has_major_event': bool,
    'instrument': 'NIFTY',
    'instrument_type': 'WEEKLY' or 'MONTHLY',
    'spot_price': float,
    'expiry': 'YYYY-MM-DD',
    'current_iv': float (optional)
}
```

---

## 💼 Position Tracking and Exit Management

### 6. Position Monitoring (`main.py` lines 281-368)

```python
Position Monitoring Loop (every 1 minute)
│
├── Get Active Positions
│   └── position_tracker.get_active_positions()
│
├── FOR each position:
│   ├── Get Current Spot Price
│   ├── Fetch Option Chain for expiry
│   ├── Build Current Prices Dict
│   │   └── Get current mid_price for each leg
│   │
│   ├── Calculate Current P&L
│   │   └── position_tracker.calculate_current_pnl(position, current_prices)
│   │       ├── Calculate current position value
│   │       └── P&L = entry_credit - current_value
│   │
│   └── Check Profit Target
│       └── position_tracker.check_profit_target(position, current_pnl)
│           ├── profit_target = margin_used × 0.01 (1%)
│           └── IF current_pnl >= profit_target:
│               ├── Close position
│               └── Mark for exit
```

---

## 📥 Data Flow

### 7. Data Collection (`data_collector.py`)

```
DataCollector.start_collection()
│
├── Subscribe to Market Data
│   ├── Equity stocks (NIFTY 50, BANKNIFTY, FINNIFTY constituents)
│   ├── Index futures (NIFTY, BANKNIFTY, FINNIFTY)
│   └── Options (ATM ± 5 strikes)
│
└── Store Data
    └── market_data_YYYYMMDD/
        ├── raw_data/cash/
        ├── raw_data/futures/
        └── raw_data/options/
```

---

## 🧩 Key Modules

### 8. Module Responsibilities

| Module | Purpose | Key Functions |
|--------|---------|---------------|
| `main.py` | System orchestration | Main loop, timing, coordination |
| `strategy_runner.py` | Strategy integration | Option chain fetching, market state building |
| `strategies/iron_condor/strategy.py` | Trade generation | Orchestrates trade proposal creation |
| `strategies/iron_condor/eligibility.py` | Market filtering | Checks if market conditions are suitable |
| `strategies/iron_condor/strike_selector.py` | Strike selection | Finds optimal strikes for 4 legs |
| `strategies/iron_condor/payoff_validator.py` | Risk validation | Validates net credit, max loss, R:R ratio |
| `strategies/iron_condor/position_sizer.py` | Position sizing | Calculates number of lots based on risk |
| `strategies/iron_condor/margin_calculator.py` | Margin calculation | Uses Shoonya SPAN API |
| `strategies/iron_condor/position_tracker.py` | Position management | Tracks open positions, P&L, exits |
| `technical_indicators.py` | Technical analysis | IV percentile, ADX, historical data |
| `shoonya_iv_fetcher.py` | IV calculation | Iterative search using Shoonya API |
| `symbol_manager.py` | Symbol management | Manages NFO/NSE symbols, expiries |
| `data_collector.py` | Data collection | Real-time market data collection |

---

## ⚙️ Configuration

### 9. Strategy Parameters (`strategies/iron_condor/config.py`)

```python
# Market Eligibility
IV_PERCENTILE_MIN = 50
IV_PERCENTILE_MAX = 100
DAYS_TO_EXPIRY_MIN = 3
DAYS_TO_EXPIRY_MAX = 30
ADX_THRESHOLD = 22

# Strike Selection
SHORT_CALL_DELTA_MIN = 0.15
SHORT_CALL_DELTA_MAX = 0.20
SHORT_PUT_DELTA_MIN = -0.20
SHORT_PUT_DELTA_MAX = -0.15
WING_WIDTH_MIN = 100
WING_WIDTH_MAX = 150

# Payoff Validation
NET_CREDIT_MIN = 30.0  # ₹ per lot
NET_CREDIT_MAX = 110.0
MAX_LOSS_PER_LOT_MAX = 1500.0
MIN_REWARD_TO_RISK = 0.9

# Position Sizing
MAX_PER_TRADE_RISK = 30000.0  # ₹
CAPITAL_ALLOCATED = 1000000.0  # ₹10L

# Exit Rules
PROFIT_TARGET_MARGIN_PCT = 0.01  # 1% of margin
```

---

## 📄 Trade Proposal Structure

### 10. Trade Proposal Format

```json
{
  "strategy": "IRON_CONDOR_WEEKLY",
  "expiry": "2026-01-27",
  "legs": [
    {
      "position": "SHORT",
      "option_type": "CE",
      "strike": 26100.0,
      "price": 154.425,
      "delta": 0.0,
      "ltp": 154.75,
      "bid": 154.2,
      "ask": 154.65,
      "oi": 1914120,
      "volume": 4080375
    },
    {
      "position": "SHORT",
      "option_type": "PE",
      "strike": 25600.0,
      "price": 78.80,
      ...
    },
    {
      "position": "LONG",
      "option_type": "CE",
      "strike": 26200.0,
      "price": 116.6,
      ...
    },
    {
      "position": "LONG",
      "option_type": "PE",
      "strike": 25500.0,
      "price": 61.825,
      ...
    }
  ],
  "lots": 663,
  "max_profit": 36332.40,
  "max_loss": 29967.60,
  "net_credit": 54.80,
  "net_credit_total": 36332.40,
  "reward_to_risk": 1.21,
  "spot_price": 25876.7,
  "lot_size": 65,
  "margin_used": 500000.0,
  "profit_target_margin": 5000.0,
  "exit_rules": {
    "profit_target_margin_pct": 0.01,
    "profit_target_pct": [0.5, 0.6],
    "stop_loss_multiplier": 1.2,
    "mandatory_exit_dte": 1,
    "mandatory_exit_time": "14:30"
  }
}
```

---

## 🌳 Decision Flow

### 11. Trade Decision Tree

```
Market Open?
├── NO → Wait
└── YES
    └── Strategy Check (every 5 min)
        └── Get Spot Price
            └── Loop Through Expiries
                └── Fetch Option Chain
                    └── Build Market State
                        └── Eligibility Check
                            ├── IV Percentile 50-100%? → NO → Skip
                            ├── DTE 3-30 days? → NO → Skip
                            ├── ADX < 22? → NO → Skip
                            ├── No major events? → NO → Skip
                            └── All Pass? → YES
                                └── Select Strikes
                                    └── Validate Payoff
                                        ├── Net Credit ₹30-110? → NO → Reject
                                        ├── Max Loss < ₹1500? → NO → Reject
                                        └── R:R ≥ 0.9? → NO → Reject
                                            └── All Pass? → YES
                                                └── Size Position
                                                    └── Calculate Margin
                                                        └── Generate Trade Proposal
                                                            └── Save & Track
```

---

## 📁 File Outputs

### 12. Output Files

- **Trade Proposals**: `trade_proposals/iron_condor_YYYYMMDD_HHMMSS.json`
- **Active Positions**: `active_positions.json`
- **Logs**: `logs/trading_system_YYYYMMDD.log`
- **Market Data**: `market_data_YYYYMMDD/raw_data/`

---

## 🔍 Detailed Component Flows

### 13. IV Calculation Flow

```
calculate_iv_percentile(option_chain, spot_price, days_to_expiry)
│
├── Get ATM IV
│   └── calculate_atm_iv(option_chain, spot_price, api, symbol_manager)
│       ├── Find ATM Call and Put
│       ├── Use Shoonya API option_greek function
│       ├── Iterative search (Brent's method) to find IV
│       └── Returns: IV as percentage (e.g., 18.0)
│
├── Load Historical IV Data
│   └── From market_data_iv/ directory
│
├── Calculate Percentile
│   └── Compare current IV with historical distribution
│
└── Return: IV Percentile (0-100%)
```

### 14. Strike Selection Flow

```
select_strikes(option_chain, spot_price)
│
├── Find Short Call
│   └── Filter by: Delta 0.15-0.20 OR Distance 0.8-1.2% from spot
│
├── Find Short Put
│   └── Filter by: Delta -0.20 to -0.15 OR Distance 0.8-1.2% from spot
│
├── Find Long Call (Hedge)
│   └── Filter by: Strike = Short Call Strike + 100-150 points
│
└── Find Long Put (Hedge)
    └── Filter by: Strike = Short Put Strike - 100-150 points
```

### 15. Payoff Calculation Flow

```
validate_payoff(legs, spot_price, days_to_expiry, iv, option_chain)
│
├── Calculate Net Credit
│   └── (Short Call Premium + Short Put Premium) - 
│       (Long Call Premium + Long Put Premium)
│
├── Calculate Max Loss
│   └── Wing Width - Net Credit
│
├── Calculate Reward-to-Risk
│   └── Net Credit / Max Loss
│
├── Calculate Probability of Profit
│   └── Based on IV, DTE, and strike distances
│
└── Validate All Thresholds
    ├── Net Credit: ₹30-110 ✓
    ├── Max Loss: < ₹1500 ✓
    └── R:R: ≥ 0.9 ✓
```

### 16. Margin Calculation Flow

```
calculate_iron_condor_margin(api, legs, lots, expiry_date, ...)
│
├── Format Expiry Date
│   └── Convert to DD-MMM-YYYY format (e.g., "13-JAN-2026")
│
├── Build Position List
│   └── Format for Shoonya SPAN API:
│       [
│         {"exch": "NFO", "tsym": "NIFTY13JAN26C26100", "qty": "-65", ...},
│         {"exch": "NFO", "tsym": "NIFTY13JAN26P25600", "qty": "-65", ...},
│         {"exch": "NFO", "tsym": "NIFTY13JAN26C26200", "qty": "65", ...},
│         {"exch": "NFO", "tsym": "NIFTY13JAN26P25500", "qty": "65", ...}
│       ]
│
├── Call SPAN Calculator API
│   └── api.span_calculator(actid, position_list)
│
├── Extract SPAN Margin
│   └── Parse API response to get margin requirement
│
├── Calculate Premium Paid
│   └── Sum of (Long Leg Price × Lots × Lot Size)
│
└── Return Total Margin
    └── SPAN Margin + Premium Paid
```

---

## 🔐 API Integration Details

### 17. Shoonya API Usage

**Authentication:**
- Requires 2FA code on startup
- Credentials stored in `cred.yml`
- Session maintained throughout runtime

**Key API Functions Used:**
- `api.login()` - Authentication
- `api.get_quotes()` - Fetch option chain data
- `api.option_greek()` - Get theoretical option price (for IV calculation)
- `api.span_calculator()` - Calculate margin requirement
- `api.get_time_price_series()` - Historical price data (for ADX)

**IV Calculation via API:**
- Shoonya API doesn't provide IV directly
- Uses `option_greek()` which takes volatility as input
- Performs iterative search (Brent's method) to find IV that matches market price
- Implemented in `shoonya_iv_fetcher.py`

---

## 📊 Data Structures

### 18. Option Chain DataFrame Structure

```python
option_chain.columns = [
    'strike',           # Strike price
    'option_type',      # 'CE' or 'PE'
    'ltp',              # Last traded price
    'bid',              # Bid price
    'ask',              # Ask price
    'mid_price',        # (bid + ask) / 2
    'oi',               # Open interest
    'volume',           # Volume
    'delta',            # Option delta (if available)
    'lot_size'          # Lot size (from NFO.csv)
]
```

### 19. Market State Dictionary Structure

```python
market_state = {
    'iv_percentile': float,        # 0-100
    'adx_14': float,               # Average Directional Index
    'days_to_expiry': int,         # Days until expiry
    'has_major_event': bool,        # True if event in next 48h
    'instrument': str,             # 'NIFTY'
    'instrument_type': str,       # 'WEEKLY' or 'MONTHLY'
    'spot_price': float,           # Current spot price
    'expiry': str,                 # 'YYYY-MM-DD'
    'current_iv': float           # Current IV (optional)
}
```

---

## 🚨 Error Handling

### 20. Error Handling Strategy

**API Errors:**
- Retry logic for transient failures
- Fallback to default values (e.g., IV = 18% if API fails)
- Logging at appropriate levels (DEBUG for expected, WARNING for issues)

**Data Validation:**
- Check for None/empty values before processing
- Validate spot prices > 0
- Ensure option chain is not empty

**Strategy Errors:**
- Catch exceptions at each step
- Log rejection reasons
- Continue to next expiry if one fails

---

## 📝 Summary

### Complete Flow Summary

1. **System starts** → Initializes API, symbols, data collection
2. **Main loop runs** → Collects data, checks strategy, monitors positions
3. **Strategy check** (every 5 min) → Fetches option chain, builds market state
4. **Eligibility check** → Filters by IV, ADX, DTE, events
5. **Strike selection** → Finds 4 strikes (2 short, 2 long)
6. **Payoff validation** → Checks credit, loss, R:R ratio
7. **Position sizing** → Calculates lots based on risk
8. **Margin calculation** → Uses SPAN API
9. **Trade proposal** → Saved to JSON, tracked in position tracker
10. **Position monitoring** (every 1 min) → Checks P&L, profit target (1% of margin)

This flow runs continuously during market hours (9:15 AM - 3:30 PM IST) until a valid trade is found or market closes.

---

## 🔄 Update Points

When updating the system, consider these key areas:

1. **Configuration** (`strategies/iron_condor/config.py`) - Adjust thresholds and parameters
2. **Eligibility Rules** (`strategies/iron_condor/eligibility.py`) - Modify market condition checks
3. **Strike Selection** (`strategies/iron_condor/strike_selector.py`) - Change delta ranges or wing widths
4. **Payoff Validation** (`strategies/iron_condor/payoff_validator.py`) - Adjust risk/reward criteria
5. **Exit Rules** (`strategies/iron_condor/exit_rules.py`) - Modify profit targets and stop losses
6. **Main Loop Timing** (`main.py`) - Adjust check intervals
7. **IV Calculation** (`technical_indicators.py`, `shoonya_iv_fetcher.py`) - Modify IV calculation logic

---

*Last Updated: 2026-01-09*
