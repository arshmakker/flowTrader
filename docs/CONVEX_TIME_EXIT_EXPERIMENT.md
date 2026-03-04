# Convex time-exit experiment (20251222–20260304)

Ran backtest with three time-exit thresholds and compared reports.

**Conclusion: inconclusive.** Outcomes were identical across 30%/40%/50%; the threshold had no effect in this run. Reasons: only 6 trades, 0 winners, and time is in whole days so all three thresholds were exceeded on the same bar for the two time-based exits.

## Commands

```bash
python3 backtest_convex_backspread.py 20251222 20260304 --time-exit 0.30
python3 backtest_convex_backspread.py 20251222 20260304 --time-exit 0.40
python3 backtest_convex_backspread.py 20251222 20260304 --time-exit 0.50
```

## Results

| Time-exit threshold | Report | Trades | Total P&L | Win rate | Exit mix |
|--------------------|--------|--------|-----------|----------|----------|
| 30% | backtest_convex_report_20260304_151313.json | 6 | ₹-384.60 | 0% | 3 REGIME_CHANGED, 2 TIME_ELAPSED_40PCT, 1 end_of_backtest |
| 40% | backtest_convex_report_20260304_151539.json | 6 | ₹-384.60 | 0% | Same |
| 50% | backtest_convex_report_20260304_151540.json | 6 | ₹-384.60 | 0% | Same |

**Conclusion (this run):** Outcomes were identical. Total P&L and exit-reason mix did not change with 30% vs 40% vs 50%.

- The two TIME_ELAPSED_40PCT exits had **hold_minutes ≈ 15** (same-day or very short hold). So in this backtest setup, time_elapsed_pct reaches the threshold early (e.g. same-day expiry or day-count logic), and all three thresholds (0.30, 0.40, 0.50) are exceeded at the same bar → same exits.
- The three REGIME_CHANGED exits fire before the time-exit check (regime is evaluated first), so they are unchanged by the time threshold.

**What would make the test conclusive:** (1) Backtest with time_elapsed in minutes (or intraday fraction) so 30% / 40% / 50% can trigger on different bars; (2) more trades (longer range or relaxed entry filters) so some exit on time at 30% and others at 40% or 50%; (3) at least some winning trades so you can compare win rate and P&L across thresholds. Until then, the time-exit experiment remains inconclusive.

## How to re-run

Use a different range or same range and inspect per-trade `hold_minutes` and `exit_reason`:

```bash
python3 backtest_convex_backspread.py <start> <end> --time-exit 0.30
python3 analyze_convex_backtest.py backtest_convex_report_<timestamp>.json
```

Compare “By exit_reason” and “By time_elapsed % (approx)” across reports.
