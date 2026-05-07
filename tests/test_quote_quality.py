"""FixQ1 + FixQ2 regression tests — quote-quality fallback chain.

Shoonya's getquotes API intermittently returns the underlying spot in the
``lp`` field for option queries (~50/day across weeklies and monthlies). The
existing 0.05–5000 sanity filter catches this, but the fallback chain had a
gap: a cold _last_valid_option_ltp cache returned 0.0, which causes the IC
monitor's ``any(p <= 0) → return None`` early-exit to silently freeze all
exit logic.

FixQ1: use the same-response bid-ask midpoint (bp1/sp1) as a secondary
       fallback before last_valid-cache.
FixQ2: seed _last_valid_option_ltp at startup from each restored leg's
       avg_price, so cycle-one queries never return 0.
FixQ3: last_valid has a 60s TTL — stale entries return 0 so callers fall
       back to quote-book mid rather than marking against an arbitrarily
       old price.
"""

import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from trading_system.existing.market_data import _LAST_VALID_LTP_TTL, MarketData

# ── FixQ1 ────────────────────────────────────────────────────────────────────


def _mock_api_returning(quote):
    class MockAPI:
        def get_quotes(self, exchange=None, token=None):
            return quote

    return MockAPI()


def _mock_sm():
    class MockSM:
        def get_token_info(self, name, exchange="NSE"):
            # any non-None token so the NFO-unresolved guard in get_ltp
            # (token == tsym_or_name && exchange == "NFO" → return 0) doesn't fire
            return {"token": "99999"}

    return MockSM()


def test_suspicious_lp_uses_bid_ask_mid_when_available():
    """FixQ1: lp=24455 (spot garbage), bid=68.50, ask=70.00 → return 69.25."""
    quote = {"lp": "24455.95", "bp1": "68.50", "sp1": "70.00"}
    md = MarketData(_mock_api_returning(quote), _mock_sm())
    price = md.get_ltp("NFO|NIFTY21APR26C24450")
    assert price == 69.25
    # cache was seeded for next call (price component of tuple)
    assert md._last_valid_option_ltp["NFO|NIFTY21APR26C24450"][0] == 69.25


def test_suspicious_lp_falls_through_to_last_valid_if_bid_ask_also_bad():
    """If both lp and bid/ask are garbage, use cached last-valid."""
    quote = {"lp": "24455.95", "bp1": "0", "sp1": "0"}
    md = MarketData(_mock_api_returning(quote), _mock_sm())
    md.seed_option_ltp("NFO|NIFTY21APR26C24450", 85.0)
    price = md.get_ltp("NFO|NIFTY21APR26C24450")
    assert price == 85.0


def test_suspicious_lp_returns_zero_when_no_bid_ask_and_no_cache():
    """Cold cache + no bid/ask = return 0.0 (existing safe behavior)."""
    quote = {"lp": "24455.95"}
    md = MarketData(_mock_api_returning(quote), _mock_sm())
    price = md.get_ltp("NFO|NIFTY21APR26C24450")
    assert price == 0.0


def test_suspicious_lp_rejects_bid_ask_that_are_themselves_spot_garbage():
    """Degenerate case: bid AND ask both come back as spot values too.
    Midpoint would still be in the spot range and must be rejected."""
    quote = {"lp": "24455.95", "bp1": "24455.00", "sp1": "24456.00"}
    md = MarketData(_mock_api_returning(quote), _mock_sm())
    price = md.get_ltp("NFO|NIFTY21APR26C24450")
    # Midpoint (24455.50) fails _is_valid_option_ltp → fall through.
    # Cold cache → 0.0.
    assert price == 0.0


def test_suspicious_lp_rejects_crossed_book():
    """bid > ask is illegal (crossed book). Must not use a negative-spread mid."""
    quote = {"lp": "24455.95", "bp1": "100.00", "sp1": "90.00"}
    md = MarketData(_mock_api_returning(quote), _mock_sm())
    md.seed_option_ltp("NFO|NIFTY21APR26C24450", 95.0)
    price = md.get_ltp("NFO|NIFTY21APR26C24450")
    # Crossed → fall through to last_valid.
    assert price == 95.0


