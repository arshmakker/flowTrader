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
| VIX stability | 45-min range ≤ 1.5, ≥ 5 samples | `IC_VIX_STABLE_MINS = 45`, `IC_VIX_STABLE_BAND = 1.5` | `regime_filter.py:53-66` |
| Day type | `day_type == RANGING` | — | `regime_filter.py:86-88` |
| Risk halted | `risk_manager.halted` is `False` | — | `main.py:409` |
| Min credit | `net_credit ≥ IC_MIN_CREDIT` | 18.0 per lot | `iron_condor.py:187-202` |
| Valid leg LTPs | all 4 legs > 0 | — | `iron_condor.py:176-178` |
| DTE | expiry ≥ 3 DTE, else rolled | `IC_DTE_THRESHOLD = 3` | `expiry_manager.py` |
| S/R buffer | shorts ≥ 50 points from 20-day H/L | `IC_SR_BUFFER = 50` | `sr_manager.apply_buffer` |
| Option-price sanity | `0.05 ≤ ltp ≤ 5000` at entry | `PAPER_OPTION_LTP_MIN/MAX` | `paper_order_manager.py:103-124`, `market_data.get_ltp` |

### 2. Position-level (continuous during monitoring)

| Net | Trigger | Setting | Source |
|---|---|---|---|
| Profit harvest | `total_pnl ≥ 1% × max_profit` | `IC_HARVEST_PCT = 0.01` | `iron_condor.py:273-276` |
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

- All 4 legs submitted in fixed order. If any returns non-`COMPLETE`, entry aborts.
- `_rollback_partial_entry` sends reverse orders for already-filled legs.
- `IC_Position` is created **only** after all 4 legs confirm.
- ⚠ **Rollback failure is currently only logged** (BUG-05). Axiom 3 + Axiom 4 violation — the axiom requires halt + alert here.

### 5. End-of-day safety (Axiom 2 carry rules)

- At `TRADE_END = 15:10`, if next calendar day is weekend or in `TRADING_HOLIDAYS_IST`: force-flatten all active ICs.
- If next calendar day is a trading day: carry overnight.
- ⚠ **Expiry-day close not enforced** (BUG-18). An IC reaching its own expiry Thursday with Friday as a trading day currently carries overnight into an expired position.

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

The paper execution model at `paper_order_manager.py:63-147` simulates partial costs:

- **Slippage:** `max(ltp × 0.05%, ₹0.25)`, with a **3× multiplier** for options with LTP below `SLIPPAGE_OTM_THRESHOLD = 50`.
- **Tick rounding:** `PRICE_TICK = 0.05`.
- **STT:** `0.05%` on options sell notional; `0.01%` on futures.
- **Brokerage:** flat `BROKERAGE_PER_ORDER = 5.0` per leg.
- **Not modelled:** GST (18% on brokerage + transaction charges), exchange transaction charges, SEBI turnover fees.

Implication for risk: the paper book is **systematically optimistic** vs. live fills. Post-go-live reconciliation must account for this gap. See `bugs_for_review.md` "Out of scope" section and `GO_LIVE_CHECKLIST.md` item 12.

## Known risk-framework gaps

These are risk-relevant but not yet implemented. Tracked in `GO_LIVE_CHECKLIST.md` or `bugs_for_review.md`:

| Gap | Axiom implication | Tracked in |
|---|---|---|
| Daily rupee loss cap | Independent of 3× max-profit stop — needed for per-day bounded loss | GO_LIVE item 6 |
| Kill switch (`data/HALT`) | Operator cannot force halt — Axiom 3 expressive gap | GO_LIVE item 7 |
| Single-instance guard (PID file) | Two concurrent processes create untrustworthy state — Axiom 3 | GO_LIVE item 16 |
| Mid-session OAuth recovery | Token expiry mid-session leaves ingestion in uncertain state — Axiom 3 | BUG-07 |
| Margin pre-check | No `get_limits()` before leg 1; rejection on leg 3 depends on a currently-broken rollback path | GO_LIVE item 5 |
| Rollback escalation | Stuck half-condor after failed reverse order — Axiom 4 clause unenforced | BUG-05 |
| Expiry-day close | Axiom 2 clause unenforced | BUG-18 |
| Force-exit logging | Force-exit events are not accounted for in realised P&L — Axiom 5 | BUG-02 |

## Priority ordering for risk hardening

1. **BUG-05** (rollback escalation) — highest. Axiom 4 failure mode with uncontrolled live exposure implication.
2. **BUG-02** (force-exit logging) — Axiom 5; required for any reconciliation.
3. **BUG-03 + BUG-04** (tracker unwind) — foundational; downstream risk calculations read the tracker.
4. **Daily loss cap + kill switch** — operator safety; low engineering effort, high protective value.
5. **BUG-18** (expiry-day close) — Axiom 2; rare in practice due to 1% harvest but still an axiom violation.
6. **BUG-07** (mid-session auth) — Axiom 3 fragility; matters once live.
