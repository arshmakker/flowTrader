"""
Backtest: PCR Contrarian Credit Spread on NIFTY / BANKNIFTY weekly options.

Data: NSE F&O bhavcopy (new format, 2019+). No external dependencies beyond
requests/pandas/numpy which are already in the project venv.

Methodology (approximations documented inline):
  - Signal day: Monday EOD bhavcopy (or Tuesday if Monday is holiday)
  - PCR: weekly chain only (near-term weekly expiry, detected dynamically)
  - Entry prices: Monday EOD settle prices for the spread legs
  - Spot: UndrlygPric column (direct from bhavcopy)
  - Stop check: Wednesday EOD mark-to-market
  - Exit: expiry-day EOD settle prices (proxy for Thu/Tue 2:45pm exit)
  - Stop trigger: MTM loss > entry_credit × STOP_MULT
  - Expiry day detection: dynamic — handles NSE shift from Thursday→Tuesday (Sep 2025)

Optional filters (experimental — validate before deploying live):
  --ema-gate        Block entry when price action contradicts PCR signal.
                    BULL_PUT: skip if spot < 5-day EMA (downtrend).
                    BEAR_CALL: skip if spot > 5-day EMA (uptrend).
                    EMA computed from 5 trading days strictly before signal day.
  --consec-loss-pause
                    After a losing week, tighten PCR thresholds by 0.1
                    (BULL_PUT requires PCR > 1.4, BEAR_CALL requires PCR < 0.6)
                    for the immediately following week only.

Usage:
    source venv/bin/activate
    python tools/backtest_pcr_spread.py --start 2024-01-01 --end 2026-05-16
    python tools/backtest_pcr_spread.py --symbol BANKNIFTY --lot-size 30 --strike-step 100
    python tools/backtest_pcr_spread.py --start 2024-01-01 --end 2026-05-16 --out results.csv
    python tools/backtest_pcr_spread.py --ema-gate --consec-loss-pause --start 2024-01-01 --end 2026-05-16
"""

import argparse
import io
import logging
import time
import zipfile
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Optional

import pandas as pd
import requests

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger("backtest")

# Defaults for NIFTY — overridden from args in main()
SYMBOL = "NIFTY"
LOT_SIZE = 65
SHORT_OTM_PTS = 100
LONG_OTM_PTS = 300
STOP_MULT = 2.0
PCR_BULL = 1.3
PCR_BEAR = 0.7
STRIKE_STEP = 50

_session = requests.Session()
_session.headers.update(
    {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        ),
        "Referer": "https://www.nseindia.com/",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
    }
)
_cookie_warmed = False
_bhav_cache: dict[str, Optional[pd.DataFrame]] = {}


def _warm_cookie():
    global _cookie_warmed
    if not _cookie_warmed:
        _session.get("https://www.nseindia.com", timeout=10)
        time.sleep(1.5)
        _cookie_warmed = True


def _round_strike(price: float) -> int:
    return int(round(price / STRIKE_STEP) * STRIKE_STEP)


