"""
Strategy Runner — helpers only (rebuild in progress per agent.md).

Provides: market hours, NIFTY spot, option chain, eligible expiries, India VIX, daily metrics.
No strategy or regime logic; build new system per docs/agent.md.
"""

import pandas as pd
import logging
import os
import json
from datetime import datetime, timedelta
from typing import Dict, Optional, List

try:
    from zoneinfo import ZoneInfo
    IST = ZoneInfo("Asia/Kolkata")
except ImportError:
    IST = None

logger = logging.getLogger(__name__)

# Minimum days to expiry for eligible expiries (was in iron_condor.config)
DAYS_TO_EXPIRY_MIN = 0

try:
    from trading_system.config import settings as _settings
    INDIA_VIX_TOKEN_NSE = _settings.INDIA_VIX_TOKEN
except ImportError:
    INDIA_VIX_TOKEN_NSE = "26017"

def _get_holiday_set_ist():
    """Returns holiday ISO dates ('YYYY-MM-DD') from settings if available."""
    try:
        from trading_system.config import settings as _settings_local
        holidays = getattr(_settings_local, 'TRADING_HOLIDAYS_IST', None)
        return set(holidays) if holidays else set()
    except Exception:
        return set()


def is_trading_day_ist(now: Optional[datetime] = None) -> bool:
    """True if weekday and not in the configured IST holiday list."""
    dt = get_now_ist() if now is None else now
    d = dt.date()
    if d.weekday() >= 5:
        return False
    return d.isoformat() not in _get_holiday_set_ist()



def _get_date_object(date_or_datetime):
    if date_or_datetime is None:
        return datetime.now().date()
    if isinstance(date_or_datetime, datetime):
        return date_or_datetime.date()
    if isinstance(date_or_datetime, type(datetime.now().date())):
        return date_or_datetime
    if isinstance(date_or_datetime, str):
        s = date_or_datetime.strip()
        try:
            if len(s) == 10 and s[4] == "-" and s[7] == "-":
                return datetime.strptime(s, "%Y-%m-%d").date()
            if "-" in s and len(s) >= 9:
                return datetime.strptime(s[:11], "%d-%b-%Y").date()
        except (ValueError, TypeError):
            pass
        return datetime.now().date()


def get_now_ist():
    """Current time in India Standard Time (IST)."""
    if IST is not None:
        return datetime.now(IST)
    return datetime.now()


def get_weekly_expiry(date=None):
    """Next weekly expiry (Thursday) for NIFTY."""
    if date is None:
        now = get_now_ist()
        reference_date = now.date()
        reference_datetime = now
    else:
        reference_datetime = date if isinstance(date, datetime) else datetime.combine(date, datetime.min.time())
        reference_date = reference_datetime.date() if isinstance(date, datetime) else date
    if reference_date.weekday() == 3:
        market_close = reference_datetime.replace(hour=15, minute=30, second=0, microsecond=0)
        if reference_datetime > market_close:
            return reference_date + timedelta(days=7)
        return reference_date
    days_ahead = 3 - reference_date.weekday()
    if days_ahead <= 0:
        days_ahead += 7
    return reference_date + timedelta(days=days_ahead)


def get_next_available_expiry(symbol_manager, preferred_date=None):
    """Next available expiry from symbol file."""
    try:
        preferred_date = _get_date_object(preferred_date or get_weekly_expiry())
        if symbol_manager.nse_fo is None:
            return preferred_date
        nifty_options = symbol_manager.nse_fo[
            (symbol_manager.nse_fo["instrument"] == "OPTIDX")
            & (symbol_manager.nse_fo["symbol"] == "NIFTY")
            & (symbol_manager.nse_fo["optiontype"].isin(["CE", "PE"]))
        ].copy()
        if nifty_options.empty:
            return preferred_date
        nifty_options["expiry_date"] = pd.to_datetime(nifty_options["expiry"], format="%d-%b-%Y", errors="coerce")
        valid_expiries = nifty_options["expiry_date"].dropna().dt.date.unique()
        if len(valid_expiries) == 0:
            return preferred_date
        future_expiries = [d for d in valid_expiries if d >= preferred_date]
        return min(future_expiries) if future_expiries else max(valid_expiries)
    except Exception as e:
        logger.error("Error getting next available expiry: %s", e, exc_info=True)
        return preferred_date if preferred_date else datetime.now().date()


