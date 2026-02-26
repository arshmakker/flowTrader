"""
Tests for Convex order guardrails: fill detection and entry/exit flow.

Validates wait_for_order_fill against mocked single_order_history responses
so we can verify fill detection without a live broker.
"""
import sys
import os
import time
import unittest
from unittest.mock import MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from strategies.convex.order_builder import (
    wait_for_order_fill,
    _is_order_filled,
    _is_order_terminal_fail,
    ORDER_STATUS_FILLED,
    ORDER_REPORT_FILL,
    build_convex_entry_orders,
    build_convex_exit_orders,
    place_single_order,
    _order_dict_to_place_kwargs,
)


class TestWaitForOrderFill(unittest.TestCase):
    """Validate fill detection per Shoonya: status (order state) and rpt (report type), not order ID."""

    def test_fill_detected_when_status_complete(self):
        api = MagicMock()
        api.single_order_history.return_value = [{"stat": "Ok", "status": "COMPLETE", "rpt": "Fill"}]
        result = wait_for_order_fill(api, "ord_123", timeout_seconds=5, poll_interval_seconds=0.1)
        self.assertTrue(result, "Should return True when status is COMPLETE")
        api.single_order_history.assert_called_with(orderno="ord_123")

    def test_fill_detected_when_rpt_fill(self):
        api = MagicMock()
        api.single_order_history.return_value = [{"stat": "Ok", "rpt": "Fill", "fillshares": "50"}]
        result = wait_for_order_fill(api, "ord_456", timeout_seconds=5, poll_interval_seconds=0.1)
        self.assertTrue(result, "Should detect Fill from rpt (report type)")

    def test_order_id_without_fill_not_fulfilled(self):
        """Having norenordno / stat Ok does NOT mean order is fulfilled; must have status COMPLETE or rpt Fill."""
        api = MagicMock()
        api.single_order_history.return_value = [{"stat": "Ok", "norenordno": "123", "status": "OPEN", "rpt": "New"}]
        result = wait_for_order_fill(api, "ord_open", timeout_seconds=1, poll_interval_seconds=0.2)
        self.assertFalse(result, "OPEN/New must not be treated as filled")

    def test_fill_detected_after_pending_then_complete(self):
        api = MagicMock()
        api.single_order_history.side_effect = [
            [{"stat": "Ok", "status": "PENDING", "rpt": "PendingNew"}],
            [{"stat": "Ok", "status": "COMPLETE", "rpt": "Fill"}],
        ]
        result = wait_for_order_fill(api, "ord_789", timeout_seconds=5, poll_interval_seconds=0.05)
        self.assertTrue(result, "Should return True once status COMPLETE / rpt Fill is returned")
        self.assertGreaterEqual(api.single_order_history.call_count, 2)

    def test_returns_false_on_rejected(self):
        api = MagicMock()
        api.single_order_history.return_value = [{"stat": "Ok", "status": "REJECTED", "rpt": "Rejected"}]
        result = wait_for_order_fill(api, "ord_rej", timeout_seconds=3, poll_interval_seconds=0.1)
        self.assertFalse(result, "Should return False when order is REJECTED")

    def test_returns_false_on_cancelled(self):
        api = MagicMock()
        api.single_order_history.return_value = [{"stat": "Ok", "status": "CANCELED", "rpt": "Canceled"}]
        result = wait_for_order_fill(api, "ord_can", timeout_seconds=3, poll_interval_seconds=0.1)
        self.assertFalse(result, "Should return False when order is CANCELED")

    def test_returns_false_on_timeout(self):
        api = MagicMock()
        api.single_order_history.return_value = [{"stat": "Ok", "status": "PENDING", "rpt": "NewAck"}]
        result = wait_for_order_fill(api, "ord_timeout", timeout_seconds=1, poll_interval_seconds=0.2)
        self.assertFalse(result, "Should return False when fill does not occur within timeout")

    def test_handles_list_response_uses_first_record_newest(self):
        api = MagicMock()
        api.single_order_history.return_value = [
            {"stat": "Ok", "status": "COMPLETE", "rpt": "Fill"},
            {"stat": "Ok", "status": "OPEN", "rpt": "New"},
        ]
        result = wait_for_order_fill(api, "ord_list", timeout_seconds=5, poll_interval_seconds=0.1)
        self.assertTrue(result, "Should use first record (newest) in list")

    def test_handles_empty_history_then_complete(self):
        api = MagicMock()
        api.single_order_history.side_effect = [[], [{"stat": "Ok", "status": "COMPLETE", "rpt": "Fill"}]]
        result = wait_for_order_fill(api, "ord_empty", timeout_seconds=5, poll_interval_seconds=0.05)
        self.assertTrue(result, "Should keep polling when history is empty then get COMPLETE/Fill")