def _fetch_bhav(dt: date) -> Optional[pd.DataFrame]:
    """Download NSE F&O bhavcopy, parse to standard DataFrame, cache result."""
    key = dt.isoformat()
    if key in _bhav_cache:
        return _bhav_cache[key]

    _warm_cookie()
    url = "https://nsearchives.nseindia.com/content/fo/" f"BhavCopy_NSE_FO_0_0_0_{dt.strftime('%Y%m%d')}_F_0000.csv.zip"
    try:
        resp = _session.get(url, timeout=20)
        if resp.status_code != 200:
            log.debug("No bhavcopy for %s (status %d — likely holiday)", dt, resp.status_code)
            _bhav_cache[key] = None
            return None

        with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
            with zf.open(zf.namelist()[0]) as f:
                raw = pd.read_csv(f)

        # New NSE column names (2019+ format):
        #   TckrSymb, FinInstrmTp (IDO=index opt), XpryDt, StrkPric,
        #   OptnTp (CE/PE), OpnIntrst, SttlmPric, ClsPric, UndrlygPric
        opts = raw[raw["TckrSymb"].str.strip() == SYMBOL].copy()
        # Drop futures (NaN OptnTp)
        opts = opts[opts["OptnTp"].notna()]
        if opts.empty:
            _bhav_cache[key] = None
            return None

        nifty = opts  # keep var name for brevity; holds whichever symbol was requested
        nifty["XpryDt"] = pd.to_datetime(nifty["XpryDt"], errors="coerce")
        nifty["StrkPric"] = pd.to_numeric(nifty["StrkPric"], errors="coerce")
        nifty["OpnIntrst"] = pd.to_numeric(nifty["OpnIntrst"], errors="coerce").fillna(0)
        nifty["SttlmPric"] = pd.to_numeric(nifty["SttlmPric"], errors="coerce")
        nifty["ClsPric"] = pd.to_numeric(nifty["ClsPric"], errors="coerce")
        nifty["UndrlygPric"] = pd.to_numeric(nifty["UndrlygPric"], errors="coerce")
        nifty["OptnTp"] = nifty["OptnTp"].str.strip().str.upper()

        _bhav_cache[key] = nifty
        time.sleep(0.35)
        return nifty

    except Exception as exc:
        log.warning("Failed bhav fetch %s: %s", dt, exc)
        _bhav_cache[key] = None
        return None


def _get_spot(bhav: pd.DataFrame) -> Optional[float]:
    """Return NIFTY spot from UndrlygPric (median across all rows to smooth noise)."""
    vals = bhav["UndrlygPric"].dropna()
    return float(vals.median()) if not vals.empty else None


def _pcr(bhav: pd.DataFrame, expiry: date) -> Optional[float]:
    """PCR = total PE OI / total CE OI for the given weekly expiry."""
    exp_ts = pd.Timestamp(expiry)
    week = bhav[bhav["XpryDt"] == exp_ts]
    if week.empty:
        return None
    ce_oi = week[week["OptnTp"] == "CE"]["OpnIntrst"].sum()
    pe_oi = week[week["OptnTp"] == "PE"]["OpnIntrst"].sum()
    if ce_oi == 0:
        return None
    return pe_oi / ce_oi


def _option_price(bhav: pd.DataFrame, expiry: date, strike: int, opt_type: str) -> Optional[float]:
    """Return settle price for a specific contract. Falls back to close price."""
    exp_ts = pd.Timestamp(expiry)
    mask = (bhav["XpryDt"] == exp_ts) & (bhav["StrkPric"] == float(strike)) & (bhav["OptnTp"] == opt_type.upper())
    rows = bhav[mask]
    if rows.empty:
        return None
    settle = rows["SttlmPric"].iloc[0]
    if pd.isna(settle) or settle <= 0:
        close = rows["ClsPric"].iloc[0]
        return float(close) if not pd.isna(close) and close > 0 else None
    return float(settle)


def _find_weekly_expiry(bhav: pd.DataFrame, signal_day: date) -> Optional[date]:
    """
    Return the near-term weekly expiry from the bhavcopy.
    NSE shifted NIFTY weekly expiry from Thursday to Tuesday in Sep 2025.
    Rather than hardcoding a day-of-week, find the earliest expiry that is
    at least 2 calendar days away (so we have time to enter) and at most
    8 calendar days away (weekly, not monthly).
    """
    if bhav is None or bhav.empty:
        return None
    expiries = bhav["XpryDt"].dropna().dt.date.unique()
    candidates = sorted(e for e in expiries if 2 <= (e - signal_day).days <= 8)
    return candidates[0] if candidates else None


def _fetch_prior_spots(signal_day: date, n: int = 5) -> list[float]:
    """Return spot prices for the n trading days strictly before signal_day (oldest first)."""
    spots: list[float] = []
    d = signal_day - timedelta(days=1)
    attempts = 0
    while len(spots) < n and attempts < n + 10:
        attempts += 1
        if d.weekday() >= 5:
            d -= timedelta(days=1)
            continue
        bhav = _fetch_bhav(d)
        if bhav is not None:
            spot = _get_spot(bhav)
            if spot is not None:
                spots.append(spot)
        d -= timedelta(days=1)
    return list(reversed(spots))  # oldest first


