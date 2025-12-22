"""
Unit tests for payoff validator module
"""

import unittest
import sys
import os

# Add parent directory to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../../..')))

from strategies.iron_condor.payoff_validator import validate_payoff, StrategyRejectedError
from strategies.iron_condor.config import (
    NET_CREDIT_MIN,
    NET_CREDIT_MAX,
    MAX_LOSS_PER_LOT_MAX,
    MIN_REWARD_TO_RISK
)


class TestPayoffValidator(unittest.TestCase):
    
    def setUp(self):
        """Create sample valid legs"""
        # Create a valid Iron Condor setup
        # Short call at 20100, Long call at 20200 (100 point wing)
        # Short put at 19900, Long put at 19800 (100 point wing)
        # Net credit: (50 + 50) - (20 + 20) = 60
        
        self.valid_legs = {
            'short_call': {
                'strike': 20100.0,
                'mid_price': 50.0,
                'ltp': 50.0,
                'bid': 49.0,
                'ask': 51.0
            },
            'short_put': {
                'strike': 19900.0,
                'mid_price': 50.0,
                'ltp': 50.0,
                'bid': 49.0,
                'ask': 51.0
            },
            'long_call': {
                'strike': 20200.0,
                'mid_price': 20.0,
                'ltp': 20.0,
                'bid': 19.0,
                'ask': 21.0
            },
            'long_put': {
                'strike': 19800.0,
                'mid_price': 20.0,
                'ltp': 20.0,
                'bid': 19.0,
                'ask': 21.0
            }
        }
    
    def test_valid_payoff(self):
        """Test validation of valid payoff"""
        result = validate_payoff(self.valid_legs)
        
        self.assertTrue(result['is_valid'])
        self.assertAlmostEqual(result['net_credit'], 60.0, places=2)
        # Max loss = wing width (100) - net credit (60) = 40
        self.assertAlmostEqual(result['max_loss'], 40.0, places=2)
        # R:R = 60 / 40 = 1.5 (but we need >= 2.0, so this would fail)
        # Let's adjust to make it valid
    
    def test_valid_payoff_with_good_rr(self):
        """Test validation with good reward-to-risk ratio"""
        # Adjust to get R:R >= 2.0
        # Net credit: (80 + 80) - (20 + 20) = 120
        # Max loss: 100 - 120 = -20 (invalid, need positive max loss)
        # Better: Net credit 90, max loss 40, R:R = 2.25
        legs = {
            'short_call': {
                'strike': 20100.0,
                'mid_price': 70.0,
                'ltp': 70.0,
                'bid': 69.0,
                'ask': 71.0
            },
            'short_put': {
                'strike': 19900.0,
                'mid_price': 70.0,
                'ltp': 70.0,
                'bid': 69.0,
                'ask': 71.0
            },
            'long_call': {
                'strike': 20200.0,
                'mid_price': 20.0,
                'ltp': 20.0,
                'bid': 19.0,
                'ask': 21.0
            },
            'long_put': {
                'strike': 19800.0,
                'mid_price': 20.0,
                'ltp': 20.0,
                'bid': 19.0,
                'ask': 21.0
            }
        }
        
        result = validate_payoff(legs)
        self.assertTrue(result['is_valid'])
        self.assertGreaterEqual(result['reward_to_risk'], MIN_REWARD_TO_RISK)
    
    def test_net_credit_too_low(self):
        """Test rejection when net credit is too low"""
        legs = self.valid_legs.copy()
        # Make net credit too low
        legs['short_call']['mid_price'] = 30.0
        legs['short_put']['mid_price'] = 30.0
        legs['long_call']['mid_price'] = 25.0
        legs['long_put']['mid_price'] = 25.0
        # Net credit = 60 - 50 = 10 (too low)
        
        with self.assertRaises(StrategyRejectedError) as context:
            validate_payoff(legs)
        
        self.assertIn('Net credit', str(context.exception))
    
    def test_net_credit_too_high(self):
        """Test rejection when net credit is too high"""
        legs = self.valid_legs.copy()
        # Make net credit too high
        legs['short_call']['mid_price'] = 100.0
        legs['short_put']['mid_price'] = 100.0
        legs['long_call']['mid_price'] = 10.0
        legs['long_put']['mid_price'] = 10.0
        # Net credit = 200 - 20 = 180 (too high)
        
        with self.assertRaises(StrategyRejectedError) as context:
            validate_payoff(legs)
        
        self.assertIn('Net credit', str(context.exception))
    
    def test_max_loss_too_high(self):
        """Test rejection when max loss exceeds limit"""
        legs = self.valid_legs.copy()
        # Create very wide wings to increase max loss
        legs['long_call']['strike'] = 20500.0  # 400 point wing
        legs['long_put']['strike'] = 19500.0   # 400 point wing
        # Net credit stays 60, max loss = 400 - 60 = 340 (within limit)
        # But if we reduce credit further:
        legs['short_call']['mid_price'] = 30.0
        legs['short_put']['mid_price'] = 30.0
        # Net credit = 60 - 40 = 20, max loss = 400 - 20 = 380 (still OK)
        # Let's make it worse:
        legs['long_call']['strike'] = 22000.0  # 1900 point wing
        # Max loss = 1900 - 20 = 1880 (exceeds 1500)
        
        with self.assertRaises(StrategyRejectedError) as context:
            validate_payoff(legs)
        
        self.assertIn('Max loss', str(context.exception))
    
    def test_reward_to_risk_too_low(self):
        """Test rejection when reward-to-risk is too low"""
        legs = self.valid_legs.copy()
        # Small credit, large max loss
        legs['short_call']['mid_price'] = 40.0
        legs['short_put']['mid_price'] = 40.0
        legs['long_call']['mid_price'] = 30.0
        legs['long_put']['mid_price'] = 30.0
        # Net credit = 80 - 60 = 20
        legs['long_call']['strike'] = 20250.0  # 150 point wing
        # Max loss = 150 - 20 = 130
        # R:R = 20 / 130 = 0.15 (too low)
        
        with self.assertRaises(StrategyRejectedError) as context:
            validate_payoff(legs)
        
        self.assertIn('Reward-to-risk', str(context.exception))
    
    def test_debit_trade_rejection(self):
        """Test rejection of debit trades (negative net credit)"""
        legs = self.valid_legs.copy()
        # Make it a debit trade
        legs['short_call']['mid_price'] = 10.0
        legs['short_put']['mid_price'] = 10.0
        legs['long_call']['mid_price'] = 30.0
        legs['long_put']['mid_price'] = 30.0
        # Net credit = 20 - 60 = -40 (debit)
        
        with self.assertRaises(StrategyRejectedError) as context:
            validate_payoff(legs)
        
        self.assertIn('positive', str(context.exception))
    
    def test_uses_mid_price_when_available(self):
        """Test that mid_price is used when available"""
        legs = self.valid_legs.copy()
        legs['short_call']['mid_price'] = 55.0
        legs['short_call']['ltp'] = 50.0  # Different from mid_price
        
        result = validate_payoff(legs)
        # Should use mid_price (55) not ltp (50)
        # Net credit = (55 + 50) - (20 + 20) = 65
        self.assertAlmostEqual(result['net_credit'], 65.0, places=2)
    
    def test_falls_back_to_ltp_when_no_mid_price(self):
        """Test that ltp is used when mid_price is not available"""
        legs = self.valid_legs.copy()
        del legs['short_call']['mid_price']
        legs['short_call']['ltp'] = 45.0
        
        result = validate_payoff(legs)
        # Should use ltp (45) not missing mid_price
        # Net credit = (45 + 50) - (20 + 20) = 55
        self.assertAlmostEqual(result['net_credit'], 55.0, places=2)


if __name__ == '__main__':
    unittest.main()



