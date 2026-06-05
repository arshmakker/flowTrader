"""
Analyze how often fixed-distance NIFTY bear-call shorts are touched.

This is a risk diagnostic, not a P&L backtest:
  - Entry is Monday EOD, Tuesday fallback, matching backtest_pcr_spread.py.
  - Strategy is forced BEAR_CALL every eligible week.
  - For each short offset, report:
      1. EOD proxy touches: any bhavcopy underlying close >= short strike.
      2. Local intraday touches: any collected Nifty 50 tick high >= short strike.

The intraday result only covers dates present under market_data_YYYYMMDD/.
"""

import argparse
import glob
from datetime import date, timedelta
from pathlib import Path

import backtest_pcr_spread as bt
import pandas as pd


def _mondays(start: date, end: date) -> list[date]:
    days: list[date] = []
    cur = start
    while cur <= end:
        if cur.weekday() == 0:
            days.append(cur)
        cur += timedelta(days=1)
    return days


def _trading_days(start: date, end: date) -> list[date]:
    days: list[date] = []
    cur = start
    while cur <= end:
        if cur.weekday() < 5:
            days.append(cur)
        cur += timedelta(days=1)
    return days


def _intraday_high(day: date) -> float | None:
    day_key = day.strftime("%Y%m%d")
    spot_path = Path(f"market_data_{day_key}/raw_data/others/Nifty 50_{day_key}.csv")
    paths = [spot_path] if spot_path.exists() else []
    if not paths:
        paths = [Path(p) for p in glob.glob(f"market_data_{day_key}/raw_data/futures/NIFTY*_{day_key}.csv")]
    if not paths:
        return None

    highs: list[float] = []
    for path in paths:
        try:
            df = pd.read_csv(path, usecols=["ltp"])
        except Exception:
            continue
        vals = pd.to_numeric(df["ltp"], errors="coerce").dropna()
        vals = vals[vals > 0]
        if not vals.empty:
            highs.append(float(vals.max()))
    return max(highs) if highs else None


def _eod_underlying(day: date) -> float | None:
    bhav = bt._fetch_bhav(day)
    if bhav is None:
        return None
    return bt._get_spot(bhav)


def _week_signal_day(monday: date) -> tuple[date, pd.DataFrame] | None:
    bhav = bt._fetch_bhav(monday)
    if bhav is not None:
        return monday, bhav
    tuesday = monday + timedelta(days=1)
    bhav = bt._fetch_bhav(tuesday)
    if bhav is not None:
        return tuesday, bhav
    return None


def analyze(start: date, end: date, offsets: list[int], width: int) -> pd.DataFrame:
    rows = []
    mondays = _mondays(start, end)
    total = len(mondays)
    for idx, monday in enumerate(mondays, start=1):
        print(f"[{idx}/{total}] {monday.isoformat()}", flush=True)
        signal = _week_signal_day(monday)
        if signal is None:
            continue
        signal_day, bhav_sig = signal
        expiry = bt._find_weekly_expiry(bhav_sig, signal_day)
        spot = bt._get_spot(bhav_sig)
        if expiry is None or spot is None:
            continue

        atm = bt._round_strike(spot)
        days = _trading_days(signal_day, expiry)
        eod_highs = [_eod_underlying(d) for d in days]
        eod_highs = [v for v in eod_highs if v is not None]
        local_intraday_highs = [_intraday_high(d) for d in days]
        local_intraday_highs = [v for v in local_intraday_highs if v is not None]

        for offset in offsets:
            short = atm + offset
            long = short + width
            credit = bt._spread_credit(bhav_sig, expiry, short, long, "CE")
            rows.append(
                {
                    "week": monday.isoformat(),
                    "signal_day": signal_day.isoformat(),
                    "expiry": expiry.isoformat(),
                    "spot": round(spot, 2),
                    "atm": atm,
                    "offset": offset,
                    "short": short,
                    "long": long,
                    "credit": credit,
                    "eod_high": max(eod_highs) if eod_highs else None,
                    "eod_touched": bool(eod_highs and max(eod_highs) >= short),
                    "intraday_high": max(local_intraday_highs) if local_intraday_highs else None,
                    "intraday_covered_days": len(local_intraday_highs),
                    "intraday_touched": bool(local_intraday_highs and max(local_intraday_highs) >= short),
                }
            )
    return pd.DataFrame(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description="Analyze NIFTY bear-call short-strike touches")
    parser.add_argument("--start", default="2024-01-01")
    parser.add_argument("--end", default="2026-05-16")
    parser.add_argument("--offsets", default="100,200,300")
    parser.add_argument("--width", type=int, default=200)
    parser.add_argument("--out", default=None)
    args = parser.parse_args()

    offsets = [int(x.strip()) for x in args.offsets.split(",") if x.strip()]
    df = analyze(date.fromisoformat(args.start), date.fromisoformat(args.end), offsets, args.width)
    if df.empty:
        print("No analyzable weeks.")
        return

    summary = []
    for offset, grp in df.groupby("offset"):
        credits = pd.to_numeric(grp["credit"], errors="coerce").dropna()
        intraday = grp[grp["intraday_covered_days"] > 0]
        summary.append(
            {
                "offset": offset,
                "weeks": len(grp),
                "avg_credit": round(float(credits.mean()), 1) if not credits.empty else None,
                "min_credit": round(float(credits.min()), 1) if not credits.empty else None,
                "eod_touches": int(grp["eod_touched"].sum()),
                "eod_touch_rate": round(float(grp["eod_touched"].mean() * 100), 1),
                "intraday_weeks": len(intraday),
                "intraday_touches": int(intraday["intraday_touched"].sum()) if not intraday.empty else 0,
                "intraday_touch_rate": round(float(intraday["intraday_touched"].mean() * 100), 1)
                if not intraday.empty
                else None,
            }
        )

    print(pd.DataFrame(summary).to_string(index=False))
    if args.out:
        df.to_csv(args.out, index=False)
        print(f"\nSaved detail rows to {args.out}")


if __name__ == "__main__":
    main()
