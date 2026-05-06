import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from trading_system.core.market_data import MarketData


def test_ltp_nifty():
    md = MarketData()
    assert md.get_ltp("NIFTY") == 20000.0
    assert md.get_ltp("NFO|NIFTY25APR24C20000") == 20000.0


def test_ltp_banknifty():
    md = MarketData()
    assert md.get_ltp("BANKNIFTY") == 45000.0
    assert md.get_ltp("NFO|BANKNIFTY25APR24C45000") == 45000.0


def test_ltp_other_symbol():
    md = MarketData()
    assert md.get_ltp("FINNIFTY") == 100.0
    assert md.get_ltp("USDINR") == 100.0
    assert md.get_ltp("") == 100.0