def get_all_eligible_expiries(symbol_manager, max_expiries_to_check=10):
    """Eligible expiries from symbol file (future, >= DAYS_TO_EXPIRY_MIN days)."""
    try:
        if symbol_manager.nse_fo is None:
            return []
        nifty_options = symbol_manager.nse_fo[
            (symbol_manager.nse_fo["instrument"] == "OPTIDX")
            & (symbol_manager.nse_fo["symbol"] == "NIFTY")
            & (symbol_manager.nse_fo["optiontype"].isin(["CE", "PE"]))
        ].copy()
        if nifty_options.empty:
            return []
        nifty_options["expiry_date"] = pd.to_datetime(nifty_options["expiry"], format="%d-%b-%Y", errors="coerce")
        valid_expiries = sorted(nifty_options["expiry_date"].dropna().dt.date.unique())
        today = datetime.now().date()
        future = [d for d in valid_expiries if d >= today and (d - today).days >= DAYS_TO_EXPIRY_MIN]
        return future[:max_expiries_to_check]
    except Exception as e:
        logger.error("Error getting eligible expiries: %s", e, exc_info=True)
        return []


def get_nifty_spot_price(api, symbol_manager):
    """Current NIFTY spot price."""
    try:
        nifty_info = symbol_manager.get_token_info("Nifty 50", exchange="NSE")
        if not nifty_info:
            try:
                nifty_info = {"token": _settings.NIFTY_SPOT_TOKEN, "exchange": _settings.NIFTY_SPOT_EXCHANGE}
            except NameError:
                nifty_info = {"token": "26000", "exchange": "NSE"}
        if not nifty_info or "token" not in nifty_info:
            return None
        quote = api.get_quotes(exchange="NSE", token=nifty_info["token"])
        if quote and "lp" in quote:
            return float(quote["lp"])
            return None
    except Exception as e:
        logger.error("Error getting NIFTY spot price: %s", e)
        return None


def get_option_chain_data(api, symbol_manager, spot_price, expiry_date, count=50):
    """Option chain for NIFTY for given expiry (DataFrame with strike, option_type, ltp, bid, ask, etc.)."""
    try:
        expiry_date_obj = _get_date_object(expiry_date)
        expiry_str = expiry_date_obj.strftime("%d-%b-%Y").upper()
        if symbol_manager.nse_fo is None:
            return pd.DataFrame()
        nifty_options = symbol_manager.nse_fo[
            (symbol_manager.nse_fo["instrument"] == "OPTIDX")
            & (symbol_manager.nse_fo["symbol"] == "NIFTY")
            & (symbol_manager.nse_fo["optiontype"].isin(["CE", "PE"]))
        ].copy()
        nifty_options["expiry_date"] = pd.to_datetime(nifty_options["expiry"], format="%d-%b-%Y", errors="coerce")
        options_df = nifty_options[nifty_options["expiry_date"].dt.date == expiry_date_obj].copy()
        if options_df.empty:
            return pd.DataFrame()
        if "expiry_date" in options_df.columns:
            options_df = options_df.drop(columns=["expiry_date"])
        strike_interval = 50
        min_strike = int(spot_price) - count * strike_interval
        max_strike = int(spot_price) + count * strike_interval
        options_df = options_df[(options_df["strikeprice"] >= min_strike) & (options_df["strikeprice"] <= max_strike)].copy()
        if options_df.empty:
            return pd.DataFrame()
        chain_data = []
        for _, option_row in options_df.iterrows():
            try:
                tsym = option_row["tradingsymbol"]
                token = str(option_row["token"])
                strike = float(option_row["strikeprice"])
                option_type = option_row["optiontype"]
                quote = api.get_quotes(option_row.get("exchange", "NFO"), token)
                if not quote or strike <= 0:
                    continue
                bid = float(quote.get("bp1", 0))
                ask = float(quote.get("sp1", 0))
                ltp = float(quote.get("lp", 0))
                mid_price = (bid + ask) / 2 if bid > 0 and ask > 0 else ltp
                chain_data.append({
                    "strike": strike,
                    "option_type": option_type,
                    "tradingsymbol": tsym,
                    "ltp": ltp,
                    "bid": bid,
                    "ask": ask,
                    "mid_price": mid_price,
                    "delta": 0.0,
                    "oi": int(quote.get("oi", 0)),
                    "volume": int(quote.get("v", 0)),
                    "lot_size": int(option_row.get("lotsize", 50)),
                })
            except Exception:
                continue
        return pd.DataFrame(chain_data) if chain_data else pd.DataFrame()
    except Exception as e:
        logger.error("Error getting option chain: %s", e, exc_info=True)
        return pd.DataFrame()


def get_india_vix(api) -> Optional[float]:
    """Fetch India VIX from NSE."""
    if api is None:
        return None
    try:
        quote = api.get_quotes(exchange="NSE", token=INDIA_VIX_TOKEN_NSE)
        if not quote:
            return None
        vix = float(quote.get("lp", 0))
        return vix if vix > 0 else None
    except Exception:
        return None