def test_clean_lp_still_preferred_over_mid():
    """If lp is valid, the bid-ask fallback must NOT run."""
    quote = {"lp": "100.00", "bp1": "50.00", "sp1": "150.00"}
    md = MarketData(_mock_api_returning(quote), _mock_sm())
    price = md.get_ltp("NFO|NIFTY21APR26C24450")
    assert price == 100.0


def test_fix_q1_does_not_affect_non_option_symbols():
    """Index spot quotes (NSE|Nifty 50) shouldn't enter the option-filter path."""
    quote = {"lp": "24455.95"}  # legitimate NIFTY spot value
    md = MarketData(_mock_api_returning(quote), _mock_sm())
    price = md.get_ltp("NSE|Nifty 50")
    assert price == 24455.95


# ── FixQ2 ────────────────────────────────────────────────────────────────────


def test_seed_option_ltp_populates_cache_for_valid_option():
    md = MarketData(None, None)
    md.seed_option_ltp("NFO|NIFTY21APR26C24350", 95.05)
    assert md._last_valid_option_ltp["NFO|NIFTY21APR26C24350"][0] == 95.05


def test_seed_option_ltp_ignores_non_option_symbol():
    """Spot/future symbol keys must not pollute the option cache."""
    md = MarketData(None, None)
    md.seed_option_ltp("NSE|Nifty 50", 24450.0)
    assert "NSE|Nifty 50" not in md._last_valid_option_ltp


def test_seed_option_ltp_rejects_out_of_range_price():
    """Garbage prices (e.g. 0 or spot-like) must not seed the cache."""
    md = MarketData(None, None)
    md.seed_option_ltp("NFO|NIFTY21APR26C24350", 0.0)
    md.seed_option_ltp("NFO|NIFTY21APR26C24350", 99999.0)
    assert "NFO|NIFTY21APR26C24350" not in md._last_valid_option_ltp


def test_seed_then_suspicious_lp_flows_through_last_valid():
    """End-to-end: seed at startup, receive a suspicious-lp response with no
    bid/ask, return the seeded value. This is the exact scenario FixQ2 is
    designed to rescue."""
    quote = {"lp": "24455.95"}  # no bid/ask either
    md = MarketData(_mock_api_returning(quote), _mock_sm())
    md.seed_option_ltp("NFO|NIFTY21APR26C24350", 95.05)
    price = md.get_ltp("NFO|NIFTY21APR26C24350")
    assert price == 95.05


# ── FixQ3 — last_valid TTL ───────────────────────────────────────────────────


def test_stale_last_valid_returns_zero():
    """FixQ3: last_valid older than TTL must not be used — return 0 so callers
    fall back to quote-book mid rather than marking against an old price.
    Root cause: 2026-05-07 BANKNIFTY SC LTP stuck at ~578 (entry price from
    11:02) while real price had moved to ~693 — daily cap never fired."""
    quote = {"lp": "56000.00", "bp1": "0", "sp1": "0"}  # both garbage
    md = MarketData(_mock_api_returning(quote), _mock_sm())
    stale_ts = time.monotonic() - (_LAST_VALID_LTP_TTL + 10)
    md._last_valid_option_ltp["NFO|BANKNIFTY26MAY26C57000"] = (578.0, stale_ts)
    price = md.get_ltp("NFO|BANKNIFTY26MAY26C57000")
    assert price == 0.0, "stale last_valid must not be returned — callers must get 0 and use quote-book mid"


def test_fresh_last_valid_still_returned():
    """Complement: last_valid within TTL continues to work as before."""
    quote = {"lp": "56000.00", "bp1": "0", "sp1": "0"}
    md = MarketData(_mock_api_returning(quote), _mock_sm())
    fresh_ts = time.monotonic() - (_LAST_VALID_LTP_TTL / 2)
    md._last_valid_option_ltp["NFO|BANKNIFTY26MAY26C57000"] = (620.0, fresh_ts)
    price = md.get_ltp("NFO|BANKNIFTY26MAY26C57000")
    assert price == 620.0
