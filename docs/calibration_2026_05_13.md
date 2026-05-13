# Strategy Calibration & PnL Reset — 2026-05-13

## Why PnL was reset

All trades before this date were run under a materially different set of strategy parameters.
The losses reflect the old configuration, not the calibrated system. Carrying them forward
would obscure whether the new parameters are working.

---

## PnL state at reset

**As of 2026-05-13 15:10 IST (end of trading)**

| Metric | Value |
|---|---|
| Realised PnL | ₹-27,838.94 |
| Unrealised PnL | ₹-3,990.00 |
| Total PnL | ₹-31,828.94 |
| Total trades | 3 |
| Winning trades | 0 |
| Win rate | 0% |
| Max drawdown | ₹27,838.94 |

### Per-instrument

| Instrument | Trades | PnL |
|---|---|---|
| NIFTY | 1 | ₹-9,060.94 |
| BANKNIFTY | 2 | ₹-18,778.00 |

### Individual trades

| Date | Instrument | SC | SP | Entry credit | Exit reason | PnL |
|---|---|---|---|---|---|---|
| 2026-05-12 | BANKNIFTY | 55500 | 54200 | 70.05 | ADJUSTMENT_REQUIRED | ₹-2,738.44 |
| 2026-05-12 | BANKNIFTY | 55100 | 53900 | 33.40 | ADJUSTMENT_REQUIRED | ₹-16,039.56 |
| 2026-05-13 | NIFTY | 23700 | 23300 | 56.10 | FORCE_EXIT | ₹-9,060.94 |

---

## What changed (commits on 2026-05-13)

### `f253144` — Symmetric LTP-primary harvest guard + switch to NIFTY
- Fixed a phantom harvest bug (mid-based PnL inflated by stale wide resting orders)
- Dual-source guard now requires both mid and LTP to agree before harvest fires
- ACTIVE_INSTRUMENTS temporarily switched to NIFTY-only during calibration work

### `6438485` — BANKNIFTY 700pt minimum OTM floor
- **Problem**: S/R buffer was compressing BANKNIFTY short strikes to 300pt OTM
- **Evidence**: 86-day backtest showed 56% breach rate at 300pt, 10.7% at 700pt
- **Change**: Enforce ≥700pt OTM for BANKNIFTY SC and SP after all S/R adjustments
- Removed `IC_SR_BUFFER_BY_INSTRUMENT` entirely (no backtest evidence of benefit)
- Kept `IC_SR_CAP_OTM_FROM_SPOT` (prevents illiquid far-OTM territory)

### `6f522e3` — Remove S/R buffer, keep S/R cap
- S/R buffer was pushing strikes inward toward S/R levels
- No evidence it helped; the 700pt floor is the operative protection now
- `calculate_strikes()` and `enter()` signatures simplified

### `ca1e0a0` — Raise NIFTY harvest threshold 2% → 15%
- **Problem**: 2% fired in minutes, netting only ₹114–295 per cycle after round-trip costs
- **Analysis**: round-trip break-even for NIFTY ≈ 1.4% of max_profit; 2% left ₹295 margin
- **Model**: total theta fixed per calendar window; fewer harvests → fewer round-trip costs
  - 2% (6 cycles/day): net ≈ ₹1,770
  - 15% (1 cycle/day): net ≈ ₹6,210
- BANKNIFTY stays at 13% (its break-even ≈ 6.9%)

### `36951e8` — Re-enable BANKNIFTY as secondary instrument
- BANKNIFTY covers VIX<14 days where IC_NIFTY_MIN_VIX=14 blocks NIFTY
- Satisfies Axiom 2 (always in market on ranging days)
- Protected by the 700pt OTM floor from this session

---

## System state after reset

- `ACTIVE_INSTRUMENTS = ["NIFTY", "BANKNIFTY"]`
- `IC_HARVEST_PCT_BY_INSTRUMENT = {"NIFTY": 0.15, "BANKNIFTY": 0.13}`
- `IC_MIN_OTM_BANKNIFTY = 700`
- S/R buffer removed; S/R cap kept at `{"NIFTY": 400, "BANKNIFTY": 1000}`
- `IC_NIFTY_MIN_VIX = 14.0` (BANKNIFTY covers the gap)
- All PnL counters reset to zero; paper_trades.csv archived as `paper_trades_pre_calibration_20260513.csv`
