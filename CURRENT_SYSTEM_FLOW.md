# Iron Condor Trading System - Current Flow Documentation

## 📋 System Overview

A regime-aware automated options trading system that:
- Collects real-time market data
- Detects market regimes (CONVEX, INCOME, NEUTRAL)
- Routes to appropriate strategies based on regime:
  - **INCOME regime** → Iron Condor (credit spread)
  - **CONVEX regime** → Convex Backspread (debit spread)
  - **NEUTRAL regime** → No new trades
- Enforces mutual exclusion between strategies
- Tracks positions and manages exits automatically
- Logs performance by regime

---

## 🏗️ System Architecture

### Core Components

```
main.py (Entry Point)
├── API Initialization (ShoonyaApiPy)
├── Symbol Manager (SymbolManager)
├── Data Collector (DataCollector)
├── Position Tracker (IronCondorPositionTracker)
└── Strategy Runner (run_strategy_with_regime)
    ├── Regime Detector (RegimeDetector)
    ├── Iron Condor Strategy (INCOME regime)
    │   ├── Eligibility Check
    │   ├── Strike Selection
    │   ├── Payoff Validation
    │   ├── Position Sizing
    │   └── Margin Calculation
    └── Convex Backspread Strategy (CONVEX regime)
        ├── Strike Selection
        ├── Payoff Validation
        └── Position Sizing
```

---

## 🔄 Main Execution Flow

### 1. System Initialization (`main.py`)

```python
main()
├── setup_logging() → Initialize logging system
├── load_credentials() → Load cred.yml
├── initialize_api() → Login to Shoonya API (requires 2FA)
├── SymbolManager() → Load symbol files (NFO.csv, NSE.csv, BSE.csv)
├── DataCollector() → Start data collection
└── IronCondorPositionTracker() → Initialize position tracking
```

**Timing Configuration:**
- **Strategy checks**: Every 5 minutes (300 seconds)
- **Position monitoring**: Every 1 minute (60 seconds)
- **IV calculation**: Every 2 minutes (120 seconds)

---

### 2. Main Loop (`main.py` lines 198-378)

```
WHILE True (every 1 second):
│
├── Check market close (3:30 PM) → Exit if closed
│
├── [Every 2 minutes] IV Calculation
│   ├── Get NIFTY spot price
│   ├── Get nearest expiry
│   ├── Fetch option chain (10 strikes)
│   └── calculate_iv_percentile() → Save to historical data
│
├── [Every 5 minutes] Strategy Check with Regime Detection
│   └── run_strategy_with_regime()
│       ├── Get spot price
│       ├── Get available expiries (up to 7)
│       ├── Fetch option chain (30 strikes)
│       ├── Build market state (IV%, ADX, DTE, etc.)
│       ├── Detect Regime (CONVEX/INCOME/NEUTRAL)
│       └── Route by regime:
│           ├── INCOME → Iron Condor (if no Convex active)
│           ├── CONVEX → Convex Backspread (if no Iron Condor active)
│           └── NEUTRAL → No trades
│
└── [Every 1 minute] Position Monitoring
    ├── Get active positions
    ├── Fetch current option prices
    ├── Calculate P&L
    └── Check profit target (1% of margin)
        └── If reached → Close position
    
    Sleep(1 second)
```

---

## 🎯 Regime Detection Flow

### 3. Regime Detection (`regime/regime_detector.py`)

```
detect_regime(market_state, recent_candles, api, symbol_manager)
│
├── Check cache (15-minute cache)
│
├── Extract inputs:
│   ├── IV Percentile (from market_state)
│   ├── ADX (from market_state)
│   └── Spot Price (from market_state)
│
├── Get recent candles (for range calculation)
│   └── get_recent_candles(api, symbol_manager, spot_price)
│       ├── Try to get from stored data (last 17 days)
│       └── If not available, fetch from API
│
├── Calculate ATR (Average True Range):
│   ├── Get historical price data (17 days)
│   ├── Calculate ATR(14) from last 15 periods
│   │   └── For each day: TR = max(H-L, |H-C_prev|, |L-C_prev|)
│   │   └── ATR(14) = Average of last 14 TR values
│   ├── Calculate historical ATRs (loop through days)
│   │   └── Generate 3 historical ATR values
│   └── Calculate ATR percentile (current vs historical)
│       └── Requires minimum 2 historical values
│
├── Calculate price range:
│   ├── Last 60 minutes range (high - low)
│   ├── Rolling average range (last 20 candles)
│   └── Range state: COMPRESSED if last_range < (rolling_avg × 0.6)
│
└── Determine regime:
    ├── CONVEX: 
    │   ├── IV Percentile < 40%
    │   ├── ATR Percentile < 25%
    │   └── Range COMPRESSED (< 60% of rolling average)
    │
    ├── INCOME:
    │   ├── IV Percentile > 60%
    │   ├── ADX < 20 (low trend strength)
    │   └── ATR Percentile < 50% (ATR not expanding)
    │
    └── NEUTRAL: Everything else
```

