"""
Live PCR computation via Shoonya get_option_chain API.

Single public function: get_weekly_pcr(api, spot, instrument) -> Optional[float]

Calls Shoonya once, filters to the nearest weekly expiry, returns pe_oi / ce_oi.
Result is cached on the api object for 5 minutes to avoid rate-limiter pressure
(PCR doesn't change meaningfully faster than that intraday).
"""

import logging
from datetime import date
from typing import Optional

from trading_system.config import settings

log = logging.getLogger(__name__)

_CACHE_TTL_SEC = 300  # 5 minutes


def _nearest_weekly_expiry(tsym_list: list[str], today: date) -> Optional[date]:
    """
    Detect the nearest weekly expiry from a list of option tsym strings.
    NSE shifted NIFTY weekly expiry from Thursday to Tuesday in Sep 2025 — we
    detect dynamically rather than hardcoding a day-of-week.

    Each tsym encodes the expiry in Shoonya format, e.g. 'NIFTY14MAY25C25000'.
    We parse the date portion and return the earliest expiry that is at least
    today and at most 8 calendar days away (weekly, not monthly).
    """
    import re

    month_map = {
        "JAN": 1,
        "FEB": 2,
        "MAR": 3,
        "APR": 4,
        "MAY": 5,
        "JUN": 6,
        "JUL": 7,
        "AUG": 8,
        "SEP": 9,
        "OCT": 10,
        "NOV": 11,
        "DEC": 12,
    }
    candidates: set[date] = set()
    for tsym in tsym_list:
        # Pattern: SYMBOL + DD + MMM + YY + (C|P) + STRIKE
        m = re.search(r"(\d{2})([A-Z]{3})(\d{2})[CP]", tsym)
        if not m:
            continue
        try:
            day = int(m.group(1))
            mon = month_map.get(m.group(2))
            year = 2000 + int(m.group(3))
            if mon is None:
                continue
            exp = date(year, mon, day)
        except (ValueError, TypeError):
            continue
        delta = (exp - today).days
        if 0 <= delta <= 8:
            candidates.add(exp)

    return min(candidates) if candidates else None


def get_weekly_pcr(api, spot: float, instrument: str = "NIFTY") -> Optional[float]:
    """
    Return PCR = pe_oi / ce_oi for the nearest weekly expiry.
    Uses a 5-minute session cache keyed on the api object.
    Returns None on any failure — caller must treat this as 'skip entry'.
    """
    import time

    cache_key = f"_pcr_cache_{instrument}"
    cached = getattr(api, cache_key, None)
    if cached is not None:
        pcr_val, ts = cached
        if time.time() - ts < _CACHE_TTL_SEC:
            log.debug("PCR cache hit for %s: %.3f", instrument, pcr_val)
            return pcr_val

    atm = int(round(spot / 50) * 50)
    try:
        result = api.get_option_chain(
            exchange="NFO",
            tradingsymbol=instrument,
            strikeprice=str(atm),
            count=settings.PCS_PCR_CHAIN_COUNT,
        )
    except Exception as exc:
        log.warning("get_option_chain failed for %s: %s", instrument, exc)
        return None

    if not result or result.get("stat") != "Ok":
        emsg = (result or {}).get("emsg") or (result or {}).get("rejreason") or repr(result)
        log.warning("get_option_chain returned non-Ok for %s: %s", instrument, emsg)
        return None

    values = result.get("values") or []
    if not values:
        log.warning("get_option_chain returned empty values for %s", instrument)
        return None

    today = date.today()
    tsym_list = [v.get("tsym", "") for v in values if v.get("tsym")]
    expiry = _nearest_weekly_expiry(tsym_list, today)

    if expiry is None:
        log.warning("Could not detect weekly expiry from option chain for %s", instrument)
        return None

    # Filter to near-weekly expiry by matching the date in tsym
    expiry_tag = expiry.strftime("%d%b%y").upper()  # e.g. "14MAY25"
    ce_oi = 0
    pe_oi = 0
    for row in values:
        tsym = row.get("tsym", "")
        if expiry_tag not in tsym:
            continue
        opt_type = row.get("optt", "").upper()
        try:
            oi = int(row.get("oi") or 0)
        except (ValueError, TypeError):
            oi = 0
        if opt_type == "CE":
            ce_oi += oi
        elif opt_type == "PE":
            pe_oi += oi

    if ce_oi == 0:
        log.warning("CE OI is zero for %s expiry %s — cannot compute PCR", instrument, expiry)
        return None

    pcr = pe_oi / ce_oi
    setattr(api, cache_key, (pcr, time.time()))
    log.info("PCR for %s (expiry %s): %.3f  [PE_OI=%d  CE_OI=%d]", instrument, expiry, pcr, pe_oi, ce_oi)
    return pcr