def _ema(prices: list[float], n: int = 5) -> Optional[float]:
    """Exponential moving average of prices list (standard alpha = 2/(n+1))."""
    if len(prices) < 2:
        return None
    alpha = 2.0 / (n + 1)
    val = prices[0]
    for p in prices[1:]:
        val = alpha * p + (1 - alpha) * val
    return val


@dataclass
class WeekResult:
    week_start: date
    expiry: date
    pcr: float
    signal: str  # BULL_PUT | BEAR_CALL | SKIP
    spot: Optional[float]
    atm: Optional[int]
    short_strike: Optional[int]
    long_strike: Optional[int]
    entry_credit: Optional[float]
    exit_debit: Optional[float]
    stopped: bool = False
    pnl_per_lot: Optional[float] = None
    pnl_total: Optional[float] = None
    ema_val: Optional[float] = None
    note: str = ""


def _simulate_week(
    monday: date,
    ema_gate: bool = False,
    pcr_tighten: float = 0.0,
) -> WeekResult:
    nan = float("nan")
    _sentinel = date(1970, 1, 1)  # placeholder before expiry is known

    # Signal day: Monday (fallback Tuesday if holiday)
    bhav_sig = _fetch_bhav(monday)
    signal_day = monday
    if bhav_sig is None:
        tuesday = monday + timedelta(days=1)
        bhav_sig = _fetch_bhav(tuesday)
        signal_day = tuesday
    if bhav_sig is None:
        return WeekResult(monday, _sentinel, nan, "SKIP", None, None, None, None, None, None, note="no_data")

    expiry = _find_weekly_expiry(bhav_sig, signal_day)
    if expiry is None:
        return WeekResult(monday, _sentinel, nan, "SKIP", None, None, None, None, None, None, note="no_weekly_chain")

    pcr = _pcr(bhav_sig, expiry)
    if pcr is None:
        return WeekResult(monday, expiry, nan, "SKIP", None, None, None, None, None, None, note="no_weekly_chain")

    # Consecutive-loss pause: tighten thresholds for the week after a loss
    bull_thresh = PCR_BULL + pcr_tighten
    bear_thresh = PCR_BEAR - pcr_tighten

    if pcr > bull_thresh:
        signal = "BULL_PUT"
        opt_type = "PE"
    elif pcr < bear_thresh:
        signal = "BEAR_CALL"
        opt_type = "CE"
    else:
        skip_note = "consec_loss_filter" if pcr_tighten > 0 and (PCR_BEAR <= pcr <= PCR_BULL) is False else ""
        return WeekResult(monday, expiry, pcr, "SKIP", None, None, None, None, None, None, note=skip_note)

    spot = _get_spot(bhav_sig)
    if spot is None:
        return WeekResult(monday, expiry, pcr, signal, None, None, None, None, None, None, note="no_spot")

    # EMA gate: block entry when price trend contradicts PCR signal
    ema_val: Optional[float] = None
    if ema_gate:
        prior_spots = _fetch_prior_spots(signal_day)
        ema_val = _ema(prior_spots)
        if ema_val is not None:
            if signal == "BULL_PUT" and spot < ema_val:
                return WeekResult(
                    monday,
                    expiry,
                    pcr,
                    "SKIP",
                    spot,
                    None,
                    None,
                    None,
                    None,
                    None,
                    ema_val=ema_val,
                    note="ema_gate",
                )
            elif signal == "BEAR_CALL" and spot > ema_val:
                return WeekResult(
                    monday,
                    expiry,
                    pcr,
                    "SKIP",
                    spot,
                    None,
                    None,
                    None,
                    None,
                    None,
                    ema_val=ema_val,
                    note="ema_gate",
                )

    atm = _round_strike(spot)
    if signal == "BULL_PUT":
        short_strike = atm - SHORT_OTM_PTS
        long_strike = atm - LONG_OTM_PTS
    else:
        short_strike = atm + SHORT_OTM_PTS
        long_strike = atm + LONG_OTM_PTS

    short_entry = _option_price(bhav_sig, expiry, short_strike, opt_type)
    long_entry = _option_price(bhav_sig, expiry, long_strike, opt_type)

    if short_entry is None or long_entry is None:
        return WeekResult(
            monday, expiry, pcr, signal, spot, atm, short_strike, long_strike, None, None, note="no_entry_prices"
        )

    entry_credit = short_entry - long_entry
    if entry_credit <= 0:
        return WeekResult(
            monday, expiry, pcr, signal, spot, atm, short_strike, long_strike, entry_credit, None, note="zero_credit"
        )

    # Wednesday stop check
    # Stop fires when mark-to-market loss > 2× entry credit.
    # Spread value = cost to close (buy short back, sell long back).
    # Loss = spread_value_now - entry_credit (positive means we owe more than we took in).
    wednesday = monday + timedelta(days=2)
    bhav_wed = _fetch_bhav(wednesday)
    if bhav_wed is not None:
        short_wed = _option_price(bhav_wed, expiry, short_strike, opt_type)
        long_wed = _option_price(bhav_wed, expiry, long_strike, opt_type)
        if short_wed is not None and long_wed is not None:
            wed_spread_value = short_wed - long_wed
            mtm_loss = wed_spread_value - entry_credit
            if mtm_loss > STOP_MULT * entry_credit:
                ppl = entry_credit - wed_spread_value
                return WeekResult(
                    monday,
                    expiry,
                    pcr,
                    signal,
                    spot,
                    atm,
                    short_strike,
                    long_strike,
                    entry_credit,
                    wed_spread_value,
                    stopped=True,
                    pnl_per_lot=ppl,
                    pnl_total=ppl * LOT_SIZE,
                    note="stopped_wed",
                )

    # Thursday exit
    bhav_thu = _fetch_bhav(expiry)
    if bhav_thu is None:
        bhav_thu = bhav_wed  # last resort: use Wednesday prices

    if bhav_thu is None:
        return WeekResult(
            monday, expiry, pcr, signal, spot, atm, short_strike, long_strike, entry_credit, None, note="no_exit_data"
        )

    short_exit = _option_price(bhav_thu, expiry, short_strike, opt_type)
    long_exit = _option_price(bhav_thu, expiry, long_strike, opt_type)

    # OTM options on expiry day frequently settle at 0.05 — treat None as expired worthless
    if short_exit is None:
        short_exit = 0.05
    if long_exit is None:
        long_exit = 0.05

    exit_debit = max(short_exit - long_exit, 0.0)
    ppl = entry_credit - exit_debit
    return WeekResult(
        monday,
        expiry,
        pcr,
        signal,
        spot,
        atm,
        short_strike,
        long_strike,
        entry_credit,
        exit_debit,
        pnl_per_lot=ppl,
        pnl_total=ppl * LOT_SIZE,
    )


