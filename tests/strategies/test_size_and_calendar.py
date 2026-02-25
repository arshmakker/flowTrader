import pandas as pd
import importlib.util
import os
import pytest

# Robust imports: try package import first, fall back to loading from file path
try:
    from strategies.size_config import clamp_lots, MIN_LOTS, MAX_LOTS
except Exception:
    spec = importlib.util.spec_from_file_location(
        "strategies.size_config",
        os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', 'strategies', 'size_config.py'))
    )
    sc = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sc)
    clamp_lots = sc.clamp_lots
    MIN_LOTS = sc.MIN_LOTS
    MAX_LOTS = sc.MAX_LOTS
    # make the module importable as a package module for other imports
    import sys
    sys.modules['strategies.size_config'] = sc

try:
    from strategies.neutral.calendar import generate_neutral_call_calendar
except Exception:
    # Ensure package placeholders exist so relative imports inside calendar.py work
    import types, sys
    if 'strategies' not in sys.modules:
        sys.modules['strategies'] = types.ModuleType('strategies')
    if 'strategies.neutral' not in sys.modules:
        sys.modules['strategies.neutral'] = types.ModuleType('strategies.neutral')

    # Load config module first (so calendar's relative import finds it)
    config_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', 'strategies', 'neutral', 'config.py'))
    spec_cfg = importlib.util.spec_from_file_location('strategies.neutral.config', config_path)
    cfg = importlib.util.module_from_spec(spec_cfg)
    spec_cfg.loader.exec_module(cfg)
    sys.modules['strategies.neutral.config'] = cfg

    # Now load calendar module as part of package
    cal_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', 'strategies', 'neutral', 'calendar.py'))
    spec = importlib.util.spec_from_file_location('strategies.neutral.calendar', cal_path)
    cal = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cal)
    sys.modules['strategies.neutral.calendar'] = cal
    generate_neutral_call_calendar = cal.generate_neutral_call_calendar


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

