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


# ── Boundary case: limit == LTP must fill (incidents 2026-04-27) ────────────
# The IC entry code at iron_condor.py:695-700 submits SELL @ real_bid (offset=0
# ticks). Under selling pressure the broker prints LTP at the bid-touch, so
# `limit == real_bid == LTP` is the COMMON case for IC short-leg submission,
# not an edge case. The pre-fix synthetic-book check (paper_bid = LTP - slip)
# was strictly narrower than the real bid — `limit == LTP` deterministically
# canceled, breaking atomic-entry. Two incidents on 2026-04-27:
#   10:35:54 — limit=166.60, ltp=0 (suspicious-quote fallback chain) → cancel
#   10:59:19 — limit=147.50, ltp=147.50 (real) → cancel
# Both same root: paper_bid was below the limit by exactly `slip`. Fix: gate
# fills on `price` vs `LTP` directly; slip applies only to fill price.

def test_sell_lmt_at_ltp_fills(tmp_path):
    """SELL @ LTP must fill at LTP. This is what the IC short-leg code submits
    every entry (limit = real_bid; LTP often == real_bid)."""
    om = _om(tmp_path, {"NFO|X": 147.50})
    order = om.place_order("NFO|X", "SELL", 300, price_type="LMT", price=147.50)
    assert order["status"] == "COMPLETE"
    assert order["fill_qty"] == 300
    assert order["fill_price"] == 147.50


def test_buy_lmt_at_ltp_fills(tmp_path):
    """BUY @ LTP must fill at LTP — symmetric to SELL boundary case."""
    om = _om(tmp_path, {"NFO|X": 147.50})
    order = om.place_order("NFO|X", "BUY", 300, price_type="LMT", price=147.50)
    assert order["status"] == "COMPLETE"
    assert order["fill_qty"] == 300
    assert order["fill_price"] == 147.50


def test_sell_lmt_one_tick_above_ltp_cancels(tmp_path):
    """Boundary integrity: SELL one tick above LTP still cancels — we haven't
    broken the cancel path, just lifted the threshold from `LTP - slip` to LTP."""
    om = _om(tmp_path, {"NFO|X": 147.50})
    order = om.place_order("NFO|X", "SELL", 300, price_type="LMT", price=147.55)
    assert order["status"] == "CANCELED"


def test_buy_lmt_one_tick_below_ltp_cancels(tmp_path):
    """Symmetric: BUY one tick below LTP cancels."""
    om = _om(tmp_path, {"NFO|X": 147.50})
    order = om.place_order("NFO|X", "BUY", 300, price_type="LMT", price=147.45)
    assert order["status"] == "CANCELED"


def test_sell_lmt_fallback_fills_at_price_when_ltp_zero(tmp_path):
    """SELL LMT @ X with get_ltp=0: substitution sets ltp ← price, so
    price <= ltp holds and fill goes at price. Subsumed by general gate but
    kept as an explicit regression for the 10:35:54 incident path."""
    om = _om(tmp_path, {"NFO|X": 0.0})
    order = om.place_order("NFO|X", "SELL", 300, price_type="LMT", price=166.60)
    assert order["status"] == "COMPLETE"
    assert order["fill_price"] == 166.60


def test_buy_lmt_fallback_fills_at_price_when_ltp_zero(tmp_path):
    """BUY LMT @ X with get_ltp=0: symmetric to SELL fallback."""
    om = _om(tmp_path, {"NFO|X": 0.0})
    order = om.place_order("NFO|X", "BUY", 300, price_type="LMT", price=166.60)
    assert order["status"] == "COMPLETE"
    assert order["fill_price"] == 166.60


# ── MKT fallback (incident 2026-04-27 11:20:19) ─────────────────────────────
# The IC wing BUYs and unwind orders are MKT (no price_type), and a fresh
# strike's broker quote can be junk → market_data fallback chain exhausted →
# get_ltp returns 0. Without a `price=` fallback to the place_order call, MKT
# rejects with `missing_ltp` → wings not both filled → halt. Fix at the IC
# caller side: pass `price=books[*].ask` for wing BUYs and `price=books[*].bid`
# for wing unwinds. Tests below confirm the order manager honors the fallback
# in MKT path the same way it does in LMT path.

def test_buy_mkt_fallback_fills_at_price_when_ltp_zero(tmp_path):
    """BUY MKT with get_ltp=0 and `price=X` provided: substitution sets ltp ← X,
    then MKT path fills at ltp + slip. Must NOT reject as missing_ltp."""
    om = _om(tmp_path, {"NFO|X": 0.0})
    order = om.place_order("NFO|X", "BUY", 300, price=679.50)  # MKT default
    assert order["status"] == "COMPLETE"
    assert order["fill_qty"] == 300
    # Fill is the synthetic ask (price + slip), snapped to tick — strictly
    # above price but in a sane neighborhood.
    assert 679.50 < order["fill_price"] < 680.50


def test_sell_mkt_fallback_fills_at_price_when_ltp_zero(tmp_path):
    """SELL MKT with get_ltp=0 and `price=X` provided: fills at ltp - slip."""
    om = _om(tmp_path, {"NFO|X": 0.0})
    order = om.place_order("NFO|X", "SELL", 300, price=679.50)  # MKT default
    assert order["status"] == "COMPLETE"
    assert 678.50 < order["fill_price"] < 679.50


def test_mkt_without_price_when_ltp_zero_still_rejects(tmp_path):
    """No fallback price + no live LTP = the order genuinely has no anchor.
    Reject path stays — the IC caller is now responsible for supplying price=."""
    om = _om(tmp_path, {"NFO|X": 0.0})
    order = om.place_order("NFO|X", "BUY", 300)  # no price, no LTP
    assert order["status"] == "REJECTED"
    assert order["reason"] == "missing_ltp"
