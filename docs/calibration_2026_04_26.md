# IC_MIN_CREDIT calibration analysis — 2026-04-26

Cost-model break-even derivation for the 1%-harvest IC cycle at 10 lots, with counterfactual replay against Friday 2026-04-24's 352 pre-entry credit rejections.

## Headline finding

**The zero-entry pattern is not a calibration problem. It is a market-state problem.** Friday's market offered no credits anywhere near the cost-model break-even for either instrument. The system rejected 352 entry attempts because all of them were structural losers, not because the floor is mis-set.

Lowering `IC_MIN_CREDIT` would not have produced profitable entries Friday. It would have produced losing ones.

## Cost-model break-even (post-LIVE-12 stack)

Break-even credit such that `harvest_target == round_trip_cost` for one IC cycle (entry + harvest + re-entry's exit) at `IC_LOT_SIZE=10`, `IC_HARVEST_PCT=0.01`, and the LIVE-12 fee stack pinned in `settings.FEES_NIFTY_OPT`.

### NIFTY, qty=650/leg

| IC structure | Wings | Shorts | Net cr/share | Fees | Slip (paper) | Total | Break-even cr |
|---|---|---|---|---|---|---|---|
| Narrow ATM | 4 | 22 | 36 | 127.32 | 28.60 | 155.92 | **23.99** |
| Balanced | 4 | 18 | 28 | 115.00 | 28.60 | 143.60 | **22.09** |
| Wide / current floor | 3 | 12 | 18 | 93.44 | 19.50 | 112.94 | **17.38** |
| Very wide (deep OTM) | 1 | 7 | 12 | 71.84 | 8.00 | 79.84 | **12.28** |

`IC_MIN_CREDIT=18` is structurally correct for the wide-IC case (₹0.62 margin above break-even) and **below break-even for narrow / balanced ICs**. The single floor implicitly assumes the strategy always picks deep-OTM shorts.

### BANKNIFTY, qty=300/leg

| IC structure | Wings | Shorts | Net cr/share | Fees | Slip (paper) | Total | Break-even cr |
|---|---|---|---|---|---|---|---|
| Narrow ATM | 10 | 50 | 80 | 132.52 | 22.80 | 155.32 | **51.77** |
| Balanced | 8 | 30 | 44 | 101.24 | 22.80 | 124.04 | **41.35** |
| Wide | 5 | 20 | 30 | 82.74 | 15.00 | 97.74 | **32.58** |
| Very wide | 2 | 10 | 16 | 64.26 | 7.50 | 71.76 | **23.92** |

`IC_MIN_CREDIT=18` is **structurally too low for BANKNIFTY across every plausible IC shape.** The smaller lot size (30 vs 65) cuts the harvest target proportionally while the per-leg cost stack stays similar. Any BANKNIFTY entry at ₹18 credit is a guaranteed money-loser.

### Slippage sensitivity

The above uses paper's current ~0.05% slippage assumption. LIVE-04 explicitly flags this as known-optimistic. Re-running NIFTY balanced under realistic and pessimistic assumptions:

| Slippage | Fees | Slip | Total | Break-even cr |
|---|---|---|---|---|
| Paper-current (0.05%) | 115.00 | 28.60 | 143.60 | 22.09 |
| Realistic (0.5%) | 115.00 | 286.00 | 401.00 | **61.69** |
| Pessimistic (1.5%) | 115.00 | 858.00 | 973.00 | **149.69** |

**The proving-period fill calibration (LIVE-04) could move the break-even floor by 3–7×.** Until that calibration completes, every paper P&L number is conditioned on a known-low slippage assumption.

## Counterfactual replay — Friday 2026-04-24

352 pre-entry credit rejections in `logs/ic_system_20260424.log`, parsed for `(symbol, offered_credit, leg_prices)`:

| Instrument | Rejections | Min cr | p50 cr | p95 cr | Max cr | Cost-model break-even |
|---|---|---|---|---|---|---|
| NIFTY | 179 | 2.80 | 3.25 | 3.70 | 3.85 | ≥17.38 (wide structure) |
| BANKNIFTY | 173 | 1.30 | 6.30 | 8.90 | 14.75 | ≥23.92 (wide structure) |

NIFTY all day: max offered credit 3.85; floor is 17.38. **One full order of magnitude below break-even.** Even a hypothetical `IC_MIN_CREDIT=5` floor would have fired entries that lose ₹85 per cycle at paper slippage.

BANKNIFTY all day: max offered credit 14.75; floor is 23.92. Every BANKNIFTY entry Friday — even the best of 173 — would have been a structural loser. At ₹14.75 credit, harvest target is ₹44.25; cost stack is ~₹85. **Net of ₹40 lost per cycle.**

Typical Friday leg prices: NIFTY shorts ₹11–13, wings ₹2; BANKNIFTY shorts ₹50–55, wings ₹5–7. The shorts were sitting that low because the 20-day S/R range is ~2,129pt NIFTY and ~6,377pt BANKNIFTY (per session resume) — pushing IC shorts deep OTM where there's nothing left to sell.

## What this means for the go-live path

1. **Don't lower `IC_MIN_CREDIT`.** The current floor is structurally correct for NIFTY and structurally too lax for BANKNIFTY. Lowering it makes things worse.

2. **Consider per-instrument floors.** `IC_MIN_CREDIT_NIFTY` ≈ 18 (current), `IC_MIN_CREDIT_BANKNIFTY` ≈ 30 (computed). The single-knob design is a latent bug at any structure tighter than "wide deep OTM."

3. **Wide-S/R regime is non-tradable.** When 20-day range is wide, S/R buffer pushes shorts so deep OTM that no profitable entry exists. The system *should* sit out. The 2-day zero-entry pattern is the strategy working as designed.

4. **The proving period's economics depend on LIVE-04.** Until empirical slippage data exists, paper P&L is biased optimistic by 3–7× on the cost side. Any "GO LIVE" verdict produced before LIVE-04 calibration is conditioned on a model that has not been tested against reality.

5. **The shakedown safety triad is still worth shipping** — but its purpose shifts. It's not "make Monday fire entries safely." It's "when the market regime *does* offer entries, cap exposure to one trade/day for the first 10 days." The triad becomes meaningful only after a normal-range day shows up.

## Recommended next steps (offline-doable)

1. **Add per-instrument `IC_MIN_CREDIT_*` settings** — small change, clearly motivated, regression-tested. Closes the BANKNIFTY structural-loser hole.
2. **Ship shakedown safety triad** — `IC_MAX_ENTRIES_PER_SESSION` (mode-gated so paper isn't capped), `data/LIVE_ACK` (live-only no-op in paper), shakedown `DAILY_MAX_LOSS`. Ready for first normal-regime day.
3. **Document the wide-range regime as expected non-trading state** — update axiom 2's "non-participation only under axiom 3 or invalid quotes" wording to include "or no entry meets the cost-model break-even floor." Today's behavior matches this de facto but the axiom doesn't cover it.

## Decision pending from operator

- Accept the per-instrument-floor change as next on critical path?
- Or: continue monitoring in paper at current settings; revisit only if a 5th consecutive zero-entry day signals the regime isn't reverting?