def main():
    parser = argparse.ArgumentParser(description="Backtest: PCR Contrarian Credit Spread")
    parser.add_argument("--start", default="2024-01-01")
    parser.add_argument("--end", default="2026-05-16")
    parser.add_argument("--symbol", default="NIFTY", choices=["NIFTY", "BANKNIFTY"], help="Index symbol to backtest")
    parser.add_argument("--lot-size", type=int, default=None, help="Lot size (default: 65 for NIFTY, 30 for BANKNIFTY)")
    parser.add_argument(
        "--strike-step", type=int, default=None, help="Strike grid step (default: 50 for NIFTY, 100 for BANKNIFTY)"
    )
    parser.add_argument(
        "--short-otm", type=int, default=100, help="Short leg distance from ATM in points (default: 100)"
    )
    parser.add_argument("--long-otm", type=int, default=300, help="Long leg distance from ATM in points (default: 300)")
    parser.add_argument(
        "--ema-gate",
        action="store_true",
        help="Skip entry when price action contradicts PCR signal (spot vs 5-day EMA)",
    )
    parser.add_argument(
        "--consec-loss-pause",
        action="store_true",
        help="After a losing week, tighten PCR thresholds by 0.1 for the following week",
    )
    parser.add_argument("--out", default=None, help="Save results CSV")
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()

    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)

    # Set module-level config from args
    global SYMBOL, LOT_SIZE, STRIKE_STEP, SHORT_OTM_PTS, LONG_OTM_PTS
    SYMBOL = args.symbol

    defaults = {"NIFTY": (65, 50), "BANKNIFTY": (30, 100)}
    default_lot, default_step = defaults[SYMBOL]
    LOT_SIZE = args.lot_size if args.lot_size is not None else default_lot
    STRIKE_STEP = args.strike_step if args.strike_step is not None else default_step
    SHORT_OTM_PTS = args.short_otm
    LONG_OTM_PTS = args.long_otm

    filters = []
    if args.ema_gate:
        filters.append("ema-gate")
    if args.consec_loss_pause:
        filters.append("consec-loss-pause")

    log.info(
        "Symbol=%s  LotSize=%d  StrikeStep=%d  Short=%dpts  Long=%dpts  Filters=[%s]",
        SYMBOL,
        LOT_SIZE,
        STRIKE_STEP,
        SHORT_OTM_PTS,
        LONG_OTM_PTS,
        ",".join(filters) if filters else "none",
    )

    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end)

    # Collect all Monday dates in range (expiry resolved dynamically per week)
    mondays: list[date] = []
    cur = start
    while cur <= end:
        if cur.weekday() == 0:
            mondays.append(cur)
        cur += timedelta(days=1)

    log.info("Running %d weeks (%s → %s)", len(mondays), start, end)
    n_weeks = len(mondays)

    results: list[WeekResult] = []
    last_traded: Optional[WeekResult] = None  # most recent week with a real trade outcome

    for i, mon in enumerate(mondays):
        log.info("[%d/%d] %s", i + 1, n_weeks, mon)

        # Consecutive-loss pause: tighten thresholds for one week after a loss
        pcr_tighten = 0.0
        if args.consec_loss_pause and last_traded is not None and last_traded.pnl_total is not None:
            if last_traded.pnl_total < 0:
                pcr_tighten = 0.1

        r = _simulate_week(mon, ema_gate=args.ema_gate, pcr_tighten=pcr_tighten)
        results.append(r)

        # Update last_traded only on weeks with an actual P&L outcome
        if r.pnl_total is not None:
            last_traded = r

    # ── Table ───────────────────────────────────────────────────────────
    rows = []
    for r in results:
        rows.append(
            {
                "week": r.week_start.isoformat(),
                "expiry": r.expiry.isoformat(),
                "pcr": f"{r.pcr:.2f}" if r.pcr == r.pcr else "—",
                "signal": r.signal,
                "spot": f"{r.spot:.0f}" if r.spot else "—",
                "ema": f"{r.ema_val:.0f}" if r.ema_val is not None else "—",
                "atm": r.atm or "—",
                "short": r.short_strike or "—",
                "long": r.long_strike or "—",
                "credit": f"{r.entry_credit:.1f}" if r.entry_credit is not None else "—",
                "exit": f"{r.exit_debit:.1f}" if r.exit_debit is not None else "—",
                "stop": "Y" if r.stopped else "",
                "pnl_pts": f"{r.pnl_per_lot:+.1f}" if r.pnl_per_lot is not None else "—",
                "pnl_inr": f"{r.pnl_total:+,.0f}" if r.pnl_total is not None else "—",
                "note": r.note,
            }
        )

    df = pd.DataFrame(rows)
    print("\n" + df.to_string(index=False))

    # ── Summary ─────────────────────────────────────────────────────────
    traded = [r for r in results if r.signal != "SKIP" and r.pnl_total is not None]
    skipped_neutral = [r for r in results if r.signal == "SKIP" and r.note not in ("ema_gate", "consec_loss_filter")]
    filtered_ema = [r for r in results if r.note == "ema_gate"]
    filtered_consec = [r for r in results if r.note == "consec_loss_filter"]
    no_data = [r for r in results if r.signal != "SKIP" and r.pnl_total is None]
    stops = [r for r in traded if r.stopped]
    wins = [r for r in traded if r.pnl_total > 0]
    bull_trades = [r for r in traded if r.signal == "BULL_PUT"]
    bear_trades = [r for r in traded if r.signal == "BEAR_CALL"]
    total_pnl = sum(r.pnl_total for r in traded)
    credits = [r.entry_credit for r in traded if r.entry_credit]
    avg_credit = sum(credits) / len(credits) if credits else 0

    # Worst drawdown (peak-to-trough on cumulative P&L)
    cum = 0.0
    peak = 0.0
    max_dd = 0.0
    for r in traded:
        cum += r.pnl_total
        if cum > peak:
            peak = cum
        dd = peak - cum
        if dd > max_dd:
            max_dd = dd

    print("\n" + "=" * 65)
    print(f"SUMMARY — {SYMBOL}  [filters: {','.join(filters) if filters else 'none'}]")
    print("=" * 65)
    print(f"Period               : {start} → {end}")
    print(f"Total weeks          : {n_weeks}")
    print(f"Traded               : {len(traded)}")
    print(f"  Bull Put Spread    : {len(bull_trades)}")
    print(f"  Bear Call Spread   : {len(bear_trades)}")
    print(f"Skipped (neutral PCR): {len(skipped_neutral)}")
    print(f"No price data        : {len(no_data)}")
    print(f"Stop-loss exits      : {len(stops)}")
    print(f"Win rate             : {len(wins)}/{len(traded)} = {len(wins)/max(len(traded),1)*100:.1f}%")
    print(f"Avg entry credit     : {avg_credit:.1f} pts")
    print(f"Total P&L (1 lot)    : ₹{total_pnl:+,.0f}")
    print(f"Avg P&L per trade    : ₹{total_pnl/max(len(traded),1):+,.0f}")
    print(f"Max drawdown (1 lot) : ₹{max_dd:,.0f}")

    # ── Filter precision report (only shown when filters are active) ─────
    # To get would-be outcomes for filtered weeks, run the baseline (no flags)
    # and compare — P&L is not available for skipped weeks in this run.
    if filtered_ema or filtered_consec:
        print()
        print("Filter precision (re-run without flags to see would-be outcomes):")
        if filtered_ema:
            print(f"  EMA gate filtered  : {len(filtered_ema)} weeks")
            for r in filtered_ema:
                spot_str = f"spot={r.spot:.0f}" if r.spot else "spot=—"
                ema_str = f"ema={r.ema_val:.0f}" if r.ema_val else "ema=—"
                orig_signal = "BULL_PUT" if r.pcr > PCR_BULL else "BEAR_CALL"
                print(f"    {r.week_start}  {orig_signal:<10}  pcr={r.pcr:.2f}  {spot_str}  {ema_str}")
        if filtered_consec:
            print(f"  Consec-loss filter : {len(filtered_consec)} weeks")
            for r in filtered_consec:
                print(f"    {r.week_start}  pcr={r.pcr:.2f} (in neutral band after tightening)")

    print()
    print("Caveats:")
    print("  - Entry/exit = Monday/Thursday EOD settle (not intraday 9:20/14:45)")
    print("  - Stop checked at Wednesday EOD only (not intraday)")
    print("  - Slippage + transaction costs not deducted (~₹200/leg/lot round trip)")
    print("  - 1 lot = 65 shares; multiply P&L for your lot count")
    if args.ema_gate:
        print("  - EMA gate: 5-day EMA computed from prior trading days' F&O bhavcopy spot")

    if args.out:
        df.to_csv(args.out, index=False)
        print(f"\nSaved to {args.out}")


if __name__ == "__main__":
    main()
