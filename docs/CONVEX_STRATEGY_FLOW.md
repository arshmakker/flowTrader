# Convex (Call Backspread) Strategy Flow

## Overview

The Convex strategy uses a Call Backspread structure:
- **Sell 1 ATM Call**
- **Buy 2 OTM Calls (~+1% strike)**
- **Same weekly expiry**

This document details the complete flow from regime detection to trade execution and exit.

---

## 1. Regime Detection (Two-Fork Model)

**File:** `regime/regime_detector.py`

### Inputs

| Input | Source | Description |
|-------|--------|-------------|
| IV Percentile | Option Chain | 0-100% |
| ADX(14) | Technical Indicator | Directional strength |
| ATR Percentile | 20-day history | Volatility percentile |
| India VIX | NSE API | Fear index |
| 15-min EMA | Price data | EMA50 vs EMA100 alignment |
| Price Range | 20-day candles | Last 1hr vs average |

### Logic

```
IF ADX >= 30 AND ATR% >= 50 AND EMA aligned (LONG: price>EMA50>EMA100 OR SHORT: price<EMA50<EMA100)
    → Regime = TRENDING
ELSE
    → Regime = SIDEWAYS
```

### Anti-Whipsaw Protection

- **Confirmation count:** Requires 3 consecutive detections to confirm regime change
- **ATR history:** Stores daily ATR to history for percentile calculation

### Thresholds (Production)

```python
CONVEX_IV_PCT_MAX = 40
CONVEX_VIX_MAX = 15
CONVEX_ATR_PCT_MAX = 25

TREND_ADX_MIN = 30
TREND_ATR_PCT_MIN = 50
```

---

## 2. Trade Generation

**Files:**
- `strategies/convex/call_backspread.py`
- `strategy_runner.py` (_run_convex_backspread_strategy)

### Entry Conditions

1. Regime = TRENDING
2. Days to expiry >= 2 (MIN_DAYS_TO_EXPIRY)
3. Option chain available

### Option Structure

| Leg | Position | Strike | Quantity | Purpose |
|-----|----------|--------|----------|---------|
| 1 | SHORT | ATM (~spot) | 1 lot | Credit collection |
| 2 | LONG | +1% OTM | 2 lots | Unlimited upside |

### Validation Rules

| Rule | Limit |
|------|-------|
| Net debit | ≤ 0.25% of spot value (MAX_NET_DEBIT_PCT) |
| Max loss | ≤ 10% of capital (MAX_LOSS_PCT_OF_CAPITAL) |
| Lots | Clamped via `strategies/size_config.py` |

### Configuration

```python
# strategies/convex/call_backspread.py
MAX_NET_DEBIT_PCT = 0.0025      # 0.25% of spot value
MAX_LOSS_PCT_OF_CAPITAL = 0.10  # 10% of total capital
MIN_DAYS_TO_EXPIRY = 2
OTM_CALL_DISTANCE_PCT = 0.01   # ~1% above ATM
```

### Trade Proposal Output

```python
{
    "strategy": "CALL_BACKSPREAD",
    "book": "CONVEX",
    "regime_at_entry": "CONVEX",
    "expiry": "YYYY-MM-DD",
    "legs": [
        {"position": "SHORT", "option_type": "CE", "strike": 25600.0, "price": 163.475, ...},
        {"position": "LONG", "option_type": "CE", "strike": 25900.0, "price": 48.825, ...}
    ],
    "max_loss": 650000.0,
    "net_debit": 4278.62,
    "net_debit_total": 85572.45,
    "lots": 20,
    "margin_estimate": 650000.0
}
```

---

## 3. Proposal Acceptance & Tracking

**File:** `strategy_runner.py` (lines 1049-1196)

### Process Flow

```
1. Generate UUID (trade_id)
       ↓
2. Snapshot option chain (for replay/debug)
       ↓
3. Estimate margin: max_loss × lots × lot_size
       ↓
4. Validate & clamp lots (safety check)
       ↓
5. Save proposal:
   - trade_proposals/{date}/
   - trade_proposals_by_id/{date}/{trade_id}.json
       ↓
6. Add to position_tracker:
   - Update active_positions.json
```

### Files Generated

