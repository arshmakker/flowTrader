# Risk Management Framework

Grounded in `docs/axioms.md`. Catalogs the hard-coded safety nets, position-sizing rules, and the known gaps in risk coverage. See `TECHNICAL_REFERENCE.md` §4 for module-level detail.

## Axiom anchors

- **Axiom 3** — safety overrides continuity; uncertain state halts new entries until trustworthy.
- **Axiom 4** — rollback failure triggers Axiom 3.
- **Axiom 2** — carry-over and expiry-day close rules define when exposure must end.

## Layered safety nets

### 1. Entry-time gates (block before placing any leg)

| Gate | Condition | Setting | Source |
|---|---|---|---|
| Trading window | `TRADE_START ≤ now < TRADE_END` | 10:00 – 15:10 | `main.py` |
| VIX ceiling | `vix < IC_VIX_MAX` | 30.0 | `regime_filter.py:90-92` |
| VIX stability | 8-min range ≤ 1.5, ≥ 5 samples | `IC_VIX_STABLE_MINS = 8`, `IC_VIX_STABLE_BAND = 1.5` | `regime_filter.py` |
| Day type | `day_type == RANGING` | — | `regime_filter.py` |
| NIFTY VIX floor | NIFTY: `vix ≥ IC_NIFTY_MIN_VIX = 14` | — | `regime_filter.get_regime_gate` |
| Risk halted | `risk_manager.halted` is `False` | — | `main.py` |
| Min credit | `net_credit ≥ IC_MIN_CREDIT_BY_INSTRUMENT` | NIFTY ₹18, BANKNIFTY ₹25 | `iron_condor.py` |
| Margin | available margin ≥ required × 1.2 buffer | `IC_MARGIN_BUFFER_MULT = 1.2` | `iron_condor._pre_entry_margin_ok` |
| Freeze qty | qty ≤ `FREEZE_QTY_{INSTRUMENT}` | NIFTY 1800, BANKNIFTY 900 | `iron_condor.enter` |
| Valid leg LTPs | all 4 legs > 0 | — | `iron_condor.py:176-178` |
| DTE | expiry ≥ 3 DTE, else rolled | `IC_DTE_THRESHOLD = 3` | `expiry_manager.py` |
| S/R buffer | shorts ≥ 50 points from 20-day H/L | `IC_SR_BUFFER = 50` | `sr_manager.apply_buffer` |
| Option-price sanity | `0.05 ≤ ltp ≤ 5000` at entry | `PAPER_OPTION_LTP_MIN/MAX` | `paper_order_manager.py:103-124`, `market_data.get_ltp` |

### 2. Position-level (continuous during monitoring)

| Net | Trigger | Setting | Source |
|---|---|---|---|
| Profit harvest | `total_pnl ≥ harvest_pct × max_profit` | `IC_HARVEST_PCT_BY_INSTRUMENT`: NIFTY 2%, BANKNIFTY 13% | `iron_condor.monitor` |
| Strike-breach adjustment (profitable only) | `spot ≥ sc_strike` or `spot ≤ sp_strike`, with `total_pnl > 0` | — | `iron_condor.py:278-286` |
| Invalid-quote skip | any leg LTP ≤ 0 → monitor returns None | — | `iron_condor.py:257-258` |

### 3. Portfolio-level (combined hard stop)

- **Rule:** sum unrealized across all active ICs. If `total_unrealized ≤ −total_max_profit × IC_STOP_LOSS_MULT`, increment `_stop_breach_streak`. On `streak ≥ IC_HARD_STOP_CONFIRM_TICKS`, halt and flatten.
- **Settings:**
  - `IC_STOP_LOSS_MULT = 3.0` — stop at 3× combined max profit.
  - `IC_HARD_STOP_CONFIRM_TICKS = 2` — require 2 consecutive valid breaches.
- **Invalid-quote defence:** if any active strategy has any non-positive leg LTP, the stop check is skipped for that tick and the streak is reset. Prevents spurious halts from bad data (Axiom 3).
- **On trigger:** `risk.halted = True`, `stop_hit_at = now`, main loop force-exits all active strategies.
- **Source:** `risk_manager.check_combined_stop_loss` (`risk_manager.py:28-86`).

### 4. Atomic entry + rollback (Axiom 4)

