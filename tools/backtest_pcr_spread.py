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
    url = f"https://nsearchives.nseindia.com/content/fo/BhavCopy_NSE_FO_0_0_0_{dt.strftime('%Y%m%d')}_F_0000.csv.zip"
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


def _spread_credit(
    bhav: pd.DataFrame, expiry: date, short_strike: int, long_strike: int, opt_type: str
) -> Optional[float]:
    """Net credit for one spread leg pair. Returns None if either price is missing."""
    s = _option_price(bhav, expiry, short_strike, opt_type)
    l = _option_price(bhav, expiry, long_strike, opt_type)
    if s is None or l is None:
        return None
    return s - l


def _prior_pcr(signal_day: date, expiry: date) -> Optional[float]:
    """PCR for `expiry` chain as seen from the Monday before signal_day.

    Tracks how OI in the same expiry evolved over the week — a rising PCR
    means fresh put buying this week, not just stale accumulated OI.
    """
    prior_monday = signal_day - timedelta(days=7)
    # Try prior Monday, then Tuesday if holiday
    for delta in (0, 1, 2):
        d = prior_monday + timedelta(days=delta)
        if d.weekday() >= 5:
            continue
        bhav = _fetch_bhav(d)
        if bhav is not None:
            val = _pcr(bhav, expiry)
            if val is not None:
                return val
    return None


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
    prior_pcr: Optional[float] = None
    delta_pcr: Optional[float] = None
    ic_secondary_credit: Optional[float] = None  # other-side credit when --ic active
    note: str = ""


