# Running Backtests End-to-End

## Data requirement

Backtests read from **`market_data_YYYYMMDD`** directories (e.g. `market_data_20251222`). Each directory should contain:

- **Regime / Trend / Convex:** `raw_data/futures/` — NIFTY futures tick or 15m data (for ADX, ATR, EMA, regime).
- **Iron Condor / Convex / Calendar:** `raw_data/options/` — option chain data; Convex also needs futures for regime.
- **India VIX:** Optional; when missing, backtests use synthetic 14. For real VIX, ensure `market_data_YYYYMMDD/daily_metrics.json` has `india_vix` (e.g. from production `save_daily_metrics`).

Collect data with `data_collector.py` (or your pipeline) for the date range you want to backtest.

---

## 1. Regime detection backtest

Validates TREND-first regime logic (CONVEX/INCOME/TREND/NEUTRAL) over history.

```bash
# Default: 20251222–20260116, no IV CSV
python3 backtest_regime_detection.py

# Custom date range and optional IV CSV
python3 backtest_regime_detection.py 20251201 20260131
python3 backtest_regime_detection.py 20251201 20260131 /path/to/iv_percentile.csv
```

**Output:** Console summary + `backtest_regime_report_YYYYMMDD_HHMMSS.json`.

---

## 2. Trend following backtest

Runs trend strategy over history (entry/exit, PnL, trailing stop).

```bash
# Default: 20251222–20260116, comparison of scenarios
python3 backtest_trend_following.py

# Single date, force 1 lot (e.g. for debugging)
python3 backtest_trend_following.py --date 20260203 --force-1-lot
```

**Output:** Console comparison table + JSON report(s).

---

## 3. Convex backspread backtest

Backtests call backspread when regime is CONVEX (not TREND, India VIX < 12).

```bash
python3 backtest_convex_backspread.py
```

Uses fixed range `20251222`–`20260116` in `main()`. Edit `backtest_convex_backspread.py` → `main()` → `run_backtest('YYYYMMDD', 'YYYYMMDD')` to change dates.

**Output:** Console summary + `backtest_convex_report_YYYYMMDD_HHMMSS.json`.

---

## 4. Iron Condor backtest

```bash
python3 backtest_iron_condor.py
```

Uses fixed range `20251222`–`20251226` in `main()`. Edit `backtest_iron_condor.py` → `main()` → `run_backtest('YYYYMMDD', 'YYYYMMDD')` to change dates.

**Output:** Console summary + `backtest_report_YYYYMMDD_HHMMSS.json`.

---

## 5. Neutral calendar backtest

```bash
python3 backtest_neutral_calendar.py
```

Uses fixed range `20251222`–`20260116` in `main()`. Edit dates in `main()` if needed.

**Output:** Console summary + `backtest_calendar_report_YYYYMMDD_HHMMSS.json`.

---

## 6. Trailing stop comparison backtest

Compares fixed vs hybrid trailing stop.

```bash
python3 backtest_trailing_stop_comparison.py
```

Uses fixed range `20251222`–`20260116` in `main()`.

**Output:** Console comparison + JSON.

---

## Run all backtests in one go

A shell script runs all six backtests in order:

```bash
chmod +x run_all_backtests.sh
./run_all_backtests.sh
```

With a custom date range (only the **regime** backtest uses these; others use their own hardcoded ranges):

```bash
./run_all_backtests.sh 20251201 20260131
```

Or run them manually from repo root (default range `20251222`–`20260116` where applicable):

```bash
python3 backtest_regime_detection.py 20251222 20260116
python3 backtest_trend_following.py
python3 backtest_convex_backspread.py
python3 backtest_iron_condor.py
python3 backtest_neutral_calendar.py
python3 backtest_trailing_stop_comparison.py
```

**Note:** Convex, Iron Condor, Calendar, and Trailing scripts have dates hardcoded in `main()`. To change their range, edit those scripts or add CLI args.
