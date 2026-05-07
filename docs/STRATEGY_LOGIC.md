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
| Regime gate | `day_type == RANGING` AND `vix < IC_VIX_MAX = 30` AND VIX range ≤ `1.5` over last 8 min | `regime_filter.get_regime_gate` |
| NIFTY VIX floor | NIFTY only: `vix ≥ IC_NIFTY_MIN_VIX = 14` (quiet days → skip; no viable credit) | `regime_filter.get_regime_gate` |
| Not halted | `risk_manager.halted` is `False` | `main.py` |
| Expiry available | DTE ≥ `IC_DTE_THRESHOLD = 3`, else rolled to next weekly | `expiry_manager.get_expiry` |
| Min credit | `(sc_ltp + sp_ltp) − (lc_ltp + lp_ltp) ≥ IC_MIN_CREDIT_BY_INSTRUMENT` (NIFTY ₹18, BANKNIFTY ₹25) | `iron_condor.enter` |
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

4. **S/R OTM cap** (`IC_SR_CAP_OTM_FROM_SPOT`): if S/R buffering pushes `sc` or `sp` more than 400 pts (NIFTY) or 1000 pts (BANKNIFTY) from spot, the strike is clamped back. Strikes beyond the cap are illiquid — credit collapses.

5. **Wing placement**: `lc = sc + width`, `lp = sp − width`, with `width` rounded to at least one strike step.

## Entry sequence

Default: **hedge-first** (`IC_ENTRY_MODE = "hedge_first"`):

1. Buy long call (`lc`) + Buy long put (`lp`) — limit orders at best-ask, submitted first
2. Await both fills; abort with wing unwind if either fails
3. Compute short-leg limit prices from actual wing fills + `IC_MIN_CREDIT` floor
4. Sell short call (`sc`) + Sell short put (`sp`) — limit orders at computed prices
5. Post-fill credit re-check; abort + full unwind if below floor

If any leg returns non-`COMPLETE`, entry aborts and `_rollback_partial_entry` reverses already-filled legs (Axiom 4). `IC_Position` is created only after all four legs confirm.

## Monitoring rules (per 60-second tick)

Computed from live leg LTPs:

```
current_premium = (sc + sp) − (lc + lp)
pnl_unit        = entry_credit − current_premium
total_pnl       = pnl_unit × lots × lot_size
```

1. **Profit harvest (Axiom 2 target):**
   ```
   harvest_pct = IC_HARVEST_PCT_BY_INSTRUMENT[instrument]  # NIFTY=2%, BANKNIFTY=13%
   harvest_trigger = pos.max_profit × harvest_pct
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
| `PROFIT_HARVEST` | `total_pnl ≥ harvest_pct × max_profit` (NIFTY 2%, BANKNIFTY 13%) | `monitor()` |
| `ADJUSTMENT_REQUIRED` | profitable + short-strike breach | `monitor()` |
| `FORCE_EXIT` | combined hard stop breach; EOD/weekend/holiday flatten; expiry-day close | `force_exit()` called from `main.py` |

## Carry-over rules (Axiom 2)

- **Weekday → next weekday (both trading days):** carry overnight.
- **Weekday → weekend or holiday:** force-flatten at `TRADE_END = 15:10`.
- **Expiry-day close:** positions whose `expiry_date` (sourced from NFO.csv at entry) matches today are force-closed at `TRADE_END_EXPIRY = 15:00`.

## Indicators

| Indicator | Purpose | Status |
|---|---|---|
| VIX level + stability | Entry gate + strike-tier selection | Active |
| VWAP | Day classification (trending vs ranging) | Active — `day_classifier` forwards `md.get_ohlcv_df()` to `signal_engine.compute_vwap_value()` |
| Open vs. current move | Day classification | Active; `is_open_price_reliable` downgrades confidence to LOW when `o` field is absent |
| 20-day high / low | Strike buffer + OTM cap | Active, sourced from collected futures data |
| RSI, PCR, Max Pain, consensus | Unused | Removed from `signal_engine.py` — IC-only scope (Axiom 1) |

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

- **BUG-07** — no mid-session OAuth recovery; a token expiry mid-day leaves the data ingestion layer in an uncertain state (Axiom 3). Tracked in `bugs_for_review.md`.
