# RegimeTrader

A Python-based multi-strategy trading system for NIFTY derivatives using the Shoonya (Noren) API. It classifies the market day (ranging vs trending), uses VIX-based regime filtering, and routes to one of five strategies. Paper trading is the default; positions and P&L are persisted across restarts.

## Trading Strategies

Strategies are selected by **day type** (from open/VWAP at classification time) and **VIX regime** (CALM / NORMAL / ELEVATED / DANGER). Routing is in `trading_system/core/regime_filter.py`.

| Day Type     | VIX Regime | Primary Strategy | Description |
|-------------|------------|------------------|-------------|
| RANGING     | CALM       | **A**            | Short Strangle |
| TRENDING_UP/DOWN | CALM  | **B**            | Directional Spread |
| RANGING     | NORMAL     | **A**            | Short Strangle |
| TRENDING_UP/DOWN | NORMAL | **B**            | Directional Spread |
| RANGING     | ELEVATED/DANGER | **D** | Wide Iron Condor |
| TRENDING_UP/DOWN | ELEVATED/DANGER | **E** | Deep ITM Directional (fallback to D) |

- **A** – Short Strangle (OTM CE/PE, target/stop by %).
- **B** – Directional Spread (debit spread in trend direction).
- **C** – Futures Scalp (single-leg NIFTY future; CALM only, secondary).
- **D** – Wide Iron Condor (high VIX, ranging).
- **E** – Deep ITM Directional (high VIX, trending; else D if conditions allow).

## Key Features

- **Day classification** – Once at 10:30 IST: RANGING vs TRENDING_UP/TRENDING_DOWN using open, spot, VWAP; confidence HIGH/LOW.
- **VIX regime** – CALM (&lt;13), NORMAL (&lt;17), ELEVATED (&lt;20), DANGER (≥20). Drives routing, size multiplier, and daily loss limit.
- **Paper trading by default** – Simulated orders, slippage, brokerage; P&L and positions in `data/` and `data/open_positions.json`.
- **Position persistence** – Open positions and session state saved; restored on restart until flat or hard close.
- **Trade window** – 10:00–14:15 IST; classification at 10:30; hard close of all positions at 14:15.
- **Risk and target** – Daily loss limit by regime; daily target gate; mutual exclusion between strategies.
- **Market data** – DataCollector (tick/stream), SymbolManager (NFO/NSE), MarketData (OHLCV, LTP, open) for signals and options.

## System Architecture

### Core components

1. **`main.py`** – Entry point. Loads creds, inits API, SymbolManager, DataCollector, MarketData, strategies, RiskManager, DailyTarget, TradeLogger, position persistence; runs the main loop (classification, risk/target gates, monitor/entry, hard close at TRADE_END).
2. **Day classification** – `trading_system/core/day_classifier.py`: classifies day type and confidence from MarketData and SignalEngine (open, spot, VWAP).
3. **Regime and routing** – `trading_system/core/regime_filter.py`: VIX regime, `get_routing(day_type)` for primary/secondary/forbidden strategies and daily target.
4. **Strategies** – `trading_system/core/strategy_*.py`: A (Short Strangle), B (Directional Spread), C (Futures Scalp), D (Wide Iron Condor), E (Deep ITM). Each has `enter`, `monitor`, `force_exit`, `is_active`.
5. **Signals** – `trading_system/core/signal_engine.py`: RSI, VWAP, consensus, max pain; used by strategies and classifier.
6. **Risk and target** – `trading_system/core/risk_manager.py`, `daily_target.py`: daily loss limit, target hit gate.
7. **Paper layer** – `trading_system/paper/`: PaperOrderManager (fills, slippage, tick 0.05), PaperPositionTracker, PaperPnLEngine; GoLiveEvaluator for paper stats.
8. **Persistence** – `trading_system/core/position_persistence.py`: save/load state to `data/open_positions.json`; cleared when flat after hard close.
9. **Market data** – `trading_system/existing/market_data.py`: OHLCV, LTP, open price, reliability flag for classifier.
10. **Data collection** – `data_collector.py` (root): DataCollector thread; symbols from `symbol_manager.py` (NSE index, NFO futures/options). Starts when market opens; stopped at hard close.
11. **Dashboards** – `trading_system/dashboard/web_dashboard.py` (Flask, port 5050), `terminal_dashboard.py` (rich); started as daemon threads from `main.py`.

### Configuration

All tunables are in **`trading_system/config/settings.py`**: trade window (`TRADE_START`, `CLASSIFY_TIME`, `TRADE_END`), VIX thresholds, strategy params, paths (`DATA_DIR`, `LOG_DIR`), `PAPER_TRADE_MODE` (True = paper only).

## System Requirements

