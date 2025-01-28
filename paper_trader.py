import pandas as pd
import numpy as np
from datetime import datetime, time
import logging
import os
from typing import Dict, List, Tuple
from data_collector import DataCollector

class PaperTrader:
    def __init__(self, data_collector: DataCollector, initial_capital: float = 900000):
        self.data_collector = data_collector
        self.initial_capital = initial_capital
        self.current_capital = initial_capital
        self.daily_pnl = 0
        self.trades = []
        self.active_positions = {}
        self.last_processed_time = {}
        
        # Index-specific parameters
        self.index_params = {
            'NIFTY': {
                'min_movement': 5,
                'initial_stop': 8,
                'target1': 8,
                'target2': 12,
                'trail_points': 3,
                'lot_size': 50,
                'margin_per_lot': 23000,
                'max_lots': 3
            },
            'BANKNIFTY': {
                'min_movement': 12,
                'initial_stop': 15,
                'target1': 20,
                'target2': 30,
                'trail_points': 6,
                'lot_size': 15,  # Updated based on actual lot size
                'margin_per_lot': 49000,
                'max_lots': 2
            },
            'FINNIFTY': {
                'min_movement': 8,
                'initial_stop': 12,
                'target1': 15,
                'target2': 22,
                'trail_points': 4,
                'lot_size': 40,
                'margin_per_lot': 23000,
                'max_lots': 3
            }
        }
        
        self.setup_logging()

    def setup_logging(self):
        """Setup logging configuration"""
        self.logger = logging.getLogger('PaperTrader')

    def is_trading_time(self) -> bool:
        """Check if current time is within trading hours"""
        current_time = datetime.now().time()
        morning_start = time(9, 30)
        morning_end = time(11, 30)
        afternoon_start = time(13, 30)
        afternoon_end = time(15, 15)
        
        return ((morning_start <= current_time <= morning_end) or 
                (afternoon_start <= current_time <= afternoon_end))

    def get_latest_data(self, symbol: str, lookback: int = 20) -> pd.DataFrame:
        """Get latest market data for a symbol"""
        try:
            # Construct the data file path
            date_str = datetime.now().strftime('%Y%m%d')
            file_path = os.path.join(
                self.data_collector.data_directory,
                'raw_data',
                f'{symbol}_{date_str}.csv'
            )
            
            if not os.path.exists(file_path):
                self.logger.warning(f"No data file found for {symbol}")
                return None
                
            # Read the latest data
            df = pd.read_csv(file_path)
            if len(df) == 0:
                return None
                
            # Convert timestamp to datetime
            df['timestamp'] = pd.to_datetime(df['timestamp'])
            
            # Get only new data since last processing
            if symbol in self.last_processed_time:
                df = df[df['timestamp'] > self.last_processed_time[symbol]]
                
            if len(df) == 0:
                return None
                
            # Update last processed time
            self.last_processed_time[symbol] = df['timestamp'].max()
            
            # Return the latest records
            return df.tail(lookback)
            
        except Exception as e:
            self.logger.error(f"Error reading data for {symbol}: {str(e)}")
            return None

    def check_entry_conditions(self, data: pd.DataFrame, index: str) -> Tuple[bool, str, float]:
        """Check if entry conditions are met"""
        if data is None or len(data) < 20:
            return False, "", 0
            
        params = self.index_params[index]
        
        # Get latest data points
        recent_data = data.tail(20)
        current_price = recent_data['ltp'].iloc[-1]
        
        # Calculate price movement
        price_change = recent_data['ltp'].diff()
        volume = recent_data['volume']
        avg_volume = volume.mean()
        
        # Check volume conditions
        volume_confirmed = (volume.iloc[-1] > avg_volume * 1.2)  # 20% above average
        
        # Check price movement conditions
        if price_change.iloc[-1] >= params['min_movement'] and volume_confirmed:
            return True, "BUY", current_price
        elif price_change.iloc[-1] <= -params['min_movement'] and volume_confirmed:
            return True, "SELL", current_price
            
        return False, "", 0

    def process_market_data(self):
        """Process latest market data and execute strategy"""
        if not self.is_trading_time():
            return
            
        # Get active symbols from data collector
        symbols = self.data_collector.get_index_symbols()
        if not symbols:
            return
            
        # Process each symbol
        for symbol_info in symbols:
            symbol = symbol_info['symbol']
            index = symbol_info['index_name']
            
            # Get latest data
            latest_data = self.get_latest_data(symbol)
            if latest_data is None:
                continue
                
            # Check for new entries if no position exists
            if index not in self.active_positions:
                entry_allowed, direction, entry_price = self.check_entry_conditions(latest_data, index)
                if entry_allowed:
                    self.execute_trade(index, direction, entry_price, symbol_info)
            
            # Manage existing position
            if index in self.active_positions:
                self.manage_position(index, latest_data)

    def execute_trade(self, index: str, direction: str, entry_price: float, symbol_info: dict):
        """Execute new trade with position sizing"""
        if not self.can_take_new_trade():
            return
            
        params = self.index_params[index]
        lots = self.calculate_lots(index)
        
        # Calculate initial position size (60% of total intended size)
        initial_lots = max(1, int(lots * 0.6))
        
        stop_loss = (entry_price - params['initial_stop']) if direction == "BUY" else (entry_price + params['initial_stop'])
        target1 = (entry_price + params['target1']) if direction == "BUY" else (entry_price - params['target1'])
        target2 = (entry_price + params['target2']) if direction == "BUY" else (entry_price - params['target2'])
        
        trade = {
            'index': index,
            'symbol': symbol_info['symbol'],
            'direction': direction,
            'entry_price': entry_price,
            'lots': initial_lots,
            'stop_loss': stop_loss,
            'target1': target1,
            'target2': target2,
            'remaining_lots': initial_lots,
            'entry_time': datetime.now(),
            'trail_stop': None
        }
        
        self.active_positions[index] = trade
        self.logger.info(f"Executed {direction} trade in {index}: {trade}")

    def manage_position(self, index: str, current_data: pd.DataFrame):
        """Manage existing position"""
        if current_data is None or len(current_data) == 0:
            return
            
        position = self.active_positions[index]
        current_price = current_data['ltp'].iloc[-1]
        
        self.check_exits(index, position, current_price)
        self.update_trailing_stop(index, position, current_price)

    def get_position_summary(self) -> dict:
        """Get summary of current positions and PnL"""
        summary = {
            'active_positions': len(self.active_positions),
            'daily_pnl': self.daily_pnl,
            'current_capital': self.current_capital,
            'positions': []
        }
        
        for index, position in self.active_positions.items():
            pos_summary = {
                'index': index,
                'symbol': position['symbol'],
                'direction': position['direction'],
                'entry_price': position['entry_price'],
                'remaining_lots': position['remaining_lots'],
                'stop_loss': position['stop_loss'],
                'trail_stop': position['trail_stop']
            }
            summary['positions'].append(pos_summary)
            
        return summary

    # Include all other methods from IndexTrader (calculate_lots, check_exits, update_trailing_stop, etc.)
    # Just ensure they use the correct lot sizes and other parameters from our actual data

    def place_order(self, symbol, quantity, side, order_type, price=None):
        """Simulate order placement"""
        # Validate symbol
        symbol_base = self._get_symbol_base(symbol)
        if symbol_base not in self.index_params:
            self.logger.warning(f"Invalid symbol {symbol}. Only NIFTY, BANKNIFTY, and FINNIFTY are supported.")
            return None
            
        # Validate lot size
        lot_size = self.index_params[symbol_base]['lot_size']
        if quantity % lot_size != 0:
            self.logger.warning(f"Quantity must be multiple of lot size {lot_size} for {symbol_base}")
            return None
            
        order_id = f"PAPER_{datetime.now().strftime('%Y%m%d%H%M%S')}"
        
        execution_price = self._get_execution_price(side, symbol, price, symbol_base)
        if execution_price is None:
            self.logger.warning(f"Could not get execution price for {symbol}")
            return None
            
        margin_required = self._calculate_margin(execution_price, quantity, symbol_base)
        
        if margin_required > self.current_capital:
            self.logger.warning(f"Insufficient margin for trade. Required: {margin_required}, Available: {self.current_capital}")
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
        
        self.active_positions[symbol_base] = trade
        self.current_capital -= margin_required
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
                        slippage_std = self.index_params.get(symbol_base, {}).get('slippage_std', 0.0002)
                        
                        # Apply random slippage using normal distribution
                        slippage_factor = np.random.normal(0, slippage_std)
                        # For buys, slippage increases price, for sells it decreases
                        slippage_direction = 1 if side == 'BUY' else -1
                        execution_price = base_price * (1 + slippage_factor * slippage_direction)
                        
                        # Round to tick size if specified
                        if symbol_base and 'tick_size' in self.index_params[symbol_base]:
                            tick_size = self.index_params[symbol_base]['tick_size']
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
        if symbol_base in self.index_params:
            margin_per_lot = self.index_params[symbol_base]['margin_per_lot']
            return price * quantity * margin_per_lot
        return price * quantity * 0.2  # Default 20% margin requirement

    def close_position(self, order_id, price=None):
        """Simulate closing a position"""
        if order_id not in self.active_positions:
            self.logger.warning(f"Position {order_id} not found")
            return False
        
        position = self.active_positions[order_id]
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
        self.current_capital += margin_used
        
        # Move to history
        self.trades.append(position)
        del self.active_positions[order_id]
        
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
        
        for pos in self.active_positions.values():
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
            'current_capital': self.current_capital,
            'positions': position_summary
        } 