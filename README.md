# RegimeTrader

A Python-based automated Iron Condor trading system for NIFTY and BANKNIFTY weekly options on the Shoonya (Noren) broker API. Classifies each trading day by regime (RANGING vs TRENDING), applies VIX-based filters, and executes 4-leg Iron Condor strategies in paper-trading mode with full cost modelling.

Paper trading is the default. Live order infrastructure is implemented but requires operator go-live sign-off (see `docs/GO_LIVE_CHECKLIST.md`).

## Strategy in one sentence

Enter an Iron Condor when the day is RANGING and VIX is below 30 and stable; harvest at 2% of max profit (NIFTY) or 13% (BANKNIFTY); re-enter immediately; stop at 3× combined max loss; be flat before any weekend or holiday.

## Key parameters

| Parameter | Value |
|---|---|
| Instruments | NIFTY + BANKNIFTY (simultaneously) |
| Lot size | 10 lots per instrument |
| Entry mode | Hedge-first (wings as MKT, shorts as LMT) |
| Harvest | NIFTY 2% of max profit · BANKNIFTY 13% |
| Stop-loss | 3× combined max profit |
| VIX ceiling | < 30 |
| VIX stability | Within 1.5-pt band for last 8 min |
| NIFTY VIX floor | VIX ≥ 14 (quiet days skip NIFTY) |
| Min credit | NIFTY ₹18/lot · BANKNIFTY ₹25/lot |
| S/R buffer | 50 pts from 20-day high/low |
| S/R OTM cap | NIFTY 400 pts · BANKNIFTY 1000 pts from spot |
| Hard close (expiry) | 15:00 on expiry day (sourced from NFO.csv) |
| Hard close (EOD) | 15:10 before any weekend or holiday |
| Carry | Allowed between normal weekday sessions |

## Architecture

```
main.py (orchestrator — 60s control loop)
  ├── api_helper.py           — Shoonya API wrapper (OAuth, rate limiting)
  ├── symbol_manager.py       — NFO/NSE/BSE symbol master + token resolution
  ├── data_collector.py       — Background tick collection (5s)
  ├── strategy_runner.py      — Market-hours, expiry, tradability helpers
  │
  └── trading_system/
      ├── config/settings.py          — All tunable parameters
      ├── core/
      │   ├── iron_condor.py          — 4-leg IC (entry, monitor, harvest, exit)
      │   ├── day_classifier.py       — RANGING / TRENDING (locked at 10:30)
      │   ├── regime_filter.py        — VIX gate + stability + NIFTY VIX floor
      │   ├── signal_engine.py        — Session VWAP for day classification
      │   ├── risk_manager.py         — 3× stop + daily loss cap + recovery gate
      │   ├── expiry_manager.py       — 3 DTE rolling rule (reads NFO.csv)
      │   ├── sr_manager.py           — 20-day high/low S/R with 50-pt buffer
      │   ├── trade_logger.py         — CSV trade log
      │   ├── position_persistence.py — JSON state save/restore
      │   ├── fees.py                 — Full F&O cost stack (STT, stamp, SEBI, GST)
      │   └── margin.py               — Pre-entry margin estimate
      ├── existing/
      │   └── market_data.py          — LTP cache, OHLCV, option validation
      ├── paper/
      │   ├── paper_order_manager.py  — Simulated fills with slippage + full costs
      │   ├── paper_position_tracker.py
      │   ├── paper_pnl_engine.py     — Cumulative + daily P&L
      │   └── go_live_evaluator.py    — Readiness thresholds for live migration
      ├── live/
      │   └── live_order_manager.py   — Live order plumbing (polling to terminal status)
      ├── ops/
      │   ├── alerts.py               — ntfy alert channel
      │   ├── reconcile.py            — Engine vs broker trade reconciliation
      │   ├── startup_reconcile.py    — Position divergence check at boot
      │   └── broker_trade_fetcher.py — Shoonya trade-book normaliser
      ├── auth/
      │   └── shoonya_selenium_auth.py
      └── dashboard/
          ├── web_dashboard.py        — Flask (port 5050)
          └── terminal_dashboard.py
```

## Common commands

```bash
# Run the system
python main.py

# Run tests
pytest

# Nightly trade reconciliation (after market close)
python tools/reconcile_trades.py --date YYYY-MM-DD

# Heartbeat check (run by launchd — see deploy/)
python tools/heartbeat_check.py
```

## Setup

```bash
git clone <repo>
cd regimetrader
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp cred.yml.template cred.yml
# Fill in cred.yml — see template for OAuth fields
python main.py
```

Logs go to `logs/ic_system_YYYYMMDD.log`. State persists in `data/open_positions.json`.

## Paper vs live

- **Paper (default):** `PAPER_TRADE_MODE = True` in `settings.py`. No real orders; all fills simulated with full cost modelling.
- **Live:** requires operator go-live sign-off per `docs/GO_LIVE_CHECKLIST.md` — OAuth recheck, `data/LIVE_ACK`, `SHAKEDOWN_MODE = True`. Flip `PAPER_TRADE_MODE = False` only after checklist is complete.

## Operator safety controls

- **Kill switch:** `touch data/HALT` — force-exits all positions and shuts down cleanly on next cycle.
- **PID guard:** refuses to start if another instance is running.
- **Daily loss cap:** ₹50k steady-state; ₹10k during shakedown proving period.
- **Heartbeat:** `deploy/launchd/com.regimetrader.heartbeat.plist` — external watchdog fires ntfy alert if snapshot goes stale during market hours.
- **Alerts:** ntfy channel fires on stop-loss hit, daily cap breach, rollback escalation, and kill-switch activation.

## Key directories

| Path | Contents |
|---|---|
| `data/` | `paper_trades.csv`, `pnl_snapshot.json`, `open_positions.json` |
| `logs/` | `ic_system_YYYYMMDD.log` |
| `symbols/` | Shoonya master files (`NFO.csv`, `NSE.csv`, `BSE.csv`) |
| `tools/` | Utility scripts (reconciliation, heartbeat, monitoring) |
| `deploy/` | launchd plist for heartbeat watchdog |
| `docs/` | Strategy logic, axioms, go-live checklist, architecture |
