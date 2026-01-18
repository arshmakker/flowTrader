# Market Identification Validation Guide

This guide explains how to verify that the system is correctly identifying market situations.

## Quick Check Script

Run the diagnostic script to see current state:

```bash
python3 diagnostics/check_current_state.py
```

This will show:
- Current indicator values (IV%, ADX, ATR%)
- Detected regime and sub-state
- Strategy routing decisions
- Why trades are/aren't being placed

## Manual Validation Steps

### 1. Check Current Indicators

**From Logs:**
```bash
# Get latest market state
tail -100 logs/trading_system_YYYYMMDD.log | grep "Market state:"

# Get latest ADX calculation
tail -100 logs/trading_system_YYYYMMDD.log | grep "Calculated ADX"

# Get latest regime detection
tail -100 logs/trading_system_YYYYMMDD.log | grep "Regime:"
```

**Expected Output:**
```
Market state: IV=56.7%, DTE=4, ADX=37.8, Event=False
Regime: NEUTRAL (IV=56.7%, ADX=37.8, ATR%=75.0)
```

### 2. Validate Regime Detection Logic

**INCOME Regime:**
- IV% > 70 AND ADX < 20
- Example: IV=75%, ADX=18 → INCOME

**CONVEX Regime:**
- IV% < 30 AND ATR% < 25
- Example: IV=25%, ATR%=20 → CONVEX

**NEUTRAL Regime:**
- Everything else
- Example: IV=56%, ADX=38 → NEUTRAL

### 3. Validate Sub-State (for NEUTRAL)

**NEUTRAL_ACTIVE:**
- IV% between 40-60 AND ADX between 18-25
- Example: IV=50%, ADX=22 → NEUTRAL_ACTIVE

**NEUTRAL_PASSIVE:**
- Any other combination
- Example: IV=50%, ADX=38 → NEUTRAL_PASSIVE

### 4. Validate Strategy Routing

**Iron Condor (INCOME):**
- Should route when: Regime = INCOME
- Check: `strategy_allowed` contains "IRON_CONDOR"

**Convex Call Backspread (CONVEX):**
- Should route when: Regime = CONVEX
- Check: `strategy_allowed` contains "CONVEX_CALL_BACKSPREAD"

**Calendar (NEUTRAL_ACTIVE):**
- Should route when: Regime = NEUTRAL, Sub-state = NEUTRAL_ACTIVE
- Check: `strategy_allowed` contains "ATM_CALL_CALENDAR"
- **Entry conditions:**
  - IV% in [40, 60] ✓
  - ADX in [18, 25] ✗ (if ADX > 25, trade rejected)
  - ATR not expanding
  - No major events
  - No active positions

### 5. Check Why Trades Are Rejected

**From Logs:**
```bash
# Calendar rejections
grep "Calendar rejected" logs/trading_system_YYYYMMDD.log | tail -5

# Strategy decisions
cat strategy_decisions.json | jq '.[-5:]'
```

**Common Rejection Reasons:**
- `ADX not in range [18, 25]` → ADX too high/low for calendar
- `IV percentile not in range [40, 60]` → IV% outside calendar range
- `NO_VALID_TRADE` → Conditions met but no valid trade found (check option chain)
- `MUTUAL_EXCLUSION` → Another strategy already active

## Current System Status (2026-01-16)

Based on latest logs:

**Indicators:**
- IV%: 56.6-56.7% ✓ (within 40-60 range)
- ADX: 37.8 ✗ (above 25, blocks calendar)
- ATR%: 75.0% (normal, not compressed)

**Regime Detection:**
- Detected: NEUTRAL ✓
- Reasoning: IV% not high enough for INCOME (need >70%), not low enough for CONVEX (need <30%)

**Sub-State:**
- Detected: NEUTRAL_ACTIVE ✓
- Reasoning: IV% in [40, 60] ✓, but ADX=37.8 > 25 ✗

**Strategy Routing:**
- Allowed: ATM_CALL_CALENDAR ✓
- Executed: None ✗
- Reason: ADX 37.8 not in [18, 25] → Calendar entry condition failed

## Validation Checklist

- [ ] IV% calculated correctly (check log for "IV Percentile")
- [ ] ADX calculated correctly (check log for "Calculated ADX")
- [ ] ATR% calculated correctly (check log for "ATR percentile")
- [ ] Regime matches indicator thresholds
- [ ] Sub-state matches IV%/ADX ranges
- [ ] Strategy routing matches regime/sub-state
- [ ] Rejection reasons are accurate

## Troubleshooting

**If indicators seem wrong:**
1. Check if historical data is available (ADX needs 30 days)
2. Check if IV data is being saved (check `market_data_iv/`)
3. Check if ATR history exists (check `market_data_atr/atr_history.json`)

**If regime seems wrong:**
1. Verify indicator values are correct
2. Check regime persistence (may need 2 confirmations)
3. Check if regime changed recently (look for "regime change confirmed")

**If strategy not routing:**
1. Check all entry conditions are met
2. Check for active positions (mutual exclusion)
3. Check option chain availability
4. Check risk limits

## Files to Check

- `logs/trading_system_YYYYMMDD.log` - Main system log
- `strategy_decisions.json` - Strategy routing decisions
- `market_data_iv/` - Historical IV data
- `market_data_atr/atr_history.json` - ATR history
- `active_positions.json` - Current open positions
