## Project context

- **Name**: `ironcondor`
- **Goal**: Options strategy system with regime detection + strategy selection; includes historical market-data storage and backtesting utilities.
- **Market data layout**: `market_data_YYYYMMDD/raw_data/{futures,options,...}` with per-underlying option CSVs.
- **Backtest**: `backtest_iron_condor.py` runs Iron Condor proposal/exit logic against stored tick data.

## Current state (2026-01-14)

- **Synthetic regime tests**: Passing (4/4 scenarios).
- **Backtest dataset available**: 17 daily folders from **20251222 → 20260114**.
- **Backtest outcome (Iron Condor)**: **0 trades across all available dates** → P&L **₹0**, capital unchanged.

### Root cause for 0 trades

- The stored NIFTY option snapshots do **not** provide enough simultaneous strikes per expiry to build a 4-leg iron condor.
- Across all 17 days, the **max unique strikes available at any sampled time (per chosen expiry)** was **2** (needs ≥4).

### Recent changes

- **`backtest_iron_condor.py`** (backtest-only mechanics):
  - Option chain now uses **last-known quote** within a staleness window (default 10 minutes) instead of requiring a quote within ±1 minute.
  - Spot selection now prefers **last-known futures price at/before timestamp** (within 10 minutes) instead of nearest timestamp.
