"""
Unit tests for eligibility module
"""

import unittest
import sys
import os

# Add parent directory to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../../..')))

from strategies.iron_condor.eligibility import is_market_eligible
from strategies.iron_condor.config import (
    IV_PERCENTILE_MIN,
    IV_PERCENTILE_MAX,
    DAYS_TO_EXPIRY_MIN,
    DAYS_TO_EXPIRY_MAX,
    ADX_THRESHOLD,
    TARGET_INSTRUMENT,
    INSTRUMENT_TYPE
)


class TestEligibility(unittest.TestCase):
    
    def setUp(self):
        """Create base eligible market state"""
        self.eligible_state = {
            'iv_percentile': 70.0,
            'days_to_expiry': 4,
            'adx_14': 18.0,
            'has_major_event': False,
            'instrument': 'NIFTY',
            'instrument_type': 'WEEKLY'
        }
    
    def test_eligible_market(self):
        """Test that eligible market passes all checks"""
        self.assertTrue(is_market_eligible(self.eligible_state))
    
    def test_iv_percentile_too_low(self):
        """Test rejection when IV percentile is too low"""
        state = self.eligible_state.copy()
        state['iv_percentile'] = IV_PERCENTILE_MIN - 1
        self.assertFalse(is_market_eligible(state))
    
    def test_iv_percentile_too_high(self):
        """Test rejection when IV percentile is too high"""
        state = self.eligible_state.copy()
        state['iv_percentile'] = IV_PERCENTILE_MAX + 1
        self.assertFalse(is_market_eligible(state))
    
    def test_iv_percentile_at_boundaries(self):
        """Test IV percentile at min and max boundaries"""
        state_min = self.eligible_state.copy()
        state_min['iv_percentile'] = IV_PERCENTILE_MIN
        self.assertTrue(is_market_eligible(state_min))
        
        state_max = self.eligible_state.copy()
        state_max['iv_percentile'] = IV_PERCENTILE_MAX
        self.assertTrue(is_market_eligible(state_max))
    
    def test_days_to_expiry_too_low(self):
        """Test rejection when days to expiry is too low"""
        state = self.eligible_state.copy()
        state['days_to_expiry'] = DAYS_TO_EXPIRY_MIN - 1
        self.assertFalse(is_market_eligible(state))
    
    def test_days_to_expiry_too_high(self):
        """Test rejection when days to expiry is too high"""
        state = self.eligible_state.copy()
        state['days_to_expiry'] = DAYS_TO_EXPIRY_MAX + 1
        self.assertFalse(is_market_eligible(state))
    
    def test_days_to_expiry_at_boundaries(self):
        """Test days to expiry at min and max boundaries"""
        state_min = self.eligible_state.copy()
        state_min['days_to_expiry'] = DAYS_TO_EXPIRY_MIN
        self.assertTrue(is_market_eligible(state_min))
        
        state_max = self.eligible_state.copy()
        state_max['days_to_expiry'] = DAYS_TO_EXPIRY_MAX
        self.assertTrue(is_market_eligible(state_max))
    
    def test_adx_too_high(self):
        """Test rejection when ADX is too high"""
        state = self.eligible_state.copy()
        state['adx_14'] = ADX_THRESHOLD
        self.assertFalse(is_market_eligible(state))
        
        state['adx_14'] = ADX_THRESHOLD + 1
        self.assertFalse(is_market_eligible(state))
    
    def test_adx_below_threshold(self):
        """Test acceptance when ADX is below threshold"""
        state = self.eligible_state.copy()
        state['adx_14'] = ADX_THRESHOLD - 0.1
        self.assertTrue(is_market_eligible(state))
    
    def test_major_event_rejection(self):
        """Test rejection when major event is present"""
        state = self.eligible_state.copy()
        state['has_major_event'] = True
        self.assertFalse(is_market_eligible(state))
    
    def test_wrong_instrument(self):
        """Test rejection for wrong instrument"""
        state = self.eligible_state.copy()
        state['instrument'] = 'BANKNIFTY'
        self.assertFalse(is_market_eligible(state))
    
    def test_wrong_instrument_type(self):
        """Test rejection for wrong instrument type"""
        state = self.eligible_state.copy()
        state['instrument_type'] = 'MONTHLY'
        self.assertFalse(is_market_eligible(state))
    
    def test_missing_fields(self):
        """Test rejection when required fields are missing"""
        state = self.eligible_state.copy()
        del state['iv_percentile']
        self.assertFalse(is_market_eligible(state))
        
        state = self.eligible_state.copy()
        del state['days_to_expiry']
        self.assertFalse(is_market_eligible(state))
        
        state = self.eligible_state.copy()
        del state['adx_14']
        self.assertFalse(is_market_eligible(state))
    
    def test_default_has_major_event(self):
        """Test that has_major_event defaults to False if not provided"""
        state = self.eligible_state.copy()
        del state['has_major_event']
        # Should still pass if other conditions are met
        self.assertTrue(is_market_eligible(state))


if __name__ == '__main__':
    unittest.main()



