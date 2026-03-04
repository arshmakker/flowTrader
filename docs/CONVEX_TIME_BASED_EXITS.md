# Convex: Time-based exits

How time is used in Convex backspread exit logic (production and backtest). All use **time elapsed as a fraction of expiry duration** (`time_elapsed_pct`).

---

## How `time_elapsed_pct` is computed

- **Entry**: `entry_days_to_expiry` = days from entry to expiry (e.g. 7 for weekly).
- **Current**: `days_to_expiry` = days from now to expiry.
- **Formula**:
  ```text
  time_elapsed_pct = (entry_days_to_expiry - days_to_expiry) / entry_days_to_expiry
  ```
- Example: entry with 7 days to expiry, now 4 days to expiry → `(7-4)/7 ≈ 0.43` (43% of option life elapsed).

---

## Time-based exit rules

### 1. Hard time exit: **TIME_ELAPSED_40PCT**

- **Condition**: `time_elapsed_pct > 0.40` **and** position is not in profit (`current_mtm <= 0`).
- **Intent**: If we’re past 40% of the trade’s life and still in loss, exit to avoid holding a loser into late theta decay.
- **Production**: `strategies/iron_condor/position_tracker.py` (Exit condition 2).  
- **Backtest**: `backtest_convex_backspread.py` (§2).  
- **Softening**: We only apply this when in loss; if `current_mtm > 0`, we do **not** exit on time and let TSL or other exits handle it.

### 2. TSL activation by time: **CONVEX_TSL_ACTIVATION_TIME_PCT = 0.25**

- **Condition**: TSL is activated when **either**:
  - MTM ≥ +5% of entry premium (`CONVEX_TSL_ACTIVATION_MTM_PCT`), **or**
  - `time_elapsed_pct >= 0.25`.
- **Intent**: After 25% of the trade’s life we start trailing (even if MTM is still small) so we don’t give back late gains.
- **Constants**: `position_tracker.py` lines 21–22.

### 3. TSL “tight” trail by time: **CONVEX_TSL_TIGHT_TIME_PCT = 0.40**

- **Condition**: When `time_elapsed_pct > 0.40` (or ATR percentile &lt; 30), the trail is tightened from 15% to 10% drawdown from peak.
- **Intent**: In the second half of the trade’s life, lock profit more aggressively (smaller pullback triggers exit).
- **Constants**: `position_tracker.py` lines 24–25 (`CONVEX_TSL_TRAIL_PCT` 15%, `CONVEX_TSL_TRAIL_TIGHT_PCT` 10%).

### 4. No ATR expansion: **NO_ATR_EXPANSION** (also time-gated)

- **Condition**: `time_elapsed_pct >= 0.40` **and** ATR percentile &lt; 30.
- **Intent**: If we’re past 40% of the trade and volatility never expanded, the thesis (vol expansion) is unlikely; exit.
- **Production / backtest**: Same 40% threshold as the hard time exit.

### 5. Re-compression (time-gated)

- **Condition**: Range was COMPRESSED at entry and still COMPRESSED, **and** `time_elapsed_pct > 0.30` and price move &lt; 0.5%.
- **Intent**: After ~30% of life, if price hasn’t moved and range is still compressed, exit (re-compression).

---

## Constants summary

| Constant | Value | Role |
|----------|--------|------|
| Hard time exit threshold | **0.40** (40%) | Exit when in loss and time &gt; 40% (TIME_ELAPSED_40PCT). |
| TSL activation (time) | **0.25** (25%) | Activate TSL by time if not already activated by MTM. |
| TSL tight (time) | **0.40** (40%) | Use 10% trail instead of 15% when time &gt; 40%. |
| No-ATR-expansion gate | **0.40** (40%) | Only check “no ATR expansion” after 40% of life. |
| Re-compression time gate | **0.30** (30%) | Only check re-compression after 30% of life. |

---

## Order of evaluation (production / backtest)

1. Regime change (with confirmation; skipped when TSL active or in profit).
2. **Time &gt; 40% and in loss** → TIME_ELAPSED_40PCT.
3. Time ≥ 40% and no ATR expansion → NO_ATR_EXPANSION.
4. Re-compression (time &gt; 30%).
5. Max loss (MTM ≤ -30% of entry premium).
6. TSL: activate at 25% time or +5% MTM; trail 15% (10% when time &gt; 40%); exit on CONVEX_TSL_HIT.

So **time-based exits** are: the 40% hard exit (when in loss), TSL activation at 25%, TSL tight at 40%, and the 40% gate for NO_ATR_EXPANSION.

---

## Exploring different time thresholds

- **Softer time exit** (e.g. 50%): fewer TIME_ELAPSED_40PCT exits; positions held longer when in loss (more theta risk, more chance for a reversal).
- **Tighter time exit** (e.g. 30%): more early exits when in loss; less time for the trade to recover.
- **TSL activation**: Raising 25% → e.g. 35% delays when we start trailing; lowering it (e.g. 20%) trails earlier.
- **TSL tight**: Raising 40% → e.g. 50% keeps the 15% trail longer; lowering 40% tightens earlier.

**Exploring in backtest:** Run with a different time-exit threshold to compare P&L and exit mix:

```bash
python3 backtest_convex_backspread.py 20251222 20260304 --time-exit 0.30   # exit at 30% of life when in loss
python3 backtest_convex_backspread.py 20251222 20260304 --time-exit 0.50   # exit at 50% of life when in loss
```

Default is 0.40 (40%). Then run `python3 analyze_convex_backtest.py <report>` and check “By time_elapsed % (approx)” and exit_reason mix.
