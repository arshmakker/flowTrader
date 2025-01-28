import pandas as pd
from datetime import datetime
import logging
import os
import json
import random
import numpy as np

class PaperTrader:
    def __init__(self, capital=100000, data_collector=None, slippage_std=0.0002):
        self.capital = capital
        self.available_margin = capital
        self.positions = {}
        self.trades_history = []
        self.logger = logging.getLogger('PaperTrader')
        self.trade_log_file = f"paper_trades_{datetime.now().strftime('%Y%m%d')}.csv"
        self.data_collector = data_collector
        self.slippage_std = slippage_std  # 0.02% standard deviation for slippage
        
        # Define index futures specifications
        self.index_specs = {
            'NIFTY': {
                'lot_size': 50,
                'margin_pct': 0.12,  # 12% margin requirement
                'tick_size': 0.05,
                'slippage_std': 0.0001  # Lower slippage for Nifty (0.01%)
            },
            'BANKNIFTY': {
                'lot_size': 15,
                'margin_pct': 0.15,  # 15% margin requirement
                'tick_size': 0.05,
                'slippage_std': 0.00015  # Slightly higher slippage (0.015%)
            },
            'FINNIFTY': {
                'lot_size': 40,
                'margin_pct': 0.12,  # 12% margin requirement
                'tick_size': 0.05,
                'slippage_std': 0.00012  # Medium slippage (0.012%)
            }
        }
        
    def place_order(self, symbol, quantity, side, order_type, price=None):
        """Simulate order placement"""
        # Validate symbol
        symbol_base = self._get_symbol_base(symbol)
        if symbol_base not in self.index_specs:
            self.logger.warning(f"Invalid symbol {symbol}. Only NIFTY, BANKNIFTY, and FINNIFTY are supported.")
            return None
            
        # Validate lot size
        lot_size = self.index_specs[symbol_base]['lot_size']
        if quantity % lot_size != 0:
            self.logger.warning(f"Quantity must be multiple of lot size {lot_size} for {symbol_base}")
            return None
            
        order_id = f"PAPER_{datetime.now().strftime('%Y%m%d%H%M%S')}"
        
        execution_price = self._get_execution_price(side, symbol, price, symbol_base)
        if execution_price is None:
            self.logger.warning(f"Could not get execution price for {symbol}")
            return None
            
        margin_required = self._calculate_margin(execution_price, quantity, symbol_base)
        
        if margin_required > self.available_margin:
            self.logger.warning(f"Insufficient margin for trade. Required: {margin_required}, Available: {self.available_margin}")
            return None
        
        trade = {
            'order_id': order_id,
            'symbol': symbol,
            'symbol_base': symbol_base,
            'quantity': quantity,
            'lots': quantity // lot_size,
            'side': side,
            'entry_price': execution_price,
            'entry_time': datetime.now(),
            'status': 'OPEN',
            'pnl': 0
        }
        
        self.positions[order_id] = trade
        self.available_margin -= margin_required
        self._log_trade(trade)
        
        self.logger.info(f"Paper trade placed: {trade}")
        return order_id

    def _get_symbol_base(self, symbol):
        """Extract base symbol from full symbol name"""
        if 'NIFTY' in symbol.upper():
            if 'FINNIFTY' in symbol.upper():
                return 'FINNIFTY'
            elif 'BANKNIFTY' in symbol.upper():
                return 'BANKNIFTY'
            else:
                return 'NIFTY'
        return None

    def _get_execution_price(self, side, symbol, requested_price=None, symbol_base=None):
        """Get execution price with realistic slippage and spread consideration"""
        try:
            # If we have a data collector, try to get real market data
            if self.data_collector:
                market_data = None
                while not self.data_collector.data_queue.empty():
                    data = self.data_collector.data_queue.get()
                    if data['symbol'] == symbol:
                        market_data = data
                        break
                
                if market_data:
                    # Use bid/ask prices based on side
                    base_price = market_data['ask'] if side == 'BUY' else market_data['bid']
                    
                    # If no bid/ask available, use LTP
                    if base_price == 0:
                        base_price = market_data['ltp']
                        
                    # If we still don't have a valid price, use requested price
                    if base_price == 0 and requested_price:
                        base_price = requested_price
                        
                    if base_price > 0:
                        # Use symbol-specific slippage if available
                        slippage_std = self.index_specs.get(symbol_base, {}).get('slippage_std', self.slippage_std)
                        
                        # Apply random slippage using normal distribution
                        slippage_factor = np.random.normal(0, slippage_std)
                        # For buys, slippage increases price, for sells it decreases
                        slippage_direction = 1 if side == 'BUY' else -1
                        execution_price = base_price * (1 + slippage_factor * slippage_direction)
                        
                        # Round to tick size if specified
                        if symbol_base and 'tick_size' in self.index_specs[symbol_base]:
                            tick_size = self.index_specs[symbol_base]['tick_size']
                            execution_price = round(execution_price / tick_size) * tick_size
                        
                        # Log the slippage details
                        self.logger.debug(f"Price execution details for {symbol}: Base Price={base_price:.2f}, " +
                                        f"Slippage={slippage_factor*100:.4f}%, Final Price={execution_price:.2f}")
                        
                        return execution_price
            
            # Fallback to requested price if available
            if requested_price:
                return requested_price
                
            # Final fallback to dummy price
            self.logger.warning(f"Using fallback price for {symbol} due to no market data")
            return 100.0
            
        except Exception as e:
            self.logger.error(f"Error getting execution price: {str(e)}")
            return None

    def _calculate_margin(self, price, quantity, symbol_base):
        """Calculate required margin based on symbol specifications"""
        if symbol_base in self.index_specs:
            margin_pct = self.index_specs[symbol_base]['margin_pct']
            return price * quantity * margin_pct
        return price * quantity * 0.2  # Default 20% margin requirement

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
        margin_used = self._calculate_margin(position['entry_price'], position['quantity'], position['symbol_base'])
        self.available_margin += margin_used
        
        # Move to history
        self.trades_history.append(position)
        del self.positions[order_id]
        
        self._log_trade(position)
        self.logger.info(f"Position closed: {position}")
        return True

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