**Regime Detection Logic:**
- **CONVEX**: Low IV (< 40%), low ATR (< 25%), compressed range → Expect volatility expansion
- **INCOME**: High IV (> 60%), low ADX (< 20), stable ATR → Range-bound, high premium
- **NEUTRAL**: All other conditions → Wait for clearer signal

---

## 🎯 Strategy Execution Flow

### 4. Strategy Runner with Regime Routing (`strategy_runner.py`)

```python
run_strategy_with_regime(api, symbol_manager, position_tracker, capital)
│
├── Step 1: Get NIFTY Spot Price
│   └── get_nifty_spot_price(api, symbol_manager)
│
├── Step 2: Get Available Expiries
│   └── get_all_eligible_expiries(symbol_manager, max_expiries_to_check=7)
│
├── Step 3: Fetch Option Chain (for regime detection)
│   └── get_option_chain_data(api, symbol_manager, spot_price, expiry_date, count=30)
│
├── Step 4: Build Market State
│   └── build_market_state_from_chain(api, symbol_manager, spot_price, expiry_date, option_chain)
│       ├── Calculate IV Percentile
│       ├── Calculate ADX(14)
│       ├── Calculate Days to Expiry
│       └── Returns: market_state dict
│
├── Step 5: Detect Regime
│   └── RegimeDetector().detect_regime(market_state, recent_candles, api, symbol_manager)
│       └── Returns: regime_info dict with regime, IV%, ADX, ATR%, range_state
│
└── Step 6: Route Based on Regime
    │
    ├── Regime = INCOME?
    │   ├── Check mutual exclusion
    │   │   └── can_enter_strategy(STRATEGY_IRON_CONDOR, position_tracker)
    │   ├── YES → Run Iron Condor
    │   │   └── _run_iron_condor_strategy_internal(...)
    │   └── NO → Blocked (Convex strategy active)
    │
    ├── Regime = CONVEX?
    │   ├── Check mutual exclusion
    │   │   └── can_enter_strategy(STRATEGY_CONVEX, position_tracker)
    │   ├── YES → Run Convex Backspread
    │   │   └── _run_convex_backspread_strategy(...)
    │   └── NO → Blocked (Iron Condor active)
    │
    └── Regime = NEUTRAL?
        └── No new trades allowed
```

---

## 📊 Iron Condor Trade Generation (INCOME Regime)

### 5. Iron Condor Strategy (`strategies/iron_condor/strategy.py`)

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
│   └── From option_chain or symbol_manager.nse_fo (NFO.csv)
│
├── Step 6: Calculate Margin
│   └── calculate_iron_condor_margin(api, legs, lots, expiry_date, ...)
│       ├── Use Shoonya SPAN Calculator API
│       ├── Calculate premium paid for long legs
│       └── Total Margin = SPAN Margin + Premium Paid
│
└── Step 7: Build Trade Proposal
    └── Returns: Dictionary with all trade details
        ├── strategy: "IRON_CONDOR_WEEKLY"
        ├── regime_at_entry: "INCOME"
        ├── margin_used: float
        └── profit_target_margin: margin_used × 0.01