class TestFillHelpers(unittest.TestCase):
    """Test _is_order_filled and _is_order_terminal_fail against Shoonya status/rpt."""

    def test_filled_status_complete(self):
        self.assertTrue(_is_order_filled({"status": "COMPLETE"}))

    def test_filled_rpt_fill(self):
        self.assertTrue(_is_order_filled({"rpt": "Fill"}))

    def test_not_filled_open_or_pending(self):
        self.assertFalse(_is_order_filled({"status": "OPEN", "rpt": "New"}))
        self.assertFalse(_is_order_filled({"status": "PENDING", "rpt": "PendingNew"}))

    def test_terminal_fail_rejected_canceled(self):
        self.assertTrue(_is_order_terminal_fail({"status": "REJECTED"}))
        self.assertTrue(_is_order_terminal_fail({"status": "CANCELED"}))
        self.assertTrue(_is_order_terminal_fail({"rpt": "Rejected"}))
        self.assertTrue(_is_order_terminal_fail({"rpt": "Canceled"}))


class TestOrderBuilderHelpers(unittest.TestCase):
    """Sanity checks for order build and place helpers."""

    def test_build_entry_orders_long_before_short(self):
        proposal = {
            "legs": [
                {"option_type": "CE", "strike": 25000, "quantity": 50, "price": 100, "action": "BUY"},
                {"option_type": "CE", "strike": 25100, "quantity": 100, "price": 50, "action": "SELL"},
                {"option_type": "CE", "strike": 25200, "quantity": 50, "price": 30, "action": "BUY"},
            ],
            "expiry": "2026-02-27",
            "lots": 1,
            "lot_size": 50,
        }
        orders = build_convex_entry_orders(proposal)
        buys = [o for o in orders if o.get("buy_or_sell") == "B"]
        sells = [o for o in orders if o.get("buy_or_sell") == "S"]
        self.assertEqual(orders, buys + sells, "BUY orders must come before SELL")
        self.assertTrue(all(o.get("product_type") == "M" for o in orders), "All orders must be MIS (M)")
        self.assertTrue(all(o.get("price_type") == "LMT" for o in orders), "Entry must be LMT")

    def test_build_exit_orders_product_mis_price_mkt(self):
        position = {
            "legs": [
                {"option_type": "CE", "strike": 25000, "quantity": 50, "price": 100},
                {"option_type": "CE", "strike": 25100, "quantity": 100, "price": 50},
                {"option_type": "CE", "strike": 25200, "quantity": 50, "price": 30},
            ],
            "expiry": "2026-02-27",
            "lots": 1,
            "lot_size": 50,
        }
        orders = build_convex_exit_orders(position)
        self.assertTrue(all(o.get("product_type") == "M" for o in orders), "Exit orders must be MIS")
        self.assertTrue(all(o.get("price_type") == "MKT" for o in orders), "Exit must be MKT")

    def test_order_dict_to_place_kwargs_has_required_fields(self):
        order = {
            "buy_or_sell": "B",
            "product_type": "M",
            "exchange": "NFO",
            "tradingsymbol": "NIFTY26FEB25000CE",
            "quantity": 50,
            "price_type": "LMT",
            "price": 100.0,
        }
        kwargs = _order_dict_to_place_kwargs(order)
        self.assertEqual(kwargs["buy_or_sell"], "B")
        self.assertEqual(kwargs["product_type"], "M")
        self.assertEqual(kwargs["price_type"], "LMT")
        self.assertEqual(kwargs["quantity"], 50)
        self.assertIn("remarks", kwargs)


if __name__ == "__main__":
    unittest.main()