- Python 3.8+
- Dependencies (see `requirements.txt`):

  ```bash
  NorenRestApiPy>=0.0.22
  websocket-client>=1.0.0
  pandas>=1.3.0
  numpy>=1.21.0
  scipy>=1.7.0
  python-dateutil>=2.8.2
  pytz>=2021.1
  requests>=2.26.0
  PyYAML>=5.4.1
  psutil>=5.8.0
  colorama>=0.4.4
  flask>=2.0.0
  ```

## Installation & Setup

### Virtual environment (recommended)

```bash
git clone <repository-url>
cd regimetrader
python3 -m venv venv
source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### Credentials

1. Copy and edit credentials:
   ```bash
   cp cred.yml.template cred.yml
   ```
2. Edit `cred.yml` with your Shoonya details: `user`, `pwd`, `vc`, `apikey`, `imei`.
3. 2FA: set env `TWOFA` to your one-time code for non-interactive runs, or enter when prompted.

### Paper vs live

- **Paper (default)** – `PAPER_TRADE_MODE = True` in `trading_system/config/settings.py`. No real orders; all fills and P&L are simulated; state in `data/`.
- **Live** – Set `PAPER_TRADE_MODE = False` in settings. Real orders are not implemented in this repo; the app will exit with a message.

Run the app:

```bash
python main.py
```

Logs go to `logs/trading_system_YYYYMMDD.log`. You’ll see STATE lines (e.g. WAITING_FOR_CLASSIFICATION, NO_ACTIVE_STRATEGIES, ACTIVE_STRATEGIES:D), day classification, and at 14:15 IST: `HARD CLOSE: trade window over at 14:15 IST — forcing all active strategies flat`, then END OF DAY summary and DataCollector stop.

## Data Collection

- **Symbols** – NIFTY spot (index), NFO index futures (NIFTY, BANKNIFTY, FINNIFTY), and index options; see `SymbolManager.get_data_collection_symbols()`.
- **Storage** – Date-specific dirs (e.g. `market_data_YYYYMMDD/`), with `raw_data/` (and optionally `processed_data/`) for tick/derived data.
- **Lifecycle** – Data collection starts when market opens and stops at hard close (or shutdown).

## Directory Structure

```
regimetrader/
├── main.py                    # Entry point, main loop
├── api_helper.py              # Shoonya/Noren API wrapper
├── symbol_manager.py           # Symbols, NFO/NSE, data collection symbol list
├── data_collector.py           # Real-time data collection thread
├── strategy_runner.py          # Helpers: option chain, expiry, market hours
├── technical_indicators.py     # Indicators used by signals/strategies
├── cred.yml                    # Credentials (from cred.yml.template)
├── requirements.txt
├── trading_system/
│   ├── config/
│   │   └── settings.py         # All config (trade window, VIX, paths, etc.)
│   ├── core/
│   │   ├── strategy_a.py .. strategy_e.py
│   │   ├── day_classifier.py
│   │   ├── regime_filter.py
│   │   ├── signal_engine.py
│   │   ├── risk_manager.py
│   │   ├── daily_target.py
│   │   ├── position_persistence.py
│   │   └── trade_logger.py
│   ├── paper/
│   │   ├── paper_order_manager.py
│   │   ├── paper_position_tracker.py
│   │   ├── paper_pnl_engine.py
│   │   └── go_live_evaluator.py
│   ├── existing/
│   │   └── market_data.py
│   └── dashboard/
│       ├── web_dashboard.py   # Flask, port 5050
│       └── terminal_dashboard.py
├── data/                       # Persisted state, P&L snapshots, trade CSV
│   └── open_positions.json    # Position/session state (when not flat)
├── logs/
│   └── trading_system_YYYYMMDD.log
├── market_data_YYYYMMDD/       # Per-day market data dirs
└── tests/
```

## Logging

- **Main log** – `logs/trading_system_YYYYMMDD.log`: startup, day classification, STATE (why idle or which strategy active), monitoring, P&L CHECK, HARD CLOSE, END OF DAY, DataCollector start/stop.
- **Trade log** – CSV and signals written by `TradeLogger` (see `trading_system/core/trade_logger.py`); consumed by web dashboard and GoLiveEvaluator.

## Web Dashboard

The Flask dashboard runs on **port 5050** as a daemon thread started from `main.py` (no separate `python web_dashboard.py`). When the app is running, open:

`http://localhost:5050`  
(or `http://<your-machine-ip>:5050` from another device)

See `WEB_DASHBOARD_README.md` if present for more detail.

## Note

This system is designed for market analysis and paper trading. Use paper mode to validate behaviour. Real order execution is not implemented; do not set `PAPER_TRADE_MODE = False` for live trading without implementing and testing the live order path. Always validate data and logic before relying on results.