```

---

## 📊 Convex Backspread Trade Generation (CONVEX Regime)

### 6. Convex Backspread Strategy (`strategies/convex/call_backspread.py`)

```python
generate_nifty_call_backspread(market_state, option_chain, capital)
│
├── Step 1: Extract Market Data
│   ├── Spot Price
│   ├── Expiry Date
│   ├── Days to Expiry
│   └── Lot Size (from NFO.csv)
│
├── Step 2: Select Strikes
│   ├── Select ATM Call (short leg)
│   │   └── Closest to spot price
│   └── Select OTM Call (long legs, quantity=2)
│       └── ~+1% from ATM strike
│
├── Step 3: Calculate Net Debit
│   └── net_debit = (OTM_Call_Price × 2) - ATM_Call_Price
│
├── Step 4: Validate Net Debit
│   └── Must be ≤ 0.25% of spot value
│
├── Step 5: Calculate Position Size
│   └── Based on max loss ≤ 1% of total capital
│
├── Step 6: Calculate Max Loss
│   └── max_loss = (ATM_Strike - OTM_Strike) - net_debit
│
└── Step 7: Build Trade Proposal
    └── Returns: Dictionary with trade details
        ├── strategy: "CALL_BACKSPREAD"
        ├── book: "CONVEX"
        ├── regime_at_entry: "CONVEX"
        ├── legs: [ATM_Call (SHORT, qty=1), OTM_Call (LONG, qty=2)]
        ├── net_debit_total: float
        └── max_loss: float
```

---

## 🔒 Mutual Exclusion

### 7. Strategy Exclusion (`strategies/strategy_exclusion.py`)

```
can_enter_strategy(strategy_type, position_tracker)
│
├── Get active strategy type
│   └── get_active_strategy_type(position_tracker)
│       ├── Check active positions
│       ├── If Iron Condor active → Return "IRON_CONDOR_WEEKLY"
│       ├── If Convex active → Return "CALL_BACKSPREAD"
│       └── If none active → Return None
│
└── Check exclusion rules:
    ├── No active strategy? → Allow entry
    ├── Same strategy type active? → Allow entry (can add more positions)
    └── Different strategy active? → Block entry
```

**Rules:**
- Only one strategy type can be active at a time
- Can add more positions of the same strategy type
- Cannot enter new strategy if different type is active

---

## 📈 Market State Building

### 8. Market State (`strategy_runner.py`)

```python
build_market_state_from_chain(api, symbol_manager, spot_price, expiry_date, option_chain)
│
├── Calculate IV Percentile
│   └── calculate_iv_percentile(option_chain, spot_price, days_to_expiry, api, symbol_manager)
│       ├── Get ATM IV using Shoonya API (iterative search)
│       │   └── Fallback to 18.0% if API fails
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

### 9. Position Monitoring (`main.py` lines 289-376)

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
│   │       ├── For Iron Condor (credit):
│   │       │   └── P&L = entry_credit + current_value
│   │       └── For Convex Backspread (debit):
│   │           └── P&L = current_value - entry_debit
│   │
│   └── Check Profit Target
│       └── position_tracker.check_profit_target(position, current_pnl)
│           ├── For Iron Condor:
│           │   └── profit_target = margin_used × 0.01 (1%)
│           └── For Convex Backspread:
│               └── profit_target = max_profit × 0.50 (50% of max profit)
│           └── IF current_pnl >= profit_target:
│               ├── Close position
│               ├── Log performance (by regime)
│               └── Mark for exit
```

---

## 📊 Performance Logging

### 10. Performance Tracking (`strategies/iron_condor/position_tracker.py`)

```
log_performance(position, exit_reason, final_pnl)
│
├── Extract trade details:
│   ├── Strategy type
│   ├── Regime at entry
│   ├── Entry time
│   ├── Exit time
│   ├── P&L
│   └── Exit reason
│
├── Load existing performance logs
│
├── Group by regime and strategy
│
└── Save to performance_by_regime.json
    └── Structure:
        {
            "INCOME": {
                "IRON_CONDOR_WEEKLY": {
                    "total_trades": int,
                    "winning_trades": int,
                    "total_pnl": float,
                    "avg_pnl": float
                }
            },
            "CONVEX": {
                "CALL_BACKSPREAD": { ... }
            }
        }
