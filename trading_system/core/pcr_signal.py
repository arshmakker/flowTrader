"""
Live PCR computation via Shoonya API.

Strategy:
  1. Resolve a valid weekly NFO option tsym via searchscrip (cached per session).
  2. Call get_option_chain to get CE/PE token list for that expiry (no OI in response).
  3. Call get_quotes per token to read the 'oi' field — the only way Shoonya exposes OI.
  4. Sum CE OI and PE OI, return pe_oi / ce_oi.

We fetch 10 strikes each side (20 tokens total) — enough for a reliable sentiment ratio
without burning rate-limit budget. Result is cached for 5 minutes.
"""

import logging
import time
from datetime import date, timedelta
from typing import Optional

log = logging.getLogger(__name__)

_CACHE_TTL_SEC = 300  # 5 minutes
_PCR_OI_COUNT = 10  # strikes each side for OI aggregation (20 get_quotes calls)
_pcr_cache: dict = {}  # instrument -> (pcr_val, timestamp)
_tsym_cache: dict = {}  # (instrument, atm) -> resolved NFO weekly option tsym

_MONTH_ABBR = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]


def _resolve_weekly_tsym(api, instrument: str, atm: int) -> Optional[str]:
    """
    Find a valid NFO weekly-expiry option tsym for get_option_chain.
    Shoonya's GetOptionChain requires a real contract symbol like NIFTY26MAY26C23950,
    not the bare index name "NIFTY".

    Scans the next 8 days for a valid tsym via searchscrip. Cached for the session
    (expiry only changes weekly).
    """
    cache_key = (instrument, atm)
    if cache_key in _tsym_cache:
        return _tsym_cache[cache_key]

    today = date.today()
    for delta in range(0, 9):
        d = today + timedelta(days=delta)
        mon = _MONTH_ABBR[d.month - 1]
        yr = str(d.year)[-2:]
        tsym = f"{instrument}{d.day:02d}{mon}{yr}C{atm}"
        try:
            result = api.searchscrip(exchange="NFO", searchtext=tsym)
        except Exception as exc:
            log.debug("searchscrip failed for %s: %s", tsym, exc)
            continue
        if not result or result.get("stat") != "Ok":
            continue
        for v in result.get("values") or []:
            if v.get("tsym") == tsym and v.get("instname") == "OPTIDX":
                log.info("Resolved weekly tsym for %s ATM=%d: %s", instrument, atm, tsym)
                _tsym_cache[cache_key] = tsym
                return tsym

    log.warning("Could not resolve weekly tsym for %s ATM=%d (searched %d days)", instrument, atm, 9)
    return None


def get_weekly_pcr(api, spot: float, instrument: str = "NIFTY") -> Optional[float]:
    """
    Return PCR = pe_oi / ce_oi for the nearest weekly expiry.
    Uses a 5-minute module-level cache. Returns None on failure — caller skips entry.
    """
    cache_key = instrument
    cached = _pcr_cache.get(cache_key)
    if cached is not None:
        pcr_val, ts = cached
        if time.time() - ts < _CACHE_TTL_SEC:
            log.debug("PCR cache hit for %s: %.3f", instrument, pcr_val)
            return pcr_val

    atm = int(round(spot / 50) * 50)

    # Step 1 — resolve a real weekly option tsym for this ATM strike
    tsym = _resolve_weekly_tsym(api, instrument, atm)
    if not tsym:
        return None

    # Step 2 — get CE/PE token list for this weekly expiry
    try:
        chain = api.get_option_chain(
            exchange="NFO",
            tradingsymbol=tsym,
            strikeprice=str(atm),
            count=_PCR_OI_COUNT,
        )
    except Exception as exc:
        log.warning("get_option_chain failed for %s: %s", instrument, exc)
        return None

    if not chain or chain.get("stat") != "Ok":
        emsg = (chain or {}).get("emsg") or (chain or {}).get("rejreason") or repr(chain)
        log.warning("get_option_chain non-Ok for %s: %s", instrument, emsg)
        return None

    rows = chain.get("values") or []
    if not rows:
        log.warning("get_option_chain returned empty values for %s", instrument)
        return None

    # Filter to only the resolved weekly expiry (tsym contains the date tag e.g. "26MAY26")
    expiry_tag = tsym[len(instrument) : len(instrument) + 7]  # e.g. "26MAY26"
    weekly_rows = [r for r in rows if expiry_tag in r.get("tsym", "")]
    if not weekly_rows:
        log.warning("No rows matching expiry %s in option chain for %s", expiry_tag, instrument)
        return None

    # Step 3 — get_quotes per token to read OI (get_option_chain does not carry OI)
    ce_oi = 0
    pe_oi = 0
    failed = 0
    for row in weekly_rows:
        token = row.get("token")
        opt_type = row.get("optt", "").upper()
        if not token or opt_type not in ("CE", "PE"):
            continue
        try:
            q = api.get_quotes(exchange="NFO", token=token)
        except Exception as exc:
            log.debug("get_quotes failed for token %s: %s", token, exc)
            failed += 1
            continue
        if not q or q.get("stat") != "Ok":
            failed += 1
            continue
        try:
            oi = int(q.get("oi") or 0)
        except (ValueError, TypeError):
            oi = 0
        if opt_type == "CE":
            ce_oi += oi
        else:
            pe_oi += oi

    if failed:
        log.debug("PCR: %d get_quotes calls failed (out of %d)", failed, len(weekly_rows))

    if ce_oi == 0:
        log.warning("CE OI is zero for %s expiry %s — cannot compute PCR", instrument, expiry_tag)
        return None

    pcr = pe_oi / ce_oi
    _pcr_cache[cache_key] = (pcr, time.time())
    log.info(
        "PCR for %s (expiry %s): %.3f  [PE_OI=%d  CE_OI=%d  rows=%d]",
        instrument,
        expiry_tag,
        pcr,
        pe_oi,
        ce_oi,
        len(weekly_rows),
    )
    return pcr
