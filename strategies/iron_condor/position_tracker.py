"""
Position Tracker for Iron Condor trades
Tracks open positions and monitors profit targets
"""

import json
import os
import logging
from datetime import datetime
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


class IronCondorPositionTracker:
    """Track and monitor open Iron Condor positions"""
    
    def __init__(self, trade_proposals_dir='trade_proposals', 
                 active_positions_file='active_positions.json'):
        self.trade_proposals_dir = trade_proposals_dir
        self.active_positions_file = active_positions_file
        self.active_positions = self._load_active_positions()
    
    def _load_active_positions(self) -> List[Dict]:
        """Load active positions from file"""
        try:
            if os.path.exists(self.active_positions_file):
                with open(self.active_positions_file, 'r') as f:
                    return json.load(f)
        except Exception as e:
            logger.error(f"Error loading active positions: {e}")
        return []
    
    def _save_active_positions(self):
        """Save active positions to file"""
        try:
            with open(self.active_positions_file, 'w') as f:
                json.dump(self.active_positions, f, indent=2)
        except Exception as e:
            logger.error(f"Error saving active positions: {e}")
    
    def add_position(self, trade_proposal: Dict):
        """Add a new position to track"""
        position = {
            'trade_id': trade_proposal.get('generated_at', datetime.now().isoformat()),
            'entry_time': datetime.now().isoformat(),
            'expiry': trade_proposal['expiry'],
            'legs': trade_proposal['legs'],
            'lots': trade_proposal['lots'],
            'lot_size': trade_proposal.get('lot_size', 50),
            'entry_credit': trade_proposal.get('net_credit_total', trade_proposal.get('net_debit_total', 0)),
            'margin_used': trade_proposal.get('margin_used'),
            'profit_target_margin': trade_proposal.get('profit_target_margin'),
            'max_loss': trade_proposal['max_loss'],
            'entry_spot': trade_proposal['spot_price'],
            'strategy': trade_proposal.get('strategy', 'UNKNOWN'),
            'book': trade_proposal.get('book', 'UNKNOWN'),
            'regime_at_entry': trade_proposal.get('regime_at_entry', 'UNKNOWN'),
            'status': 'OPEN'
        }
        self.active_positions.append(position)
        self._save_active_positions()
        logger.info(f"Added position to tracker: {position['trade_id']}")
    
    def get_active_positions(self) -> List[Dict]:
        """Get all active positions"""
        return [p for p in self.active_positions if p.get('status') == 'OPEN']
    
    def calculate_current_pnl(self, position: Dict, current_prices: Dict) -> float:
        """
        Calculate current P&L for a position
        
        Args:
            position: Position dictionary
            current_prices: Dict of {option_key: current_price}
                          e.g., {'CE26200': 50.5, 'PE25800': 45.2, ...}
        
        Returns:
            Current P&L in ₹
        """
        entry_credit = position['entry_credit']
        lots = position['lots']
        lot_size = position.get('lot_size', 50)
        
        current_value = 0.0
        
        for leg in position['legs']:
            strike = int(leg['strike'])
            option_type = leg['option_type']
            entry_price = leg['price']
            quantity = leg.get('quantity', 1)  # Support different quantities (e.g., 2 for convex backspread)
            
            # Get current price
            option_key = f"{option_type}{strike}"
            current_price = current_prices.get(option_key, entry_price)
            
            # Calculate value change
            if leg['position'] == 'SHORT':
                # Short: profit when price decreases
                # Value = (entry_price - current_price) × lots × lot_size × quantity
                value = (entry_price - current_price) * lots * lot_size * quantity
            else:  # LONG
                # Long: profit when price increases
                # Value = (current_price - entry_price) × lots × lot_size × quantity
                value = (current_price - entry_price) * lots * lot_size * quantity
            
            current_value += value
        
        # P&L calculation depends on strategy type
        strategy = position.get('strategy', '').upper()
        if 'BACKSPREAD' in strategy or position.get('book') == 'CONVEX':
            # For convex backspread: entry_credit is actually net_debit (negative)
            # P&L = current_value - entry_credit (where entry_credit is negative)
            pnl = current_value - entry_credit
        else:
            # For Iron Condor: entry_credit is positive (we received it)
            # P&L = entry_credit - current_value
            pnl = entry_credit - current_value
        
        return pnl
    
    def check_profit_target(self, position: Dict, current_pnl: float) -> bool:
        """Check if profit target (1% of margin) is reached"""
        margin_used = position.get('margin_used')
        if margin_used and margin_used > 0:
            profit_target = margin_used * 0.01  # 1% of margin
            if current_pnl >= profit_target:
                return True
        return False
    
    def close_position(self, position: Dict, exit_reason: str, final_pnl: float):
        """Close a position and log performance by regime"""
        position['status'] = 'CLOSED'
        position['exit_time'] = datetime.now().isoformat()
        position['exit_reason'] = exit_reason
        position['final_pnl'] = final_pnl
        self._save_active_positions()
        
        # Log performance by regime
        self._log_performance_by_regime(position, final_pnl)
        
        logger.info(
            f"Closed position {position['trade_id']}: {exit_reason}, "
            f"P&L=₹{final_pnl:.2f}"
        )
    
    def _log_performance_by_regime(self, position: Dict, final_pnl: float):
        """Log trade performance by regime to performance_by_regime.json"""
        try:
            performance_file = 'performance_by_regime.json'
            
            # Load existing performance data
            performance_data = []
            if os.path.exists(performance_file):
                with open(performance_file, 'r') as f:
                    performance_data = json.load(f)
            
            # Create performance entry
            performance_entry = {
                "strategy": position.get('strategy', 'UNKNOWN'),
                "book": position.get('book', 'UNKNOWN'),
                "regime_at_entry": position.get('regime_at_entry', 'UNKNOWN'),
                "entry_time": position.get('entry_time', ''),
                "exit_time": position.get('exit_time', datetime.now().isoformat()),
                "pnl": final_pnl,
                "max_loss": position.get('max_loss', 0),
                "lots": position.get('lots', 0),
                "trade_id": position.get('trade_id', '')
            }
            
            performance_data.append(performance_entry)
            
            # Save to file
            with open(performance_file, 'w') as f:
                json.dump(performance_data, f, indent=2)
            
            logger.debug(f"Logged performance by regime: {performance_entry}")
            
        except Exception as e:
            logger.error(f"Error logging performance by regime: {e}")
