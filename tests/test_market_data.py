"""Tests for MarketData — get_open_price fallback, is_open_price_reliable."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from trading_system.config import settings
from trading_system.existing.market_data import MarketData, QuoteBook


def test_get_open_price_fallback_to_ltp_and_unreliable():
    """When API returns no 'o' field, get_open_price falls back to LTP and marks symbol unreliable."""
    call_count = [0]

    class MockAPI:
        def get_quotes(self, exchange=None, token=None):
            call_count[0] += 1
            if call_count[0] == 1:
                return {}  # no open, no lp
            return {"lp": "24000.0"}  # LTP for fallback

    md = MarketData(MockAPI(), None)
    open_px = md.get_open_price(settings.NIFTY_SYMBOL)
    assert open_px == 24000.0
    assert md.is_open_price_reliable(settings.NIFTY_SYMBOL) is False


def test_is_open_price_reliable_true_when_open_from_api():
    """When API returns valid 'o', symbol is not in _open_price_fallback."""

    class MockAPI:
        def get_quotes(self, exchange=None, token=None):
            return {"o": "24100.0", "lp": "24200.0"}

    md = MarketData(MockAPI(), None)
    open_px = md.get_open_price(settings.NIFTY_SYMBOL)
    assert open_px == 24100.0
    assert md.is_open_price_reliable(settings.NIFTY_SYMBOL) is True


def test_quote_with_lp_but_no_o_does_not_silently_use_lp():
    """BUG-06: when quote payload has 'lp' but no 'o', get_open_price must NOT
    silently substitute lp as the open. The symbol must be flagged as unreliable
    so DayClassifier downgrades confidence to LOW.
    """
    call_count = [0]

    class MockAPI:
        def get_quotes(self, exchange=None, token=None):
            call_count[0] += 1
            # First call (for open): quote has lp but no o.
            if call_count[0] == 1:
                return {"lp": "24200.0"}  # current tick, NOT a real open
            # Subsequent calls (LTP fallback): return the same lp.
            return {"lp": "24200.0"}

    md = MarketData(MockAPI(), None)
    open_px = md.get_open_price(settings.NIFTY_SYMBOL)
    # Value matches lp (via the ltp fallback path), but the key assertion is
    # that the symbol is now flagged as unreliable — which would NOT be true
    # with the old silent-substitution behavior.
    assert open_px == 24200.0
    assert (
        md.is_open_price_reliable(settings.NIFTY_SYMBOL) is False
    ), "Symbol must be flagged unreliable when open was inferred from lp"


def test_empty_o_string_does_not_silently_use_lp():
    """BUG-06 edge: 'o' present as empty string or '0' must also fall through."""
    call_count = [0]

    class MockAPI:
        def get_quotes(self, exchange=None, token=None):
            call_count[0] += 1
            if call_count[0] == 1:
                return {"o": "", "lp": "24050.0"}
            return {"lp": "24050.0"}

    md = MarketData(MockAPI(), None)
    md.get_open_price(settings.NIFTY_SYMBOL)
    assert md.is_open_price_reliable(settings.NIFTY_SYMBOL) is False


# ── LIVE-06: get_quote_book ─────────────────────────────────────────────


class _FakeSymbolManager:
    """Returns a fixed token for any tradingsymbol so _resolve_token succeeds
    on NFO legs in tests. Without this, _resolve_token falls back to returning
    the tsym itself, which get_quote_book then treats as 'unresolved'."""

    def get_token_info(self, name, exchange=None):
        return {"token": "12345"}


def test_get_quote_book_parses_shoonya_response():
    """LIVE-06: happy path — bp1/sp1/bq1/sq1 map to QuoteBook fields, mid is
    computed as midpoint, is_tradable reports True for a clean book."""

    class MockAPI:
        def get_quotes(self, exchange=None, token=None):
            return {
                "lp": "20.5",
                "bp1": "20.0",
                "sp1": "21.0",
                "bq1": "1500",
                "sq1": "1800",
            }

    md = MarketData(MockAPI(), _FakeSymbolManager())
    book = md.get_quote_book("NFO|NIFTY25APR26C22150")
    assert book is not None
    assert isinstance(book, QuoteBook)
    assert book.bid == 20.0
    assert book.ask == 21.0
    assert book.bid_qty == 1500
    assert book.ask_qty == 1800
    assert book.mid == 20.5
    assert book.spread == 1.0
    assert book.is_tradable is True


def test_get_quote_book_zero_bid_marks_untradable():
    """LIVE-06 evidence scenario: Shoonya returns lp=20 but bp1=0 — book is
    structurally untradable on the short side. The check belongs with the
    caller (IC entry); get_quote_book must just report what the book said."""

    class MockAPI:
        def get_quotes(self, exchange=None, token=None):
            return {
                "lp": "20.0",
                "bp1": "0",
                "sp1": "21.0",
                "bq1": "0",
                "sq1": "1800",
            }

    md = MarketData(MockAPI(), _FakeSymbolManager())
    book = md.get_quote_book("NFO|NIFTY25APR26C22150")
    assert book is not None
    assert book.bid == 0.0
    assert book.is_tradable is False


def test_get_quote_book_returns_none_on_empty_response():
    class MockAPI:
        def get_quotes(self, exchange=None, token=None):
            return None

    md = MarketData(MockAPI(), _FakeSymbolManager())
    assert md.get_quote_book("NFO|NIFTY25APR26C22150") is None


def test_get_quote_book_returns_none_on_api_exception():
    class MockAPI:
        def get_quotes(self, exchange=None, token=None):
            raise RuntimeError("network down")

    md = MarketData(MockAPI(), _FakeSymbolManager())
    assert md.get_quote_book("NFO|NIFTY25APR26C22150") is None


def test_get_quote_book_tolerates_missing_qty_fields():
    """Some Shoonya responses omit bq1/sq1. Qty defaults to 0 — still returns
    a QuoteBook so the caller can see the prices, but is_tradable is False."""

    class MockAPI:
        def get_quotes(self, exchange=None, token=None):
            return {"bp1": "20.0", "sp1": "21.0"}  # no qty

    md = MarketData(MockAPI(), _FakeSymbolManager())
    book = md.get_quote_book("NFO|NIFTY25APR26C22150")
    assert book is not None
    assert book.bid_qty == 0
    assert book.ask_qty == 0
    assert book.is_tradable is False


def test_get_quote_book_returns_none_on_malformed_numeric_fields():
    class MockAPI:
        def get_quotes(self, exchange=None, token=None):
            return {"bp1": "not-a-number", "sp1": "21.0"}

    md = MarketData(MockAPI(), _FakeSymbolManager())
    assert md.get_quote_book("NFO|NIFTY25APR26C22150") is None


def test_get_quote_book_unresolved_nfo_token_returns_none():
    """When the symbol master doesn't know the tsym, _resolve_token returns
    the tsym itself — get_quote_book must refuse rather than asking the API
    for a token-that-is-actually-a-tradingsymbol."""

    class EmptySM:
        def get_token_info(self, name, exchange=None):
            return None

    class MockAPI:
        def get_quotes(self, exchange=None, token=None):
            raise AssertionError("should not be called when token is unresolved")

    md = MarketData(MockAPI(), EmptySM())
    assert md.get_quote_book("NFO|NIFTY25APR26C22150") is None


def test_get_quote_book_rejects_contaminated_option_bid_ask():
    """Regression (2026-05-08 FixQ3): Shoonya intermittently returns the underlying
    futures price in bp1/sp1 for option queries (same contamination as lp in FixQ1).
    A bid or ask > PAPER_OPTION_LTP_MAX (5000) must cause get_quote_book to return None
    rather than a QuoteBook whose is_tradable would pass and corrupt _harvest_fill_viable."""

    class MockAPI:
        def get_quotes(self, exchange=None, token=None):
            return {
                "bp1": "55568.0",  # futures price bled into option bid
                "sp1": "55600.0",  # futures price in option ask
                "bq1": "50",
                "sq1": "50",
            }

    md = MarketData(MockAPI(), _FakeSymbolManager())
    result = md.get_quote_book("NFO|BANKNIFTY26MAY26C56700")
    assert result is None, (
        "get_quote_book must return None when bid or ask exceeds PAPER_OPTION_LTP_MAX "
        "(futures-price contamination in bp1/sp1)"
    )


def test_get_quote_book_allows_valid_option_bid_ask():
    """Non-contaminated option bid/ask within [PAPER_OPTION_LTP_MIN, PAPER_OPTION_LTP_MAX]
    must still produce a QuoteBook normally."""

    class MockAPI:
        def get_quotes(self, exchange=None, token=None):
            return {"bp1": "562.0", "sp1": "563.5", "bq1": "200", "sq1": "150"}

    md = MarketData(MockAPI(), _FakeSymbolManager())
    result = md.get_quote_book("NFO|BANKNIFTY26MAY26C56700")
    assert result is not None
    assert result.bid == 562.0
    assert result.ask == 563.5


class _MockSM:
    def get_token_info(self, name, exchange=None):
        return {"token": "12345"}


def test_get_ltp_with_age_returns_zero_for_fresh_broker_response():
    """A fresh broker response (valid lp) returns age=0 — the price reflects
    the current tick."""

    class MockAPI:
        def get_quotes(self, exchange=None, token=None):
            return {"lp": "120.50", "bp1": "120.0", "sp1": "121.0"}

    md = MarketData(MockAPI(), _MockSM())
    price, age = md.get_ltp_with_age("NFO|NIFTY13APR26C24850")
    assert price == 120.50
    assert age == 0.0


def test_get_ltp_with_age_returns_positive_age_for_stale_fallback():
    """When Shoonya returns the underlying spot in lp (FixQ1) and bid/ask are
    also bogus, get_ltp_with_age falls back to last_valid and reports the age
    of that substitution. Callers combining multi-leg PnL refuse stale-mix."""
    import time

    class MockAPI:
        call = 0

        def get_quotes(self, exchange=None, token=None):
            self.call += 1
            if self.call == 1:
                return {"lp": "120.50", "bp1": "120.0", "sp1": "121.0"}
            # Subsequent calls: spot-leak in lp + bogus bid/ask → forces last_valid fallback
            return {"lp": "23500.0", "bp1": "0", "sp1": "0"}

    md = MarketData(MockAPI(), _MockSM())
    sym = "NFO|NIFTY13APR26C24850"

    # First call seeds last_valid at t0
    md.get_ltp_with_age(sym)
    # Wait beyond cache window so second call goes to broker, hits poison, falls back
    time.sleep(settings.LTP_CACHE_SEC + 0.05)

    price, age = md.get_ltp_with_age(sym)
    assert price == 120.50, "fallback must return last_valid, not the spot-leak"
    assert age >= settings.LTP_CACHE_SEC, f"age must reflect time since last_valid was recorded (got {age:.2f}s)"


def test_get_ltp_with_age_propagates_through_cache():
    """Cached stale-fallback entries report growing age across cache hits — the
    underlying market keeps moving while the substituted price stays frozen."""
    import time

    class MockAPI:
        call = 0

        def get_quotes(self, exchange=None, token=None):
            self.call += 1
            if self.call == 1:
                return {"lp": "120.50", "bp1": "120.0", "sp1": "121.0"}
            return {"lp": "23500.0", "bp1": "0", "sp1": "0"}

    md = MarketData(MockAPI(), _MockSM())
    sym = "NFO|NIFTY13APR26C24850"

    md.get_ltp_with_age(sym)
    time.sleep(settings.LTP_CACHE_SEC + 0.05)
    _, age_first = md.get_ltp_with_age(sym)  # hits poison, stores fallback

    # Immediate cache hit — age should still reflect last_valid origin, not the cache fetch
    _, age_cached = md.get_ltp_with_age(sym)
    assert age_cached >= age_first - 0.05, (
        "cached stale-fallback must keep reporting age from last_valid origin, "
        "not reset to 0 just because the cache fetch was recent"
    )


def test_reset_daily_clears_open_price_fallback():
    """reset_daily() clears _open_price_fallback."""
    call_count = [0]

    class MockAPI:
        def get_quotes(self, exchange=None, token=None):
            call_count[0] += 1
            if call_count[0] == 1:
                return {}
            return {"lp": "24000.0"}

    md = MarketData(MockAPI(), None)
    md.get_open_price(settings.NIFTY_SYMBOL)
    assert md.is_open_price_reliable(settings.NIFTY_SYMBOL) is False
    md.reset_daily()
    assert settings.NIFTY_SYMBOL not in md._open_price_fallback
