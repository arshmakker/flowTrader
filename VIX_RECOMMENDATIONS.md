# VIX Recommendations (India VIX for Regime Detection)

## Current behaviour

- **CONVEX**: Regime is CONVEX only when **India VIX < VIX_LOW (12)** and TREND is not detected.
- **INCOME**: Regime is INCOME when **India VIX ≥ VIX_HIGH (18)** and TREND is not detected.
- **NEUTRAL**: 12 ≤ India VIX < 18 (or VIX missing) → no volatility regime; Convex runs only as **NEUTRAL fallback** (after Calendar, Trend, then Convex in the fallback order).

In practice, India VIX is often in the 12–18 band, so the **primary CONVEX path** (regime == CONVEX) rarely runs; Convex entries usually happen via the NEUTRAL fallback.

Constants in `regime/regime_detector.py`:

- **VIX_LOW = 12.0** — CONVEX eligible when India VIX < 12
- **VIX_HIGH = 18.0** — INCOME eligible when India VIX ≥ 18
- **CONVEX_VIX_MAX = 15** — not used in production logic (comment: "convexcall-style")
- **INCOME_VIX_MIN = 20** — only used inside TREND branch for debug (`income_vol_ok`), not for INCOME regime

---

## Recommendation options

### 1. Raise VIX_LOW (e.g. 12 → 14 or 15)

- **Effect**: More days qualify as CONVEX; primary CONVEX path runs more often.
- **Trade-off**: Slightly higher VIX (e.g. 13–14) may be less “ideal” for backspreads; you get more CONVEX entries but possibly noisier regime.
- **Implementation**: Change `VIX_LOW = 12.0` to `14.0` or `15.0` in `regime_detector.py`. If you use 15, you can align with the existing comment for `CONVEX_VIX_MAX` and consider removing or documenting the unused constant.

### 2. Add IV-based CONVEX trigger (OR with VIX)

- **Idea**: Allow CONVEX when **either** India VIX < VIX_LOW **or** (e.g.) **IV percentile < 40** (low vol by IV), when not TREND.
- **Effect**: CONVEX can trigger on low IV even when VIX is in the 12–18 band, using data you already compute.
- **Trade-off**: Need a consistent IV percentile source and threshold; avoid double-counting (e.g. only use IV when VIX is missing or in mid-band).
- **Implementation**: In `detect_regime` and `classify_regime_from_indicators`, add:  
  `convex_vol_ok = (india_vix is not None and india_vix < VIX_LOW) or (iv_percentile is not None and iv_percentile < CONVEX_IV_PCT_MAX)`  
  and use `convex_vol_ok` for CONVEX (with CONVEX_IV_PCT_MAX already 40 in the file).

### 3. Widen CONVEX band and narrow NEUTRAL (e.g. VIX_LOW=14, VIX_HIGH=18)

- **Effect**: CONVEX 14–18 band shrinks; more CONVEX, same INCOME threshold.
- **No change to INCOME** unless you also want to tune VIX_HIGH (e.g. 17 vs 18).

### 4. Align / document legacy constants

- **CONVEX_VIX_MAX (15)** and **INCOME_VIX_MIN (20)** are not used for the main CONVEX/INCOME regime logic. Either:
  - Use them (e.g. CONVEX when VIX < CONVEX_VIX_MAX, INCOME when VIX ≥ INCOME_VIX_MIN) and make VIX_LOW/VIX_HIGH aliases or remove, or
  - Keep VIX_LOW/VIX_HIGH as single source of truth and document or remove CONVEX_VIX_MAX / INCOME_VIX_MIN to avoid confusion.

---

## Suggested next step

- **Quick win**: Raise **VIX_LOW to 14** (or 15) so the primary CONVEX path runs more often without touching INCOME or backtest logic beyond the same constant.
- **Richer**: Add **IV-based CONVEX** (option 2) so low IV percentile can also trigger CONVEX when VIX is mid-band; then optionally raise VIX_LOW as well.
- **Clean-up**: Decide whether CONVEX/INCOME should use **VIX_LOW/VIX_HIGH only** or **CONVEX_VIX_MAX/INCOME_VIX_MIN** and update code + comments so one set is the single source of truth.

If you tell me which direction you prefer (raise VIX only, add IV trigger, or both), I can outline exact code changes in `regime_detector.py` and any backtest defaults.
