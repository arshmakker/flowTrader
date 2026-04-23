"""LIVE-25: paper-side LMT order support.

Paper book is modeled as ask=ltp+slip, bid=ltp-slip. A BUY LMT fills only if
the limit is at or above the ask; a SELL LMT only if the limit is at or below
the bid. Otherwise the order is CANCELED (reason='limit_not_reached') which
is paper's equivalent of a live limit that times out without trading through.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from trading_system.config import settings
from trading_system.paper.paper_order_manager import PaperOrderManager


class _MD:
    def __init__(self, prices):
        self._prices = prices
    def get_ltp(self, sym):
        return self._prices.get(sym, 0.0)


def _om(tmp_path, prices):
    csv_path = str(tmp_path / "paper_orders.csv")
    return PaperOrderManager(_MD(prices), position_tracker=None, orders_csv_path=csv_path)


def test_buy_lmt_at_or_above_ask_fills(tmp_path):
    """Buy LMT @ 20.0 when paper ask is ~18.25 (LTP 18 + slip 0.25) → fills."""
    om = _om(tmp_path, {"NFO|X": 18.0})
    order = om.place_order("NFO|X", "BUY", 65, price_type="LMT", price=20.0)
    assert order["status"] == "COMPLETE"
    assert order["fill_qty"] == 65
    # Fill snaps to the ask (lower of limit and ask) — paper never worse than ask.
    assert order["fill_price"] <= 20.0


def test_buy_lmt_below_ask_cancels(tmp_path):
    """Buy LMT @ 17.0 when ask is ~18.25 → CANCELED, no position created."""
    om = _om(tmp_path, {"NFO|X": 18.0})
    order = om.place_order("NFO|X", "BUY", 65, price_type="LMT", price=17.0)
    assert order["status"] == "CANCELED"
    assert order["fill_qty"] == 0
    assert order["fill_price"] == 0.0
    assert order["reason"] == "limit_not_reached"
    assert order["limit_price"] == 17.0
    assert order["ltp_at_submit"] == 18.0


def test_sell_lmt_at_or_below_bid_fills(tmp_path):
    """Sell LMT @ 16.0 when paper bid is ~17.75 (LTP 18 - slip 0.25) → fills."""
    om = _om(tmp_path, {"NFO|X": 18.0})
    order = om.place_order("NFO|X", "SELL", 65, price_type="LMT", price=16.0)
    assert order["status"] == "COMPLETE"
    # Fill snaps to the bid (higher of limit and bid) — paper never worse than bid.
    assert order["fill_price"] >= 16.0


def test_sell_lmt_above_bid_cancels(tmp_path):
    """Sell LMT @ 19.0 when bid is ~17.75 → CANCELED."""
    om = _om(tmp_path, {"NFO|X": 18.0})
    order = om.place_order("NFO|X", "SELL", 65, price_type="LMT", price=19.0)
    assert order["status"] == "CANCELED"
    assert order["reason"] == "limit_not_reached"


def test_lmt_without_price_is_rejected(tmp_path):
    """LMT without a price is invalid input, not a fill-failure. Must REJECT."""
    om = _om(tmp_path, {"NFO|X": 18.0})
    order = om.place_order("NFO|X", "BUY", 65, price_type="LMT", price=0.0)
    assert order["status"] == "REJECTED"
    assert order["reason"] == "limit_price_missing"


def test_mkt_path_unchanged_by_lmt_addition(tmp_path):
    """Regression: default MKT behavior must not change. LTP 18 + slip → fills."""
    om = _om(tmp_path, {"NFO|X": 18.0})
    order = om.place_order("NFO|X", "BUY", 65)
    assert order["status"] == "COMPLETE"
    # Classic paper MKT fill: ltp + slip, snapped to tick.
    assert order["fill_price"] > 18.0


def test_canceled_lmt_persists_to_csv(tmp_path):
    """CANCELED LMTs must still appear in paper_orders.csv so reconciliation
    (LIVE-08) sees 'engine tried X limit, broker confirms no trade' rather
    than an unexplained missing row."""
    import csv
    csv_path = str(tmp_path / "paper_orders.csv")
    om = PaperOrderManager(_MD({"NFO|X": 18.0}), position_tracker=None, orders_csv_path=csv_path)

    om.place_order("NFO|X", "BUY", 65, price_type="LMT", price=17.0)

    with open(csv_path) as f:
        rows = list(csv.reader(f))
    assert len(rows) == 2  # header + one CANCELED row
    data_row = dict(zip(rows[0], rows[1]))
    assert data_row["status"] == "CANCELED"
    assert data_row["reason"] == "limit_not_reached"


def test_lmt_cancel_does_not_create_position(tmp_path):
    """Position tracker must not acquire a phantom position when the LMT
    doesn't fill — this is the whole point of the CANCELED branch."""
    from trading_system.paper.paper_position_tracker import PaperPositionTracker
    tracker = PaperPositionTracker()
    csv_path = str(tmp_path / "paper_orders.csv")
    om = PaperOrderManager(_MD({"NFO|X": 18.0}), position_tracker=tracker, orders_csv_path=csv_path)

    om.place_order("NFO|X", "BUY", 65, price_type="LMT", price=17.0)

    assert tracker._positions == {}  # no phantom position
