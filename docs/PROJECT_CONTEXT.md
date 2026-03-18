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

## Key documents

- `docs/BUSINESS_OVERVIEW.md`: business & operating model overview.
- `docs/IRON_CONDOR_PRODUCT_REQUIREMENTS.md`: product requirements for Iron Condor–only system (benchmarked defaults + TSL KPI).

