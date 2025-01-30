import pandas as pd
import numpy as np
from datetime import datetime, time, date
import logging
import os
from typing import Dict, List, Tuple
from data_collector import DataCollector
import json
import csv
from colorama import Fore, Style

class PaperTrader:
    def __init__(self, data_collector: DataCollector, initial_capital: float = 900000):
        self.data_collector = data_collector
        self.initial_capital = initial_capital
        self.current_capital = initial_capital
        self.daily_pnl = 0
        self.trades = []
        self.active_positions = {}
        self.last_processed_time = {}
        
        # Cache for symbol information
        self._symbol_cache = {}
        self._update_symbol_cache()
        
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
                'max_lots': 3,
                'tick_size': 0.05,
                'slippage_std': 0.0002,
                'min_movement_multiplier': 1.5
            },
            'BANKNIFTY': {
                'min_movement': 12,
                'initial_stop': 15,
                'target1': 20,
                'target2': 30,
                'trail_points': 6,
                'lot_size': 15,
                'margin_per_lot': 49000,
                'max_lots': 2,
                'tick_size': 0.1,
                'slippage_std': 0.0003,
                'min_movement_multiplier': 2
            },
            'FINNIFTY': {
                'min_movement': 8,
                'initial_stop': 12,
                'target1': 15,
                'target2': 22,
                'trail_points': 4,
                'lot_size': 40,
                'margin_per_lot': 23000,
                'max_lots': 3,
                'tick_size': 0.05,
                'slippage_std': 0.0002,
                'min_movement_multiplier': 1.8
            }
        }
        
        self.setup_logging()
        self.setup_trade_logging()

    def setup_logging(self):
        """Setup logging configuration"""
        self.logger = logging.getLogger('PaperTrader')
        
        # Create a trade log directory if it doesn't exist
        self.trade_log_dir = 'logs/paper_trades'
        if not os.path.exists(self.trade_log_dir):
            os.makedirs(self.trade_log_dir)

    def setup_trade_logging(self):
        """Setup trade logging files"""
        today = datetime.now().strftime('%Y%m%d')
        self.trade_log_file = os.path.join(self.trade_log_dir, f'paper_trades_{today}.csv')
        self.position_log_file = os.path.join(self.trade_log_dir, f'positions_{today}.csv')
        
        # Create trade log if it doesn't exist
        if not os.path.exists(self.trade_log_file):
            with open(self.trade_log_file, 'w', newline='') as f:
                writer = csv.writer(f)
                writer.writerow(['timestamp', 'symbol', 'action', 'direction', 'price', 
                               'quantity', 'pnl', 'remaining_capital', 'reason'])

        # Create position log if it doesn't exist
        if not os.path.exists(self.position_log_file):
            with open(self.position_log_file, 'w', newline='') as f:
                writer = csv.writer(f)
                writer.writerow(['timestamp', 'symbol', 'direction', 'entry_price', 
                               'current_price', 'quantity', 'unrealized_pnl', 'stop_loss', 'targets'])

    def log_trade(self, symbol: str, action: str, direction: str, price: float, 
                  quantity: int, pnl: float, reason: str):
        """Log trade details to CSV"""
        with open(self.trade_log_file, 'a', newline='') as f:
            writer = csv.writer(f)
            writer.writerow([
                datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                symbol,
                action,
                direction,
                price,
                quantity,
                pnl,
                self.current_capital,
                reason
            ])
        
        self.logger.info(
            f"{Fore.CYAN}TRADE: {symbol} {action} {direction} | "
            f"Price: {price:.2f} | Qty: {quantity} | "
            f"PnL: {pnl:.2f} | Reason: {reason}{Style.RESET_ALL}"
        )

    def log_position(self, symbol: str, position: dict):
        """Log current position details"""
        with open(self.position_log_file, 'a', newline='') as f:
            writer = csv.writer(f)
            writer.writerow([
                datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                symbol,
                position['direction'],
                position['entry_price'],
                position['current_price'],
                position['quantity'],
                position['unrealized_pnl'],
                position['stop_loss'],
                json.dumps(position['targets'])
            ])

    def is_trading_time(self) -> bool:
        """Check if current time is within trading hours"""
        current_time = datetime.now().time()
        morning_start = time(9, 30)
        morning_end = time(11, 30)
        afternoon_start = time(13, 30)
        afternoon_end = time(15, 15)
        
        return ((morning_start <= current_time <= morning_end) or 
                (afternoon_start <= current_time <= afternoon_end))

    def _update_symbol_cache(self):
        """Update the cache of symbol information"""
        symbols = self.data_collector.get_index_symbols()
        self._symbol_cache = {
            s['index_name']: {
                'symbol': s['symbol'],
                'token': s['token']
            } for s in symbols
        }
        
    def get_latest_data(self, symbol: str, lookback: int = 20) -> pd.DataFrame:
        """Get latest market data for a symbol"""
        try:
            # Use cached symbol information
            if symbol not in self._symbol_cache:
                self.logger.warning(f"No futures symbol found for {symbol}")
                return None
            
            symbol_info = self._symbol_cache[symbol]
            
            # Get live quote from API
            quote = self.data_collector.api.get_quotes(
                exchange='NFO',
                token=symbol_info['token']
            )
            
            if not quote:
                self.logger.warning(f"No live quote available for {symbol}")
                return None
            
            # Create a DataFrame with the live quote
            data_point = {
                'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f'),
                'symbol': symbol_info['symbol'],
                'index_name': symbol,
                'ltp': float(quote.get('lp', 0)),
                'volume': int(quote.get('v', 0)),
                'bid': float(quote.get('bp1', 0)),
                'ask': float(quote.get('sp1', 0)),
                'oi': int(quote.get('oi', 0)),
                'bid_qty': int(quote.get('bq1', 0)),
                'ask_qty': int(quote.get('sq1', 0))
            }
            
            # Create DataFrame with live data
            df = pd.DataFrame([data_point])
            df['timestamp'] = pd.to_datetime(df['timestamp'])
            
            return df
            
        except Exception as e:
            self.logger.error(f"Error getting live data for {symbol}: {str(e)}")
            return None

    def calculate_position_size(self, symbol: str) -> int:
        """Calculate position size based on risk parameters"""
        params = self.index_params[symbol]
        available_margin = self.current_capital * 0.3  # Max 30% capital per trade
        max_lots = min(
            params['max_lots'],
            int(available_margin / params['margin_per_lot'])
        )
        return max_lots * params['lot_size']

    def can_take_new_trade(self) -> bool:
        """Check if new trades are allowed based on risk parameters"""
        today = date.today()
        
        # Check daily loss limit (2%)
        if abs(self.daily_pnl) > self.initial_capital * 0.02:
            self.logger.warning(f"{Fore.YELLOW}Daily loss limit reached. No new trades.{Style.RESET_ALL}")
            return False
            
        # Check maximum trades per day (3)
        today_trades = [t for t in self.trades if t['date'].date() == today]
        if len(today_trades) >= 3:
            self.logger.warning(f"{Fore.YELLOW}Maximum daily trades reached.{Style.RESET_ALL}")
            return False
            
        # Check consecutive losses
        if len(self.trades) >= 2:
            last_two_trades = self.trades[-2:]
            if all(t['pnl'] < 0 for t in last_two_trades):
                self.logger.warning(f"{Fore.YELLOW}Two consecutive losses. No new trades.{Style.RESET_ALL}")
                return False
        
        return True

    def check_entry_conditions(self, data: pd.DataFrame, symbol: str) -> Tuple[bool, str, float]:
        """Check entry conditions for a new trade using live data"""
        if data is None or len(data) == 0:
            return False, None, None
            
        # Get current price and parameters
        current_price = data['ltp'].iloc[-1]
        bid = data['bid'].iloc[-1]
        ask = data['ask'].iloc[-1]
        volume = data['volume'].iloc[-1]
        oi = data['oi'].iloc[-1]
        
        # Store last processed values for comparison
        if symbol not in self.last_processed_time:
            self.last_processed_time[symbol] = {
                'price': current_price,
                'volume': volume,
                'oi': oi,
                'time': datetime.now()
            }
            return False, None, None
            
        last_data = self.last_processed_time[symbol]
        time_diff = (datetime.now() - last_data['time']).total_seconds()
        
        # Only process if at least 5 seconds have passed
        if time_diff < 5:
            return False, None, None
            
        # Calculate changes
        price_change = current_price - last_data['price']
        volume_change = volume - last_data['volume']
        oi_change = oi - last_data['oi']
        
        min_movement = self.index_params[symbol]['min_movement']
        
        # Update last processed values
        self.last_processed_time[symbol] = {
            'price': current_price,
            'volume': volume,
            'oi': oi,
            'time': datetime.now()
        }
        
        # Entry conditions:
        # 1. Price movement exceeds minimum movement
        # 2. Volume is increasing
        # 3. Open Interest is increasing (showing new positions)
        # 4. Bid-Ask spread is reasonable
        if (abs(price_change) >= min_movement and 
            volume_change > 0 and 
            oi_change > 0 and 
            (ask - bid) <= min_movement):
            
            # Determine direction based on price change
            direction = 'BUY' if price_change > 0 else 'SELL'
            return True, direction, current_price
            
        return False, None, None

    def execute_trade(self, symbol: str, direction: str, price: float, params: dict):
        """Execute a new trade"""
        quantity = self.calculate_position_size(symbol)
        
        # Apply simulated slippage
        slippage = np.random.normal(0, params['slippage_std'])
        executed_price = price * (1 + slippage)
        
        position = {
            'symbol': symbol,
            'direction': direction,
            'entry_price': executed_price,
            'current_price': executed_price,
            'quantity': quantity,
            'entry_time': datetime.now(),
            'unrealized_pnl': 0,
            'stop_loss': executed_price * (0.99 if direction == 'BUY' else 1.01),
            'targets': {
                'target1': executed_price * (1.004 if direction == 'BUY' else 0.996),
                'target2': executed_price * (1.006 if direction == 'BUY' else 0.994)
            },
            'exits_done': set()
        }
        
        self.active_positions[symbol] = position
        self.log_trade(symbol, 'ENTRY', direction, executed_price, quantity, 0, 'New position')
        self.log_position(symbol, position)

    def manage_position(self, symbol: str, data: pd.DataFrame):
        """Manage existing position"""
        if data is None or len(data) == 0:
            return
            
        position = self.active_positions[symbol]
        current_price = data.iloc[-1]['ltp']
        position['current_price'] = current_price
        
        # Calculate unrealized P&L
        price_diff = current_price - position['entry_price']
        if position['direction'] == 'SELL':
            price_diff = -price_diff
        position['unrealized_pnl'] = price_diff * position['quantity']
        
        # Check stop loss
        if ((position['direction'] == 'BUY' and current_price <= position['stop_loss']) or
            (position['direction'] == 'SELL' and current_price >= position['stop_loss'])):
            self.exit_position(symbol, current_price, 'Stop loss hit')
            return
            
        # Check targets
        remaining_qty = position['quantity']
        for target_name, target_price in position['targets'].items():
            if target_name not in position['exits_done']:
                if ((position['direction'] == 'BUY' and current_price >= target_price) or
                    (position['direction'] == 'SELL' and current_price <= target_price)):
                    # Exit 40% at first target, 30% at second target
                    exit_portion = 0.4 if target_name == 'target1' else 0.3
                    exit_qty = int(position['quantity'] * exit_portion)
                    if exit_qty > 0:
                        self.partial_exit(symbol, current_price, exit_qty, f'{target_name} reached')
                        position['exits_done'].add(target_name)
                        remaining_qty -= exit_qty
        
        # Update trailing stop for remaining position
        if remaining_qty > 0 and len(position['exits_done']) > 0:
            trail_points = self.index_params[symbol]['trail_points']
            if position['direction'] == 'BUY':
                new_stop = current_price - trail_points
                if new_stop > position['stop_loss']:
                    position['stop_loss'] = new_stop
            else:
                new_stop = current_price + trail_points
                if new_stop < position['stop_loss']:
                    position['stop_loss'] = new_stop
        
        self.log_position(symbol, position)

    def partial_exit(self, symbol: str, price: float, quantity: int, reason: str):
        """Execute a partial exit of a position"""
        position = self.active_positions[symbol]
        
        # Calculate P&L for partial exit
        price_diff = price - position['entry_price']
        if position['direction'] == 'SELL':
            price_diff = -price_diff
        pnl = price_diff * quantity
        
        # Update position
        position['quantity'] -= quantity
        self.daily_pnl += pnl
        self.current_capital += pnl
        
        self.log_trade(symbol, 'PARTIAL_EXIT', position['direction'], 
                      price, quantity, pnl, reason)

    def exit_position(self, symbol: str, price: float, reason: str):
        """Exit entire position"""
        position = self.active_positions[symbol]
        
        # Calculate final P&L
        price_diff = price - position['entry_price']
        if position['direction'] == 'SELL':
            price_diff = -price_diff
        pnl = price_diff * position['quantity']
        
        # Update account
        self.daily_pnl += pnl
        self.current_capital += pnl
        
        # Log the exit
        self.log_trade(symbol, 'EXIT', position['direction'], 
                      price, position['quantity'], pnl, reason)
        
        # Add to trades history
        self.trades.append({
            'date': datetime.now(),
            'symbol': symbol,
            'direction': position['direction'],
            'entry_price': position['entry_price'],
            'exit_price': price,
            'quantity': position['quantity'],
            'pnl': pnl
        })
        
        # Remove position
        del self.active_positions[symbol]

    def process_market_data(self):
        """Process latest market data and execute trading logic"""
        for symbol in self.index_params.keys():
            data = self.get_latest_data(symbol)
            if data is None:
                continue
                
            # Manage existing position
            if symbol in self.active_positions:
                self.manage_position(symbol, data)
                continue
                
            # Check for new entry if no position exists
            if self.can_take_new_trade():
                entry_signal, direction, price = self.check_entry_conditions(data, symbol)
                if entry_signal:
                    self.execute_trade(symbol, direction, price, self.index_params[symbol])

    def get_position_summary(self) -> str:
        """Get current positions and P&L summary"""
        summary = []
        for symbol, pos in self.active_positions.items():
            summary.append(
                f"{symbol}: {pos['direction']} {pos['quantity']} @ {pos['entry_price']:.2f} "
                f"Current: {pos['current_price']:.2f} PnL: {pos['unrealized_pnl']:.2f}"
            )
        
        if not summary:
            summary = ["No active positions"]
            
        summary.append(f"Daily P&L: {self.daily_pnl:.2f}")
        summary.append(f"Current Capital: {self.current_capital:.2f}")
        return " | ".join(summary) 