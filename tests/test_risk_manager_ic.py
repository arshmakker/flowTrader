import pytest
from unittest.mock import MagicMock
from trading_system.core.risk_manager import RiskManager
from trading_system.core.iron_condor import IronCondorStrategy, IC_Position
from trading_system.config import settings

@pytest.fixture
def mock_strats():
    s1 = MagicMock()
    s1.instrument = 'NIFTY'
    s1.is_active.return_value = True
    s1.md.get_ltp.return_value = 10.0
    s1._position = IC_Position(
        instrument='NIFTY',
        sc_sym='SC1', sp_sym='SP1', lc_sym='LC1', lp_sym='LP1',
        sc_strike=22150, sp_strike=21850, lc_strike=22200, lp_strike=21800,
        max_profit=1000, entry_credit=20, lots=2, entry_time='10:00:00'
    )
    
    s2 = MagicMock()
    s2.instrument = 'BANKNIFTY'
    s2.is_active.return_value = True
    s2.md.get_ltp.return_value = 10.0
    s2._position = IC_Position(
        instrument='BANKNIFTY',
        sc_sym='SC2', sp_sym='SP2', lc_sym='LC2', lp_sym='LP2',
        sc_strike=52100, sp_strike=51800, lc_strike=52200, lp_strike=51700,
        max_profit=1500, entry_credit=50, lots=2, entry_time='10:05:00'
    )
    return [s1, s2]

def test_combined_stop_loss(mock_strats):
    rm = RiskManager()
    s1, s2 = mock_strats
    
    # Combined max profit = 1000 + 1500 = 2500.
    # Stop loss limit = -2500 * 3 = -7500.
    
    # Simulate P&L: s1=-2000, s2=-6000. Total = -8000.
    # s1: entry_credit=20. need current_prem such that (20 - current_prem) * 2 * 25 = -2000
    # (20 - current_prem) * 50 = -2000 => 20 - current_prem = -40 => current_prem = 60.
    # s2: entry_credit=50. need current_prem such that (50 - current_prem) * 2 * 15 = -6000
    # (50 - current_prem) * 30 = -6000 => 50 - current_prem = -200 => current_prem = 250.
    
    def s1_ltp(sym):
        if sym in ('SC1', 'SP1'): return 35.0 # (35+35) - (10+10) = 70-20 = 50. wait.
        if sym in ('LC1', 'LP1'): return 10.0 # (35+35) - (10+10) = 50. 20-50 = -30. -30 * 50 = -1500.
        return 10.0
    s1.md.get_ltp.side_effect = s1_ltp # s1 pnl = -1500
    
    def s2_ltp(sym):
        if sym in ('SC2', 'SP2'): return 150.0 # (150+150) - (25+25) = 300-50 = 250.
        if sym in ('LC2', 'LP2'): return 25.0  # 50 - 250 = -200. -200 * 30 = -6000.
        return 10.0
    s2.md.get_ltp.side_effect = s2_ltp # s2 pnl = -6000
    
    # Total = -1500 - 6000 = -7500.
    # Limit = -2500 * 3 = -7500.
    assert rm.check_combined_stop_loss(mock_strats) is True
    assert rm.halted is True

def test_recovery_protocol_gates():
    rm = RiskManager()
    rm.halted = True
    from datetime import datetime, time
    rm.stop_hit_at = datetime.combine(datetime.now().date(), time(12, 0)) # 12:00 PM
    
    # Before 1 PM + VIX stable = OK
    assert rm.can_enter_recovery(vix_stable=True, vix_falling=False) is True
    
    # After 1 PM = BLOCKED
    rm.stop_hit_at = datetime.combine(datetime.now().date(), time(13, 1))
    assert rm.can_enter_recovery(vix_stable=True, vix_falling=False) is False
    
    # Before 1 PM + VIX NOT stable/falling = BLOCKED
    rm.stop_hit_at = datetime.combine(datetime.now().date(), time(12, 0))
    assert rm.can_enter_recovery(vix_stable=False, vix_falling=False) is False
