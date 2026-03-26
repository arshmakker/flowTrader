# Project Context

**Project:** RegimeTrader  
**Last Updated:** March 2026  

## What this repo is

RegimeTrader is a Python-based automated trading system for NIFTY derivatives (options and futures). It classifies the trading day (ranging vs trending), applies volatility (VIX) regime filters, routes to a strategy, and manages paper-mode execution with position persistence, daily risk gates, and dashboards.

## Current product direction

The system is shifting to an **Iron Condor–only** product direction:

- Focus on a single defined-risk short premium structure (Iron Condor).
- Paper-first validation and go-live gating.
- Exit logic designed to **lock in profit** and maximize **exits with positive trailing stop (TSL)**.

## Trading calendar & session gating

Runtime gating for “market open” is handled in `strategy_runner.py` and `main.py`:

- `is_market_hours()` / `is_market_closed_ist()` now treat **weekends and configured NSE holidays** as closed.
- `main.py` exits early on non-trading days (before login/symbol load) to avoid creating `market_data_YYYYMMDD` folders on holidays/weekends.
- Holiday dates are configured in `trading_system/config/settings.py` via `TRADING_HOLIDAYS_IST` (ISO `YYYY-MM-DD` strings).
- `SRManager.get_20day_high_low()` skips `market_data_YYYYMMDD` directories that fall on weekends/holidays to avoid stale “holiday quotes” polluting the 20-day S/R window.

## Key documents

- `docs/BUSINESS_OVERVIEW.md`: business & operating model overview.
- `docs/IRON_CONDOR_PRODUCT_REQUIREMENTS.md`: product requirements for Iron Condor–only system (benchmarked defaults + TSL KPI).

## Operational utilities

- `tools/purge_non_trading_day_data.py`: deletes `market_data_YYYYMMDD/` folders that correspond to weekends or configured holidays.
- `tools/dry_run_holiday_impact.py`: offline dry-run to show how holiday/weekend gating evaluates for a given timestamp.


## Testing

- `tests/conftest.py` ignores legacy/manual/integration tests by default so `pytest` runs offline.

## Dead weight cleanup

- Removed legacy/unused modules not referenced by the current IC runtime (e.g. `trading_system/core/strategy_a.py`, `technical_indicators.py`, `temp_optimization.py`).
