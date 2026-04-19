# Trading Strategy Logic

Grounded in `docs/axioms.md`. Documents the iron condor strategy's entry conditions, strike selection, monitoring rules, exit conditions, and timeframes. See `TECHNICAL_REFERENCE.md` for module-level detail.

## Axiom anchors

- **Axiom 1** — iron condor is the only structure the system trades.
- **Axiom 2** — presence during trading hours, 1%-of-max-profit harvest, re-entry cycle, carry rules.
- **Axiom 3** — gates that override continuity (VIX, risk halt, invalid quotes).

## Strategy at a glance

Four-leg iron condor on NIFTY and BANKNIFTY, placed conditionally on regime + VIX state, monitored continuously, exited at 1% of calculated max profit (or on breach, or on safety override), and re-entered on the next cycle.

## Entry conditions

All of the following must be true for a new IC to be placed:

| Gate | Condition | Source |
|---|---|---|
| Market hours | trading day, 09:15–15:30 IST | `strategy_runner.is_market_hours` |
| Trading window | `TRADE_START = 10:00` ≤ now < `TRADE_END = 15:10` | `main.py` |
| Day classified | `day_type` set (computed at `CLASSIFY_TIME = 10:30`) | `day_classifier.py` |
| Regime gate | `day_type == RANGING` AND `vix < IC_VIX_MAX = 30` AND VIX range ≤ `1.5` over last 45 min | `regime_filter.get_regime_gate` |
| Not halted | `risk_manager.halted` is `False` | `main.py:409` |
| Expiry available | DTE ≥ `IC_DTE_THRESHOLD = 3`, else rolled to next weekly | `expiry_manager.get_expiry` |
| Min credit | `(sc_ltp + sp_ltp) − (lc_ltp + lp_ltp) ≥ IC_MIN_CREDIT = 18` | `iron_condor.enter` |
| Valid quotes | all 4 leg LTPs > 0 | `iron_condor.enter` |

## Strike selection

1. **Base OTM distance by VIX tier** (`iron_condor.get_vix_tier_params`):

   | VIX range | OTM distance | Wing width |
   |---|---:|---:|
   | `< 14` | 150 | 50 |
   | `14 ≤ VIX < 20` | 200 | 100 |
   | `≥ 20` | 300 | 150 |

2. **Symmetric around spot** (`calculate_strikes`):
   ```
   sc = round((spot + otm_dist) / step) * step
   sp = round((spot − otm_dist) / step) * step
   ```
   `step = 50` for NIFTY, `step = 100` for BANKNIFTY.

3. **S/R buffer** (`sr_manager.apply_buffer`): shifts `sc` outward to clear `sr_high + 50`; shifts `sp` outward to clear `sr_low − 50`.

4. **Wing placement**: `lc = sc + width`, `lp = sp − width`, with `width` rounded to at least one strike step.

## Entry sequence

Four legs placed in fixed order (`iron_condor.py:210-215`):

1. Sell short call (`sc`)
2. Sell short put (`sp`)
3. Buy long call (`lc`)
4. Buy long put (`lp`)

If any leg returns non-`COMPLETE`, entry aborts and `_rollback_partial_entry` sends reverse orders for already-filled legs (Axiom 4). `IC_Position` is created only after all four legs confirm.

## Monitoring rules (per 60-second tick)

Computed from live leg LTPs:

```
current_premium = (sc + sp) − (lc + lp)
pnl_unit        = entry_credit − current_premium
total_pnl       = pnl_unit × lots × lot_size
```

1. **Profit harvest (Axiom 2 target):**
   ```
   harvest_trigger = pos.max_profit × IC_HARVEST_PCT   # = 1%
   if total_pnl ≥ harvest_trigger → exit(PROFIT_HARVEST)
   ```
   Re-entry is attempted on the next loop cycle if entry conditions still hold.

2. **Adjustment on breach (only when profitable):**
   ```
   if total_pnl > 0 and (spot ≥ sc_strike or spot ≤ sp_strike):
       exit(ADJUSTMENT_REQUIRED)
   ```
   Implemented as exit-and-allow-reentry, not in-place roll.

3. **Invalid quote defence:** if any leg LTP ≤ 0, monitor returns `None` and defers the decision (Axiom 3).

## Exit conditions

| Reason | Trigger | Origin |
|---|---|---|
| `PROFIT_HARVEST` | `total_pnl ≥ 1% × max_profit` | `monitor()` |
| `ADJUSTMENT_REQUIRED` | profitable + short-strike breach | `monitor()` |
| `FORCE_EXIT` | combined hard stop breach; EOD/weekend/holiday flatten | `force_exit()` called from `main.py` |

## Carry-over rules (Axiom 2)

- **Weekday → next weekday (both trading days):** carry overnight.
- **Weekday → weekend or holiday:** force-flatten at `TRADE_END`.
- **Expiry-day close:** must be flat — ⚠ **not yet enforced** (see BUG-18).

## Indicators

| Indicator | Purpose | Status |
|---|---|---|
| VIX level + stability | Entry gate + strike-tier selection | Active |
| VWAP | Day classification (trending vs ranging) | ⚠ Wired but broken — VWAP never reaches classifier; every day classifies as RANGING (BUG-01) |
| Open vs. current move | Day classification | Active (but dependent on `get_open_price` reliability; see BUG-06) |
| 20-day high / low | Strike buffer | Active, sourced from collected futures data |
| RSI, PCR, Max Pain, consensus | None | Present in `signal_engine.py` but **unused** (BUG-16) |

## Timeframes

| Event | Frequency |
|---|---|
| Main control loop | Every 60 s (`SIGNAL_RECHECK_SEC`) |
| Quote collector | Every 5 s (background thread) |
| LTP cache | 2 s TTL |
| VIX cache | 60 s TTL |
| OHLCV cache | 60 s TTL |
| Day classification | Once per day at 10:30 IST |
| Combined hard-stop check | Every main-loop tick |
| EOD gate | At or after `TRADE_END = 15:10` |

## Known strategy-logic gaps

Cross-referenced to `bugs_for_review.md`:

- **BUG-01** — VWAP not wired into classifier; trending branch never fires.
- **BUG-06** — `get_open_price` silently substitutes `lp` for missing `o`, bypassing LOW-confidence downgrade.
- **BUG-08** — single NIFTY-based classification gates BANKNIFTY entries too.
- **BUG-18** — expiry-day close not enforced; IC can carry past its own expiry if next day is a trading day.
