"""
MarketData adapter — wraps Shoonya API for the new trading system.

Provides get_ltp(), get_open_price(), get_nearest_expiry() and an
OHLCV bar accumulator for VWAP / RSI computations.
"""

import logging
import re
import time
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Dict, Optional

import pandas as pd

from trading_system.config import settings

logger = logging.getLogger(__name__)

# Suspicious-LTP fallback: if last_valid is older than this, return 0 so callers
# fall back to quote-book mid rather than marking against a stale price.
_LAST_VALID_LTP_TTL = 60.0  # seconds


@dataclass(frozen=True)
class QuoteBook:
    """Top-of-book snapshot for LIVE-06 (bid/ask visibility).

    ``bid``/``ask`` are the best bid/offer prices; ``bid_qty``/``ask_qty`` are
    the sizes available at those levels. ``mid`` is ``(bid+ask)/2`` — meaningful
    only when both sides are valid (use ``is_tradable``).
    """

    symbol: str
    bid: float
    ask: float
    bid_qty: int
    ask_qty: int

    @property
    def mid(self) -> float:
        return (self.bid + self.ask) / 2.0

    @property
    def spread(self) -> float:
        return max(self.ask - self.bid, 0.0)

    @property
    def is_tradable(self) -> bool:
        """Both sides quoted, non-zero size. Callers pre-checking liquidity
        for an order should still compare against their own qty requirement."""
        return self.bid > 0 and self.ask > 0 and self.ask >= self.bid and self.bid_qty > 0 and self.ask_qty > 0


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
        self._last_valid_option_ltp: Dict[str, tuple[float, float]] = {}  # symbol → (price, mono_ts)
        self._open_prices: Dict[str, float] = {}
        self._open_price_fallback: set[str] = set()
        self._ohlcv_bars: list[Dict] = []
        self._bars_cache: Optional[tuple[pd.DataFrame, float]] = None  # (df, mono_ts)

    # ── LTP ─────────────────────────────────────────────────────────────

    @staticmethod
    def _is_option_symbol_key(symbol_key: str) -> bool:
        core = str(symbol_key or "").split("|", 1)[-1]
        # Matches common option tradingsymbol forms like NIFTY13APR26C24850 / BANKNIFTY28APR26P51000.
        return bool(re.search(r"[CP]\d+$", core))

    @staticmethod
    def _is_valid_option_ltp(ltp: float) -> bool:
        return settings.PAPER_OPTION_LTP_MIN <= ltp <= settings.PAPER_OPTION_LTP_MAX

    def seed_option_ltp(self, symbol_key: str, price: float) -> None:
        """FixQ2: seed the last-valid-option-LTP cache for ``symbol_key``.

        Called at startup for each leg of a restored position using the leg's
        ``avg_price``. Without this, the first post-restore monitor cycle can
        hit a Shoonya ``lp``-is-spot response on a symbol with no cache entry,
        causing ``get_ltp`` to return 0.0 — which in turn makes the monitor's
        ``any(p <= 0) → return None`` early-exit fire and silently skip all
        exit logic for that instrument. Seeding with the entry avg_price gives
        a stale-but-finite fallback until a real clean tick arrives.
        """
        if not self._is_option_symbol_key(symbol_key):
            return
        if not self._is_valid_option_ltp(price):
            return
        self._last_valid_option_ltp[symbol_key] = (float(price), time.monotonic())

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
                if self._is_option_symbol_key(symbol_key):
                    if not self._is_valid_option_ltp(ltp):
                        # FixQ1: Shoonya sometimes returns the underlying spot
                        # in the ``lp`` field for option queries (~50/day across
                        # weeklies and monthlies). When ``lp`` fails the sanity
                        # filter, try the bid-ask midpoint from the same quote
                        # response — these fields are populated independently
                        # server-side and empirically stay clean ~98% of the
                        # time even when ``lp`` is bogus.
                        try:
                            bid = float(quote.get("bp1", 0) or 0)
                            ask = float(quote.get("sp1", 0) or 0)
                        except (TypeError, ValueError):
                            bid = ask = 0.0
                        if self._is_valid_option_ltp(bid) and self._is_valid_option_ltp(ask) and ask >= bid > 0:
                            mid = (bid + ask) / 2.0
                            logger.info(
                                "get_ltp: suspicious lp %.2f for %s; using bid-ask mid %.2f (bid=%.2f ask=%.2f)",
                                ltp,
                                symbol_key,
                                mid,
                                bid,
                                ask,
                            )
                            self._last_valid_option_ltp[symbol_key] = (mid, now)
                            self._ltp_cache[symbol_key] = (mid, now)
                            return mid

                        entry = self._last_valid_option_ltp.get(symbol_key)
                        if entry:
                            fallback, fallback_ts = entry
                            age = now - fallback_ts
                            if age <= _LAST_VALID_LTP_TTL:
                                logger.warning(
                                    "get_ltp: suspicious option LTP %.2f for %s; using last valid %.2f (age=%.0fs)",
                                    ltp,
                                    symbol_key,
                                    fallback,
                                    age,
                                )
                                self._ltp_cache[symbol_key] = (fallback, now)
                                return fallback
                            logger.warning(
                                "get_ltp: suspicious option LTP %.2f for %s; last valid %.2f is stale "
                                "(%.0fs > %.0fs TTL) — returning 0 for fresh mark",
                                ltp,
                                symbol_key,
                                fallback,
                                age,
                                _LAST_VALID_LTP_TTL,
                            )
                            return 0.0
                        logger.error(
                            "get_ltp: suspicious option LTP %.2f for %s; no valid fallback available", ltp, symbol_key
                        )
                        return 0.0
                    self._last_valid_option_ltp[symbol_key] = (ltp, now)
                self._ltp_cache[symbol_key] = (ltp, now)
                return ltp
            else:
                logger.warning("get_ltp: no quote or no 'lp' for %s (response=%s)", symbol_key, quote)
        except Exception:
            logger.warning("get_ltp failed for %s", symbol_key, exc_info=True)
        return 0.0

    def get_quote_book(self, symbol_key: str) -> Optional[QuoteBook]:
        """LIVE-06: top-of-book snapshot for an F&O symbol. Returns None on
        failure (missing token, API error, malformed response, or no bid/ask
        fields). Callers must handle None — this is the go/no-go signal for
        liquidity pre-checks (LIVE-25 Phase 1 / Phase 4).

        Uses Shoonya fields: ``bp1`` / ``sp1`` (best bid/ask), ``bq1`` / ``sq1``
        (sizes). The existing ``get_ltp`` FixQ1 path already reads bp1/sp1 as a
        fallback for a suspicious ``lp``, so the wire shape is known."""
        try:
            parts = symbol_key.split("|", 1)
            if len(parts) == 2:
                exchange, tsym = parts
                token = self._resolve_token(exchange, tsym)
                if token == tsym and exchange == "NFO":
                    logger.warning("get_quote_book: unresolved token for %s", symbol_key)
                    return None
                quote = self.api.get_quotes(exchange=exchange, token=token)
            else:
                quote = self.api.get_quotes(exchange="NSE", token=symbol_key)
        except Exception:
            logger.warning("get_quote_book: API call failed for %s", symbol_key, exc_info=True)
            return None

        if not quote:
            logger.warning("get_quote_book: empty quote for %s", symbol_key)
            return None

        try:
            bid = float(quote.get("bp1", 0) or 0)
            ask = float(quote.get("sp1", 0) or 0)
            bid_qty = int(float(quote.get("bq1", 0) or 0))
            ask_qty = int(float(quote.get("sq1", 0) or 0))
        except (TypeError, ValueError):
            logger.warning("get_quote_book: malformed fields for %s (quote=%s)", symbol_key, quote)
            return None

        return QuoteBook(
            symbol=symbol_key,
            bid=bid,
            ask=ask,
            bid_qty=bid_qty,
            ask_qty=ask_qty,
        )

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
                # BUG-06: accept ONLY q["o"] as a real open. Fall through to the
                # explicit LTP fallback path below if 'o' is missing/zero — that
                # path flags _open_price_fallback so the classifier downgrades
                # confidence to LOW.
                op = float(q.get("o") or 0)
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
                symbol,
                ltp,
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
                    (nfo["symbol"] == settings.NIFTY_SYMBOL) & (nfo["instrument"].isin(["OPTIDX", "FUTIDX"]))
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
                exchange="NSE",
                token=self.NIFTY_SPOT_TOKEN,
                starttime=start_epoch,
                interval=15,
            )

            if not bars or not isinstance(bars, list):
                return empty

            rows = []
            for b in bars:
                try:
                    rows.append(
                        {
                            "open": float(b.get("into", 0)),
                            "high": float(b.get("inth", 0)),
                            "low": float(b.get("intl", 0)),
                            "close": float(b.get("intc", 0)),
                            "volume": int(float(b.get("v", 0))),
                        }
                    )
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
        self._last_valid_option_ltp.clear()
        self._bars_cache = None
