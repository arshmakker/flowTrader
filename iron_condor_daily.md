# Iron Condor Trading System — Product Requirements Document

**Version:** 1.0  
**Date:** March 2026  
**Instruments:** Nifty, BankNifty  
**Status:** Draft

---

## 1. Overview

This document defines the complete rule set for an automated iron condor trading system designed for Indian equity index options. The system trades Nifty and BankNifty simultaneously on a weekly options cycle, Monday through Thursday, with zero open positions over the weekend. All parameters — strike selection, spread width, expiry choice, profit harvest, adjustments, and stop-loss — are determined by a defined decision framework requiring no manual input beyond capital allocation.

---

## 2. Objectives

**Primary goal: trade every eligible session to maximise net P&L after all taxes and brokerage costs.**

All parameters — credit floors, harvest thresholds, VIX stability window, OTM distances — are calibrated so a winning cycle clears the full F&O cost stack (STT, exchange, SEBI, stamp, GST, brokerage). A cycle that wins gross but loses net is not a win.

- Trade every eligible session — non-participation must be justified by a regime gate, not by parameter miscalibration
- Harvest theta decay through systematic profit-taking and immediate re-entry
- Adapt spread width and strike placement dynamically to prevailing VIX and S/R levels
- Cap maximum drawdown per trade at 3× the max profit of the spread
- Be flat before any weekend or holiday gap; operator targets Thursday 3:15 PM as personal weekly cut-off

---

## 3. Instruments & Schedule

| Parameter | Rule |
|---|---|
| Instruments | Nifty + BankNifty (both traded simultaneously) |
| Trading days | Normal weekday sessions (Mon–Fri, non-holiday) |
| Pre-weekend exit | All positions closed by 15:10 on the last trading day before any weekend or holiday |
| Weekend | Flat — no exceptions |
| Lot size | 10 lots per instrument |

---

## 4. Module 1 — Expiry Selection

The system selects the expiry at market open each day based on days-to-expiry (DTE) of the current weekly contract.

| Condition | Action |
|---|---|
| Current weekly expiry has ≥ 3 DTE | Use current week's expiry |
| Current weekly expiry has < 3 DTE | Roll to next week's expiry |

**Rationale:** Entering a condor with fewer than 3 DTE exposes the position to excessive gamma risk where small intraday moves can cause disproportionate P&L swings. Rolling to the next week preserves theta decay while managing gamma exposure.

---

## 5. Module 2 — Spread Width & Strike Selection (Automated)

At market open, the system reads India VIX and identifies key support/resistance levels using the 20-day high and 20-day low as automated S/R proxies.

### 5.1 VIX-Based Spread Rules

| India VIX | Spread Width | Strike Distance OTM | S/R Buffer | Notes |
|---|---|---|---|---|
| Below 14 | 50 points | 150 points | 50 points minimum | NIFTY skips entirely — credit below ₹18 fee floor |
| 14–20 | 100 points | 200 points | 50 points minimum | — |
| Above 20 | 150 points | 300 points | 50 points minimum | — |

### 5.2 S/R Constraint

Neither the short call strike nor the short put strike may be placed within 50 points of the 20-day high or 20-day low. If the VIX-derived strike falls within this buffer, the strike is moved further OTM until the buffer is respected, even if this means slightly reduced premium.

### 5.3 Skip Rule (NIFTY)

NIFTY entries are skipped entirely when VIX < 14. On quiet-market days, NIFTY IC credit is structurally ₹3–9 per lot — below the ₹18 fee break-even regardless of strike selection. Skipping saves hundreds of wasted quote-API calls. BANKNIFTY is unaffected and continues to trade. On high-VIX days (above 20), risk is managed through wider spreads and further OTM placement for both instruments.

---

## 6. Module 3 — Profit Harvest & Re-Entry

### 6.1 Trigger

The system monitors each position's P&L in real time. When unrealised profit reaches the instrument's harvest threshold, the entire condor is closed immediately.

| Instrument | Harvest threshold |
|---|---|
| NIFTY | 2% of max profit |
| BANKNIFTY | 13% of max profit |

> **Example:** NIFTY IC with max profit ₹5,000 → harvest fires at ₹100. BANKNIFTY IC with max profit ₹3,000 → harvest fires at ₹390.

### 6.2 Re-Entry

Immediately after closing, the system re-enters a fresh iron condor on the same instrument using the live VIX and live S/R levels at that moment — not the levels read at market open. This means a re-entry later in the day may have different strikes and spread width than the original entry.

