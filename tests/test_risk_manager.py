"""Tests for RiskManager — loss limits, halting, lot sizing, monthly drawdown."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from trading_system.config import settings
from trading_system.core.risk_manager import RiskManager


def test_initial_state():
    rm = RiskManager()
    assert rm.daily_pnl == 0.0
    assert rm.monthly_pnl == 0.0
    assert rm.trades_today == 0
    assert rm.can_trade() is True
    assert rm.halted is False


def test_update_pnl_tracks_daily():
    rm = RiskManager()
    rm.update_pnl(5000)
    assert rm.daily_pnl == 5000
    assert rm.trades_today == 1
    rm.update_pnl(-2000)
    assert rm.daily_pnl == 3000
    assert rm.trades_today == 2


def test_update_pnl_tracks_monthly():
    rm = RiskManager()
    rm.update_pnl(5000)
    rm.update_pnl(-2000)
    assert rm.monthly_pnl == 3000


def test_daily_loss_limit_halts_trading():
    rm = RiskManager()
    rm.update_loss_limit(10_000)
    rm.update_pnl(-10_001)
    assert rm.halted is True
    assert rm.can_trade() is False


def test_exact_limit_halts():
    rm = RiskManager()
    rm.update_loss_limit(5000)
    rm.update_pnl(-5000)
    assert rm.halted is True


def test_loss_just_below_limit_does_not_halt():
    rm = RiskManager()
    rm.update_loss_limit(5000)
    rm.update_pnl(-4999)
    assert rm.halted is False
    assert rm.can_trade() is True


def test_update_loss_limit():
    rm = RiskManager()
    rm.update_loss_limit(7500)
    assert rm._daily_loss_limit == 7500


def test_allowed_lots_base():
    rm = RiskManager()
    lots = rm.allowed_lots("A", 1.0)
    assert lots == settings.SA_MAX_LOTS


def test_allowed_lots_with_multiplier():
    rm = RiskManager()
    lots = rm.allowed_lots("A", 0.5)
    assert lots == max(1, int(settings.SA_MAX_LOTS * 0.5))


def test_allowed_lots_minimum_is_1():
    rm = RiskManager()
    lots = rm.allowed_lots("A", 0.01)
    assert lots >= 1


def test_monthly_drawdown_halves_size():
    rm = RiskManager()
    rm.monthly_pnl = -(settings.MONTHLY_DD_LIMIT + 1)
    base = settings.SA_MAX_LOTS
    lots = rm.allowed_lots("A", 1.0)
    expected = max(1, int(base * 0.5))
    assert lots == expected


def test_per_trade_risk():
    rm = RiskManager()
    expected = settings.CAPITAL * settings.MAX_RISK_PCT_TRADE
    assert rm.per_trade_risk() == expected


def test_reset_daily():
    rm = RiskManager()
    rm.update_pnl(-50000)
    rm.halted = True
    rm.reset_daily()
    assert rm.daily_pnl == 0.0
    assert rm.trades_today == 0
    assert rm.halted is False


def test_reset_daily_keeps_monthly():
    rm = RiskManager()
    rm.update_pnl(-5000)
    rm.reset_daily()
    assert rm.monthly_pnl == -5000


def test_save_restore_state():
    rm = RiskManager()
    rm.update_loss_limit(7500)
    rm.update_pnl(-8000)
    state = rm.save_state()

    fresh = RiskManager()
    fresh.restore_state(state)
    assert fresh.daily_pnl == rm.daily_pnl
    assert fresh.monthly_pnl == rm.monthly_pnl
    assert fresh.trades_today == rm.trades_today
    assert fresh.halted == rm.halted
    assert fresh._daily_loss_limit == 7500


def test_all_strategy_lot_keys():
    rm = RiskManager()
    for key in ("A", "B", "C", "D", "E"):
        lots = rm.allowed_lots(key, 1.0)
        assert lots >= 1, f"Strategy {key} lots should be >= 1"


if __name__ == "__main__":
    passed = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            passed += 1
            print(f"  [PASS] {name}")
    print(f"\n✅ {passed} RiskManager tests passed")
