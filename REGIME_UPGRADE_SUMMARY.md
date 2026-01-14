# Regime Awareness and Convex Strategy Upgrade - Implementation Summary

## Overview

The system has been upgraded with regime detection and a new Convex Backspread strategy. The system now:
- Detects market regimes (CONVEX, INCOME, NEUTRAL)
- Routes to appropriate strategies based on regime
- Enforces hard mutual exclusion between strategies
- Logs performance by regime

---

## ✅ Completed Components

### 1. RegimeDetector (`regime/regime_detector.py`)

**Features:**
- Calculates ATR(14) from historical price data
- Calculates ATR percentile over last ~20 trading days
- Calculates recent price range (last 30-60 minutes)
- Detects three regimes:
  - **CONVEX**: IV < 40%, ATR percentile < 25%, compressed range
  - **INCOME**: IV > 60%, ADX < 20, ATR not expanding
  - **NEUTRAL**: All other conditions
- Caches regime for 15 minutes to prevent flip-flopping

**Key Methods:**
- `detect_regime(market_state, recent_candles, api, symbol_manager)` → Returns regime dict
- `calculate_atr(high_prices, low_prices, close_prices, period=14)` → Returns ATR value
- `calculate_atr_percentile(current_atr, historical_atrs)` → Returns percentile (0-100)
- `calculate_recent_range(recent_candles, minutes=60)` → Returns price range

---

### 2. Convex Backspread Strategy (`strategies/convex/call_backspread.py`)

**Structure:**
- Sell 1 ATM Call
- Buy 2 OTM Calls (~ +1% above spot)
- Same weekly expiry

**Rules:**
- Net debit ≤ 0.25% of spot value
- Max loss per trade ≤ 1% of total capital
- Rejects trade if debit too high

**Trade Proposal Format:**
```json
{
  "strategy": "CALL_BACKSPREAD",
  "book": "CONVEX",
  "regime_at_entry": "CONVEX",
  "legs": [
    {"position": "SHORT", "option_type": "CE", "strike": float, "quantity": 1, ...},
    {"position": "LONG", "option_type": "CE", "strike": float, "quantity": 2, ...}
  ],
  "net_debit": float,
  "max_loss": float,
  "lots": int
}
```

---

### 3. Strategy Mutual Exclusion (`strategies/strategy_exclusion.py`)

**Features:**
- Single source of truth: `get_active_strategy_type()`
- Checks active positions to determine which strategy is running
- Enforces hard mutual exclusion:
  - If Iron Condor active → Block Convex entries
  - If Convex active → Block Iron Condor entries
  - If none active → Allow either

**Key Functions:**
- `get_active_strategy_type(position_tracker)` → Returns "IRON_CONDOR", "CONVEX", or "NONE"
- `can_enter_strategy(strategy_type, position_tracker)` → Returns True/False

---

### 4. Regime-Based Routing (`strategy_runner.py`)

**New Function: `run_strategy_with_regime()`**

**Flow:**
1. Get NIFTY spot price
2. Fetch option chain
3. Build market state
4. **Detect regime** using RegimeDetector
5. **Route based on regime:**
   - **INCOME regime** → Run Iron Condor (if not blocked by exclusion)
   - **CONVEX regime** → Run Convex Backspread (if not blocked by exclusion)
   - **NEUTRAL regime** → No new trades
6. Check mutual exclusion before entering any trade
7. Save and track trade proposal

**Key Functions:**
- `run_strategy_with_regime(api, symbol_manager, position_tracker, capital)` → Main entry point
- `_run_iron_condor_strategy_internal(...)` → Iron Condor execution
- `_run_convex_backspread_strategy(...)` → Convex Backspread execution

---

### 5. Performance Logging by Regime (`strategies/iron_condor/position_tracker.py`)

**Features:**
- Extended `add_position()` to store:
  - `strategy`: Strategy name
  - `book`: Strategy book (INCOME/CONVEX)
  - `regime_at_entry`: Regime when trade was entered
- Extended `close_position()` to log performance to `performance_by_regime.json`

