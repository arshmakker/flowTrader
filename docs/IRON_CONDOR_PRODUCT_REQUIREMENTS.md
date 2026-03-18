# RegimeTrader — Iron Condor Only Product Requirements (PRD)

**Document Type:** Product Requirements Document (PRD)  
**Version:** 1.0  
**Last Updated:** March 2026  
**Owner:** RegimeTrader team  

---

## 1) Purpose

Build an **Iron Condor–only** trading product for NIFTY index options that:

- Runs **systematically** (rule-based entries/exits, no discretionary overrides required for normal operation).
- Prioritizes the north star: **trades should be profitable and exit with positive TSL (trailing stop loss)**.
- Preserves core operational standards already present in RegimeTrader: **paper-first**, **risk gates**, **full audit trail**, **hard close**, and **position persistence**.

---

## 2) Background & rationale

Recent paper trading results indicate:

- `CALL_BACKSPREAD` / convex book dominates losses in recent periods.
- `IRON_CONDOR_WEEKLY` has strong positive contribution but was not sufficient to offset convex drawdowns.

The product direction is to **remove non–iron condor strategies** from execution and operate a focused, benchmark-driven iron condor system.

---

## 3) In scope

- A single, production-grade strategy: **Iron Condor** (defined-risk short premium using a short call spread + short put spread).
- A complete lifecycle:
  - Market regime checks / trade enablement filters
  - Entry (strike selection, order construction, slippage assumptions)
  - Monitoring (PnL, Greeks/risk proxies, time)
  - Exits (profit, TSL, loss, time, regime invalidation, hard close)
  - Logging and evaluation metrics
  - Paper execution + go-live gating
- Dashboards showing condor-specific state and key risk metrics.

---

## 4) Out of scope (v1)

- Any non-condor strategy execution (strangles, futures scalps, backspreads, directional spreads).
- Multi-leg adjustment engines (rolling individual legs, converting to flies, dynamic hedging) beyond a simple, deterministic set of **whole-position** actions.
- Live broker execution (unless the existing project already supports it end-to-end).
- ML/AI-based parameter adaptation.

---

## 5) Users & operating model

### 5.1 Primary user

- A single operator running the system during Indian market hours.

### 5.2 Operating posture

- **Paper-first** is default. Go-live is allowed only when acceptance criteria are met.
- **Single active position at a time** (v1) unless explicitly changed in later versions.

---

## 6) Key product decisions (benchmarks / defaults)

These defaults are based on widely used retail/systematic benchmarks (e.g., “45 DTE entry, manage at 50% profit, close at 21 DTE”, 16–20 delta shorts, defined-risk spreads).

### 6.1 Expiry / DTE policy

The product must support at least one of these modes:

- **Monthly-style condor mode (benchmark-default)**:
  - **Entry DTE:** 30–45 DTE
  - **Time exit / management checkpoint:** close/roll decision at **21 DTE**
- **Weekly condor mode (existing system uses `IRON_CONDOR_WEEKLY`)**:
  - **Entry DTE:** 5–10 calendar days (or a configurable weekly window)
  - **Time exit:** close before **2–3 DTE**

v1 must ship with **one mode selected as default** via configuration, and the other mode can remain disabled.

### 6.2 Strike selection

The product must select strikes deterministically using one of:

- **Delta-based** selection:
  - short call: \( \Delta \approx +0.16 \) to \( +0.20 \)
  - short put: \( \Delta \approx -0.16 \) to \( -0.20 \)
- **Move/ATR-based** selection (backup if deltas unavailable in data feed):
  - short strikes placed at \( k \times ATR \) from spot, configurable \(k\)

### 6.3 Spread width and credit constraint

- Spread width must be defined and configurable (e.g., points or strike steps).
- **Minimum net credit** must be enforced:
  - credit ≥ **25–33%** of spread width (configurable threshold)
- If the credit constraint fails, the trade must be **skipped** with a logged reason.

### 6.4 Profit taking and trailing stop

To align with “exit with positive TSL”:

- **Profit target** (take-profit): close at **50% of max credit** (default).
- **TSL activation**: after reaching a profit threshold (default **30–40% of credit**).
- **TSL trail rule** (profit-based trailing):
  - Trail at max_profit minus a giveback buffer (default allow **30–40% giveback of max profit**).
- TSL must be computed on **net PnL (after costs)** in paper mode.

### 6.5 Loss and invalidation exits

Hard exits must exist and take precedence over TSL:

- **Max loss stop**: exit when loss reaches **2× credit** (default) OR a configurable % of theoretical max loss.
- **Regime invalidation**: close if preconditions break (vol shock / trend regime / liquidity) per filters below.
- **Hard close**: forced flat at configured end time.

---

## 7) Market / regime filters (trade enablement)

The system must gate entries using:

### 7.1 Volatility filter (VIX / IV proxy)

- A configurable “safe” volatility band where condors are allowed.
- Must support at minimum:
  - **Normal sizing** under a threshold (e.g., VIX < 25)
  - **Reduced sizing / more conservative deltas** above that threshold (e.g., 25–30)
  - **No new entries** above a “danger” threshold (e.g., VIX ≥ 30) unless explicitly overridden in config

### 7.2 Day type / trend filter

Condors are enabled only when the day classifier indicates a non-trending/range condition (or equivalent “neutral” status). If the existing classifier only yields RANGING/TRENDING_UP/TRENDING_DOWN, v1 must:

