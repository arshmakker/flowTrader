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

    NIFTY_SPOT_TOKEN = "26000"
    INDIA_VIX_TOKEN = "26017"

    def __init__(self, api: Any, symbol_manager: Any) -> None:
        self.api = api
        self.sm = symbol_manager
        self._ltp_cache: Dict[str, tuple[float, float]] = {}  # symbol → (ltp, mono_ts)
        self._open_prices: Dict[str, float] = {}
        self._ohlcv_bars: list[Dict] = []

    # ── LTP ─────────────────────────────────────────────────────────────

    def get_ltp(self, symbol_key: str) -> float:
        """
        Get last-traded price. Caches for 2 seconds to reduce API calls.
        symbol_key: 'NSE|Nifty 50', 'NFO|NIFTY25MAR24000CE', etc.
        """
        now = time.monotonic()
        if symbol_key in self._ltp_cache:
            cached_ltp, ts = self._ltp_cache[symbol_key]
            if (now - ts) < 2.0:
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
        if name == "Nifty 50":
            return self.NIFTY_SPOT_TOKEN
        if name == "India VIX":
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
        ltp = self.get_ltp("NSE|Nifty 50")
        return ltp if ltp > 0 else 0.0

    # ── Nearest expiry ──────────────────────────────────────────────────

    def get_nearest_expiry(self) -> Optional[date]:
        """Delegate to strategy_runner helper if available."""
        try:
            from strategy_runner import get_next_available_expiry
            return get_next_available_expiry(self.sm)
        except Exception:
            from datetime import timedelta
            today = datetime.today().date()
            days_ahead = 3 - today.weekday()
            if days_ahead <= 0:
                days_ahead += 7
            return today + timedelta(days=days_ahead)

    # ── OHLCV bar accumulation ──────────────────────────────────────────

    def get_ohlcv_df(self) -> pd.DataFrame:
        """
        Return accumulated 15-min bars as DataFrame.
        For now returns an empty DF if no bars collected.
        The orchestrator should call record_bar() periodically.
        """
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

    def reset_daily(self) -> None:
        """Call at start of each day."""
        self._open_prices.clear()
        self._ohlcv_bars.clear()
        self._ltp_cache.clear()
