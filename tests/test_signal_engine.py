"""Tests for SignalEngine — VWAP, RSI, PCR, Max Pain, consensus aggregation."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pandas as pd
import numpy as np
from trading_system.config import settings
from trading_system.core.signal_engine import SignalEngine, SignalResult


se = SignalEngine()


# ── VWAP ──────────────────────────────────────────────────────────────

def _ohlcv(rows):
    return pd.DataFrame(rows, columns=["open", "high", "low", "close", "volume"])


def test_vwap_value_basic():
    df = _ohlcv([(100, 110, 90, 100, 1000)])
    vwap = se.compute_vwap_value(df)
    expected = (110 + 90 + 100) / 3.0
    assert abs(vwap - expected) < 0.01


def test_vwap_value_empty():
    assert se.compute_vwap_value(pd.DataFrame()) == 0.0
    assert se.compute_vwap_value(None) == 0.0


def test_vwap_value_zero_volume():
    df = _ohlcv([(100, 110, 90, 100, 0)])
    assert se.compute_vwap_value(df) == 0.0


def test_vwap_bias_bull():
    df = _ohlcv([(100, 102, 99, 100, 1000)])
    vwap = se.compute_vwap_value(df)
    spot = vwap * (1 + settings.VWAP_BAND_PCT + 0.001)
    assert se.compute_vwap(df, spot) == "BULL"


def test_vwap_bias_bear():
    df = _ohlcv([(100, 102, 99, 100, 1000)])
    vwap = se.compute_vwap_value(df)
    spot = vwap * (1 - settings.VWAP_BAND_PCT - 0.001)
    assert se.compute_vwap(df, spot) == "BEAR"


def test_vwap_bias_range():
    df = _ohlcv([(100, 102, 99, 100, 1000)])
    vwap = se.compute_vwap_value(df)
    assert se.compute_vwap(df, vwap) == "RANGE"


# ── RSI ───────────────────────────────────────────────────────────────

def _rising_series(n=30, start=100):
    return pd.Series([start + i * 1.5 for i in range(n)])


def _falling_series(n=30, start=200):
    return pd.Series([start - i * 1.5 for i in range(n)])


def _flat_series(n=30, value=100):
    return pd.Series([value] * n)


def test_rsi_flat_is_oversold():
    """Flat series → all deltas=0 → RSI=0 → OS (oversold). Mathematically correct."""
    eng = SignalEngine()
    result = eng.compute_rsi(_flat_series())
    assert result == "OS"


def test_rsi_too_few_bars():
    eng = SignalEngine()
    short = pd.Series([100, 101, 102])
    assert eng.compute_rsi(short) == "NEUTRAL"


def test_rsi_none_input():
    eng = SignalEngine()
    assert eng.compute_rsi(None) == "NEUTRAL"


def test_rsi_overbought():
    eng = SignalEngine()
    prices = _flat_series(20, 100).tolist() + [i for i in range(101, 131)]
    result = eng.compute_rsi(pd.Series(prices))
    assert result in ("OB", "BULL")


def test_rsi_oversold():
    eng = SignalEngine()
    prices = _flat_series(20, 200).tolist() + [200 - i for i in range(1, 31)]
    result = eng.compute_rsi(pd.Series(prices))
    assert result in ("OS", "BEAR")


# ── PCR ───────────────────────────────────────────────────────────────

def _chain(ce_oi, pe_oi):
    return pd.DataFrame([
        {"strike": 24000, "option_type": "CE", "oi": ce_oi},
        {"strike": 24000, "option_type": "PE", "oi": pe_oi},
    ])


def test_pcr_bull():
    chain = _chain(1000, 1500)
    assert se.compute_pcr(chain) == "BULL"


def test_pcr_bear():
    chain = _chain(1000, 500)
    assert se.compute_pcr(chain) == "BEAR"


def test_pcr_neutral():
    chain = _chain(1000, 900)
    assert se.compute_pcr(chain) == "NEUTRAL"


def test_pcr_empty():
    assert se.compute_pcr(pd.DataFrame()) == "NEUTRAL"
    assert se.compute_pcr(None) == "NEUTRAL"


def test_pcr_zero_call_oi():
    chain = _chain(0, 1000)
    assert se.compute_pcr(chain) == "NEUTRAL"


# ── Max Pain ──────────────────────────────────────────────────────────

def _option_chain():
    return pd.DataFrame([
        {"strike": 24000, "option_type": "CE", "oi": 5000},
        {"strike": 24000, "option_type": "PE", "oi": 3000},
        {"strike": 24050, "option_type": "CE", "oi": 4000},
        {"strike": 24050, "option_type": "PE", "oi": 6000},
        {"strike": 24100, "option_type": "CE", "oi": 2000},
        {"strike": 24100, "option_type": "PE", "oi": 8000},
    ])


def test_max_pain_returns_strike():
    mp = se.compute_max_pain(_option_chain())
    assert mp in (24000, 24050, 24100)


def test_max_pain_empty():
    assert se.compute_max_pain(pd.DataFrame()) == 0.0
    assert se.compute_max_pain(None) == 0.0


def test_max_pain_signal_bull():
    assert se.max_pain_signal(23000, 24000) == "BULL"


def test_max_pain_signal_bear():
    assert se.max_pain_signal(25000, 24000) == "BEAR"


def test_max_pain_signal_neutral():
    assert se.max_pain_signal(24050, 24000) == "NEUTRAL"


def test_max_pain_signal_zero():
    assert se.max_pain_signal(24000, 0) == "NEUTRAL"


# ── Consensus / get_signals ──────────────────────────────────────────

def test_get_signals_returns_signal_result():
    eng = SignalEngine()
    df = _ohlcv([(100, 102, 99, 100, 1000)] * 20)
    close = pd.Series([100 + i * 0.01 for i in range(20)])
    chain = _option_chain()
    result = eng.get_signals(df, close, chain, 24050.0)
    assert isinstance(result, SignalResult)
    assert result.consensus in ("BULL", "BEAR", "RANGE")
    assert 0 <= result.confidence <= 4


def test_get_signals_all_bull():
    eng = SignalEngine()
    df = _ohlcv([(24000, 24200, 23900, 24100, 1000)] * 20)
    vwap = eng.compute_vwap_value(df)
    spot = vwap * 1.01
    close = pd.Series([23900 + i * 10 for i in range(20)])
    chain = _chain(500, 2000)
    result = eng.get_signals(df, close, chain, spot, cached_max_pain=spot + 200)
    assert result.vwap_bias == "BULL"
    assert result.pcr_signal == "BULL"


def test_reset():
    eng = SignalEngine()
    eng._prev_rsi = 55.0
    eng.reset()
    assert eng._prev_rsi is None


if __name__ == "__main__":
    passed = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            passed += 1
            print(f"  [PASS] {name}")
    print(f"\n✅ {passed} SignalEngine tests passed")
