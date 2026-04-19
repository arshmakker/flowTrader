"""Tests for MarketData — get_open_price fallback, is_open_price_reliable."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from trading_system.config import settings
from trading_system.existing.market_data import MarketData


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
    assert md.is_open_price_reliable(settings.NIFTY_SYMBOL) is False, (
        "Symbol must be flagged unreliable when open was inferred from lp"
    )


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
    open_px = md.get_open_price(settings.NIFTY_SYMBOL)
    assert md.is_open_price_reliable(settings.NIFTY_SYMBOL) is False


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