### 6.3 Frequency

This cycle (monitor → 1% hit → close → re-enter) repeats as many times as it is triggered within the trading day. There is no cap on the number of re-entries per day.

---

## 7. Module 4 — Adjustment Logic

### 7.1 Condition for Adjustment

Adjustment is triggered when a short strike is breached by the underlying **and** the overall position is still showing a net profit. Adjustment is **not** made when the position is at a net loss.

### 7.2 Adjustment Mechanics

| Adjustment | Action |
|---|---|
| Tested side (breached) | Roll 1–2 strikes further OTM |
| Untested side (safe) | Roll 1–2 strikes closer in to collect additional premium |

The premium collected from the untested side roll funds the cost of rolling the tested side, keeping the adjustment as close to cost-neutral as possible.

### 7.3 When Adjustment is Not Made

If the breach has already driven the position to a net loss, no adjustment is made. The position is held as-is. The only active rule at this point is the hard stop-loss defined in Module 5.

---

## 8. Module 5 — Stop-Loss & Breach Protocol

### 8.1 Hard Stop-Loss

| Parameter | Value |
|---|---|
| Stop-loss level | 3× the max profit of the spread |
| Measurement | Combined P&L of both instruments |

> **Example:** If the combined max profit across Nifty + BankNifty condors is ₹10,000, the hard stop fires at ₹30,000 loss.

### 8.2 Default Action on Stop Trigger

On stop-loss trigger: **exit all positions immediately and remain flat for the rest of the trading day.**

### 8.3 Exception — Single-Sided Re-Entry

A conditional re-entry is permitted after a stop-loss trigger if **both** of the following are true:

1. The stop-loss triggered **before 1:00 PM**
2. India VIX has **stabilised or is falling** at the time of the stop trigger

If both conditions are met, the system may re-enter as a **single-sided credit spread only** — on the side away from the direction of the move.

| Market direction at stop | Re-entry allowed |
|---|---|
| Market broke upward | Bear call spread only (no put side) |
| Market broke downward | Bull put spread only (no call side) |

**This single-sided position also carries the 3× max profit stop-loss rule.** If it triggers, the position is closed and no further re-entry is permitted that day.

### 8.4 Prohibited Actions on Stop Trigger

- Re-entering a full iron condor same day after a stop
- Re-entering any position after 1:00 PM following a stop
- Re-entering when VIX is rising or elevated at time of stop

---

## 9. Module 6 — End-of-Week Exit

| Parameter | Rule |
|---|---|
| Exit trigger | Thursday 3:15 PM (15 minutes before market close) |
| Action | Close all open positions, both instruments |
| P&L condition | Irrelevant — exit is unconditional |
| Friday | No trades, no open positions |

---

## 10. Decision Hierarchy

When multiple rules could apply simultaneously, the following priority order governs:

1. **EOW exit** (Thursday 3:15 PM) — overrides all other rules
2. **Hard stop-loss** (3× max profit) — overrides profit harvest and adjustments
3. **1% profit harvest + re-entry** — overrides hold
4. **Adjustment** (if breached and in profit) — overrides hold
5. **Hold** — default state

---

## 11. Risk Parameters Summary

| Parameter | Value |
|---|---|
| Max loss per trade | 3× max profit of the spread |
| Profit target per cycle | 1% of max profit of the spread |
| Lot size | 2–3 lots per instrument |
| VIX regime adjustment | Spread width + strike distance scaled to VIX |
| Re-entry after stop | Single-sided only, before 1 PM, VIX stable/falling |
| Weekend exposure | Zero |

---

## 12. Open Questions & Future Enhancements

| # | Question | Status |
|---|---|---|
| 1 | Backtesting across 2020–2024 data to validate stop-loss multiple | Pending |
| 2 | Optimal lot sizing relative to capital (e.g., per ₹1L deployed) | Pending — current: 10 lots, ₹10L capital |
| 3 | Whether to trade both instruments when they diverge significantly in VIX behaviour | Pending |
| 4 | Broker API integration | Done — Shoonya/Noren via `NorenRestApiPy`; live order manager implemented |
| 5 | Trade log and daily P&L tracking | Done — `paper_trades.csv`, `pnl_snapshot.json`, nightly reconciliation |

---

*Document compiled from system design session. All rules are subject to backtesting validation before live deployment.*
