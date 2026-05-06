import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pandas as pd
import pytest

from trading_system.core.signal_engine import SignalEngine


def _make_ohlcv(high, low, close, volume):
    return pd.DataFrame([{"high": high, "low": low, "close": close, "volume": volume}])


def test_vwap_none_returns_zero():
    se = SignalEngine()
    assert se.compute_vwap_value(None) == 0.0


def test_vwap_empty_df_returns_zero():
    se = SignalEngine()
    assert se.compute_vwap_value(pd.DataFrame()) == 0.0


def test_vwap_single_row():
    se = SignalEngine()
    # typical = (105 + 95 + 100) / 3 = 100; vol = 10
    # cum_tp_vol = 100 * 10 = 1000; cum_vol = 10 → VWAP = 100
    df = _make_ohlcv(high=105, low=95, close=100, volume=10)
    assert se.compute_vwap_value(df) == pytest.approx(100.0)


def test_vwap_multi_row():
    se = SignalEngine()
    df = pd.DataFrame(
        [
            {"high": 105, "low": 95, "close": 100, "volume": 10},
            {"high": 115, "low": 105, "close": 110, "volume": 20},
        ]
    )
    assert se.compute_vwap_value(df) == pytest.approx(3200 / 30)


def test_vwap_zero_volume_returns_zero():
    se = SignalEngine()
    df = _make_ohlcv(high=105, low=95, close=100, volume=0)
    assert se.compute_vwap_value(df) == 0.0


def test_vwap_mixed_volume():
    se = SignalEngine()
    df = pd.DataFrame(
        [
            {"high": 105, "low": 95, "close": 100, "volume": 10},
            {"high": 115, "low": 105, "close": 110, "volume": 0},
        ]
    )
    assert se.compute_vwap_value(df) == pytest.approx(100.0)
