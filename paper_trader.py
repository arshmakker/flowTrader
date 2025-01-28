import pandas as pd
from datetime import datetime
import logging
import os
import json

class PaperTrader:
    def __init__(self, capital=100000):
        self.capital = capital
        self.available_margin = capital
        self.positions = {}
        self.trades_history = []
        self.logger = logging.getLogger('PaperTrader')
        self.trade_log_file = f"paper_trades_{datetime.now().strftime('%Y%m%d')}.csv"
        
    def place_order(self, symbol, quantity, side, order_type, price=None):
        """Simulate order placement"""
        order_id = f"PAPER_{datetime.now().strftime('%Y%m%d%H%M%S')}"
        
        if price is None:
            price = self._get_execution_price(side, symbol)
        
        margin_required = self._calculate_margin(price, quantity)
        
        if margin_required > self.available_margin:
            self.logger.warning(f"Insufficient margin for trade. Required: {margin_required}, Available: {self.available_margin}")
            return None
        
        trade = {
            'order_id': order_id,
            'symbol': symbol,
            'quantity': quantity,
            'side': side,
            'entry_price': price,
            'entry_time': datetime.now(),
            'status': 'OPEN',
            'pnl': 0
        }
        
        self.positions[order_id] = trade
        self.available_margin -= margin_required
        self._log_trade(trade)
        
        self.logger.info(f"Paper trade placed: {trade}")
        return order_id

    def close_position(self, order_id, price=None):
        """Simulate closing a position"""
        if order_id not in self.positions:
            self.logger.warning(f"Position {order_id} not found")
            return False
        
        position = self.positions[order_id]
        if price is None:
            price = self._get_execution_price('SELL' if position['side'] == 'BUY' else 'BUY', position['symbol'])
        
        # Calculate P&L
        if position['side'] == 'BUY':
            pnl = (price - position['entry_price']) * position['quantity']
        else:
            pnl = (position['entry_price'] - price) * position['quantity']
        
        position['exit_price'] = price
        position['exit_time'] = datetime.now()
        position['status'] = 'CLOSED'
        position['pnl'] = pnl
        
        # Release margin
        margin_used = self._calculate_margin(position['entry_price'], position['quantity'])
        self.available_margin += margin_used
        
        # Move to history
        self.trades_history.append(position)
        del self.positions[order_id]
        
        self._log_trade(position)
        self.logger.info(f"Position closed: {position}")
        return True

    def _get_execution_price(self, side, symbol):
        """Simulate execution price with some slippage"""
        # In real implementation, this would use the actual market data
        # For now, we'll use a dummy price
        return 100.0  # Replace with actual market data

    def _calculate_margin(self, price, quantity):
        """Calculate required margin"""
        # Simplified margin calculation
        return price * quantity * 0.2  # 20% margin requirement

    def _log_trade(self, trade):
        """Log trade to CSV file"""
        try:
            df = pd.DataFrame([trade])
            if not os.path.exists(self.trade_log_file):
                df.to_csv(self.trade_log_file, index=False)
                self.logger.info(f"Created trade log file: {self.trade_log_file}")
            else:
                df.to_csv(self.trade_log_file, mode='a', header=False, index=False)
                
        except Exception as e:
            self.logger.error(f"Error logging trade: {str(e)}")

    def get_position_summary(self):
        """Get summary of current positions"""
        total_pnl = 0
        position_summary = []
        
        for pos in self.positions.values():
            current_price = self._get_execution_price(
                'SELL' if pos['side'] == 'BUY' else 'BUY',
                pos['symbol']
            )
            
            if pos['side'] == 'BUY':
                unrealized_pnl = (current_price - pos['entry_price']) * pos['quantity']
            else:
                unrealized_pnl = (pos['entry_price'] - current_price) * pos['quantity']
            
            total_pnl += unrealized_pnl
            position_summary.append({
                'symbol': pos['symbol'],
                'side': pos['side'],
                'quantity': pos['quantity'],
                'entry_price': pos['entry_price'],
                'current_price': current_price,
                'unrealized_pnl': unrealized_pnl
            })
        
        return {
            'total_pnl': total_pnl,
            'available_margin': self.available_margin,
            'positions': position_summary
        } 