**Performance Log Format:**
```json
{
  "strategy": "IRON_CONDOR_WEEKLY" | "CALL_BACKSPREAD",
  "book": "INCOME" | "CONVEX",
  "regime_at_entry": "INCOME" | "CONVEX" | "NEUTRAL",
  "entry_time": "ISO timestamp",
  "exit_time": "ISO timestamp",
  "pnl": float,
  "max_loss": float,
  "lots": int,
  "trade_id": "string"
}
```

**File:** `performance_by_regime.json`

---

## 🔄 System Flow

### Before (Old Flow)
```
Main Loop → Strategy Check → Iron Condor → Save Proposal
```

### After (New Flow)
```
Main Loop → Strategy Check
    ↓
Regime Detection
    ↓
Route by Regime:
    ├─ INCOME → Check Exclusion → Iron Condor → Save
    ├─ CONVEX → Check Exclusion → Convex Backspread → Save
    └─ NEUTRAL → No Action
```

---

## 📊 Regime Detection Logic

### CONVEX Regime
**Conditions (ALL must be true):**
- `iv_percentile < 40`
- `atr_percentile < 25`
- `last_range < (rolling_avg_range * 0.6)`

**Action:** Allow Convex Backspread, Block Iron Condor

### INCOME Regime
**Conditions (ALL must be true):**
- `iv_percentile > 60`
- `adx_14 < 20`
- `atr_percentile < 50` (ATR not expanding)

**Action:** Allow Iron Condor, Block Convex Backspread

### NEUTRAL Regime
**Conditions:** All other cases

**Action:** Block all new trades

---

## 🔒 Mutual Exclusion Rules

1. **Single Strategy Active:**
   - Only one strategy type can have active positions at a time
   - Checked before every trade entry

2. **Conflict Detection:**
   - If both strategies have active positions → Log error, manual intervention required
   - System will not enter new trades until conflict resolved

3. **Existing Positions:**
   - Existing positions continue under their exit rules
   - Regime flip does NOT force close existing positions
   - Only blocks NEW entries

---

## 📝 Files Created/Modified

### New Files:
1. `regime/__init__.py`
2. `regime/regime_detector.py`
3. `strategies/convex/__init__.py`
4. `strategies/convex/call_backspread.py`
5. `strategies/strategy_exclusion.py`
6. `REGIME_UPGRADE_SUMMARY.md` (this file)

### Modified Files:
1. `strategy_runner.py`:
   - Added regime detection and routing
   - Added `run_strategy_with_regime()` function
   - Added `_run_iron_condor_strategy_internal()` function
   - Added `_run_convex_backspread_strategy()` function
   - Updated `save_trade_proposal()` to handle both strategies

2. `strategies/iron_condor/position_tracker.py`:
   - Extended `add_position()` to store regime info
   - Extended `close_position()` to log performance by regime
   - Updated `calculate_current_pnl()` to handle convex backspread quantities

3. `main.py`:
   - Updated to use `run_strategy_with_regime()`
   - Updated logging to handle both strategy types

---

## 🎯 Key Design Decisions

1. **Regime Caching:** 15-minute cache prevents flip-flopping
2. **Mutual Exclusion:** Hard enforcement at entry point
3. **Backward Compatibility:** `run_iron_condor_strategy()` still works (delegates to new function)
4. **Performance Logging:** Automatic logging on position close
5. **No Forced Exits:** Regime changes don't force close existing positions

---

## 📈 Performance Analysis

The `performance_by_regime.json` file allows analysis of:
- How Iron Condors perform in INCOME vs NEUTRAL regimes
- How Convex Backspread performs in CONVEX regime
- Drawdowns by regime
- Win rates by regime
- Average P&L by regime

---

## 🚀 Usage

The system now automatically:
1. Detects market regime every strategy check (every 5 minutes)
2. Routes to appropriate strategy based on regime
3. Enforces mutual exclusion
4. Logs all trades with regime information

**No manual intervention required** - the system handles everything automatically.

---

## ⚠️ Important Notes

1. **Existing Iron Condor Logic:** Unchanged - all thresholds, deltas, wings remain the same
2. **No ML/Backtests:** As requested, no machine learning or backtesting added
3. **No UI:** No user interface changes
4. **Production Ready:** All code is production-grade with proper error handling

---

*Implementation completed: 2026-01-09*
