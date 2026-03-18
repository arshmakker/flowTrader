# RegimeTrader — Business Overview

**Version:** 1.0  
**Document Type:** Business & Product Overview  
**Last Updated:** March 2026  

---

## Executive Summary

RegimeTrader is a multi-strategy derivatives trading system for NIFTY index options and futures. It automatically classifies market conditions, selects appropriate strategies based on volatility (VIX) and day type, and executes trades in a controlled, paper-first environment. The system is designed for systematic trading with built-in risk controls, position persistence, and transparent auditability.

---

## Problem Statement

- **Manual trading** is time-intensive and subject to emotional bias.
- **Single-strategy systems** underperform when market conditions change.
- **Inconsistent risk** leads to outsized losses in volatile regimes.
- **Lack of traceability** makes it difficult to attribute performance to specific regimes or strategies.

---

## Solution Overview

RegimeTrader addresses these issues by:

1. **Automated regime and day classification** — Determines market type (ranging vs trending) and VIX regime (calm, normal, elevated, danger) once per session.
2. **Strategy routing** — Selects and executes only strategies suited to the current regime, avoiding mismatch (e.g., no short premium in high-volatility trending days).
3. **Paper-first operation** — Validates logic and performance in simulation before any live capital.
4. **Built-in risk controls** — Daily loss limits, target-based stop, and mandatory hard close at session end.
5. **Full audit trail** — Logs and trade records for every decision and execution.

---

## Strategy Suite

| Strategy | Name | When Used | Risk Profile |
|----------|------|-----------|--------------|
| **A** | Short Strangle | Ranging days, low VIX (CALM/NORMAL) | Premium selling, defined risk |
| **B** | Directional Spread | Trending days, low VIX | Directional, capped loss |
| **C** | Futures Scalp | Trending days, very low VIX (CALM) | Short-horizon, intraday |
| **D** | Wide Iron Condor | Ranging days, elevated VIX | Premium selling, wider wings |
| **E** | Deep ITM Directional | Trending days, elevated VIX | Directional with defined risk |

Strategies are mutually exclusive; only one can hold a position at a time. Routing is determined by day classification (RANGING vs TRENDING_UP/DOWN) and VIX regime (CALM, NORMAL, ELEVATED, DANGER).

---

## Operational Model

### Session Structure

- **Trade window:** 10:00–14:15 IST  
- **Classification time:** 10:30 IST (single classification per day)  
- **Hard close:** All positions forced flat at 14:15 IST  

### Risk & Governance

- **Daily loss limit** — Stops new trades when cumulative realised loss hits the limit for the day.  
- **Daily target** — When profit target is reached, no new entries; existing positions monitored until exit.  
- **Position persistence** — Open positions and P&L state saved to disk; restored on restart for continuity.  
- **Graceful shutdown** — Hard close triggers planned exit; data collection stops; state cleared when flat.

### Paper vs Live

- **Paper (default):** Simulated orders, slippage, and brokerage; no real capital at risk.  
- **Live:** Real order execution (not yet implemented; system exits with a clear message if live mode is selected).

---

## Key Capabilities

| Capability | Description |
|------------|-------------|
| **Regime detection** | VIX-based classification (CALM &lt;13, NORMAL &lt;17, ELEVATED &lt;20, DANGER ≥20) |
| **Day classification** | RANGING vs TRENDING_UP/DOWN using open, spot, and VWAP |
| **Multi-strategy routing** | Automatic selection of primary and secondary strategies per regime |
| **Position management** | Single active strategy; mutual exclusion; monitor and force-exit logic |
| **P&L tracking** | Realised, unrealised, daily target, drawdown, win rate, profit factor |
| **Data collection** | Real-time tick data for NIFTY, BANKNIFTY, FINNIFTY index derivatives |
| **Dashboards** | Web (port 5050) and terminal dashboards for monitoring |

---

## Data & Auditability

- **Trade log** — CSV of all trades with timestamps, strategy, symbols, P&L, exit reason.  
- **Session logs** — Daily rotating logs: classification, STATE (idle/active reason), risk/target gates, hard close, end-of-day summary.  
- **Persistence** — Open positions and session state stored in `data/open_positions.json`; cleared when session ends flat.  
- **Go-live evaluator** — Paper performance metrics (win rate, profit factor, trade count) to inform live readiness.

---

## Assumptions & Limitations

- **NIFTY derivatives focus** — Index futures and options; equity single-name strategies are out of scope.  
- **Single session** — One classification per day; no mid-day reclassification.  
- **Paper-first** — Live order path is not implemented; system is for analysis and paper validation.  
- **Shoonya API** — Designed for Shoonya (Noren) broker; credentials and 2FA required.

---

## Intended Use

RegimeTrader is intended for:

- **Systematic traders** who want automated, regime-aware strategy selection.  
- **Quantitative research** and backtesting of strategy behaviour across regimes.  
- **Risk-conscious operators** who require daily limits, target gates, and full audit trails.  
- **Paper validation** before deploying capital in live markets.

---

## Document Control

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 1.0 | March 2026 | — | Initial business overview |

---

*This document describes the business and operational model of RegimeTrader. For technical implementation details, see [README.md](../README.md).*