- Allow condor entries only when day type == **RANGING**
- Immediately block new condors in TRENDING regimes

### 7.3 Event filter

The system must provide a configuration mechanism to block entries on known high-risk event days (manual list or calendar).

---

## 8) Risk management requirements

### 8.1 Per-trade risk cap

- Must size positions based on defined max loss:
  - target max loss per position = **1–2% of equity** (configurable)
  - contracts/lots computed as:
    - \( \text{lots} = \left\lfloor \frac{\text{equity} \times \text{risk\_pct}}{\text{max\_loss\_per\_lot}} \right\rfloor \)
- Must refuse entry if computed lots == 0.

### 8.2 Portfolio / day risk gates

Even with a single-position v1, the system must enforce:

- **Daily loss limit** (existing RiskManager concept)
- **Daily target gate** (existing DailyTarget concept)
- No new entries if target hit or loss limit breached

### 8.3 Exposure monitoring (minimum set)

The system must track and display for the active condor:

- Net PnL (gross + costs + net)
- Max profit achieved so far (for TSL)
- Distance of spot to short strikes (buffer)
- Time to expiry and time-to-hard-close

If Greeks are available, include at minimum delta and gamma. If not, use proxy measures (distance-to-strikes, ATR).

---

## 9) Execution & order management (paper v1)

### 9.1 Order construction

- Condor entry must be represented consistently in logs and PnL:
  - short put
  - long put (wing)
  - short call
  - long call (wing)
- Paper fills must model slippage and costs per existing paper layer.

### 9.2 Fill reliability

If any leg cannot be “filled” in paper mode due to missing price data, the system must:

- Abort the trade entry, ensure no partial position state is persisted, and log an explicit reason.

---

## 10) Logging, auditability, and analytics

### 10.1 Trade log requirements

Each completed trade must log:

- Strategy name (Iron Condor)
- Entry/exit timestamps
- All four strikes and instruments
- Entry/exit prices (or per-leg prices if available)
- Gross PnL, costs, net PnL
- Exit reason (PT / TSL / MaxLoss / TimeExit / RegimeExit / HardClose)
- Context at entry: VIX regime, day type, key signals used for gating

### 10.2 Signal log requirements

Signal evaluation must record:

- Why entry was allowed/blocked
- Which constraint failed if blocked (e.g., credit too low, not ranging, VIX too high)

### 10.3 Condor-specific metrics

The go-live evaluator and dashboards must compute and show at minimum:

- Trade count
- Win rate
- Profit factor
- Avg win / avg loss
- Max drawdown (paper equity curve)
- **% exits with positive TSL** (primary KPI)
- Distribution of exit reasons

---

## 11) Dashboards (terminal + web)

Dashboards must show:

- Current mode: paper/live (paper v1)
- Current day type and VIX regime
- Condor position state:
  - strikes, lots, entry time
  - net PnL and max profit (for TSL)
  - active stop levels: PT, TSL, MaxLoss
  - time-to-expiry and time-to-hard-close
- Risk gates:
  - daily loss used / remaining
  - daily target status

---

## 12) Configuration requirements

All tunables must be centralized (existing `settings.py` concept), including:

- DTE mode selection and windows
- Strike selection method and parameters
- Spread width and min credit
- Profit target, TSL activation and trail parameters
- Max loss and time exit thresholds
- Volatility and day-type filters
- Risk sizing defaults (risk per trade, daily loss limit)

Configuration must support:

- A “safe defaults” profile
- A “research” profile with easy parameter sweeps (optional v1, required v2)

---

## 13) Data requirements

Minimum required data to operate:

- Spot (index) LTP
- Option chain with strikes and prices for the chosen expiry
- VIX (or proxy) value for regime gating
- Time and trading calendar awareness (trade window / expiry dates)

If delta-based strikes are used, the product must either:

- Provide delta values from the data provider, or
- Compute deltas from IV and greeks inputs; otherwise fallback to ATR-based strikes.

---

## 14) Non-functional requirements

- **Reliability**: restart-safe (position persistence), no duplicate trade IDs, graceful shutdown.
- **Observability**: structured logs with actionable reasons for decisions.
- **Safety**: never enters new positions outside trade window; always attempts to flatten at hard close.
- **Determinism**: given the same market data stream and config, decisions are reproducible.
- **Performance**: monitoring loop must be fast enough for intraday operation (no heavy blocking calls).

---

## 15) Acceptance criteria (v1)

### Functional

- System runs in paper mode end-to-end, creates iron condor trades, and exits via PT/TSL/MaxLoss/Time/HardClose.
- No other strategies can be entered (hard blocked).
- All four strikes are logged for every trade; exit reasons are always populated.
- TSL is implemented and produces a measurable KPI: **% exits with positive TSL**.

### Risk & safety

- Per-trade sizing never exceeds configured max loss %.
- No entries when daily loss limit hit or after daily target hit.
- Forced flat at hard close.

### Analytics

- Dashboard shows the required condor fields and risk gates.
- Go-live evaluator outputs the minimum metric set, including **positive-TSL exit rate**.

---

## 16) Open questions (to resolve before build)

- **Default mode**: monthly-style (30–45 DTE) vs weekly-style (5–10 DTE). Which do you want as v1 default?
- **Delta availability**: do we have reliable deltas from the data feed? If not, confirm ATR-based strike selection.
- **Single vs multiple concurrent condors**: keep v1 single-position, or allow staggered positions by expiry?