def _simulate_week(
    monday: date,
    ema_gate: bool = False,
    pcr_tighten: float = 0.0,
    delta_pcr_min: float = 0.0,
    ic_mode: bool = False,
    no_pcr_filter: bool = False,
    adjust: str = "none",
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

    if no_pcr_filter:
        # Enter every week — use PCR only to label signal direction, not as a gate
        signal = "BULL_PUT" if pcr >= 1.0 else "BEAR_CALL"
        opt_type = "PE" if signal == "BULL_PUT" else "CE"
    elif pcr > bull_thresh:
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

    # Delta-PCR gate: require fresh OI movement, not just stale accumulated OI
    ppcr: Optional[float] = None
    dpcr: Optional[float] = None
    if delta_pcr_min > 0:
        ppcr = _prior_pcr(signal_day, expiry)
        if ppcr is not None:
            dpcr = pcr - ppcr
            # BULL_PUT: PCR must have *risen* (fresh put buying)
            # BEAR_CALL: PCR must have *fallen* (fresh call buying)
            if signal == "BULL_PUT" and dpcr < delta_pcr_min:
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
                    prior_pcr=ppcr,
                    delta_pcr=dpcr,
                    note="delta_pcr_gate",
                )
            elif signal == "BEAR_CALL" and dpcr > -delta_pcr_min:
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
                    prior_pcr=ppcr,
                    delta_pcr=dpcr,
                    note="delta_pcr_gate",
                )

    atm = _round_strike(spot)

    # Always define CE and PE legs — makes adjustment logic symmetric
    ce_short = atm + SHORT_OTM_PTS
    ce_long = atm + LONG_OTM_PTS
    pe_short = atm - SHORT_OTM_PTS
    pe_long = atm - LONG_OTM_PTS

    # Primary spread (PCR-signaled side)
    if signal == "BULL_PUT":
        short_strike, long_strike = pe_short, pe_long
    else:
        short_strike, long_strike = ce_short, ce_long

    primary_credit = _spread_credit(bhav_sig, expiry, short_strike, long_strike, opt_type)
    if primary_credit is None:
        return WeekResult(
            monday, expiry, pcr, signal, spot, atm, short_strike, long_strike, None, None, note="no_entry_prices"
        )
    if primary_credit <= 0:
        return WeekResult(
            monday, expiry, pcr, signal, spot, atm, short_strike, long_strike, primary_credit, None, note="zero_credit"
        )

    # Secondary spread (opposite side) in IC mode
    sec_credit: Optional[float] = None
    if ic_mode:
        sec_opt_type = "CE" if opt_type == "PE" else "PE"
        sec_short = ce_short if signal == "BULL_PUT" else pe_short
        sec_long = ce_long if signal == "BULL_PUT" else pe_long
        sec_credit = _spread_credit(bhav_sig, expiry, sec_short, sec_long, sec_opt_type)
        if sec_credit is not None and sec_credit <= 0:
            sec_credit = None

    # Track per-side credits for adjustment logic
    ce_credit = (
        (primary_credit if opt_type == "CE" else (sec_credit or 0.0))
        if ic_mode
        else (primary_credit if opt_type == "CE" else 0.0)
    )
    pe_credit = (
        (primary_credit if opt_type == "PE" else (sec_credit or 0.0))
        if ic_mode
        else (primary_credit if opt_type == "PE" else 0.0)
    )

    entry_credit = primary_credit + (sec_credit or 0.0)

    # ── Wednesday ──────────────────────────────────────────────────────
    wednesday = monday + timedelta(days=2)
    bhav_wed = _fetch_bhav(wednesday)

    # Per-side Wednesday values
    wed_ok = bhav_wed is not None
    ce_wed_val = _spread_credit(bhav_wed, expiry, ce_short, ce_long, "CE") if wed_ok and ic_mode else None
    pe_wed_val = _spread_credit(bhav_wed, expiry, pe_short, pe_long, "PE") if wed_ok and ic_mode else None
    primary_wed = _spread_credit(bhav_wed, expiry, short_strike, long_strike, opt_type) if wed_ok else None
    sec_wed = (
        _spread_credit(bhav_wed, expiry, sec_short, sec_long, sec_opt_type)
        if (wed_ok and ic_mode and sec_credit is not None)
        else None
    )

    adj_note = ""
    ce_closed_wed = False
    pe_closed_wed = False
    ce_wed_pnl = 0.0
    pe_wed_pnl = 0.0
    # Tracks rolled short strikes (may change under roll adjustment)
    ce_short_live = ce_short
    pe_short_live = pe_short
    ce_credit_live = ce_credit
    pe_credit_live = pe_credit

    if ic_mode and wed_ok:
        if adjust == "close-tested":
            # Close any side where spread has expanded beyond threshold × entry credit
            # Threshold=1.0 means we close as soon as we're at breakeven on that side
            CLOSE_THRESH = 1.0
            if ce_credit > 0 and ce_wed_val is not None and ce_wed_val > CLOSE_THRESH * ce_credit:
                ce_wed_pnl = ce_credit - ce_wed_val
                ce_closed_wed = True
                adj_note += "CE_closed"
            if pe_credit > 0 and pe_wed_val is not None and pe_wed_val > CLOSE_THRESH * pe_credit:
                pe_wed_pnl = pe_credit - pe_wed_val
                pe_closed_wed = True
                adj_note += "+PE_closed" if adj_note else "PE_closed"

        elif adjust == "roll-tested":
            # Roll the short leg of any tested side 100pt further OTM
            # Trigger: spread value has expanded past its entry credit (at breakeven)
            ROLL_THRESH = 1.0
            if ce_credit > 0 and ce_wed_val is not None and ce_wed_val > ROLL_THRESH * ce_credit:
                new_ce_short = ce_short + SHORT_OTM_PTS  # roll CE short further up
                buyback = _option_price(bhav_wed, expiry, ce_short, "CE")
                newsell = _option_price(bhav_wed, expiry, new_ce_short, "CE")
                if buyback is not None and newsell is not None:
                    roll_debit = buyback - newsell  # pay this to roll
                    ce_credit_live = ce_credit - roll_debit
                    ce_short_live = new_ce_short
                    adj_note += f"CE_rolled+{SHORT_OTM_PTS}"
            if pe_credit > 0 and pe_wed_val is not None and pe_wed_val > ROLL_THRESH * pe_credit:
                new_pe_short = pe_short - SHORT_OTM_PTS  # roll PE short further down
                buyback = _option_price(bhav_wed, expiry, pe_short, "PE")
                newsell = _option_price(bhav_wed, expiry, new_pe_short, "PE")
                if buyback is not None and newsell is not None:
                    roll_debit = buyback - newsell
                    pe_credit_live = pe_credit - roll_debit
                    pe_short_live = new_pe_short
                    adj_note += f"+PE_rolled-{SHORT_OTM_PTS}" if adj_note else f"PE_rolled-{SHORT_OTM_PTS}"

        else:
            # No adjustment — combined stop check
            if primary_wed is not None:
                wed_total = primary_wed + (sec_wed or 0.0)
                if wed_total - entry_credit > STOP_MULT * entry_credit:
                    ppl = entry_credit - wed_total
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
                        wed_total,
                        stopped=True,
                        pnl_per_lot=ppl,
                        pnl_total=ppl * LOT_SIZE,
                        prior_pcr=ppcr,
                        delta_pcr=dpcr,
                        ic_secondary_credit=sec_credit,
                        note="stopped_wed",
                    )
    elif wed_ok and primary_wed is not None:
        # Single-side stop check (non-IC)
        wed_total = primary_wed
        if wed_total - primary_credit > STOP_MULT * primary_credit:
            ppl = primary_credit - wed_total
            return WeekResult(
                monday,
                expiry,
                pcr,
                signal,
                spot,
                atm,
                short_strike,
                long_strike,
                primary_credit,
                wed_total,
                stopped=True,
                pnl_per_lot=ppl,
                pnl_total=ppl * LOT_SIZE,
                prior_pcr=ppcr,
                delta_pcr=dpcr,
                note="stopped_wed",
            )

    # ── Thursday exit ──────────────────────────────────────────────────
    bhav_thu = _fetch_bhav(expiry)
    if bhav_thu is None:
        bhav_thu = bhav_wed

    if bhav_thu is None:
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
            None,
            note="no_exit_data",
            prior_pcr=ppcr,
            delta_pcr=dpcr,
            ic_secondary_credit=sec_credit,
        )

    if ic_mode:
        # Per-side Thursday exit using possibly-rolled strikes
        if ce_closed_wed:
            ce_thu_pnl = ce_wed_pnl
        else:
            ce_thu_val = _spread_credit(bhav_thu, expiry, ce_short_live, ce_long, "CE") or 0.0
            ce_thu_pnl = ce_credit_live - ce_thu_val

        if pe_closed_wed:
            pe_thu_pnl = pe_wed_pnl
        else:
            pe_thu_val = _spread_credit(bhav_thu, expiry, pe_short_live, pe_long, "PE") or 0.0
            pe_thu_pnl = pe_credit_live - pe_thu_val

        ppl = ce_thu_pnl + pe_thu_pnl
        exit_debit = entry_credit - ppl
    else:
        primary_exit = _spread_credit(bhav_thu, expiry, short_strike, long_strike, opt_type) or 0.0
        ppl = primary_credit - primary_exit
        exit_debit = primary_exit

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
        max(exit_debit, 0.0),
        pnl_per_lot=ppl,
        pnl_total=ppl * LOT_SIZE,
        prior_pcr=ppcr,
        delta_pcr=dpcr,
        ic_secondary_credit=sec_credit,
        note=adj_note,
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
    parser.add_argument(
        "--delta-pcr",
        type=float,
        default=0.0,
        metavar="MIN",
        help="Only enter if PCR changed by ≥ MIN vs same expiry last Monday "
        "(e.g. 0.1). Filters stale OI, requires fresh positioning this week.",
    )
    parser.add_argument(
        "--ic",
        action="store_true",
        help="Iron Condor mode: sell both sides (primary PCR-signaled spread + "
        "opposing spread at same OTM distances). Doubles credit on winning weeks.",
    )
    parser.add_argument(
        "--no-pcr-filter",
        action="store_true",
        help="Remove PCR threshold gate — enter every week regardless of PCR value. "
        "Pure weekly premium-selling. Use with --ic for a symmetric weekly IC.",
    )
    parser.add_argument(
        "--adjust",
        default="none",
        choices=["none", "close-tested", "roll-tested"],
        help="Wednesday adjustment: close-tested = close whichever side expanded past "
        "its entry credit; roll-tested = roll that side's short 100pt further OTM.",
    )
    parser.add_argument(
        "--pcr-bear", type=float, default=None, help="Bear trigger: enter BEAR_CALL when PCR < this (default: 0.7)"
    )
    parser.add_argument(
        "--pcr-bull", type=float, default=None, help="Bull trigger: enter BULL_PUT when PCR > this (default: 1.3)"
    )
    parser.add_argument("--out", default=None, help="Save results CSV")
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()

    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)

    # Set module-level config from args
    global SYMBOL, LOT_SIZE, STRIKE_STEP, SHORT_OTM_PTS, LONG_OTM_PTS, PCR_BEAR, PCR_BULL
    SYMBOL = args.symbol
    if args.pcr_bear is not None:
        PCR_BEAR = args.pcr_bear
    if args.pcr_bull is not None:
        PCR_BULL = args.pcr_bull

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
    if args.delta_pcr > 0:
        filters.append(f"delta-pcr≥{args.delta_pcr}")
    if args.ic:
        filters.append("ic")
    if args.no_pcr_filter:
        filters.append("no-pcr-filter")
    if args.adjust != "none":
        filters.append(args.adjust)

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

        r = _simulate_week(
            mon,
            ema_gate=args.ema_gate,
            pcr_tighten=pcr_tighten,
            delta_pcr_min=args.delta_pcr,
            ic_mode=args.ic,
            no_pcr_filter=args.no_pcr_filter,
            adjust=args.adjust,
        )
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
                "dpcr": f"{r.delta_pcr:+.2f}" if r.delta_pcr is not None else "—",
                "signal": r.signal,
                "spot": f"{r.spot:.0f}" if r.spot else "—",
                "ema": f"{r.ema_val:.0f}" if r.ema_val is not None else "—",
                "atm": r.atm or "—",
                "short": r.short_strike or "—",
                "long": r.long_strike or "—",
                "credit": f"{r.entry_credit:.1f}" if r.entry_credit is not None else "—",
                "sec_cr": f"{r.ic_secondary_credit:.1f}" if r.ic_secondary_credit is not None else "—",
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
    skipped_neutral = [
        r for r in results if r.signal == "SKIP" and r.note not in ("ema_gate", "consec_loss_filter", "delta_pcr_gate")
    ]
    filtered_ema = [r for r in results if r.note == "ema_gate"]
    filtered_delta = [r for r in results if r.note == "delta_pcr_gate"]
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
    print(f"Win rate             : {len(wins)}/{len(traded)} = {len(wins) / max(len(traded), 1) * 100:.1f}%")
    print(f"Avg entry credit     : {avg_credit:.1f} pts")
    print(f"Total P&L (1 lot)    : ₹{total_pnl:+,.0f}")
    print(f"Avg P&L per trade    : ₹{total_pnl / max(len(traded), 1):+,.0f}")
    print(f"Max drawdown (1 lot) : ₹{max_dd:,.0f}")

    # ── Filter precision report (only shown when filters are active) ─────
    # To get would-be outcomes for filtered weeks, run the baseline (no flags)
    # and compare — P&L is not available for skipped weeks in this run.
    if filtered_ema or filtered_consec or filtered_delta:
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
        if filtered_delta:
            print(f"  Delta-PCR filtered : {len(filtered_delta)} weeks (stale OI, no fresh move)")
            for r in filtered_delta:
                orig_signal = "BULL_PUT" if r.pcr > PCR_BULL else "BEAR_CALL"
                dstr = f"{r.delta_pcr:+.2f}" if r.delta_pcr is not None else "—"
                print(f"    {r.week_start}  {orig_signal:<10}  pcr={r.pcr:.2f}  Δpcr={dstr}")

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
