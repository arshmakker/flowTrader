# Iron Condor + Convex Strategy Flow

## Overview

This document describes the trading system flow after removing Trend and Calendar strategies. The system now runs only **Iron Condor** and **Convex (Call Backspread)** strategies.

## Active Strategies

| Strategy | Book | Regime | Performance |
|----------|------|--------|-------------|
| **Iron Condor** | INCOME | INCOME | +600,993 PnL, 85.7% win rate, +47$/lot |
| **Convex (Call Backspread)** | CONVEX | CONVEX | +93,628 PnL, 53.5% win rate, +75$/lot |

## Strategy Flow

```
┌─────────────────────────────────────────────────────────────┐
│                     Main Entry Point                        │
│                      (check_trades)                        │
└─────────────────────────────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────┐
│  1. Load Market Data                                        │
│     - Get spot price                                        │
│     - Get option chain                                      │
│     - Get available expiries                                │
└─────────────────────────────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────┐
│  2. Regime Detection (regime_detector.py)                   │
│     - Detect CONVEX (Call Backspread regime)                │
│     - Detect INCOME (Iron Condor regime)                     │
│     - Returns regime_info dict                               │
└─────────────────────────────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────┐
│  3. Strategy Runner (strategy_runner.py)                    │
│                                                             │
│     ┌─────────────────────────────────────────────────────┐ │
│     │  3a. Try CONVEX Strategy                           │ │
│     │       - _run_convex_backspread_strategy()          │ │
│     │       - Generates CALL_BACKSPREAD proposal         │ │
│     │       - Runs in CONVEX or NEUTRAL regime           │ │
│     └─────────────────────────────────────────────────────┘ │
│                           OR                                │
│     ┌─────────────────────────────────────────────────────┐ │
│     │  3b. Try IRON CONDOR Strategy                     │ │
│     │       - _run_iron_condor_strategy_internal()      │ │
│     │       - Generates IRON_CONDOR proposal             │ │
│     │       - Runs in INCOME regime                      │ │
│     └─────────────────────────────────────────────────────┘ │
│                                                             │
│     NOTE: Both strategies can run concurrently!             │
└─────────────────────────────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────┐
│  4. Position Tracking (position_tracker.py)                 │
│     - IronCondorPositionTracker manages all positions       │
│     - check_convex_exit_conditions()                        │
│     - check_iron_condor_exit_conditions()                   │
│     - check_trailing_stop()                                 │
└─────────────────────────────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────┐
│  5. Exit Conditions                                        │
│                                                             │
│     CONVEX Exit:                                           │
│     - Regime changed from CONVEX                            │
│     - Days to expiry <= 2                                  │
│     - Profit target reached                                │
│     - Trailing stop hit                                    │
│                                                             │
│     IRON CONDOR Exit:                                      │
│     - Wing breached (OTM call/put)                         │
│     - Days to expiry <= 3                                  │
│     - Profit target reached                                │
│     - Trailing stop hit                                    │
└─────────────────────────────────────────────────────────────┘
```

## Strategy Exclusion

Both strategies can **coexist** - they don't block each other:

```python
# strategy_exclusion.py
def can_enter_strategy(strategy_type, position_tracker):
    # Iron Condor can enter if NONE or CONVEX active
    if strategy_type == STRATEGY_IRON_CONDOR:
        return active_strategy in (STRATEGY_NONE, STRATEGY_CONVEX)
    
    # Convex can enter if NONE, IRON_CONDOR, or CONVEX active
    if strategy_type == STRATEGY_CONVEX:
        return active_strategy in (STRATEGY_NONE, STRATEGY_IRON_CONDOR, STRATEGY_CONVEX)
```

## Lot Sizing

Configured in `strategies/size_config.py`:

```python
MIN_LOTS = 10
MAX_LOTS = 10
```

All trades are executed with exactly **10 lots**.

## Files Modified

| File | Purpose |
|------|---------|
| `strategy_runner.py` | Routes to Convex and Iron Condor strategies |
| `strategies/strategy_exclusion.py` | Manages strategy coexistence |
| `strategies/iron_condor/position_tracker.py` | Tracks positions and exit conditions |
| `main.py` | Main loop with position monitoring |
| `strategies/size_config.py` | Lot sizing configuration |

## Strategy Entry Conditions

### Convex (Call Backspread)
- Regime: CONVEX or NEUTRAL
- Spot price near ATM
- Sufficient liquidity in options
- Sufficient time to expiry

### Iron Condor
- Regime: INCOME (sideways/low volatility)
- Multiple expiries available
- Can sell OTM call and put spreads
- Sufficient credit collected

## Performance Summary

Based on historical data (performance_by_regime.deduped.json):

| Regime | Trades | PnL | $/Lot |
|--------|--------|-----|-------|
| INCOME (Iron Condor) | 21 | +600,993 | +47 |
| CONVEX | 86 | +93,628 | +75 |
| NEUTRAL | 7 | -538 | -77 |
| TRENDING | 32 | -547,072 | -855 |

**Note**: TRENDING regime trades were from before the strategy cleanup. With the new convex-only configuration, TRENDING regime trades would be avoided for Convex strategy.

## Future Improvements

1. Add regime filtering to prevent Convex in TRENDING
2. Optimize Iron Condor position sizing
3. Add more exit condition variations
4. Consider adding Calendar as a third strategy for INCOME regime
