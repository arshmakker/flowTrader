# Convex: “Percentage of time in profit” — what it means and how to estimate

## Two different metrics

1. **Win rate** = % of **trades** that close in profit.  
   - Example: 12 trades, 7 closed with positive P&L → win rate ≈ 58%.  
   - This is what the backtest already reports (`win_rate` in `generate_report()`).

2. **Time in profit** = % of the **holding period** (e.g. bar-by-bar or minute-by-minute) that the position had **positive unrealized MTM**.  
   - Example: a trade lasts 20 bars; MTM &gt; 0 on 12 of them → time in profit = 60%.  
   - The backtest does **not** currently compute this; you’d need to add it (see below).

So “how much percentage of time would we be in profit” can mean either **win rate** (share of trades that end in profit) or **time in profit** (share of the life of each trade spent with MTM &gt; 0).

---

## Can we predict a number?

**Not a single reliable number** without running your setup on your data.

- **Win rate** and **time in profit** both depend on:
  - Date range and regime mix (how often regime is TRENDING, how long positions last).
  - Volatility (VIX, ATR) and how often TSL activates in profit vs time/regime/max-loss exit in loss.
  - Entry filters (net debit cap, DTE, etc.) and exit rules (TSL only in profit, no ATR/re-compression when in profit, etc.).

So the only way to get a **concrete percentage** for your trade setup is to:

1. **Run the Convex backtest** on your chosen period:  
   `python3 backtest_convex_backspread.py <start_YYYYMMDD> <end_YYYYMMDD>`
2. Read **win rate** from the generated report (that’s “% of trades that were in profit at exit”).
3. Optionally add a **time-in-profit** metric to the backtest (below) and run again to get “% of bars in profit”.

---

## Qualitative expectations (no guarantee)

- **Win rate**  
  - With current rules (entry in TRENDING only; exit on TSL only when MTM &gt; 0; time/regime/NO_ATR/RE_COMP only when in loss), you’d *hope* for something in a **40–60%+** band in favorable regimes, but it can be lower or higher depending on period and data.
  - One existing main-flow report (different backtester) had 3/3 Convex trades in profit; that’s too few and too specific to generalize.

- **Time in profit**  
  - Convex benefits from **upside + vol**. So you’d expect that when the trade is “working,” MTM can stay positive for a good part of the hold.  
  - There is no generic “X% of the time you’ll be in profit” — it’s path-dependent. Adding the metric below will give you an empirical distribution (e.g. “on average, 55% of bars were in profit”) for the period you backtest.

---

## How to add “time in profit” to the backtest

To get **percentage of time in profit** per trade (and on average):

1. In `backtest_convex_backspread.py`, for each open position at each check time (each “bar”):
   - Compute `current_mtm = self._estimate_convex_mtm(position, spot_price)` (you already do this).
   - Increment a counter for “bars with position open” and, if `current_mtm > 0`, increment “bars in profit.”
2. When the position is closed, store in the trade record:
   - `bars_total`, `bars_in_profit`, and e.g. `pct_time_in_profit = 100 * bars_in_profit / bars_total`.
3. In `generate_report()`, add:
   - Average of `pct_time_in_profit` across trades.
   - Optionally: distribution (min, max, median) and share of trades with e.g. &gt; 50% time in profit.

Then “what percentage of time would we be in profit” for that setup and period is the **average (or median) pct_time_in_profit** from the report, and win rate remains the existing **win_rate** (percentage of trades that closed in profit).
