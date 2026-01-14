# How to Check if Regime Detection is Working

## Current Status

Based on the logs, the system appears to be running the **old code** (before regime detection was added). 

**Evidence:**
- Logs show: `"Running Iron Condor strategy check"` (old message)
- Should show: `"Running strategy check with regime detection"` (new message)

## To Verify Regime Detection is Working

### 1. Check if System Needs Restart

The system needs to be **restarted** to load the new code. After restart, you should see:

```
INFO - Running strategy check with regime detection...
INFO - === Running Strategy Check with Regime Detection ===
INFO - Regime detected: INCOME (or CONVEX or NEUTRAL)
INFO -   IV Percentile: XX.X%
INFO -   ADX: XX.X
INFO -   ATR Percentile: XX.X
INFO -   Range State: COMPRESSED/NORMAL/EXPANDING
```

### 2. What to Look For in Logs

**After restart, check logs for:**

1. **Regime Detection Messages:**
   ```
   Regime detected: INCOME/CONVEX/NEUTRAL
   ```

2. **Regime Metrics:**
   ```
   IV Percentile: XX.X%
   ADX: XX.X
   ATR Percentile: XX.X
   Range State: COMPRESSED/NORMAL/EXPANDING
   ```

3. **Routing Messages:**
   - `"=== Running Iron Condor Strategy (INCOME regime) ==="` (if INCOME)
   - `"=== Running Convex Backspread Strategy (CONVEX regime) ==="` (if CONVEX)
   - `"NEUTRAL regime: No new trades allowed"` (if NEUTRAL)

4. **Mutual Exclusion Messages:**
   - `"Iron Condor blocked by mutual exclusion"` (if Convex active)
   - `"Convex strategy blocked by mutual exclusion"` (if Iron Condor active)

### 3. Expected Behavior by Regime

#### INCOME Regime
- IV Percentile > 60%
- ADX < 20
- ATR not expanding
- **Action:** Attempts Iron Condor trades
- **Blocks:** Convex Backspread

#### CONVEX Regime
- IV Percentile < 40%
- ATR Percentile < 25%
- Range compressed (< 60% of rolling average)
- **Action:** Attempts Convex Backspread trades
- **Blocks:** Iron Condor

#### NEUTRAL Regime
- All other conditions
- **Action:** No new trades
- **Logs:** `"NEUTRAL regime: No new trades allowed"`

### 4. Test the System

Run the test script:
```bash
python3 test_regime_detection.py
```

This will verify the regime detector can be imported and basic structure works.

### 5. Check Performance Logging

After trades are closed, check:
```bash
cat performance_by_regime.json
```

This file should contain entries with:
- `regime_at_entry`: The regime when trade was entered
- `strategy`: Strategy name
- `book`: INCOME or CONVEX

### 6. Verify Mutual Exclusion

Check active positions:
```bash
cat active_positions.json
```

The system should:
- Only allow one strategy type at a time
- Block new entries if opposite strategy is active
- Log exclusion messages when blocked

## Troubleshooting

### If Regime Detection Not Working:

1. **Check imports:**
   ```python
   python3 -c "from regime import RegimeDetector; print('OK')"
   ```

2. **Check for errors in logs:**
   ```bash
   grep -i "error\|exception\|traceback" logs/trading_system_*.log | tail -20
   ```

3. **Verify code is loaded:**
   - Check that `strategy_runner.py` has `run_strategy_with_regime` function
   - Check that `main.py` calls `run_strategy_with_regime`

4. **Restart the system:**
   - Stop the current process
   - Restart with: `python3 main.py`

## Current Market Conditions Check

To see what regime the current market would be detected as:

1. Check current IV Percentile in logs
2. Check current ADX in logs
3. Compare against regime thresholds:
   - INCOME: IV > 60%, ADX < 20
   - CONVEX: IV < 40%, ATR < 25%, Range compressed
   - NEUTRAL: Everything else

---

*Last Updated: 2026-01-13*