```

---

## 📥 Data Flow

### 11. Data Collection (`data_collector.py`)

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

### 12. Module Responsibilities

| Module | Purpose | Key Functions |
|--------|---------|---------------|
| `main.py` | System orchestration | Main loop, timing, coordination |
| `strategy_runner.py` | Strategy integration | Regime routing, option chain fetching |
| `regime/regime_detector.py` | Regime detection | ATR calculation, range analysis, regime classification |
| `strategies/iron_condor/strategy.py` | Iron Condor trade generation | Orchestrates trade proposal creation |
| `strategies/convex/call_backspread.py` | Convex Backspread trade generation | Generates call backspread trades |
| `strategies/strategy_exclusion.py` | Mutual exclusion | Enforces one strategy type at a time |
| `strategies/iron_condor/eligibility.py` | Market filtering | Checks if market conditions are suitable |
| `strategies/iron_condor/strike_selector.py` | Strike selection | Finds optimal strikes for 4 legs |
| `strategies/iron_condor/payoff_validator.py` | Risk validation | Validates net credit, max loss, R:R ratio |
| `strategies/iron_condor/position_sizer.py` | Position sizing | Calculates number of lots based on risk |
| `strategies/iron_condor/margin_calculator.py` | Margin calculation | Uses Shoonya SPAN API |
| `strategies/iron_condor/position_tracker.py` | Position management | Tracks positions, P&L, exits, performance logging |
| `technical_indicators.py` | Technical analysis | IV percentile, ADX, historical data |
| `shoonya_iv_fetcher.py` | IV calculation | Iterative search using Shoonya API |
| `symbol_manager.py` | Symbol management | Manages NFO/NSE symbols, expiries |
| `data_collector.py` | Data collection | Real-time market data collection |

---

## ⚙️ Configuration

### 13. Strategy Parameters

**Iron Condor (`strategies/iron_condor/config.py`):**
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

**Convex Backspread (`strategies/convex/call_backspread.py`):**
```python
# Net Debit Validation
MAX_NET_DEBIT_PCT = 0.0025  # 0.25% of spot value

# Max Loss Validation
MAX_LOSS_PCT_OF_CAPITAL = 0.01  # 1% of total capital

# Strike Selection
OTM_STRIKE_PCT = 0.01  # ~+1% from ATM
```

**Regime Detection (`regime/regime_detector.py`):**
```python
# CONVEX Regime
IV_PERCENTILE_THRESHOLD_CONVEX = 40
ATR_PERCENTILE_THRESHOLD_CONVEX = 25
RANGE_COMPRESSION_FACTOR = 0.6  # 60% of rolling average

# INCOME Regime
IV_PERCENTILE_THRESHOLD_INCOME = 60
ADX_THRESHOLD_INCOME = 20
ATR_PERCENTILE_THRESHOLD_INCOME = 50

