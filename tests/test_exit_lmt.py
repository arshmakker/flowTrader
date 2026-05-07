import os
import sys
from unittest.mock import MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from trading_system.config import settings
from trading_system.core.iron_condor import IC_Position, IronCondorStrategy
from trading_system.existing.market_data import QuoteBook


def _setup_ic(om, md):
    ic = IronCondorStrategy(om, md, "NIFTY")
    ic._position = IC_Position(
        instrument="NIFTY",
        sc_sym="NFO|NIFTY26M19C22200",
        sp_sym="NFO|NIFTY26M19P21800",
        lc_sym="NFO|NIFTY26M19C22300",
        lp_sym="NFO|NIFTY26M19P21700",
        sc_strike=22200,
        sp_strike=21800,
        lc_strike=22300,
        lp_strike=21700,
        max_profit=1000,
        entry_credit=20,
        lots=2,
        entry_time="10:00:00",
    )
    return ic


def _book(bid, ask, symbol=""):
    return QuoteBook(symbol=symbol, bid=bid, ask=ask, bid_qty=10_000, ask_qty=10_000)


class TestExitPassesPriceFromQuoteBook:
    def setup_method(self):
        self.om = MagicMock()
        self.om.tracker = None
        self.om.get_available_margin.return_value = float("inf")
        self.om.place_order = MagicMock(
            return_value={"status": "COMPLETE", "fill_qty": 130, "fill_price": 10.0, "order_id": "X"}
        )
        self.md = MagicMock()
        self.md.get_lot_size.return_value = settings.NIFTY_LOT_SIZE
        books = {
            "NFO|NIFTY26M19C22200": _book(bid=14.0, ask=14.25),
            "NFO|NIFTY26M19P21800": _book(bid=13.5, ask=13.75),
            "NFO|NIFTY26M19C22300": _book(bid=4.5, ask=4.75),
            "NFO|NIFTY26M19P21700": _book(bid=4.0, ask=4.25),
        }
        self.md.get_quote_book.side_effect = lambda sym: books.get(sym)
        self.md.get_ltp.return_value = 10.0

    def test_buy_legs_receive_ask_price(self):
        ic = _setup_ic(self.om, self.md)
        ic.exit("TEST")
        calls = self.om.place_order.call_args_list
        assert calls[0].kwargs["price"] == 14.25
        assert calls[0].args[1] == "BUY"
        assert calls[1].kwargs["price"] == 13.75
        assert calls[1].args[1] == "BUY"

    def test_sell_legs_receive_bid_price(self):
        ic = _setup_ic(self.om, self.md)
        ic.exit("TEST")
        calls = self.om.place_order.call_args_list
        assert calls[2].kwargs["price"] == 4.5
        assert calls[2].args[1] == "SELL"
        assert calls[3].kwargs["price"] == 4.0
        assert calls[3].args[1] == "SELL"

    def test_all_four_legs_receive_price(self):
        ic = _setup_ic(self.om, self.md)
        ic.exit("TEST")
        for i, c in enumerate(self.om.place_order.call_args_list):
            assert "price" in c.kwargs, f"Leg {i} missing price= argument"
            assert c.kwargs["price"] > 0


class TestExitFallsBackToLTP:
    def setup_method(self):
        self.om = MagicMock()
        self.om.tracker = None
        self.om.get_available_margin.return_value = float("inf")
        self.om.place_order = MagicMock(
            return_value={"status": "COMPLETE", "fill_qty": 130, "fill_price": 10.0, "order_id": "X"}
        )
        self.md = MagicMock()
        self.md.get_lot_size.return_value = settings.NIFTY_LOT_SIZE
        self.md.get_quote_book.return_value = None
        ltp_map = {
            "NFO|NIFTY26M19C22200": 14.25,
            "NFO|NIFTY26M19P21800": 13.75,
            "NFO|NIFTY26M19C22300": 4.75,
            "NFO|NIFTY26M19P21700": 4.25,
        }
        self.md.get_ltp.side_effect = lambda sym: ltp_map.get(sym, 10.0)

    def test_fallback_uses_ltp_for_each_leg(self):
        ic = _setup_ic(self.om, self.md)
        ic.exit("TEST")
        calls = self.om.place_order.call_args_list
        assert calls[0].kwargs["price"] == 14.25
        assert calls[1].kwargs["price"] == 13.75
        assert calls[2].kwargs["price"] == 4.75
        assert calls[3].kwargs["price"] == 4.25


class TestExitFallsBackWhenNotTradable:
    """Test B: QuoteBook exists but is not tradable (bid=0) → LTP fallback."""

    def setup_method(self):
        self.om = MagicMock()
        self.om.tracker = None
        self.om.get_available_margin.return_value = float("inf")
        self.om.place_order = MagicMock(
            return_value={"status": "COMPLETE", "fill_qty": 130, "fill_price": 10.0, "order_id": "X"}
        )
        self.md = MagicMock()
        self.md.get_lot_size.return_value = settings.NIFTY_LOT_SIZE
        # QuoteBook exists but is NOT tradable (bid=0, bid_qty=0)
        untradable_book = QuoteBook(symbol="X", bid=0.0, ask=0.0, bid_qty=0, ask_qty=0)
        self.md.get_quote_book.return_value = untradable_book
        self.md.get_ltp.return_value = 4.5

    def test_non_tradable_book_falls_back_to_ltp(self):
        ic = _setup_ic(self.om, self.md)
        ic.exit("TEST")
        for c in self.om.place_order.call_args_list:
            assert c.kwargs["price"] == 4.5, f"Non-tradable book should fall back to LTP=4.5, got {c.kwargs['price']}"