def save_daily_metrics(metrics: Dict, date_str: Optional[str] = None) -> None:
    """Persist daily metrics to market_data_YYYYMMDD/daily_metrics.json."""
    try:
        when = date_str or datetime.now().strftime("%Y%m%d")
        data_dir = f"market_data_{when}"
        os.makedirs(data_dir, exist_ok=True)
        path = os.path.join(data_dir, "daily_metrics.json")
        existing = {}
        if os.path.exists(path):
            try:
                with open(path, "r") as f:
                    existing = json.load(f)
            except Exception:
                pass
        existing.update(metrics)
        existing.setdefault("date", when)
        with open(path, "w") as f:
            json.dump(existing, f, indent=2)
    except Exception as e:
        logger.debug("Could not save daily metrics: %s", e)


def is_market_closed_ist(now: Optional[datetime] = None) -> bool:
    """
    True if market is closed in IST.
    - All day on weekends / configured holidays
    - After 3:30 PM IST on trading days
    """
    dt = get_now_ist() if now is None else now
    if not is_trading_day_ist(dt):
        return True
    return dt >= dt.replace(hour=15, minute=30, second=0, microsecond=0)


def _parse_hhmm(s: str) -> "tuple[int, int]":
    h, m = s.strip().split(":")
    return int(h), int(m)


def _muhurat_window_for_date(d) -> Optional["tuple[datetime, datetime]"]:
    """If date ``d`` has a muhurat session configured, return (open, close)
    as tz-aware datetimes on that date. Otherwise None. Unknown-shape entries
    are skipped rather than raising — an operator typo shouldn't kill the
    loop."""
    try:
        from trading_system.config import settings as _settings_local
        sessions = getattr(_settings_local, "MUHURAT_SESSIONS", None) or []
    except Exception:
        return None

    iso = d.isoformat()
    for entry in sessions:
        if not isinstance(entry, dict):
            continue
        if entry.get("date") != iso:
            continue
        try:
            oh, om = _parse_hhmm(entry["open"])
            ch, cm = _parse_hhmm(entry["close"])
        except (KeyError, ValueError, TypeError):
            logger.warning("Malformed MUHURAT_SESSIONS entry; skipping: %r", entry)
            continue
        base = datetime.combine(d, datetime.min.time())
        if IST is not None:
            base = base.replace(tzinfo=IST)
        return (
            base.replace(hour=oh, minute=om),
            base.replace(hour=ch, minute=cm),
        )
    return None


def is_tradable_now(now: Optional[datetime] = None) -> "tuple[bool, str]":
    """LIVE-18: single authority for whether a new entry may be placed right now.

    Returns ``(is_tradable, reason)`` where ``reason`` is a short tag safe to
    log or include in structured IC_REJECT records. The regular session is the
    only tradable window on a normal trading day; pre-open, post-close,
    weekends, holidays, and muhurat-date-outside-window all refuse.
    """
    dt = get_now_ist() if now is None else now

    muhurat = _muhurat_window_for_date(dt.date())
    if muhurat is not None:
        m_open, m_close = muhurat
        # Muhurat sessions live on dates that may OR may not also appear in
        # TRADING_HOLIDAYS_IST. Either way, the muhurat window is the sole
        # tradable slice on that date.
        if m_open <= dt < m_close:
            return (True, "muhurat")
        return (False, "muhurat_closed")

    if dt.date().weekday() >= 5:
        return (False, "weekend")
    if dt.date().isoformat() in _get_holiday_set_ist():
        return (False, "holiday")

    try:
        from trading_system.config import settings as _cfg
        po_start = getattr(_cfg, "PRE_OPEN_START_IST", "09:00")
        po_end = getattr(_cfg, "PRE_OPEN_END_IST", "09:15")
    except ImportError:
        po_start, po_end = "09:00", "09:15"
    po_start_h, po_start_m = _parse_hhmm(po_start)
    po_end_h, po_end_m = _parse_hhmm(po_end)
    pre_open_start = dt.replace(hour=po_start_h, minute=po_start_m, second=0, microsecond=0)
    pre_open_end = dt.replace(hour=po_end_h, minute=po_end_m, second=0, microsecond=0)
    market_close = dt.replace(hour=15, minute=30, second=0, microsecond=0)

    if dt < pre_open_start:
        return (False, "before_open")
    if pre_open_start <= dt < pre_open_end:
        return (False, "pre_open")
    if dt >= market_close:
        return (False, "after_close")
    return (True, "regular")


def is_market_hours(now: Optional[datetime] = None) -> bool:
    """True if 9:15 AM - 3:30 PM IST on a trading day."""
    dt = get_now_ist() if now is None else now
    if not is_trading_day_ist(dt):
        return False
    market_open = dt.replace(hour=9, minute=15, second=0, microsecond=0)
    market_close = dt.replace(hour=15, minute=30, second=0, microsecond=0)
    return market_open <= dt <= market_close
