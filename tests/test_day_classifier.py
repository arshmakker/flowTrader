"""Tests for DayClassifier — trending, ranging, edge cases, locking, reset."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from trading_system.config import settings
from trading_system.core.day_classifier import DayClassifier, DayClassification


class MockMD:
    def __init__(self, open_px, current):
        self._open = open_px
        self._current = current

    def get_open_price(self, sym):
        return self._open

    def get_ltp(self, sym):
        return self._current

    def get_ohlcv_df(self):
        return None  # MockSE ignores this arg; see compute_vwap_value below


class MockSE:
    def __init__(self, vwap_value):
        self._vwap = vwap_value

    def compute_vwap_value(self, ohlcv_df=None):
        return self._vwap


def _classify(open_px, current, vwap):
    md = MockMD(open_px, current)
    se = MockSE(vwap)
    dc = DayClassifier(md, se)
    return dc.classify()


def test_ranging_small_move():
    """Small move + near VWAP → RANGING HIGH."""
    r = _classify(24000, 24100, 24050)
    assert r.day_type == "RANGING"
    assert r.confidence == "HIGH"


def test_trending_up():
    """Big move up + far from VWAP → TRENDING_UP."""
    open_px = 24000
    move = settings.TREND_MOVE_THRESHOLD + 0.005
    current = open_px * (1 + move)
    vwap = open_px * 1.001  # VWAP near open, price far away
    r = _classify(open_px, current, vwap)
    assert r.day_type == "TRENDING_UP"
    assert r.confidence in ("HIGH", "MEDIUM")


def test_trending_down():
    """Big move down + far from VWAP → TRENDING_DOWN."""
    open_px = 24000
    move = settings.TREND_MOVE_THRESHOLD + 0.005
    current = open_px * (1 - move)
    vwap = open_px * 0.999
    r = _classify(open_px, current, vwap)
    assert r.day_type == "TRENDING_DOWN"


def test_big_move_near_vwap_is_ranging():
    """Big price move but close to VWAP → RANGING MEDIUM."""
    open_px = 24000
    move = settings.TREND_MOVE_THRESHOLD + 0.005
    current = open_px * (1 + move)
    vwap = current  # VWAP equals current → vwap_dist = 0
    r = _classify(open_px, current, vwap)
    assert r.day_type == "RANGING"
    assert r.confidence == "MEDIUM"


def test_zero_open_guard():
    r = _classify(0, 24000, 24000)
    assert r.day_type == "RANGING"
    assert r.confidence == "LOW"


def test_zero_current_guard():
    r = _classify(24000, 0, 24000)
    assert r.day_type == "RANGING"
    assert r.confidence == "LOW"


def test_zero_vwap():
    """VWAP=0 should not crash."""
    r = _classify(24000, 24100, 0)
    assert r.day_type in ("RANGING", "TRENDING_UP", "TRENDING_DOWN")


def test_confidence_low_when_open_price_unreliable():
    """When is_open_price_reliable(symbol) is False, confidence is downgraded to LOW."""
    open_px = 24000
    move = settings.TREND_MOVE_THRESHOLD + 0.005
    current = open_px * (1 + move)
    vwap = open_px * 0.99  # far from VWAP → would normally be TRENDING_UP HIGH/MEDIUM

    class MockMDUnreliableOpen(MockMD):
        def is_open_price_reliable(self, symbol):
            return False

    md = MockMDUnreliableOpen(open_px, current)
    se = MockSE(vwap)
    dc = DayClassifier(md, se)
    r = dc.classify()
    assert r.day_type == "TRENDING_UP"
    assert r.confidence == "LOW"


def test_classification_is_locked():
    md = MockMD(24000, 24100)
    se = MockSE(24050)
    dc = DayClassifier(md, se)
    first = dc.classify()
    md._current = 30000
    second = dc.classify()
    assert first is second


def test_reset_unlocks():
    md = MockMD(24000, 24100)
    se = MockSE(24050)
    dc = DayClassifier(md, se)
    first = dc.classify()
    dc.reset()
    md._current = 30000
    second = dc.classify()
    assert second.current_price == 30000


def test_move_pct_in_result():
    r = _classify(24000, 24360, 24100)
    assert abs(r.move_pct - 1.5) < 0.1  # ~1.5% move


def test_classifier_uses_per_instrument_symbol_and_spot_key():
    """BUG-08: DayClassifier must honor the symbol and spot_key passed to __init__
    so BANKNIFTY classification uses BANKNIFTY data, not NIFTY."""
    seen = {"open_sym": None, "ltp_key": None}

    class RecordingMD:
        def get_open_price(self, sym):
            seen["open_sym"] = sym
            return 45000.0
        def get_ltp(self, key):
            seen["ltp_key"] = key
            return 45200.0
        def get_ohlcv_df(self):
            return None

    se = MockSE(45100.0)
    dc = DayClassifier(RecordingMD(), se, symbol="BANKNIFTY", spot_key="NSE|Nifty Bank")
    dc.classify()
    assert seen["open_sym"] == "BANKNIFTY"
    assert seen["ltp_key"] == "NSE|Nifty Bank"


def test_classifier_defaults_to_nifty_when_symbol_not_passed():
    """Backward compat: existing callers that omit symbol/spot_key keep NIFTY behaviour."""
    dc = DayClassifier(MockMD(24000, 24100), MockSE(24050))
    assert dc.symbol == settings.NIFTY_SYMBOL
    assert dc.spot_key == settings.NIFTY_SPOT_KEY


def test_ohlcv_is_forwarded_to_vwap_engine():
    """Regression for the bug where classify() called compute_vwap_value() with
    no argument, so the trending branch could never fire. This asserts the df
    returned by MarketData.get_ohlcv_df() is the exact object passed to
    SignalEngine.compute_vwap_value()."""
    sentinel = object()

    class SpyMD(MockMD):
        def get_ohlcv_df(self):
            return sentinel

    class SpySE:
        def __init__(self):
            self.seen = []

        def compute_vwap_value(self, ohlcv_df=None):
            self.seen.append(ohlcv_df)
            return 24050.0

    md = SpyMD(24000, 24100)
    se = SpySE()
    dc = DayClassifier(md, se)
    dc.classify()
    assert se.seen == [sentinel], (
        f"classify() must forward md.get_ohlcv_df() to compute_vwap_value(); "
        f"got {se.seen}"
    )


def test_classification_dataclass_fields():
    r = _classify(24000, 24100, 24050)
    assert hasattr(r, "day_type")
    assert hasattr(r, "confidence")
    assert hasattr(r, "open_price")
    assert hasattr(r, "current_price")
    assert hasattr(r, "move_pct")
    assert hasattr(r, "vwap_distance_pct")
    assert hasattr(r, "classified_at")


if __name__ == "__main__":
    passed = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            passed += 1
            print(f"  [PASS] {name}")
    print(f"\n✅ {passed} DayClassifier tests passed")
