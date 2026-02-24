import sys, os
# Ensure repo root is on sys.path for imports when running tests directly
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))
import pandas as pd
from strategies.size_config import clamp_lots, MIN_LOTS, MAX_LOTS
from strategies.neutral.calendar import generate_neutral_call_calendar
import pytest


def test_clamp_lots_basic():
    assert clamp_lots(None) == 0
    assert clamp_lots(0) == 0
    # below min should become MIN_LOTS
    assert clamp_lots(1) == MIN_LOTS
    # above max should become MAX_LOTS
    assert clamp_lots(1000) == MAX_LOTS


def test_calendar_clamps_lots():
    # Build minimal market_state and option chains where calendar would accept
    market_state = {
        "spot_price": 10000.0,
        "regime": "NEUTRAL",
        "sub_state": "NEUTRAL_ACTIVE",
        "iv_percentile": 50.0,
        "adx_14": 20.0,
        "expiry": "2026-03-05",
        "days_to_expiry": 10
    }

    # Build option chains with matching ATM strike and valid prices
    cols = ["strike", "option_type", "ltp", "mid_price", "bid", "ask", "delta", "oi", "volume", "lot_size"]
    weekly = pd.DataFrame([
        [10000.0, "CE", 10.0, 10.0, 9.5, 10.5, 0.5, 1000, 10, 50]
    ], columns=cols)
    monthly = pd.DataFrame([
        [10000.0, "CE", 30.0, 30.0, 29.5, 30.5, 0.5, 500, 5, 50]
    ], columns=cols)

    prop = generate_neutral_call_calendar(market_state, weekly, monthly, capital=1000000.0)
    assert prop is not None
    # Calendar initially sets lots=1, but clamp should bump it to MIN_LOTS
    assert prop.get("lots") == MIN_LOTS

