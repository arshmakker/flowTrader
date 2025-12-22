"""
Unit tests for strike selector module
"""

import unittest
import sys
import os
import pandas as pd
import numpy as np

# Add parent directory to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../../..')))

from strategies.iron_condor.strike_selector import select_strikes
from strategies.iron_condor.config import (
    SHORT_CALL_DELTA_MIN,
    SHORT_CALL_DELTA_MAX,
    SHORT_PUT_DELTA_MIN,
    SHORT_PUT_DELTA_MAX,
    WING_WIDTH_MIN,
    WING_WIDTH_MAX
)


class TestStrikeSelector(unittest.TestCase):
    
    def setUp(self):
        """Create sample option chain"""
        self.spot_price = 20000.0
        
        # Create calls with delta
        calls_data = []
        for i in range(20):
            strike = 20000 + (i * 50)
            delta = 0.50 - (i * 0.02)  # Decreasing delta as strike increases
            calls_data.append({
                'strike': strike,
                'option_type': 'CE',
                'ltp': max(10, 200 - (i * 5)),
                'bid': max(9, 199 - (i * 5)),
                'ask': max(11, 201 - (i * 5)),
                'delta': delta,
                'oi': 1000 + (i * 100),
                'volume': 500 + (i * 50)
            })
        
        # Create puts with delta
        puts_data = []
        for i in range(20):
            strike = 20000 - (i * 50)
            delta = -0.50 + (i * 0.02)  # Increasing delta (less negative) as strike decreases
            puts_data.append({
                'strike': strike,
                'option_type': 'PE',
                'ltp': max(10, 200 - (i * 5)),
                'bid': max(9, 199 - (i * 5)),
                'ask': max(11, 201 - (i * 5)),
                'delta': delta,
                'oi': 1000 + (i * 100),
                'volume': 500 + (i * 50)
            })
        
        self.option_chain = pd.DataFrame(calls_data + puts_data)
    
    def test_select_strikes_with_delta(self):
        """Test strike selection with delta available"""
        legs = select_strikes(self.option_chain, self.spot_price)
        
        self.assertIn('short_call', legs)
        self.assertIn('short_put', legs)
        self.assertIn('long_call', legs)
        self.assertIn('long_put', legs)
        
        # Check short call delta
        if legs['short_call'].get('delta') is not None:
            delta = legs['short_call']['delta']
            self.assertGreaterEqual(delta, SHORT_CALL_DELTA_MIN)
            self.assertLessEqual(delta, SHORT_CALL_DELTA_MAX)
        
        # Check short put delta
        if legs['short_put'].get('delta') is not None:
            delta = legs['short_put']['delta']
            self.assertGreaterEqual(delta, SHORT_PUT_DELTA_MIN)
            self.assertLessEqual(delta, SHORT_PUT_DELTA_MAX)
        
        # Check wing widths
        call_wing = legs['long_call']['strike'] - legs['short_call']['strike']
        put_wing = legs['short_put']['strike'] - legs['long_put']['strike']
        
        self.assertGreaterEqual(call_wing, WING_WIDTH_MIN)
        self.assertLessEqual(call_wing, WING_WIDTH_MAX * 1.5)  # Allow some flexibility
        
        self.assertGreaterEqual(put_wing, WING_WIDTH_MIN)
        self.assertLessEqual(put_wing, WING_WIDTH_MAX * 1.5)  # Allow some flexibility
        
        # Check strikes are in correct order
        self.assertLess(legs['long_put']['strike'], legs['short_put']['strike'])
        self.assertLess(legs['short_put']['strike'], legs['short_call']['strike'])
        self.assertLess(legs['short_call']['strike'], legs['long_call']['strike'])
    
    def test_select_strikes_without_delta(self):
        """Test strike selection without delta (fallback to distance)"""
        # Remove delta column
        chain_no_delta = self.option_chain.drop(columns=['delta'])
        
        legs = select_strikes(chain_no_delta, self.spot_price)
        
        self.assertIn('short_call', legs)
        self.assertIn('short_put', legs)
        self.assertIn('long_call', legs)
        self.assertIn('long_put', legs)
        
        # Check that strikes are selected
        self.assertGreater(legs['short_call']['strike'], self.spot_price)
        self.assertLess(legs['short_put']['strike'], self.spot_price)
    
    def test_empty_option_chain(self):
        """Test error handling for empty option chain"""
        empty_chain = pd.DataFrame()
        with self.assertRaises(ValueError):
            select_strikes(empty_chain, self.spot_price)
    
    def test_invalid_spot_price(self):
        """Test error handling for invalid spot price"""
        with self.assertRaises(ValueError):
            select_strikes(self.option_chain, -100)
        
        with self.assertRaises(ValueError):
            select_strikes(self.option_chain, 0)
    
    def test_missing_calls_or_puts(self):
        """Test error handling when calls or puts are missing"""
        calls_only = self.option_chain[self.option_chain['option_type'] == 'CE']
        with self.assertRaises(ValueError):
            select_strikes(calls_only, self.spot_price)
        
        puts_only = self.option_chain[self.option_chain['option_type'] == 'PE']
        with self.assertRaises(ValueError):
            select_strikes(puts_only, self.spot_price)
    
    def test_strike_format(self):
        """Test that returned strikes have correct format"""
        legs = select_strikes(self.option_chain, self.spot_price)
        
        for leg_name, leg in legs.items():
            self.assertIn('strike', leg)
            self.assertIn('option_type', leg)
            self.assertIn('ltp', leg)
            self.assertIn('bid', leg)
            self.assertIn('ask', leg)
            self.assertIn('oi', leg)
            self.assertIn('volume', leg)
            self.assertIn('mid_price', leg)
            
            self.assertIsInstance(leg['strike'], (int, float))
            self.assertIsInstance(leg['ltp'], (int, float))
            self.assertIsInstance(leg['oi'], (int, np.integer))
            self.assertIsInstance(leg['volume'], (int, np.integer))


if __name__ == '__main__':
    unittest.main()