- Default: hedge-first (`IC_ENTRY_MODE = "hedge_first"`) — wings placed as MKT first, shorts as LMT second; worst-case failure is a bounded long-strangle position, not a naked short.
- If any leg returns non-`COMPLETE`, entry aborts and `_rollback_partial_entry` sends reverse orders for already-filled legs.
- `IC_Position` is created **only** after all 4 legs confirm.
- Rollback failure: stuck legs are recorded; `_drain_rollback_failures` → `RiskManager.escalate_rollback_failure` halts entries and fires a LIVE-23 alert.

### 5. End-of-day safety (Axiom 2 carry rules)

- At `TRADE_END_EXPIRY = 15:00`: positions whose `expiry_date` matches today are force-closed.
- At `TRADE_END = 15:10`: if next calendar day is weekend or in `TRADING_HOLIDAYS_IST`, force-flatten all remaining active ICs.
- If next calendar day is a normal trading day: carry overnight.

### 6. Daily loss cap + kill switch

- **Daily loss cap:** `RiskManager.check_daily_loss_cap()` — halts and flattens when `realised + unrealised < −DAILY_MAX_LOSS`. Cap is `DAILY_MAX_LOSS_SHAKEDOWN = ₹10k` during `SHAKEDOWN_MODE`; else `DAILY_MAX_LOSS = ₹50k`.
- **Kill switch:** presence of `data/HALT` file → force-exits all positions and shuts down cleanly.
- **PID guard:** startup refuses if another instance is alive (`data/regimetrader.pid`).
- **Operator alerts:** ntfy channel fires on stop-loss hit, daily cap breach, rollback escalation, kill-switch activation.

## Position sizing

Fixed, not adaptive:

| Parameter | Value | Source |
|---|---:|---|
| `IC_LOT_SIZE` | 10 lots per entry per instrument | `settings.py:37` |
| `NIFTY_LOT_SIZE` | 65 contracts / lot | `settings.py:13` |
| `BANKNIFTY_LOT_SIZE` | 30 contracts / lot | `settings.py:14` |
| `CAPITAL` | ₹10,00,000 base | `settings.py:95` |

Per entry, a NIFTY IC deploys `10 × 65 = 650` contracts per leg; a BANKNIFTY IC deploys `10 × 30 = 300`.

There is no volatility- or credit-based sizing; no Kelly or IV-percentile scaling; no per-trade rupee risk cap.

## Recovery mode (gated, not wired)

`RiskManager.can_enter_recovery` permits a single-sided credit spread after a hard stop, only if:

- `halted is True`
- recovery mode not already active
- `stop_hit_at` is recorded
- stop occurred before `RECOVERY_DEADLINE = 13:00`
- VIX is stable or falling

⚠ The gating logic exists (`risk_manager.py:88-110`), but `main.py` does not wire an execution flow for recovery trades (BUG-15). The capability is documented but dormant.

## Cost model in paper (risk-relevant)

The paper execution model simulates the full F&O cost stack:

- **Slippage:** `max(ltp × 0.05%, ₹0.25)`, with a **3× multiplier** for options with LTP below `SLIPPAGE_OTM_THRESHOLD = 50`.
- **Tick rounding:** `PRICE_TICK = 0.05`.
- **STT:** 0.15% on options sell notional (Budget 2026 rate); 0.01% on futures.
- **Brokerage:** flat ₹5 per leg.
- **Exchange transaction charges:** 0.03553% (NSE options).
- **SEBI fees:** ₹10 per crore turnover.
- **Stamp duty:** 0.003% on BUY side.
- **GST:** 18% on brokerage + exchange + SEBI (not on STT or stamp).

Cost stack is applied on every entry leg (`fees.compute_taxes_and_fees`) and every exit leg (`PaperPositionTracker.close_position`). See `trading_system/core/fees.py` and `settings.FEES_NIFTY_OPT`.

## Known risk-framework gaps

| Gap | Axiom implication | Status |
|---|---|---|
| Mid-session OAuth recovery | Token expiry mid-session leaves ingestion in uncertain state — Axiom 3 | Open (BUG-07) |

All other previously-tracked gaps (daily loss cap, kill switch, PID guard, margin pre-check, rollback escalation, expiry-day close, force-exit logging) are resolved. See `bugs_for_review.md` for full fix history.
