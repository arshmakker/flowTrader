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
