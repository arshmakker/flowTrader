import pytest
from unittest.mock import MagicMock, patch
from datetime import date, datetime
import pandas as pd
from trading_system.core.expiry_manager import ExpiryManager
from trading_system.core.sr_manager import SRManager
from trading_system.core.regime_filter import RegimeFilter
from trading_system.config import settings

@pytest.fixture
def mock_sm():
    sm = MagicMock()
    # Mock NFO symbols dataframe
    df = pd.DataFrame({
        'instrument': ['OPTIDX', 'OPTIDX', 'OPTIDX'],
        'symbol': ['NIFTY', 'NIFTY', 'NIFTY'],
        'expiry': ['19-MAR-2026', '26-MAR-2026', '02-APR-2026']
    })
    sm.nse_fo = df
    return sm

def test_expiry_manager_roll_rule(mock_sm):
    mgr = ExpiryManager(mock_sm)
    
    # Current date: March 17, 2026. Nearest expiry: March 19. DTE = 2.
    # Should roll to March 26.
    curr_date = date(2026, 3, 17)
    expiry = mgr.get_expiry('NIFTY', current_date=curr_date)
    assert expiry == '26-MAR-2026'
    
    # Current date: March 10, 2026. Nearest expiry: March 19 (in mock). DTE = 9.
    # Should stay on March 19.
    curr_date = date(2026, 3, 10)
    expiry = mgr.get_expiry('NIFTY', current_date=curr_date)
    assert expiry == '19-MAR-2026'

def test_sr_manager_buffer():
    mgr = SRManager()
    sr_high = 22500
    sr_low = 22000
    
    # CE Strike: 22520. Buffer: 22500 + 50 = 22550.
    # Should adjust to 22600 (next 50 step).
    adjusted_ce = mgr.apply_buffer(22520, sr_high, sr_low, 'CE')
    assert adjusted_ce == 22600.0
    
    # PE Strike: 21980. Buffer: 22000 - 50 = 21950.
    # Should adjust to 21950 (or stay if it respects).
    # Wait, 21980 is ABOVE 21950. It should adjust DOWN to 21950.
    adjusted_pe = mgr.apply_buffer(21980, sr_high, sr_low, 'PE')
    assert adjusted_pe == 21950.0

def test_sr_manager_buffer_banknifty():
    mgr = SRManager()
    sr_high = 61780
    sr_low = 61200
    
    # BANKNIFTY Step: 100
    # CE Strike: 61800. Buffer: 61780 + 50 = 61830.
    # Should adjust to 61900 (next 100 step).
    adjusted_ce = mgr.apply_buffer(61800, sr_high, sr_low, 'CE', step=100)
    assert adjusted_ce == 61900.0
    
    # PE Strike: 61200. Buffer: 61200 - 50 = 61150.
    # Should adjust to 61100.
    adjusted_pe = mgr.apply_buffer(61200, sr_high, sr_low, 'PE', step=100)
    assert adjusted_pe == 61100.0


def test_regime_filter_gates():
    api = MagicMock()
    api.get_quotes.return_value = {'lp': '15.0'}
    
    rf = RegimeFilter(api)
    
    # Mock history for stability
    rf._vix_history = [(datetime.now().timestamp(), 15.0)] * 10
    
    # RANGING + VIX 15 + Stable = OK
    assert rf.get_regime_gate('RANGING') is True
    
    # TRENDING = BLOCKED
    assert rf.get_regime_gate('TRENDING_UP') is False
    
    # VIX 31 = BLOCKED
    api.get_quotes.return_value = {'lp': '31.0'}
    rf._vix_cache = None # clear cache
    assert rf.get_regime_gate('RANGING') is False
