# System Architecture: Iron Condor Trading System

## 🏗️ System Overview

This is a **risk-first, prop-style options trading system** that collects real-time market data and generates validated Iron Condor trade proposals. The system is designed with strict separation of concerns: data collection, strategy logic, and execution are decoupled.

---

## 📊 System Components

### 1. **Core Infrastructure** (Existing - Unmodified)

```
┌─────────────────────────────────────────────────────────┐
│                    Shoonya API Layer                      │
│  (api_helper.py) - Broker authentication & API calls     │
└─────────────────────────────────────────────────────────┘
                          ↓
┌─────────────────────────────────────────────────────────┐
│              Symbol Management Layer                      │
│  (symbol_manager.py) - Symbol lookup, expiry calc       │
└─────────────────────────────────────────────────────────┘
                          ↓
┌─────────────────────────────────────────────────────────┐
│              Data Collection Layer                        │
│  (data_collector.py) - Real-time market data collection  │
└─────────────────────────────────────────────────────────┘
```

### 2. **Strategy Module** (New - Iron Condor)

```
┌─────────────────────────────────────────────────────────┐
│              Iron Condor Strategy Module                  │
│  (strategies/iron_condor/) - Rule-based trade generation │
└─────────────────────────────────────────────────────────┘
```

---

## 🔄 End-to-End Data Flow

### **Phase 1: System Initialization**

```
1. main.py starts
   ↓
2. Load credentials (cred.yml)
   ↓
3. Initialize ShoonyaApiPy → Login with 2FA
   ↓
4. Initialize SymbolManager → Load symbol files (NSE.csv, NFO.csv)
   ↓
5. Initialize DataCollector → Set up data directories
   ↓
6. Start data collection thread
```

### **Phase 2: Real-Time Data Collection**

```
DataCollector._collect_data() runs in background thread:
   ↓
For each symbol (every 1 second):
   ↓
1. api.get_quotes(exchange, token)
   ↓
2. Receive quote data:
   {
     'lp': last_price,
     'bp1': bid_price,
     'sp1': ask_price,
     'v': volume,
     'oi': open_interest,
     ...
   }
   ↓
3. Format data point:
   {
     'timestamp': '2024-01-15 10:30:45.123',
     'symbol': 'NIFTY24JAN20200CE',
     'strike': 20000,
     'option_type': 'CE',
     'ltp': 150.50,
     'bid': 149.00,
     'ask': 151.00,
     'oi': 50000,
     'volume': 10000
   }
   ↓
4. Save to CSV: market_data_YYYYMMDD/raw_data/options/NIFTY/ce/
   ↓
5. Queue for processing (if needed)
```

### **Phase 3: Iron Condor Trade Generation** (New Integration Point)

```
When you want to generate a trade:
   ↓
1. Fetch option chain for NIFTY weekly:
   option_chain = api.get_option_chain(
       exchange='NFO',
       tradingsymbol='NIFTY24JAN24F',  # Weekly futures
       strikeprice=20000,
       count=50  # Get 50 strikes on each side
   )
   ↓
2. Get current market state:
   market_state = {
       'iv_percentile': 70.0,      # From IV calculation
       'days_to_expiry': 4,         # Calculated from expiry date
       'adx_14': 18.0,              # From technical analysis
       'has_major_event': False,    # From calendar/events
       'instrument': 'NIFTY',
       'instrument_type': 'WEEKLY',
       'spot_price': 20000.0,       # Current NIFTY spot
       'expiry': '2024-01-18'       # Weekly expiry date
   }
   ↓
3. Convert option chain to DataFrame:
   option_chain_df = pd.DataFrame([
       {
           'strike': 19800,
           'option_type': 'PE',
           'ltp': 50.0,
           'bid': 49.0,
           'ask': 51.0,
           'delta': -0.17,
           'oi': 50000,
           'volume': 10000
       },
       ...  # More strikes
   ])
   ↓
4. Call Iron Condor strategy:
   from strategies.iron_condor import generate_iron_condor_trade
   
   trade_proposal = generate_iron_condor_trade(
       market_state, 
       option_chain_df
   )
   ↓
5. Strategy module processes:
   ├─ eligibility.py: Check market conditions
   ├─ strike_selector.py: Select 4 strikes (2 short, 2 long)
   ├─ payoff_validator.py: Validate risk/reward
   ├─ position_sizer.py: Calculate lot size
   └─ strategy.py: Orchestrate and return proposal
   ↓
6. Return trade proposal or None:
   {
       "strategy": "IRON_CONDOR_WEEKLY",
       "expiry": "2024-01-18",
       "legs": [
           {"position": "SHORT", "option_type": "CE", "strike": 20100, ...},
           {"position": "SHORT", "option_type": "PE", "strike": 19900, ...},
           {"position": "LONG", "option_type": "CE", "strike": 20250, ...},
           {"position": "LONG", "option_type": "PE", "strike": 19750, ...}
       ],
       "lots": 30,
       "net_credit": 85.50,
       "max_loss": 450.00,
       "reward_to_risk": 2.1,
       ...
   }
```

