# Testing Regimes in the Backtest

The backtest uses the **same regime criteria as production** via `classify_regime_from_indicators()` in `regime/regime_detector.py`. You can test all four regimes when IV data is provided, or TREND + NEUTRAL only when it is not.

## How it works

1. **Per bar** the backtest computes: ADX(14), ATR percentile (from last 100 candles), range (high−low) and whether it is compressed (< 0.6× rolling avg), and EMA direction (LONG/SHORT/None).
2. **Regime** is set by `classify_regime_from_indicators(iv_percentile, adx_14, atr_percentile, range_compressed, ema_direction)` using production thresholds:
   - **CONVEX:** IV < 40%, ATR% < 25%, range compressed
   - **INCOME:** IV > 60%, ADX < 20, ATR% < 50
   - **TREND_CONTINUATION:** ADX ≥ 30, ATR% ≥ 50, EMA direction LONG or SHORT
   - **NEUTRAL:** else
3. **Without IV:** `iv_percentile` is `None` → CONVEX and INCOME never trigger; only TREND and NEUTRAL are tested (same thresholds as production).
4. **With IV:** Pass a CSV so the backtest can test all four regimes.

## Testing without IV (default)

- Run: `python3 backtest_trend_following.py`
- Regime stats: **P(CONVEX per day)** and **P(INCOME per day)** will be 0%; **P(TREND per day)** and **P(NEUTRAL per day)** use production thresholds (ADX ≥ 30, ATR% ≥ 50, EMA direction for TREND).

## Testing all four regimes (with IV)

1. **Create an IV CSV** with columns:
   - `date`: YYYYMMDD (e.g. 20260115)
   - `iv_percentile`: 0–100 (e.g. 55.2)
2. **Run the backtest with IV:**

   ```python
   from backtest_trend_following import TrendFollowingBacktester

   b = TrendFollowingBacktester(initial_capital=1_000_000)
   b.run_backtest('20251222', '20260116', check_interval_minutes=15,
                  iv_csv_path='path/to/iv_percentile_by_date.csv')
   report = b.generate_report()
   # report has days_with_convex, days_with_income, days_with_trend, days_with_neutral
   # and prob_*_per_day_pct for each regime
   ```

   Or add an optional `--iv-csv` (or similar) to the script’s `main()` and pass it into `run_backtest(iv_csv_path=...)`.

3. **Where to get IV:** From your options data (e.g. NIFTY option chain) compute IV and then IV percentile vs recent history; dump one row per date with `date` and `iv_percentile`.

## Summary

| IV data      | CONVEX/INCOME      | TREND/NEUTRAL              |
|-------------|--------------------|-----------------------------|
| No IV CSV   | Never trigger (0%) | Production thresholds       |
| IV CSV provided | Tested with production thresholds | Same as above            |

Thresholds are defined in `regime/regime_detector.py`: `CONVEX_IV_PCT_MAX`, `CONVEX_ATR_PCT_MAX`, `INCOME_IV_PCT_MIN`, `INCOME_ADX_MAX`, `INCOME_ATR_PCT_MAX`, `TREND_ADX_MIN`, `TREND_ATR_PCT_MIN`.
