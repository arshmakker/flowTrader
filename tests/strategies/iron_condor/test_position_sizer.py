"""
Unit tests for position sizer module
"""

import unittest
import sys
import os

# Add parent directory to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../../..')))

from strategies.iron_condor.position_sizer import calculate_lots
from strategies.iron_condor.config import MAX_PER_TRADE_RISK


class TestPositionSizer(unittest.TestCase):
    
    def test_calculate_lots_normal(self):
        """Test normal lot calculation"""
        max_loss_per_lot = 500.0
        expected_lots = int(MAX_PER_TRADE_RISK / max_loss_per_lot)  # 60 lots
        lots = calculate_lots(max_loss_per_lot)
        self.assertEqual(lots, expected_lots)
    
    def test_calculate_lots_small_loss(self):
        """Test with small max loss per lot (more lots)"""
        max_loss_per_lot = 100.0
        expected_lots = int(MAX_PER_TRADE_RISK / max_loss_per_lot)  # 300 lots
        lots = calculate_lots(max_loss_per_lot)
        self.assertEqual(lots, expected_lots)
    
    def test_calculate_lots_large_loss(self):
        """Test with large max loss per lot (fewer lots)"""
        max_loss_per_lot = 1000.0
        expected_lots = int(MAX_PER_TRADE_RISK / max_loss_per_lot)  # 30 lots
        lots = calculate_lots(max_loss_per_lot)
        self.assertEqual(lots, expected_lots)
    
    def test_calculate_lots_exceeds_max_risk(self):
        """Test rejection when max loss per lot exceeds max risk"""
        max_loss_per_lot = MAX_PER_TRADE_RISK + 100.0
        lots = calculate_lots(max_loss_per_lot)
        self.assertEqual(lots, 0)
    
    def test_calculate_lots_equal_to_max_risk(self):
        """Test when max loss per lot equals max risk"""
        max_loss_per_lot = MAX_PER_TRADE_RISK
        lots = calculate_lots(max_loss_per_lot)
        self.assertEqual(lots, 1)
    
    def test_calculate_lots_just_below_max_risk(self):
        """Test when max loss per lot is just below max risk"""
        max_loss_per_lot = MAX_PER_TRADE_RISK - 1.0
        lots = calculate_lots(max_loss_per_lot)
        self.assertEqual(lots, 1)
    
    def test_calculate_lots_zero(self):
        """Test rejection with zero max loss"""
        lots = calculate_lots(0.0)
        self.assertEqual(lots, 0)
    
    def test_calculate_lots_negative(self):
        """Test rejection with negative max loss"""
        lots = calculate_lots(-100.0)
        self.assertEqual(lots, 0)
    
    def test_calculate_lots_flooring(self):
        """Test that lots are floored (not rounded)"""
        # If max_loss_per_lot = 333.33, then 30000 / 333.33 = 90.0009
        # Should floor to 90, not round to 90
        max_loss_per_lot = 333.33
        expected_lots = int(MAX_PER_TRADE_RISK / max_loss_per_lot)  # 90
        lots = calculate_lots(max_loss_per_lot)
        self.assertEqual(lots, expected_lots)
        self.assertEqual(lots, 90)


if __name__ == '__main__':
    unittest.main()



