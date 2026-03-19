"""
MarketData adapter — wraps Shoonya API for the new trading system.

Provides get_ltp(), get_open_price(), get_nearest_expiry() and an
OHLCV bar accumulator for VWAP / RSI computations.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, date
from typing import Any, Dict, Optional

import pandas as pd

from trading_system.config import settings

logger = logging.getLogger(__name__)


class MarketData:
    """
    Thin layer over ShoonyaApiPy / SymbolManager for use by the new
    trading system modules.

    Usage:
        md = MarketData(api, symbol_manager)
        spot = md.get_ltp('NSE|Nifty 50')
        open_px = md.get_open_price('NIFTY')
    """

    @property
    def NIFTY_SPOT_TOKEN(self):
        return settings.NIFTY_SPOT_TOKEN

    @property
    def INDIA_VIX_TOKEN(self):
        return settings.INDIA_VIX_TOKEN

    def __init__(self, api: Any, symbol_manager: Any) -> None:
        self.api = api
        self.sm = symbol_manager
        self._ltp_cache: Dict[str, tuple[float, float]] = {}  # symbol → (ltp, mono_ts)
        self._open_prices: Dict[str, float] = {}
        self._open_price_fallback: set[str] = set()
        self._ohlcv_bars: list[Dict] = []
        self._bars_cache: Optional[tuple[pd.DataFrame, float]] = None  # (df, mono_ts)

    # ── LTP ─────────────────────────────────────────────────────────────

    def get_ltp(self, symbol_key: str) -> float:
        """
        Get last-traded price. Caches for 2 seconds to reduce API calls.
        symbol_key: 'NSE|Nifty 50', 'NFO|NIFTY25MAR24000CE', etc.
        """
        now = time.monotonic()
        if symbol_key in self._ltp_cache:
            cached_ltp, ts = self._ltp_cache[symbol_key]
            if (now - ts) < settings.LTP_CACHE_SEC:
                return cached_ltp

        try:
            parts = symbol_key.split("|", 1)
            if len(parts) == 2:
                exchange, tsym_or_name = parts
                token = self._resolve_token(exchange, tsym_or_name)
                if token == tsym_or_name and exchange == "NFO":
                    logger.warning("get_ltp: could not resolve token for %s — symbol not in master", symbol_key)
                    return 0.0
                quote = self.api.get_quotes(exchange=exchange, token=token)
            else:
                quote = self.api.get_quotes(exchange="NSE", token=symbol_key)

            if quote and "lp" in quote:
                ltp = float(quote["lp"])
                self._ltp_cache[symbol_key] = (ltp, now)
                return ltp
            else:
                logger.warning("get_ltp: no quote or no 'lp' for %s (response=%s)", symbol_key, quote)
        except Exception:
            logger.warning("get_ltp failed for %s", symbol_key, exc_info=True)
        return 0.0

    def _resolve_token(self, exchange: str, name: str) -> str:
        """Resolve a trading symbol or index name to its token."""
        spot_name = settings.NIFTY_SPOT_KEY.split("|", 1)[-1] if "|" in settings.NIFTY_SPOT_KEY else "Nifty 50"
        vix_name = settings.INDIA_VIX_KEY.split("|", 1)[-1] if "|" in settings.INDIA_VIX_KEY else "India VIX"
        if name == spot_name:
            return self.NIFTY_SPOT_TOKEN
        if name == vix_name:
            return self.INDIA_VIX_TOKEN
        if self.sm is not None:
            info = self.sm.get_token_info(name, exchange=exchange)
            if info and "token" in info:
                return str(info["token"])
        return name

    # ── Open price ──────────────────────────────────────────────────────

    def get_open_price(self, symbol: str) -> float:
        """
        Today's opening price for an index. Fetched once then cached.
        symbol: 'NIFTY' or 'BANKNIFTY'
        """
        if symbol in self._open_prices and self._open_prices[symbol] > 0:
            return self._open_prices[symbol]
        try:
            token = self.NIFTY_SPOT_TOKEN
            q = self.api.get_quotes(exchange="NSE", token=token)
            if q:
                op = float(q.get("o", 0) or q.get("lp", 0))
                if op > 0:
                    self._open_prices[symbol] = op
                    return op
        except Exception:
            logger.debug("get_open_price failed for %s", symbol, exc_info=True)
        ltp = self.get_ltp(settings.NIFTY_SPOT_KEY)
        if ltp > 0:
            logger.warning(
                "get_open_price(%s): API 'o' field missing — falling back to LTP %.2f. "
                "Day classification will see 0%% move and always classify as RANGING.",
                symbol, ltp,
            )
            self._open_price_fallback.add(symbol)
            return ltp
        return 0.0

    # ── Nearest expiry ──────────────────────────────────────────────────

    def get_nearest_expiry(self) -> Optional[date]:
        """Get nearest expiry from NFO.csv via SymbolManager, then strategy_runner, then fallback."""
        # Primary: read from loaded NFO data (no Thursday assumption)
        if self.sm is not None and self.sm.nse_fo is not None:
            try:
                nfo = self.sm.nse_fo
                idx_opts = nfo[
                    (nfo["symbol"] == settings.NIFTY_SYMBOL)
                    & (nfo["instrument"].isin(["OPTIDX", "FUTIDX"]))
                ]
                if not idx_opts.empty and "expiry" in idx_opts.columns:
                    today = datetime.today().date()
                    expiries = pd.to_datetime(idx_opts["expiry"], format="%d-%b-%Y", errors="coerce")
                    future = expiries[expiries.dt.date >= today]
                    if not future.empty:
                        nearest = future.min().date()
                        return nearest
            except Exception:
                logger.debug("get_nearest_expiry from NFO.csv failed", exc_info=True)

        # Secondary: strategy_runner helper
        try:
            from strategy_runner import get_next_available_expiry
            return get_next_available_expiry(self.sm)
        except Exception:
            pass

        # Last resort: next Thursday (kept for offline/test scenarios only)
        from datetime import timedelta
        today = datetime.today().date()
        days_ahead = (3 - today.weekday()) % 7
        if days_ahead == 0:
            days_ahead = 7
        return today + timedelta(days=days_ahead)

    # ── OHLCV bar accumulation ──────────────────────────────────────────

    def _fetch_intraday_bars(self) -> pd.DataFrame:
        """Fetch today's 15-min OHLCV bars for NIFTY from Shoonya API, cached 60s."""
        now = time.monotonic()
        if self._bars_cache is not None:
            df, ts = self._bars_cache
            if (now - ts) < settings.OHLCV_CACHE_SEC and not df.empty:
                return df

        empty = pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
        try:
            today_start = datetime.now().replace(hour=9, minute=15, second=0, microsecond=0)
            start_epoch = str(int(today_start.timestamp()))

            bars = self.api.get_time_price_series(
                exchange="NSE", token=self.NIFTY_SPOT_TOKEN,
                starttime=start_epoch, interval=15,
            )

            if not bars or not isinstance(bars, list):
                return empty

            rows = []
            for b in bars:
                try:
                    rows.append({
                        "open": float(b.get("into", 0)),
                        "high": float(b.get("inth", 0)),
                        "low": float(b.get("intl", 0)),
                        "close": float(b.get("intc", 0)),
                        "volume": int(float(b.get("v", 0))),
                    })
                except (ValueError, TypeError):
                    continue

            df = pd.DataFrame(rows) if rows else empty
            self._bars_cache = (df, now)
            return df
        except Exception:
            logger.warning("Failed to fetch intraday bars from API", exc_info=True)
            return empty

    def get_ohlcv_df(self) -> pd.DataFrame:
        """
        Return 15-min OHLCV bars. Tries Shoonya API first, then falls
        back to manually accumulated bars.
        """
        df = self._fetch_intraday_bars()
        if not df.empty:
            return df
        if not self._ohlcv_bars:
            return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
        return pd.DataFrame(self._ohlcv_bars)

    def record_bar(self, bar: Dict) -> None:
        self._ohlcv_bars.append(bar)

    def get_close_series(self) -> pd.Series:
        df = self.get_ohlcv_df()
        if df.empty or "close" not in df.columns:
            return pd.Series(dtype=float)
        return df["close"]

    def is_open_price_reliable(self, symbol: str) -> bool:
        return symbol not in self._open_price_fallback

    def get_lot_size(self, symbol_key: str) -> int:
        """Get lot size for a symbol."""
        try:
            parts = symbol_key.split("|", 1)
            if len(parts) == 2:
                exchange, tsym_or_name = parts
                if self.sm is not None:
                    info = self.sm.get_token_info(tsym_or_name, exchange=exchange)
                    if info and "lotsize" in info:
                        return int(info["lotsize"])
        except Exception:
            logger.debug("get_lot_size failed for %s", symbol_key)
        
        # Fallback to settings
        if "NIFTY" in symbol_key:
            return settings.NIFTY_LOT_SIZE
        if "BANKNIFTY" in symbol_key:
            return settings.BANKNIFTY_LOT_SIZE
        return 1

    def reset_daily(self) -> None:
        """Call at start of each day."""
        self._open_prices.clear()
        self._open_price_fallback.clear()
        self._ohlcv_bars.clear()
        self._ltp_cache.clear()
        self._bars_cache = None
