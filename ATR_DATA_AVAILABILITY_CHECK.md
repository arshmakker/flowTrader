# ATR Percentile Data Availability Check

**Date:** January 18, 2026  
**Status:** ✅ **REQUIREMENTS MET**

---

## 📊 Data Availability Summary

### Overall Status
- **Total days with data:** 18 days
- **Days with sufficient candles (≥15):** 14 days
- **Days with insufficient candles (<15):** 4 days
- **Minimum required:** 10 days
- **Result:** ✅ **REQUIREMENTS MET** (14 > 10)

---

## ✅ Days with Sufficient Data (14 days)

These days have **≥15 candles** and can calculate ATR(14):

| Date | Candles | Ticks | Status |
|------|---------|-------|--------|
| 20251222 | 18 | 976 | ✅ Yes |
| 20251224 | 25 | 1,565 | ✅ Yes |
| 20251226 | 18 | 1,050 | ✅ Yes |
| 20251229 | 16 | 938 | ✅ Yes |
| 20251231 | 15 | 940 | ✅ Yes |
| 20260102 | 21 | 1,369 | ✅ Yes |
| 20260105 | 19 | 1,166 | ✅ Yes |
| 20260106 | 18 | 1,149 | ✅ Yes |
| 20260107 | 15 | 1,003 | ✅ Yes |
| 20260108 | 21 | 1,241 | ✅ Yes |
| 20260109 | 23 | 1,459 | ✅ Yes |
| 20260113 | 23 | 1,438 | ✅ Yes |
| 20260114 | 23 | 1,580 | ✅ Yes |
| 20260116 | 23 | 1,538 | ✅ Yes |

**Total:** 14 days ✅

---

## ❌ Days with Insufficient Data (4 days)

These days have **<15 candles** and cannot calculate ATR(14):

| Date | Candles | Ticks | Status | Reason |
|------|---------|-------|--------|--------|
| 20251223 | 13 | 702 | ❌ No | Short trading day? |
| 20251230 | 13 | 757 | ❌ No | Short trading day? |
| 20260101 | 8 | 522 | ❌ No | Likely holiday/short day |
| 20260112 | 6 | 310 | ❌ No | Very short day |

**Note:** These days will be skipped during ATR calculation, but we still have 14 days which exceeds the minimum requirement of 10.

---

## 📋 Requirements Check

### ATR Percentile Calculation Requirements

| Requirement | Needed | Available | Status |
|-------------|--------|-----------|--------|
| **Minimum days with ATR data** | 10 | 14 | ✅ **MET** |
| **Candles per day for ATR(14)** | ≥15 | 14 days have ≥15 | ✅ **MET** |
| **Historical ATR values** | 10+ | 14 available | ✅ **MET** |

### Stored ATR History

| Source | Entries | Status |
|--------|---------|--------|
| `market_data_atr/atr_history.json` | 2 entries | ⚠️ Insufficient (need 10+) |
| **Backtest data** | 14 days | ✅ **Sufficient** |

**Conclusion:** Must calculate ATR from backtest data (cannot rely on stored history alone).

---

## 🎯 Implementation Feasibility

### ✅ Can Implement ATR Percentile Calculation

**Why:**
1. **14 days available** (exceeds minimum of 10)
2. **All 14 days have ≥15 candles** (sufficient for ATR(14))
3. **Can build rolling window** of last 10-20 days
4. **No external dependencies** needed (can calculate from backtest data)

### Implementation Approach

**Option A: Pre-calculate ATR History** ✅ **RECOMMENDED**

1. **Before backtest starts:**
   - Calculate ATR for each of the 14 days with sufficient data
   - Store in memory: `atr_by_date = {date: atr_value}`

2. **During backtest:**
   - For each day, use rolling window of last 10-20 days
   - Calculate percentile using `RegimeDetector.calculate_atr_percentile()`

3. **Fallback:**
   - If < 10 days available for a specific date, use hardcoded 75.0

**Example Rolling Window:**
```
For date 20260116:
  - Use ATR from: 20251222, 20251224, 20251226, ..., 20260116
  - Total: 14 days (exceeds minimum 10)
  - Calculate percentile: ✅
```

---

## 📈 Data Quality Assessment

### Days with Most Data
- **20251224:** 25 candles (best)
- **20260109, 20260113, 20260114, 20260116:** 23 candles each
- **20260102, 20260108:** 21 candles each

### Days with Least Data
- **20260112:** 6 candles (very short day)
- **20260101:** 8 candles (likely holiday)
- **20251223, 20251230:** 13 candles each (short days)

### Average Data Quality
- **Average candles per day (14 good days):** ~19 candles
- **Trading hours coverage:** Good (most days have full trading session)
- **Data completeness:** 78% (14 out of 18 days usable)

---

## ✅ Final Verdict

### Requirements Status: ✅ **MET**

**Summary:**
- ✅ **14 days** with sufficient data (exceeds minimum 10)
- ✅ **All 14 days** have ≥15 candles for ATR(14) calculation
- ✅ **Can build rolling window** of 10-20 days for percentile
- ✅ **No blockers** for implementation

**Recommendation:**
- ✅ **Proceed with implementation**
- Use **Option A: Pre-calculate ATR history** approach
- Skip the 4 days with insufficient data
- Use rolling window of last 10-20 days for percentile calculation

---

## 🔧 Implementation Notes

### Days to Process
- **Process:** 14 days with ≥15 candles
- **Skip:** 4 days with <15 candles (20251223, 20251230, 20260101, 20260112)

### Rolling Window Strategy
- **For early days:** Use all available days (will be < 10 initially)
- **For later days:** Use last 10-20 days (will have 10-14 days available)
- **Fallback:** Use 75.0 if < 10 days available

### Expected Behavior
- **First few days:** May need fallback (75.0) if < 10 days accumulated
- **After day 10:** Should have sufficient history for percentile calculation
- **Last days:** Will have 10-14 days in rolling window

---

**Status:** ✅ **Ready for Implementation**
