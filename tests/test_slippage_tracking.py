"""Regression tests for slippage tracking fields (ltp_at_submit, expected_price).

These fields enable slippage derivation: slippage = fill_price - expected_price
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from trading_system.paper.paper_order_manager import _ORDERS_CSV_COLUMNS, PaperOrderManager


class _MD:
    def __init__(self, prices):
        self._prices = prices

    def get_ltp(self, sym):
        return self._prices.get(sym, 0.0)

    def get_quote_book(self, sym):
        class QB:
            def __init__(self, bid, ask):
                self.bid = bid
                self.ask = ask

        return QB(100.0, 102.0)


def _om(tmp_path, prices):
    csv_path = str(tmp_path / "paper_orders.csv")
    return PaperOrderManager(_MD(prices), position_tracker=None, orders_csv_path=csv_path)


class TestCSVSchema:
    """Verify CSV columns include new slippage tracking fields."""

    def test_csv_columns_contain_expected_fields(self):
        assert "ltp_at_submit" in _ORDERS_CSV_COLUMNS
        assert "expected_price" in _ORDERS_CSV_COLUMNS

    def test_csv_columns_order(self):
        idx_fill = _ORDERS_CSV_COLUMNS.index("fill_price")
        idx_ltp = _ORDERS_CSV_COLUMNS.index("ltp_at_submit")
        idx_exp = _ORDERS_CSV_COLUMNS.index("expected_price")
        assert idx_ltp == idx_fill + 1
        assert idx_exp == idx_ltp + 1


class TestMKTOrderSlippage:
    """MKT orders: expected_price = ltp_at_submit (before slippage)."""

    def test_mkt_buy_order_has_expected_price_equal_to_ltp(self, tmp_path):
        om = _om(tmp_path, {"NFO|X": 100.0})
        order = om.place_order("NFO|X", "BUY", 65, price_type="MKT")
        assert order["status"] == "COMPLETE"
        assert order["ltp_at_submit"] == 100.0
        assert order["expected_price"] == 100.0

    def test_mkt_sell_order_has_expected_price_equal_to_ltp(self, tmp_path):
        om = _om(tmp_path, {"NFO|X": 100.0})
        order = om.place_order("NFO|X", "SELL", 65, price_type="MKT")
        assert order["status"] == "COMPLETE"
        assert order["ltp_at_submit"] == 100.0
        assert order["expected_price"] == 100.0

    def test_mkt_slippage_derivable(self, tmp_path):
        om = _om(tmp_path, {"NFO|X": 100.0})
        order = om.place_order("NFO|X", "BUY", 65, price_type="MKT")
        slippage = order["fill_price"] - order["expected_price"]
        assert slippage >= 0

    def test_mkt_rejected_order_has_expected_price(self, tmp_path):
        om = _om(tmp_path, {"NFO|X": 0.0})
        order = om.place_order("NFO|X", "BUY", 65, price_type="MKT", price=0.0)
        assert order["status"] == "REJECTED"
        assert "ltp_at_submit" in order
        assert "expected_price" in order


class TestLMTOrderSlippage:
    """LMT orders: expected_price = limit_price (before slippage)."""

    def test_lmt_buy_order_has_expected_price_equal_to_limit(self, tmp_path):
        om = _om(tmp_path, {"NFO|X": 100.0})
        order = om.place_order("NFO|X", "BUY", 65, price_type="LMT", price=105.0)
        assert order["status"] == "COMPLETE"
        assert order["ltp_at_submit"] == 100.0
        assert order["expected_price"] == 105.0

    def test_lmt_sell_order_has_expected_price_equal_to_limit(self, tmp_path):
        om = _om(tmp_path, {"NFO|X": 100.0})
        order = om.place_order("NFO|X", "SELL", 65, price_type="LMT", price=95.0)
        assert order["status"] == "COMPLETE"
        assert order["ltp_at_submit"] == 100.0
        assert order["expected_price"] == 95.0

    def test_lmt_slippage_derivable(self, tmp_path):
        om = _om(tmp_path, {"NFO|X": 100.0})
        order = om.place_order("NFO|X", "SELL", 65, price_type="LMT", price=95.0)
        slippage = order["expected_price"] - order["fill_price"]
        assert slippage != 0

    def test_lmt_canceled_order_has_expected_price(self, tmp_path):
        om = _om(tmp_path, {"NFO|X": 100.0})
        order = om.place_order("NFO|X", "BUY", 65, price_type="LMT", price=95.0)
        assert order["status"] == "CANCELED"
        assert order["ltp_at_submit"] == 100.0
        assert order["expected_price"] == 95.0

    def test_lmt_rejected_order_has_expected_price(self, tmp_path):
        om = _om(tmp_path, {"NFO|X": 100.0})
        order = om.place_order("NFO|X", "SELL", 65, price_type="LMT", price=0)
        assert order["status"] == "REJECTED"
        assert order["ltp_at_submit"] == 100.0
        assert order["expected_price"] == 0


class TestSlippageDerivation:
    """Verify slippage can be derived from new fields."""

    def test_slippage_calculation_for_mkt_buy(self, tmp_path):
        om = _om(tmp_path, {"NFO|X": 100.0})
        order = om.place_order("NFO|X", "BUY", 65, price_type="MKT")
        slippage = order["fill_price"] - order["expected_price"]
        slippage_pct = (slippage / order["expected_price"]) * 100
        assert slippage_pct >= 0.05

    def test_slippage_calculation_for_lmt_sell(self, tmp_path):
        om = _om(tmp_path, {"NFO|X": 100.0})
        order = om.place_order("NFO|X", "SELL", 65, price_type="LMT", price=99.0)
        slippage = order["expected_price"] - order["fill_price"]
        slippage_pct = abs(slippage / order["expected_price"]) * 100
        assert slippage_pct >= 0.05

    def test_slippage_minimum_abs_applied(self, tmp_path):
        om = _om(tmp_path, {"NFO|X": 1000.0})
        order = om.place_order("NFO|X", "SELL", 65, price_type="MKT")
        slippage = order["expected_price"] - order["fill_price"]
        assert slippage >= 0.25