| File | Location | Purpose |
|------|----------|---------|
| Trade Proposal | `trade_proposals/YYYYMMDD/` | All proposals |
| By-ID Proposal | `trade_proposals_by_id/YYYYMMDD/{uuid}.json` | Quick lookup |
| Active Positions | `active_positions.json` | Current positions |

---

## 4. Position Monitoring (Live Loop)

**Files:**
- `main.py` (lines 650-800)
- `strategies/iron_condor/position_tracker.py`

### Monitoring Frequency

- Runs every position check cycle (configurable in main loop)

### Per-Position Checks

```
For each open position:
    1. Get current spot price
    2. Fetch current option prices from API
    3. Calculate MTM PnL (mark-to-market)
    4. Check exit conditions
    5. If exit triggered → close_position()
    6. Save to active_positions.json
```

### MTM Calculation (Convex)

```python
# strategies/iron_condor/position_tracker.py (calculate_pnl)
if 'BACKSPREAD' in strategy or book == 'CONVEX':
    # P&L = current_value (entry credit + mark-to-market)
    pnl = current_value
```

---

## 5. Exit Conditions (Convex)

**File:** `strategies/iron_condor/position_tracker.py` (check_convex_exit_conditions)

### Mandatory Exit Triggers

| # | Condition | Threshold | Exit Reason |
|---|-----------|-----------|-------------|
| 1 | **Regime Change** | TRENDING → SIDEWAYS for 3 checks | REGIME_CHANGED |
| 2 | **Time Elapsed** | >40% of expiry duration | TIME_ELAPSED_40PCT |
| 3 | **No ATR Expansion** | After 40% time, ATR% still <30 | NO_ATR_EXPANSION |
| 4 | **Re-compression** | Price in compression >30% time, <0.5% move | RE_COMPRESSION |
| 5 | **Max Loss** | MTM ≤ -30% of entry premium | CONVEX_MAX_LOSS |

### Trailing Stop Loss (TSL)

```python
# Configuration
CONVEX_TSL_ACTIVATION_MTM_PCT = 0.20   # Activate at +20% of entry premium
CONVEX_TSL_TRAIL_PCT = 0.35            # Trail at 35% from peak
CONVEX_TSL_TRAIL_TIGHT_PCT = 0.25      # Tighten to 25% when time >40%
CONVEX_TSL_TIGHT_TIME_PCT = 0.40
CONVEX_TSL_ATR_TIGHT_THRESHOLD = 30
CONVEX_MAX_LOSS_MTM_PCT = 0.30          # Absolute max loss
```

### TSL Logic Flow

```
Entry Premium = ₹10,000 (absolute value of entry_credit)

1. TSL Activation:
   IF MTM >= +20% of entry premium (₹2,000)
      OR time_elapsed >= 25%
      → TSL becomes ACTIVE

2. TSL Hit:
   IF TSL is ACTIVE AND MTM <= peak_mtm - 35%
      → Exit: CONVEX_TSL_HIT

3. Tightening (when):
   IF time_elapsed > 40% OR ATR% < 30
      → Trail tightens to 25%

4. Absolute Max Loss:
   IF MTM <= -30% of entry premium
      → Exit: CONVEX_MAX_LOSS (always, regardless of TSL)
```

### Exit Flow

```
Exit Triggered
    ↓
position_tracker.close_position(position, exit_reason, final_pnl)
    ↓
Update active_positions.json:
    - status = "CLOSED"
    - exit_time = {timestamp}
    - exit_reason = {reason}
    - final_pnl = {pnl}
    ↓
Log: "Position {trade_id} closed: {exit_reason}, P&L=₹{pnl}"
```

---

## 6. Key Configuration Files

| File | Purpose |
|------|---------|
| `strategies/convex/call_backspread.py` | Trade generation logic |
| `strategies/iron_condor/position_tracker.py` | Position tracking + exit rules |
| `strategies/size_config.py` | MIN_LOTS, MAX_LOTS, clamp_lots() |
| `regime/regime_detector.py` | Regime detection logic |
| `strategy_runner.py` | Main strategy orchestration |

---

## 7. Data Flow Diagram

