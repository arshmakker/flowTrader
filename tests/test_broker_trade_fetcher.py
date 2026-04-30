"""LIVE-08 broker-fetcher: pin the Shoonya trade-book → reconcile.Leg map.

These tests double as the field-name contract for ``get_trade_book()``. If the
broker ships under a different key on day-1 of shakedown, the failure mode is
visible here: legs come back empty or malformed, and the constants in
``broker_trade_fetcher`` are the single point of update.
"""
import os
import sys
from datetime import datetime
from unittest.mock import MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from trading_system.ops import broker_trade_fetcher as btf
from trading_system.ops import reconcile as rec


def _row(**overrides):
    """A canonical Shoonya trade-book row; overrides surgically replace fields."""
    base = {
        "tsym": "BANKNIFTY26MAY26C57600",
        "exch": "NFO",
        "trantype": "S",
        "flqty": "300",
        "flprc": "62.20",
        "fltm": "28-04-2026 11:35:42",
        "norenordno": "1234567890",
    }
    base.update(overrides)
    return base


def test_canonical_row_maps_to_leg():
    """Every documented field flows into the Leg in the expected place."""
    legs = btf.normalize_trade_book([_row()])
    assert len(legs) == 1
    leg = legs[0]
    assert leg.symbol == "NFO|BANKNIFTY26MAY26C57600"
    assert leg.side == "SELL"
    assert leg.quantity == 300
    assert leg.fill_price == 62.20
    assert leg.timestamp == datetime(2026, 4, 28, 11, 35, 42)
    # Costs come from contract note (LIVE-12), not trade book — emit zero.
    assert leg.costs == 0.0


def test_trantype_b_maps_to_buy():
    legs = btf.normalize_trade_book([_row(trantype="B")])
    assert len(legs) == 1 and legs[0].side == "BUY"


def test_trantype_full_words_also_accepted():
    """A normalising upstream wrapper might emit BUY/SELL instead of B/S."""
    legs = btf.normalize_trade_book([_row(trantype="BUY"), _row(trantype="sell")])
    assert [l.side for l in legs] == ["BUY", "SELL"]


def test_unknown_side_is_skipped():
    """Malformed side surfaces as a dropped row, not an exception."""
    legs = btf.normalize_trade_book([_row(trantype="X")])
    assert legs == []


def test_iso_timestamp_fallback_parses():
    """If a wrapper has pre-normalised fltm to ISO, that still works."""
    legs = btf.normalize_trade_book([_row(fltm="2026-04-28T11:35:42")])
    assert len(legs) == 1
    assert legs[0].timestamp == datetime(2026, 4, 28, 11, 35, 42)


def test_unparseable_timestamp_skipped():
    legs = btf.normalize_trade_book([_row(fltm="garbage")])
    assert legs == []


def test_missing_required_fields_skipped():
    """tsym, exch, qty, price, time are all required — any missing → skip."""
    cases = [
        _row(tsym=""),
        _row(exch=""),
        _row(flqty="0"),
        _row(flprc="0"),
        _row(fltm=""),
    ]
    assert btf.normalize_trade_book(cases) == []


def test_date_filter_drops_other_days():
    """date_iso filter keeps only matching exchange-fill dates."""
    rows = [
        _row(fltm="28-04-2026 09:30:00"),
        _row(fltm="27-04-2026 14:50:00"),
        _row(fltm="28-04-2026 14:14:59"),
    ]
    legs = btf.normalize_trade_book(rows, date_iso="2026-04-28")
    assert len(legs) == 2
    assert all(l.timestamp.date().isoformat() == "2026-04-28" for l in legs)


def test_none_response_returns_empty():
    """Noren SDK returns None for the no-trades / error envelope case."""
    api = MagicMock()
    api.get_trade_book.return_value = None
    assert btf.fetch_trade_book_legs(api, date_iso="2026-04-28") == []


def test_empty_list_response_returns_empty():
    api = MagicMock()
    api.get_trade_book.return_value = []
    assert btf.fetch_trade_book_legs(api) == []


def test_fetch_propagates_underlying_errors():
    """A broker-side fetch failure must NOT silently produce a partial set —
    reconciliation against a partial broker view is worse than no
    reconciliation."""
    api = MagicMock()
    api.get_trade_book.side_effect = ConnectionError("broker timeout")
    try:
        btf.fetch_trade_book_legs(api)
    except ConnectionError:
        pass
    else:
        raise AssertionError("ConnectionError must propagate, not be swallowed")


def test_non_dict_row_skipped_not_raised():
    """A list with a stray non-dict (e.g. error string) doesn't crash the
    normalizer — it emits the valid rows and drops the rest."""
    legs = btf.normalize_trade_book([_row(), "stat: error", None, _row(trantype="B")])
    assert len(legs) == 2
    assert {l.side for l in legs} == {"BUY", "SELL"}


def test_end_to_end_through_reconcile():
    """Drives the full pipeline: fake broker rows → fetcher → reconcile against
    matching engine legs. Same timestamp, same symbol, small price drift —
    must land in `matched`, not flagged (drift below 2% default)."""
    api = MagicMock()
    api.get_trade_book.return_value = [
        _row(tsym="NIFTY24APR26C22150", flprc="18.0", fltm="28-04-2026 10:00:03"),
    ]
    broker_legs = btf.fetch_trade_book_legs(api, date_iso="2026-04-28")

    engine_legs = [
        rec.Leg(
            timestamp=datetime(2026, 4, 28, 10, 0, 0),
            symbol="NFO|NIFTY24APR26C22150",
            side="SELL",
            quantity=300,
            fill_price=18.20,
            costs=5.0,
        ),
    ]
    report = rec.reconcile(engine_legs, broker_legs, date_iso="2026-04-28")

    assert len(report.matched) == 1
    pair = report.matched[0]
    assert round(pair.price_delta, 4) == round(18.0 - 18.20, 4)
    assert abs(pair.price_delta_pct) < 0.02
    assert report.flagged == []
    # Cost delta = 0 (broker) - 5 (engine) until LIVE-12 lands.
    assert pair.cost_delta == -5.0
