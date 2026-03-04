# Convex Backtest: Winning Pattern (from existing data)

Summary from analyzing `backtest_convex_report_*.json` files (no live market data).

---

## Current backtest (TRENDING-only, 45min no-entry before close)

- **Latest run** (e.g. 20251222–20260304): 4 trades, 0 winners, total P&L ₹-47.31.
- **Winning pattern**: None in this sample. **Least bad exit**: REGIME_CHANGED (avg -₹11.14) vs TIME_ELAPSED_40PCT (avg -₹12.52).

---

## Historical reports (different config: NEUTRAL regime, variable lots)

### Report: 51 trades, 3 wins (backtest_convex_report_20260209_153748.json)

- **Total P&L**: ₹4,241.10 | **Win rate**: 5.9%.
- **Winning pattern**:
  - **Exit reason**: All 3 winners exited on **TIME_ELAPSED_40PCT**.
  - **Entry debit**: Winners had entry_debit in range ₹-3128 to ₹755 (two were credits).
  - **Segment**: TIME_ELAPSED_40PCT had 50 trades, avg P&L +₹85.01, 6% win rate (reliable).
- **Losing segment**: end_of_day (1 trade, -₹9.59).

### Report: 220 trades, 219 wins (backtest_convex_report_20260208_122234.json)

- **Config**: regime_at_entry "NEUTRAL", variable lots (1–15). Likely different backtest logic (e.g. P&L formula or entry rules).
- **By exit**: TIME_ELAPSED_40PCT 210 trades, 99.5% win rate; end_of_day 9 trades, 100% win rate.
- **By debit**: All bins (e.g. &lt;1k, 1k–3k, 3k–5k, 5k–10k, &gt;10k) had 98–100% win rate. Not comparable to current TRENDING-only, single-lot backtest.

---

## Takeaways (from data we have)

1. **Exit**: When there are winners, they consistently exit on **TIME_ELAPSED_40PCT**. REGIME_CHANGED exits tend to be worse or similar to time-based exits in losing runs.
2. **Current run**: No winners; too few trades (4) to infer a robust pattern. Need more trades (e.g. more dates or relaxed net-debit/entry rules) to see winning segments.
3. **end_of_day**: In the 51-trade run the single end_of_day trade lost; supports avoiding late entry (e.g. no entry within 45 min of close).
4. **Entry debit**: In the 51-trade run, the two largest winners had credit at entry (negative entry_debit). May be artifact of that run’s logic; worth checking if credit/cheap debit structures help in your strategy.

---

## How to re-run

```bash
# Backtest
python3 backtest_convex_backspread.py 20251222 20260304

# Analyze latest report and print winning-pattern summary
python3 analyze_convex_backtest.py
# Or a specific report:
python3 analyze_convex_backtest.py backtest_convex_report_YYYYMMDD_HHMMSS.json
```

The analyzer prints a **WINNING PATTERN SUMMARY** at the end: either “No winning trades” with least-bad exit, or exit reason(s) and entry hour/debit range for winners.