# ATR Calculation
ATR_PERIOD = 14
MIN_HISTORICAL_ATR_VALUES = 2
CACHE_DURATION_MINUTES = 15
```

---

## 📄 Trade Proposal Structure

### 14. Trade Proposal Format

**Iron Condor:**
```json
{
  "strategy": "IRON_CONDOR_WEEKLY",
  "book": "INCOME",
  "regime_at_entry": "INCOME",
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
    ...
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

**Convex Backspread:**
```json
{
  "strategy": "CALL_BACKSPREAD",
  "book": "CONVEX",
  "regime_at_entry": "CONVEX",
  "expiry": "2026-01-27",
  "legs": [
    {
      "position": "SHORT",
      "option_type": "CE",
      "strike": 26000.0,
      "price": 200.0,
      "quantity": 1
    },
    {
      "position": "LONG",
      "option_type": "CE",
      "strike": 26260.0,
      "price": 150.0,
      "quantity": 2
    }
  ],
  "lots": 10,
  "max_loss": 10000.0,
  "net_debit": 100.0,
  "net_debit_total": 10000.0,
  "spot_price": 25876.7,
  "lot_size": 65,
  "margin_used": 10000.0,
  "profit_target_margin": null
}
```

---

## 🌳 Decision Flow

### 15. Complete Trade Decision Tree

```
Market Open?
├── NO → Wait
└── YES
    └── Strategy Check (every 5 min)
        └── Get Spot Price
            └── Fetch Option Chain
                └── Build Market State
                    └── Detect Regime
                        │
                        ├── INCOME Regime?
                        │   ├── Check mutual exclusion
                        │   ├── NO (Convex active) → Blocked
                        │   └── YES → Run Iron Condor
                        │       └── Eligibility Check
                        │           ├── IV 50-100%? → NO → Skip
                        │           ├── DTE 3-30 days? → NO → Skip
                        │           ├── ADX < 22? → NO → Skip
                        │           └── All Pass? → YES
                        │               └── Select Strikes
                        │                   └── Validate Payoff
                        │                       ├── Net Credit ₹30-110? → NO → Reject
                        │                       ├── Max Loss < ₹1500? → NO → Reject
                        │                       └── R:R ≥ 0.9? → NO → Reject
                        │                           └── All Pass? → YES
                        │                               └── Size Position
                        │                                   └── Calculate Margin
                        │                                       └── Generate Trade Proposal
                        │
                        ├── CONVEX Regime?
                        │   ├── Check mutual exclusion
                        │   ├── NO (Iron Condor active) → Blocked
                        │   └── YES → Run Convex Backspread
                        │       └── Select Strikes
                        │           └── Validate Payoff
                        │               ├── Net Debit ≤ 0.25%? → NO → Reject
                        │               └── Max Loss ≤ 1% capital? → NO → Reject
                        │                   └── All Pass? → YES
                        │                       └── Generate Trade Proposal
                        │
                        └── NEUTRAL Regime?
                            └── No new trades allowed
```

---

## 📁 File Outputs

### 16. Output Files

- **Trade Proposals**: `trade_proposals/iron_condor_YYYYMMDD_HHMMSS.json`
- **Active Positions**: `active_positions.json`
- **Performance Logs**: `performance_by_regime.json`
- **Logs**: `logs/trading_system_YYYYMMDD.log`
- **Market Data**: `market_data_YYYYMMDD/raw_data/`
- **IV Historical Data**: `market_data_iv/`

---

## 🔍 Detailed Component Flows

### 17. ATR Calculation Flow

```
calculate_atr(historical_data, period=14)
│
├── Get historical price data (17 days)
│   └── From stored data or API
│
├── Calculate True Range for each day:
│   ├── TR = max(
│   │   H - L,                    # Current high - low
│   │   |H - C_prev|,             # High - previous close
│   │   |L - C_prev|               # Low - previous close
│   │   )
│
├── Calculate ATR(14):
│   ├── For each day i (starting from day 14):
│   │   ├── Get last 14 TR values: TR[i-13:i+1]
│   │   └── ATR[i] = Average(TR[i-13:i+1])
│   │
│   └── Current ATR = ATR[most recent]
│
├── Calculate Historical ATRs:
│   ├── Loop through days (starting from day 15):
│   │   └── Calculate ATR for that day
│   └── Generate 3 historical ATR values
│
└── Calculate ATR Percentile:
    └── Percentile rank of current ATR vs historical ATRs
```

### 18. Price Range Calculation

```
calculate_price_range(recent_candles)
│
├── Get last 60 minutes of candles
│
├── Calculate last_range:
│   └── last_range = max(highs) - min(lows) (last 60 min)
│
├── Calculate rolling_avg_range:
│   ├── For each candle: range = high - low
│   ├── Get last 20 candles
│   └── rolling_avg_range = Average(range values)
│
└── Determine range_state:
    └── COMPRESSED if last_range < (rolling_avg_range × 0.6)
```

### 19. IV Calculation Flow

```
calculate_iv_percentile(option_chain, spot_price, days_to_expiry, api, symbol_manager)
│
├── Get ATM IV
│   └── calculate_atm_iv(option_chain, spot_price, api, symbol_manager)
│       ├── Find ATM Call and Put
│       ├── Use Shoonya API option_greek function
│       ├── Iterative search (Brent's method) to find IV
│       └── Fallback to 18.0% if API fails
│
├── Load Historical IV Data
│   └── From market_data_iv/ directory
│
├── Calculate Percentile
│   └── Compare current IV with historical distribution
│
└── Return: IV Percentile (0-100%)
```

### 20. Margin Calculation Flow

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

### 21. Shoonya API Usage

**Authentication:**
- Requires 2FA code on startup
- Credentials stored in `cred.yml`
- Session maintained throughout runtime

**Key API Functions Used:**
- `api.login()` - Authentication
- `api.get_quotes()` - Fetch option chain data
- `api.option_greek()` - Get theoretical option price (for IV calculation)
- `api.span_calculator()` - Calculate margin requirement
- `api.get_time_price_series()` - Historical price data (for ADX, ATR)

**IV Calculation via API:**
- Shoonya API doesn't provide IV directly
- Uses `option_greek()` which takes volatility as input
- Performs iterative search (Brent's method) to find IV that matches market price
- Fallback to 18.0% if API fails
- Implemented in `shoonya_iv_fetcher.py`

---

## 📊 Data Structures

### 22. Option Chain DataFrame Structure

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

### 23. Market State Dictionary Structure

```python
market_state = {
    'iv_percentile': float,        # 0-100
    'adx_14': float,               # Average Directional Index
    'days_to_expiry': int,         # Days until expiry
    'has_major_event': bool,        # True if event in next 48h
    'instrument': str,             # 'NIFTY'
    'instrument_type': str,         # 'WEEKLY' or 'MONTHLY'
    'spot_price': float,           # Current spot price
    'expiry': str,                 # 'YYYY-MM-DD'
    'current_iv': float,           # Current IV (optional)
    'regime': str                  # 'CONVEX', 'INCOME', or 'NEUTRAL' (optional)
}
```

### 24. Regime Info Dictionary Structure

```python
regime_info = {
    'regime': str,                 # 'CONVEX', 'INCOME', or 'NEUTRAL'
    'iv_percentile': float,        # 0-100
    'adx': float,                  # Average Directional Index
    'atr': float,                  # Current ATR value
    'atr_percentile': float,       # ATR percentile (0-100)
    'range_state': str             # 'COMPRESSED' or 'NORMAL'
}
```

---

## 🚨 Error Handling

### 25. Error Handling Strategy

**API Errors:**
- Retry logic for transient failures
- Fallback to default values (e.g., IV = 18% if API fails)
- Logging at appropriate levels (DEBUG for expected, WARNING for issues)

**Data Validation:**
- Check for None/empty values before processing
- Validate spot prices > 0
- Ensure option chain is not empty
- Validate ATR calculation has sufficient data (minimum 2 historical values)

**Strategy Errors:**
- Catch exceptions at each step
- Log rejection reasons
- Continue to next expiry if one fails
- Regime detection errors fall back to NEUTRAL

**Regime Detection Errors:**
- If ATR calculation fails → ATR percentile = None → Regime = NEUTRAL
- If insufficient historical data → Use available data or default to NEUTRAL
- Cache regime results for 15 minutes to reduce API calls

---

## 📝 Summary

### Complete Flow Summary

1. **System starts** → Initializes API, symbols, data collection
2. **Main loop runs** → Collects data, checks strategy, monitors positions
3. **IV calculation** (every 2 min) → Builds historical IV data
4. **Strategy check** (every 5 min) → Fetches option chain, builds market state
5. **Regime detection** → Analyzes IV%, ADX, ATR%, range state
6. **Strategy routing** → Routes to Iron Condor (INCOME) or Convex Backspread (CONVEX)
7. **Mutual exclusion check** → Ensures only one strategy type is active
8. **Eligibility check** → Filters by IV, ADX, DTE, events
9. **Strike selection** → Finds optimal strikes
10. **Payoff validation** → Checks credit/debit, loss, R:R ratio
11. **Position sizing** → Calculates lots based on risk
12. **Margin calculation** → Uses SPAN API
13. **Trade proposal** → Saved to JSON, tracked in position tracker
14. **Position monitoring** (every 1 min) → Checks P&L, profit target
15. **Performance logging** → Records trade results by regime

This flow runs continuously during market hours (9:15 AM - 3:30 PM IST) until a valid trade is found or market closes.

---

## 🔄 Update Points

When updating the system, consider these key areas:

1. **Configuration** (`strategies/iron_condor/config.py`) - Adjust thresholds and parameters
2. **Regime Detection** (`regime/regime_detector.py`) - Modify regime classification logic
3. **Strategy Exclusion** (`strategies/strategy_exclusion.py`) - Adjust mutual exclusion rules
4. **Eligibility Rules** (`strategies/iron_condor/eligibility.py`) - Modify market condition checks
5. **Strike Selection** (`strategies/iron_condor/strike_selector.py`) - Change delta ranges or wing widths
6. **Payoff Validation** (`strategies/iron_condor/payoff_validator.py`) - Adjust risk/reward criteria
7. **Exit Rules** (`strategies/iron_condor/exit_rules.py`) - Modify profit targets and stop losses
8. **Main Loop Timing** (`main.py`) - Adjust check intervals
9. **IV Calculation** (`technical_indicators.py`, `shoonya_iv_fetcher.py`) - Modify IV calculation logic
10. **ATR Calculation** (`regime/regime_detector.py`) - Adjust ATR period or percentile thresholds

---

*Last Updated: 2026-01-14*