```
┌─────────────────────────────────────────────────────────────────────┐
│                        MAIN LOOP (main.py)                          │
├─────────────────────────────────────────────────────────────────────┤
│  1. Initialize API & SymbolManager                                   │
│                                                                     │
│  2. Every Cycle:                                                    │
│      ├── Get NIFTY spot price                                       │
│      ├── Get option chain (expiry)                                  │
│      ├── Build market_state                                         │
│      │                                                              │
│      ├── DETECT REGIME (RegimeDetector)                            │
│      │   ├── ADX >= 30?                                            │
│      │   ├── ATR% >= 50?                                           │
│      │   └── EMA alignment?                                        │
│      │        ↓                                                    │
│      │   TRENDING ──→ Generate CONVEX trade                       │
│      │   SIDEWAYS ──→ Generate IRON CONDOR                         │
│      │                                                              │
│      ├── RUN STRATEGY (_run_convex_backspread_strategy)            │
│      │   ├── Generate Call Backspread                               │
│      │   ├── Validate (net debit, max loss)                        │
│      │   └── Create trade proposal                                 │
│      │                                                              │
│      ├── ACCEPT PROPOSAL                                           │
│      │   ├── Generate trade_id                                      │
│      │   ├── Estimate margin                                        │
│      │   └── Add to position_tracker                               │
│      │                                                              │
│      ├── MONITOR POSITIONS                                         │
│      │   ├── For each open position:                               │
│      │   │   ├── Calculate MTM PnL                                 │
│      │   │   ├── Check CONVEX exit conditions:                   │
│      │   │   │   ├── Regime change? 3x                            │
│      │   │   │   ├── Time 40%?                                   │
│      │   │   │   ├── No ATR expansion?                          │
│      │   │   │   ├── Max loss -30%?                              │
│      │   │   │   └── TSL hit?                                    │
│      │   │   └── If exit → close_position()                       │
│      │   └── Save to active_positions.json                         │
│      │                                                              │
│  3. End of Day: Generate summary                                    │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 8. Today's Metrics (2026-02-25)

| Metric | Value |
|--------|-------|
| Total Trades | 24 |
| Regime at Entry | CONVEX |
| Lots per Trade | 20 |
| Max Loss per Trade | ₹6.5-7.8L |
| Peak Simultaneous Margin | ₹20.8L |
| Total P&L | ₹13,000.00 |
| Win Rate | ~68% |

### P&L Breakdown

| Trade ID | Entry Time | Exit Time | Lots | Max Loss | P&L |
|----------|------------|-----------|------|----------|-----|
| 2026-02-25T09:25:47 | 09:25:47 | 09:31:56 | 20 | 6.5L | +14.63 |
| 2026-02-25T09:36:57 | 09:36:57 | 09:45:53 | 20 | 7.8L | -6.50 |
| ... | ... | ... | ... | ... | ... |

---

## 9. Going Live - What's Missing

The current system:
- ✅ Generates trade proposals
- ✅ Tracks positions
- ✅ Monitors exit conditions
- ❌ **Does NOT place live orders via broker**

### To Go Live

1. **Add live order execution** in `strategy_runner.py`:
   - Convert proposal legs to broker orders
   - Use `api.place_order()` or `api.place_basket()`
   - Wait for order confirmations
   - Update position with order IDs

2. **Risk controls** (recommended):
   - Max position size: 20 lots (per trade)
   - Max concurrent trades: 3-5
   - Daily loss limit: ₹50,000

3. **Start small**:
   - Paper trade first (simulate fills)
   - 1 lot for first week
   - Scale to 5-10 lots after 2-3 weeks

---

## 10. Configuration Reference

### Size Config (strategies/size_config.py)

```python
MIN_LOTS = 1
MAX_LOTS = 20

def clamp_lots(lots):
    return max(MIN_LOTS, min(MAX_LOTS, lots))
```

### Position Tracker Constants

```python
# strategies/iron_condor/position_tracker.py
CONVEX_REGIME_CHANGE_CONFIRMATION_CHECKS = 3
CONVEX_TSL_ACTIVATION_MTM_PCT = 0.20
CONVEX_TSL_TRAIL_PCT = 0.35
CONVEX_TSL_TRAIL_TIGHT_PCT = 0.25
CONVEX_MAX_LOSS_MTM_PCT = 0.30
```

---

*Last Updated: 2026-02-25*
