#!/usr/bin/env python3
"""
Analyze IV and India VIX from stored market data.
Run from project root: python analyze_iv_vix_data.py

Reads:
- market_data_YYYYMMDD/daily_metrics.json (india_vix, iv_percentile when saved by strategy_runner)
- market_data_iv/iv_data_YYYYMMDD.json (raw IV history if present)

Outputs summary stats to decide VIX_LOW / VIX_HIGH and IV-based CONVEX thresholds.
"""

import glob
import json
import os
from collections import defaultdict
from datetime import datetime

# Current regime thresholds (for reference in output)
VIX_LOW = 12.0
VIX_HIGH = 18.0
CONVEX_IV_PCT_MAX = 40


def load_daily_metrics(date_str: str):
    """Load daily_metrics from market_data_YYYYMMDD/daily_metrics.json."""
    data_dirs = glob.glob(f"market_data_{date_str}")
    if not data_dirs:
        return None
    path = os.path.join(data_dirs[0], "daily_metrics.json")
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r") as f:
            return json.load(f)
    except Exception:
        return None


def get_all_market_data_dates():
    """Return sorted list of date strings that have market_data_YYYYMMDD dirs."""
    dirs = glob.glob("market_data_[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]")
    dates = []
    for d in dirs:
        suffix = d.replace("market_data_", "")
        if len(suffix) == 8 and suffix.isdigit():
            dates.append(suffix)
    return sorted(dates)


def analyze_daily_metrics():
    """Collect IV and VIX from all daily_metrics.json files."""
    dates = get_all_market_data_dates()
    rows = []
    for date_str in dates:
        m = load_daily_metrics(date_str)
        if not m:
            continue
        vix = m.get("india_vix")
        iv_pct = m.get("iv_percentile")
        if vix is None and iv_pct is None:
            continue
        try:
            if vix is not None:
                vix = float(vix)
            if iv_pct is not None:
                iv_pct = float(iv_pct)
        except (TypeError, ValueError):
            continue
        rows.append({"date": date_str, "india_vix": vix, "iv_percentile": iv_pct})
    return rows


def analyze_market_data_iv():
    """Optional: list IV data files in market_data_iv/."""
    if not os.path.isdir("market_data_iv"):
        return []
    files = glob.glob(os.path.join("market_data_iv", "iv_data_*.json"))
    return [os.path.basename(f) for f in sorted(files)]


def main():
    print("=" * 60)
    print("IV & India VIX data analysis (from market_data_* and daily_metrics)")
    print("=" * 60)

    # Daily metrics (IV percentile + India VIX)
    rows = analyze_daily_metrics()
    if not rows:
        print("\nNo daily_metrics.json with iv_percentile or india_vix found.")
        print("Checked: market_data_YYYYMMDD/daily_metrics.json")
        print("If you have data elsewhere, point this script at that path.")
        iv_files = analyze_market_data_iv()
        if iv_files:
            print(f"\nFound {len(iv_files)} files in market_data_iv/ (raw IV; no VIX there).")
        return

    n = len(rows)
    print(f"\nFound {n} day(s) with at least one of (india_vix, iv_percentile).")

    # VIX stats
    vix_vals = [r["india_vix"] for r in rows if r["india_vix"] is not None]
    vix_missing = sum(1 for r in rows if r["india_vix"] is None)

    if vix_vals:
        vix_min = min(vix_vals)
        vix_max = max(vix_vals)
        vix_avg = sum(vix_vals) / len(vix_vals)
        below_low = sum(1 for v in vix_vals if v < VIX_LOW)
        mid_band = sum(1 for v in vix_vals if VIX_LOW <= v < VIX_HIGH)
        at_or_above_high = sum(1 for v in vix_vals if v >= VIX_HIGH)

        print("\n--- India VIX ---")
        print(f"  Present: {len(vix_vals)} days  |  Missing: {vix_missing} days")
        print(f"  Min: {vix_min:.2f}  |  Max: {vix_max:.2f}  |  Mean: {vix_avg:.2f}")
        print(f"  Current thresholds: VIX_LOW={VIX_LOW}, VIX_HIGH={VIX_HIGH}")
        print(f"  VIX < {VIX_LOW} (CONVEX):     {below_low:3d} days ({100*below_low/len(vix_vals):.1f}%)")
        print(f"  {VIX_LOW} <= VIX < {VIX_HIGH} (NEUTRAL): {mid_band:3d} days ({100*mid_band/len(vix_vals):.1f}%)")
        print(f"  VIX >= {VIX_HIGH} (INCOME):   {at_or_above_high:3d} days ({100*at_or_above_high/len(vix_vals):.1f}%)")

        # Buckets for threshold sensitivity
        for low in [12, 14, 15]:
            count = sum(1 for v in vix_vals if v < low)
            print(f"  If VIX_LOW={low}: CONVEX would trigger on {count} days ({100*count/len(vix_vals):.1f}%)")
    else:
        print("\n--- India VIX ---")
        print("  No india_vix in any daily_metrics.json.")

    # IV percentile stats
    iv_vals = [r["iv_percentile"] for r in rows if r["iv_percentile"] is not None]
    iv_missing = sum(1 for r in rows if r["iv_percentile"] is None)

    if iv_vals:
        iv_min = min(iv_vals)
        iv_max = max(iv_vals)
        iv_avg = sum(iv_vals) / len(iv_vals)
        convex_iv = sum(1 for v in iv_vals if v < CONVEX_IV_PCT_MAX)
        income_iv = sum(1 for v in iv_vals if v >= 60)

        print("\n--- IV Percentile ---")
        print(f"  Present: {len(iv_vals)} days  |  Missing: {iv_missing} days")
        print(f"  Min: {iv_min:.1f}  |  Max: {iv_max:.1f}  |  Mean: {iv_avg:.1f}")
        print(f"  IV% < {CONVEX_IV_PCT_MAX} (CONVEX-style): {convex_iv:3d} days ({100*convex_iv/len(iv_vals):.1f}%)")
        print(f"  IV% >= 60 (INCOME-style):   {income_iv:3d} days ({100*income_iv/len(iv_vals):.1f}%)")
    else:
        print("\n--- IV Percentile ---")
        print("  No iv_percentile in any daily_metrics.json.")

    # Last 20 rows for quick look
    print("\n--- Sample (last 20 days with data) ---")
    for r in rows[-20:]:
        vix_s = f"{r['india_vix']:.2f}" if r["india_vix"] is not None else "N/A"
        iv_s = f"{r['iv_percentile']:.1f}%" if r["iv_percentile"] is not None else "N/A"
        print(f"  {r['date']}  India VIX: {vix_s:>6}  IV%: {iv_s:>6}")

    # market_data_iv
    iv_files = analyze_market_data_iv()
    if iv_files:
        print(f"\n--- market_data_iv ---")
        print(f"  {len(iv_files)} iv_data_*.json file(s) (raw IV history).")

    print("\n" + "=" * 60)


if __name__ == "__main__":
    main()