### **Phase 4: Trade Execution** (Future - Not Implemented Yet)

```
If trade_proposal is not None:
   ↓
1. Review trade proposal (manual or automated)
   ↓
2. Create Order objects:
   orders = [
       Order(buy_or_sell='S', ...),  # Short call
       Order(buy_or_sell='S', ...),  # Short put
       Order(buy_or_sell='B', ...),  # Long call
       Order(buy_or_sell='B', ...)   # Long put
   ]
   ↓
3. Place basket order:
   api.place_basket(orders)
   ↓
4. Monitor position (future: exit_rules.py logic)
```

---

## 🔌 Integration Points

### **How Iron Condor Strategy Integrates**

The Iron Condor strategy module is **completely decoupled** from the existing system:

1. **No modifications** to existing code
2. **Consumes data** from your existing API/data collection
3. **Returns proposals** - doesn't place orders
4. **Stateless** - each call is independent

### **Data Requirements**

The strategy needs:

1. **Option Chain Data** (from `api.get_option_chain()`):
   ```python
   # Format: pandas DataFrame
   columns = ['strike', 'option_type', 'ltp', 'bid', 'ask', 
              'delta', 'oi', 'volume']
   ```

2. **Market State** (you provide):
   ```python
   {
       'iv_percentile': float,      # 0-100
       'days_to_expiry': int,        # 3-6 for weekly
       'adx_14': float,              # < 22
       'has_major_event': bool,      # False
       'instrument': 'NIFTY',       # Must be NIFTY
       'instrument_type': 'WEEKLY', # Must be WEEKLY
       'spot_price': float,          # Current spot
       'expiry': 'YYYY-MM-DD'        # Expiry date
   }
   ```

---

## 📝 Example Integration Code

Here's how you would integrate the Iron Condor strategy into your existing system:

```python
# In your main.py or a new strategy_runner.py

from strategies.iron_condor import generate_iron_condor_trade
import pandas as pd
from datetime import datetime, timedelta

def run_iron_condor_strategy(api, symbol_manager):
    """Run Iron Condor strategy check"""
    
    # 1. Get NIFTY weekly expiry
    expiry_date = symbol_manager.get_weekly_expiry('NIFTY')
    days_to_expiry = (expiry_date - datetime.now().date()).days
    
    # 2. Get current spot price
    nifty_spot = api.get_quotes(exchange='NSE', token='NIFTY_TOKEN')
    spot_price = float(nifty_spot['lp'])
    
    # 3. Get option chain
    futures_symbol = f"NIFTY{expiry_date.strftime('%d%b%y').upper()}F"
    option_chain_raw = api.get_option_chain(
        exchange='NFO',
        tradingsymbol=futures_symbol,
        strikeprice=int(spot_price),
        count=50  # 50 strikes on each side
    )
    
    # 4. Convert to DataFrame
    chain_data = []
    for option in option_chain_raw.get('values', []):
        quote = api.get_quotes(option['exch'], option['token'])
        chain_data.append({
            'strike': float(option.get('strike', 0)),
            'option_type': 'CE' if 'CE' in option['tsym'] else 'PE',
            'ltp': float(quote.get('lp', 0)),
            'bid': float(quote.get('bp1', 0)),
            'ask': float(quote.get('sp1', 0)),
            'delta': float(option.get('delta', 0)),  # If available
            'oi': int(quote.get('oi', 0)),
            'volume': int(quote.get('v', 0))
        })
    
    option_chain_df = pd.DataFrame(chain_data)
    
    # 5. Calculate market state (you need to implement these)
    market_state = {
        'iv_percentile': calculate_iv_percentile(spot_price, option_chain_df),
        'days_to_expiry': days_to_expiry,
        'adx_14': calculate_adx(spot_price),  # From historical data
        'has_major_event': check_major_events(expiry_date),
        'instrument': 'NIFTY',
        'instrument_type': 'WEEKLY',
        'spot_price': spot_price,
        'expiry': expiry_date.strftime('%Y-%m-%d')
    }
    
    # 6. Generate trade proposal
    trade_proposal = generate_iron_condor_trade(market_state, option_chain_df)
    
    # 7. Handle result
    if trade_proposal:
        print("✅ Valid Iron Condor trade found!")
        print(f"   Lots: {trade_proposal['lots']}")
        print(f"   Net Credit: ₹{trade_proposal['net_credit']:.2f} per lot")
        print(f"   Max Loss: ₹{trade_proposal['max_loss']:.2f} per lot")
        print(f"   Reward-to-Risk: {trade_proposal['reward_to_risk']:.2f}")
        
        # Save proposal for review/execution
        save_trade_proposal(trade_proposal)
        
        return trade_proposal
    else:
        print("❌ No valid trade found (market conditions not suitable)")
        return None

# Add to main loop (optional)
def main():
    # ... existing initialization code ...
    
    collector = DataCollector(api, symbol_manager)
    collector.start_collection()
    
    # Run strategy check periodically (e.g., every 5 minutes)
    while True:
        try:
            # Check if it's a good time to run strategy (e.g., during market hours)
            if is_market_hours():
                trade = run_iron_condor_strategy(api, symbol_manager)
                if trade:
                    # Optionally: auto-execute or send notification
                    pass
            
            time.sleep(300)  # Check every 5 minutes
        except KeyboardInterrupt:
            break
```

