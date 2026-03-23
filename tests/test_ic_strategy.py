import pytest
from unittest.mock import MagicMock
from trading_system.core.iron_condor import IronCondorStrategy, IC_Position
from trading_system.config import settings

@pytest.fixture
def mock_om():
    om = MagicMock()
    om.build_option_symbol.side_effect = lambda inst, exp, strike, type: f"NFO|{inst}{exp}{type[0]}{int(strike)}"
    return om

@pytest.fixture
def mock_md():
    md = MagicMock()
    # Default LTP
    md.get_ltp.return_value = 10.0
    md.get_lot_size.return_value = settings.NIFTY_LOT_SIZE
    return md

def test_ic_strategy_entry_success(mock_om, mock_md):
    s = IronCondorStrategy(mock_om, mock_md, 'NIFTY')
    
    # Mock spot=22000, vix=12, sr_high=22500, sr_low=21500
    sr_mgr = MagicMock()
    sr_mgr.apply_buffer.side_effect = lambda strike, h, l, type, step=50: strike
    
    # Prices: SC=15, SP=15, LC=5, LP=5. Credit = (15+15)-(5+5) = 20.
    # Width = LC-SC = 50. 20/50 = 40% (>= 25% Rule)
    def ltp_side_effect(sym):
        if 'C22150' in sym or 'P21850' in sym: return 15.0
        if 'C22200' in sym or 'P21800' in sym: return 5.0
        return 10.0
    mock_md.get_ltp.side_effect = ltp_side_effect
    
    success = s.enter(22000, 12, 22500, 21500, sr_mgr, '19-MAR-2026', 2)
    assert success is True
    assert s.is_active() is True
    assert s._position.max_profit == 20.0 * 2 * settings.NIFTY_LOT_SIZE

def test_ic_strategy_harvest(mock_om, mock_md):
    s = IronCondorStrategy(mock_om, mock_md, 'NIFTY')
    s._position = IC_Position(
        instrument='NIFTY',
        sc_sym='SC', sp_sym='SP', lc_sym='LC', lp_sym='LP',
        sc_strike=22150, sp_strike=21850, lc_strike=22200, lp_strike=21800,
        max_profit=1000, entry_credit=20, lots=2, entry_time='10:00:00'
    )
    
    # Harvest trigger = 1% of max_profit (1000) = 10
    # Current premium: 19.0. PnL = (20 - 19.0) * 2 * 25 = 1.0 * 50 = 50.
    # Should trigger harvest (trigger is 10).
    def ltp_side_effect(sym):
        if sym in ('SC', 'SP'): return 14.5
        if sym in ('LC', 'LP'): return 5.0
        return 0.0
    mock_md.get_ltp.side_effect = ltp_side_effect
    
    result = s.monitor()
    assert result is not None
    assert result['exit_reason'] == 'PROFIT_HARVEST'
    assert s.is_active() is False

def test_ic_strategy_force_exit_pnl(mock_om, mock_md):
    s = IronCondorStrategy(mock_om, mock_md, 'NIFTY')
    s._position = IC_Position(
        instrument='NIFTY',
        sc_sym='SC', sp_sym='SP', lc_sym='LC', lp_sym='LP',
        sc_strike=22150, sp_strike=21850, lc_strike=22200, lp_strike=21800,
        max_profit=1000, entry_credit=20.0, lots=2, entry_time='10:00:00'
    )
    
    # Current premium: 25.0. PnL = (20.0 - 25.0) * 2 * 65 = -5.0 * 130 = -650.0
    def ltp_side_effect(sym):
        if sym in ('SC', 'SP'): return 15.0 # 30
        if sym in ('LC', 'LP'): return 2.5  # 5
        return 0.0                          # Net = 25
    mock_md.get_ltp.side_effect = ltp_side_effect
    
    result = s.force_exit()
    assert result is not None
    assert result['exit_reason'] == 'FORCE_EXIT'
    assert result['pnl'] == -650.0
    assert result['net_pnl'] == -650.0
    assert s.is_active() is False
