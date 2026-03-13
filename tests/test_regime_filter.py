"""Tests for RegimeFilter — VIX regimes, routing table, size multipliers, loss limits."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from trading_system.config import settings
from trading_system.core.regime_filter import RegimeFilter


class FakeAPI:
    def __init__(self, vix_value):
        self._vix = vix_value

    def get_quotes(self, exchange, token):
        return {"lp": str(self._vix)}


def _make(vix):
    return RegimeFilter(FakeAPI(vix))


def test_regime_boundaries():
    assert _make(10.0).get_regime() == "CALM"
    assert _make(12.9).get_regime() == "CALM"
    assert _make(13.0).get_regime() == "NORMAL"
    assert _make(16.9).get_regime() == "NORMAL"
    assert _make(17.0).get_regime() == "ELEVATED"
    assert _make(19.9).get_regime() == "ELEVATED"
    assert _make(20.0).get_regime() == "DANGER"
    assert _make(35.0).get_regime() == "DANGER"


def test_size_multipliers():
    assert _make(10).size_multiplier() == 1.0
    assert _make(15).size_multiplier() == 0.5
    assert _make(18).size_multiplier() == 0.3
    assert _make(25).size_multiplier() == 0.25


def test_daily_loss_limits():
    assert _make(10).daily_loss_limit() == settings.LOSS_LIMIT_CALM
    assert _make(15).daily_loss_limit() == settings.LOSS_LIMIT_NORMAL
    assert _make(18).daily_loss_limit() == settings.LOSS_LIMIT_ELEVATED
    assert _make(25).daily_loss_limit() == settings.LOSS_LIMIT_HIGH_VIX


def test_routing_calm_ranging():
    r = _make(10).get_routing("RANGING")
    assert "A" in r["primary"]
    assert "D" in r["forbidden"]
    assert "E" in r["forbidden"]


def test_routing_calm_trending_up():
    r = _make(10).get_routing("TRENDING_UP")
    assert "B" in r["primary"]


def test_routing_normal_ranging():
    r = _make(15).get_routing("RANGING")
    assert "A" in r["primary"]


def test_routing_elevated_ranging():
    r = _make(18).get_routing("RANGING")
    assert "D" in r["primary"]
    assert "A" in r["forbidden"]


def test_routing_elevated_trending():
    r = _make(18).get_routing("TRENDING_UP")
    assert "E" in r["primary"]
    assert "D" in r.get("secondary", [])


def test_routing_danger_ranging():
    r = _make(25).get_routing("RANGING")
    assert "D" in r["primary"]


def test_routing_danger_trending():
    r = _make(25).get_routing("TRENDING_DOWN")
    assert "E" in r["primary"]


def test_routing_unknown_combo_fallback():
    r = _make(25).get_routing("UNKNOWN_TYPE")
    assert "D" in r["primary"]


def test_vix_cache():
    rf = _make(15)
    v1 = rf.get_vix()
    assert v1 == 15.0
    rf.api._vix = 99.0
    v2 = rf.get_vix()
    assert v2 == 15.0  # still cached


def test_get_vix_returns_zero_when_api_returns_none():
    class FakeAPINone:
        def get_quotes(self, exchange, token):
            return None
    rf = RegimeFilter(FakeAPINone())
    assert rf.get_vix() == 0.0
    assert rf.get_regime() == "CALM"


def test_get_vix_returns_zero_when_lp_invalid():
    class FakeAPIInvalid:
        def get_quotes(self, exchange, token):
            return {"lp": "not_a_number"}
    rf = RegimeFilter(FakeAPIInvalid())
    assert rf.get_vix() == 0.0
    assert rf.get_regime() == "CALM"


if __name__ == "__main__":
    passed = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            passed += 1
            print(f"  [PASS] {name}")
    print(f"\n✅ {passed} RegimeFilter tests passed")
