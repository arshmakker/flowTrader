# CLAUDE.md — RegimeTrader

## On conversation start

Every time this project is opened in Claude, automatically compute and display the day's PnL summary. Use the system date (do NOT hardcode a date). Steps:

1. Get today's date from the system (`date` command)
2. Read `data/pnl_snapshot.json` — check if the `timestamp` field matches today's date
3. If it matches today, display a summary: daily realised PnL, unrealised PnL, net total, trade count, win rate, and per-instrument breakdown (NIFTY/BANKNIFTY)
4. Also scan `data/paper_trades.csv` for rows matching today's date to show individual trade details if useful
5. If the snapshot is from a previous day, report "No trading data for today yet" with the date of the last snapshot

Format the output as a concise table. Always show the PnL in INR (₹).

## What is this project

RegimeTrader is a Python-based automated trading system for NIFTY derivatives (options/futures) on the Indian stock market. It classifies trading days by regime (ranging vs trending), applies VIX-based filters, and executes **Iron Condor** strategies in paper-trading mode via the **Shoonya (Noren) broker API**.

The system is **paper-trade only** — live order execution is not implemented. All fills, costs, and P&L are simulated with realistic slippage and transaction costs.

## Architecture overview

```
main.py (orchestrator)
  ├── api_helper.py         — Shoonya API wrapper (OAuth + legacy 2FA, rate limiting)
  ├── symbol_manager.py     — NFO/NSE/BSE symbol master loading + token resolution
  ├── data_collector.py     — Background tick collection thread (5s cycle)
  ├── strategy_runner.py    — Market-hours helpers, expiry/VIX utilities
  │
  └── trading_system/
      ├── config/settings.py        — All tunable parameters (single file)
      ├── core/
      │   ├── day_classifier.py     — RANGING vs TRENDING classification (locks at 10:30)
      │   ├── regime_filter.py      — VIX monitoring + entry gate (VIX<30, stable 45min)
      │   ├── iron_condor.py        — 4-leg IC strategy (entry, monitor, harvest, exit)
      │   ├── signal_engine.py      — VWAP, RSI, PCR, Max Pain signals
      │   ├── risk_manager.py       — 3x stop-loss + recovery exception logic
      │   ├── expiry_manager.py     — 3 DTE rolling rule
      │   ├── sr_manager.py         — 20-day high/low S/R with 50-point buffer
      │   ├── trade_logger.py       — CSV trade log + signal log
      │   └── position_persistence.py — JSON state save/restore
      ├── existing/
      │   └── market_data.py        — MarketData adapter (LTP caching, OHLCV, option validation)
      ├── paper/
      │   ├── paper_order_manager.py    — Simulated fills with slippage + costs
      │   ├── paper_position_tracker.py — In-memory position management
      │   ├── paper_pnl_engine.py       — Cumulative + daily P&L tracking
      │   └── go_live_evaluator.py      — Readiness thresholds for live migration
      └── dashboard/
          ├── web_dashboard.py      — Flask dashboard (port 5050)
          └── terminal_dashboard.py — Rich terminal display
```

## Key execution flow

1. **Auth** — Dual-mode: OAuth (preferred, token-cached in `cred.yml`) or legacy 2FA
2. **Data collection** — Background thread collects ticks from 09:15
3. **Classification** — Day type locked at 10:30 (RANGING/TRENDING)
4. **Entry gate** — Only enters on RANGING days with VIX < 30 and stable for 45 min
5. **IC strategy** — VIX-adaptive strikes, S/R buffered, 4-leg atomic entry
6. **Monitoring** — 1% harvest cycles (close + re-enter), breach adjustments
7. **Hard close** — All positions closed at 14:15, system shutdown by 15:30
8. **Persistence** — State saved to `data/open_positions.json` after every cycle

## Common commands

```bash
# Run the system
python main.py

# Run with startup script (handles auth mode detection)
./start.sh

# Run tests (fast unit tests only by default)
pytest

# Run specific test file
pytest tests/test_paper_trading.py

# Install dependencies
pip install -r requirements.txt
```

## Testing

Tests live in `tests/`. Fast offline unit tests run by default; integration tests requiring broker credentials are excluded via `conftest.py`.

Key test files:
- `test_paper_trading.py` — Paper order manager + tracker
- `test_ic_strategy.py` — Iron Condor strike calculation, entry/exit
- `test_day_classifier.py` — Day classification logic
- `test_ic_logic.py` — IC-specific logic
- `test_operational_safety.py` — Safety checks
- `test_market_data.py` — MarketData adapter

## Configuration

All tunable parameters are in `trading_system/config/settings.py`. Key ones:

- `PAPER_TRADE_MODE` — Always True (live not implemented)
- `IC_LOT_SIZE` — Lots per entry (default 10)
- `IC_VIX_MAX` — Max VIX for entry (30.0)
- `IC_MIN_CREDIT` — Min per-lot credit to accept (18)
- `IC_STOP_LOSS_MULT` — Hard stop at 3x max profit
- `IC_HARVEST_PCT` — Close at 1% of max profit
- `IC_DTE_THRESHOLD` — Roll if DTE < 3
- `IC_SR_BUFFER` — Min 50-point distance from 20-day H/L
- `NIFTY_LOT_SIZE` / `BANKNIFTY_LOT_SIZE` — 65 / 30

Credentials go in `cred.yml` (git-ignored). See `cred.yml.template` for schema.

## Important data directories

- `data/` — Paper trades CSV, P&L snapshots, open positions JSON, signal logs
- `logs/` — Daily rotating logs (`ic_system_YYYYMMDD.log`)
- `symbols/` — Shoonya master files (NFO.csv, NSE.csv, BSE.csv)
- `market_data_YYYYMMDD/` — Per-day tick data (raw_data/ + processed_data/)
- `tools/` — Utility scripts (backtesting, data cleanup)

## Broker API

The system uses **Shoonya/Noren API** via `NorenRestApiPy`. The `api_helper.py` wrapper adds:
- Thread-safe quote rate limiting (10 calls/sec hard cap, configurable via env)
- Priority lanes (high for strategy, low for background polling)
- OAuth token caching and session validation with retry
- Quote-level auth fallback (OAuth header -> jKey)

## Key constraints to respect

- **No live trading** — `PAPER_TRADE_MODE` must stay True
- **Atomic IC entry** — All 4 legs must fill or entire entry is rolled back
- **Option LTP validation** — Quotes outside [0.05, 5000] are rejected
- **Hard stop confirmation** — Requires 2 consecutive tick breaches before halt
- **No weekend exposure** — Flat by Thursday 3:15 PM
- **Credential safety** — Never commit `cred.yml` or token artifacts

## Dependencies

NorenRestApiPy (broker SDK), pandas, numpy, scipy, Flask, PyYAML, requests, psutil, colorama, websocket-client, python-dateutil, pytz.

Python 3.13 (venv in `venv/`).
