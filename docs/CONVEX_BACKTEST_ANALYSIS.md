# Convex backtest results – analysis

Analysis of the 6-trade run (20251222–20260304) and the time-exit experiment.

**Caveat:** This run is **inconclusive** for drawing firm conclusions: only 6 trades, 0 winners, and the time-exit threshold (30% vs 40% vs 50%) did not change behaviour. The patterns below are suggestive only; more data and a conclusive time-exit test are needed before changing strategy.

---

## 1. Overall result

| Metric | Value |
|--------|--------|
| Total trades | 6 |
| Winning trades | 0 |
| Total P&L | **₹-384.60** |
| Return | -0.38% |
| Avg P&L per trade | ₹-64.10 |

All trades lost. No winning pattern in this sample.

---

## 2. Exit reason vs P&L

| Exit reason | Count | Total P&L | Avg P&L | Comment |
|-------------|--------|-----------|---------|--------|
| **TIME_ELAPSED_40PCT** | 2 | ₹-25.04 | **₹-12.52** | Small, capped losses; held ~15 min only. |
| **REGIME_CHANGED** | 3 | ₹-171.95 | **₹-57.32** | One small (-₹9.59, -₹12.69), one large (-₹149.68). |
| **end_of_backtest** | 1 | ₹-187.61 | ₹-187.61 | Single open position closed at end of run. |

**Takeaway:** Time-based exits (TIME_ELAPSED_40PCT) gave the **least bad** outcome per trade (avg -₹12.52). REGIME_CHANGED and end_of_backtest carried the bulk of the loss (₹-171.95 and ₹-187.61) and had worse average loss.

---

## 3. Hold duration vs outcome

| Hold (approx) | Trades | Exit reason(s) | Total P&L |
|---------------|--------|----------------|-----------|
| **15 min** | 2 | TIME_ELAPSED_40PCT | ₹-25.04 |
| **~21–24 h** | 3 | REGIME_CHANGED | ₹-171.95 |
| **~3.5 h** | 1 | end_of_backtest | ₹-187.61 |

**Takeaway:** Very short holds (15 min) exited on time and lost little. Longer holds either exited on regime change (mixed: two small losses, one large) or were still open at end_of_backtest (one very large loss). So in this run, **exiting sooner when in loss** (time rule) limited damage; **holding into regime change or end of backtest** increased it.

---

## 4. Entry conditions vs loss size

**Entry debit**

- **&lt;₹1k** (4 trades): total ₹-47.31, avg **₹-11.83**.
- **&gt;₹10k** (2 trades): total ₹-337.29, avg **₹-168.64**.

**IV percentile at entry**

- **Low IV** (5–20%): 4 trades, total ₹-47.31 (small losses).
- **Very high IV** (99–100%): 2 trades, total ₹-337.29 (the two big losers: -₹149.68, -₹187.61).

**Takeaway:** The two worst trades had **high entry debit** and **very high IV at entry**. So in this sample, **high debit and very high IV at entry** were associated with much larger losses when the trade went wrong.

---

## 5. Time-exit experiment (30% vs 40% vs 50%)

- **Result:** Identical for all three thresholds: same 6 trades, same total P&L (₹-384.60), same exit mix.
- **Reason:** The two TIME_ELAPSED exits had **hold_minutes = 15**. In the backtest, time is in **whole days** to expiry; on the bar where those trades exited, `time_elapsed_pct` was already above 50%, so 30%, 40%, and 50% were all exceeded on the same bar → same exit time and same outcome.
- **Conclusion:** For this run, changing the time-exit threshold **did not** change behaviour. To see a difference, the backtest would need either time in finer units (e.g. minutes) or more multi-day holds so that some trades cross 30% but not 40%, etc.

---

## 6. Summary and recommendations

**What the data shows**

1. **Time-based exit (when in loss)** produced the **least bad** avg P&L (₹-12.52) and acted as a loss cap on short holds.
2. **REGIME_CHANGED** and **end_of_backtest** contributed most of the total loss and had worse average loss per trade.
3. **High entry debit** and **very high IV at entry** (99–100%) were associated with the two largest losses.
4. **Short holds** (15 min) that hit the time exit lost little; **longer holds** that hit regime or end_of_backtest lost more.

**Possible next steps (no change applied)**

- **Entry filters:** Consider tightening entry when IV is very high (e.g. &gt;90%) or when net debit is large (e.g. cap debit or require higher conviction).
- **Regime exit:** Current logic already “ignores regime change when in profit”. For losing trades, regime change is the main driver of loss; more confirmation or a time cap after regime flip could be tested in backtest.
- **Time granularity:** If you want the time-exit threshold (30% / 40% / 50%) to matter in backtest, implement time_elapsed in minutes (or intraday fraction) so 30%/40%/50% can fall on different bars.
- **More data:** Run longer ranges or relax filters to get more trades, then re-run the analyzer and this analysis to see if these patterns hold.

---

## 7. Report and analyzer reference

- **Latest 6-trade report:** e.g. `backtest_convex_report_20260304_151539.json`.
- **Full breakdown:**  
  `python3 analyze_convex_backtest.py backtest_convex_report_20260304_151539.json`
- **Time-exit experiment:** `docs/CONVEX_TIME_EXIT_EXPERIMENT.md`.
