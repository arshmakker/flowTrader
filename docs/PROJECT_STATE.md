# Project State Snapshot

**Snapshot Date:** March 2026  
**Repo:** `regimetrader`  

## Working tree summary (snapshot)

- Modified:
  - `main.py`
  - `strategy_runner.py` (market-hours gating now respects weekends + configured holidays)
  - `trading_system/config/settings.py` (adds `TRADING_HOLIDAYS_IST` for 2026)
  - `trading_system/core/sr_manager.py` (skips weekend/holiday `market_data_YYYYMMDD` dirs so S/R isn’t polluted by stale holiday quotes)
  - `tests/test_strategy_runner.py` (adds deterministic holiday/weekend market-hours tests)
  - `docs/PROJECT_CONTEXT.md`
  - `docs/PROJECT_STATE.md`

- Added:
  - `tools/purge_non_trading_day_data.py`
  - `tools/dry_run_holiday_impact.py`

## Working tree summary (snapshot)

- Modified: `tests/conftest.py`, `trading_system/paper/paper_pnl_engine.py`, `trading_system/config/settings.py`
- Deleted: `tests/test_forgot_password.py`

## Working tree summary (snapshot)

- Modified: `tests/conftest.py`, `trading_system/paper/paper_pnl_engine.py`, `trading_system/config/settings.py`
- Removed: `trading_system/core/strategy_a.py`, `technical_indicators.py`, `temp_optimization.py`, `tests/test_forgot_password.py`

## Latest product artifact added

- `docs/IRON_CONDOR_PRODUCT_REQUIREMENTS.md` (PRD v1.0): requirements for an Iron Condor–only trading product, including:
  - Benchmark defaults (delta selection, DTE conventions, 50% profit taking, 21 DTE management)
  - Risk sizing and daily gates
  - Logging/metrics requirements with primary KPI: **% exits with positive TSL**