---

## 🎯 Key Design Principles

### 1. **Separation of Concerns**
- **Data Collection**: Handled by existing `DataCollector`
- **Strategy Logic**: Isolated in `strategies/iron_condor/`
- **Execution**: Future module (not implemented)

### 2. **Stateless Design**
- Each strategy call is independent
- No internal state between calls
- Deterministic outputs for same inputs

### 3. **Fail-Safe Validation**
- Multiple validation layers
- Rejects trades that don't meet criteria
- Returns `None` instead of raising exceptions (at top level)

### 4. **Risk-First Approach**
- Strict position sizing (max ₹30k risk)
- Reward-to-risk ratio enforcement (≥ 2.0)
- Maximum loss limits (≤ ₹1,500 per lot)

---

## 🔍 Strategy Decision Flow

```
Market State Available?
    ↓ YES
Is Market Eligible?
    ├─ NO → Return None
    ↓ YES
Can Select Strikes?
    ├─ NO → Return None
    ↓ YES
Does Payoff Validate?
    ├─ NO → Return None (StrategyRejectedError)
    ↓ YES
Calculate Position Size
    ↓
Lots > 0?
    ├─ NO → Return None
    ↓ YES
Return Trade Proposal
```

---

## 📦 File Structure

```
shoonyapythonmod/
├── main.py                          # System entry point
├── api_helper.py                    # Shoonya API wrapper (UNMODIFIED)
├── symbol_manager.py                # Symbol management (UNMODIFIED)
├── data_collector.py                # Data collection (UNMODIFIED)
├── paper_trader.py                  # Paper trading (UNMODIFIED)
│
├── strategies/                      # NEW: Strategy modules
│   └── iron_condor/
│       ├── __init__.py
│       ├── config.py                # Strategy constants
│       ├── eligibility.py           # Market eligibility checks
│       ├── strike_selector.py       # Strike selection logic
│       ├── payoff_validator.py      # Risk/reward validation
│       ├── position_sizer.py        # Position sizing
│       ├── exit_rules.py            # Exit rule config
│       ├── strategy.py              # Main orchestrator
│       └── example_usage.py         # Usage example
│
├── tests/
│   └── strategies/
│       └── iron_condor/             # Unit tests
│
└── market_data_YYYYMMDD/            # Collected data
    └── raw_data/
        └── options/
```

---

## 🚀 Next Steps (Future Enhancements)

1. **IV Calculation Module**: Calculate IV percentile from option prices
2. **ADX Calculator**: Calculate ADX(14) from historical price data
3. **Event Calendar**: Check for RBI meetings, major events
4. **Execution Module**: Auto-place orders when trade approved
5. **Position Monitor**: Track open positions and apply exit rules
6. **Backtesting**: Test strategy on historical data

---

## ✅ Current Status

- ✅ **Data Collection**: Working
- ✅ **Iron Condor Strategy**: Complete and tested
- ✅ **Integration Ready**: Can be called from any part of system
- ⏳ **Execution**: Not implemented (returns proposals only)
- ⏳ **Monitoring**: Not implemented (exit rules defined but not enforced)

The system is **production-ready** for generating trade proposals. Execution and monitoring can be added as separate modules without modifying existing code.



