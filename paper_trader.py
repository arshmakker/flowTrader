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
    def __init__(self, data_collector: DataCollector, initial_capital: float = 1000000):
        self.logger = logging.getLogger('PaperTrader')
        self.data_collector = data_collector
        self.capital = initial_capital
        self.positions = {}  # Dictionary to track open positions
        self.trades = []     # List to track all trades
        self.symbol_cache = {}  # Cache for symbol details
        self.daily_pnl = 0   # Track daily P&L
        self.last_processed_time = {}  # Track last processed time for each symbol
        self._update_symbol_cache()
        self.logger.info(f"Paper trader initialized with capital: {self.capital}")
        
        # Index-specific parameters
        self.index_params = {
            'NIFTY': {
                'min_movement': 15,
                'initial_stop': 20,
                'target1': 25,
                'target2': 35,
                'trail_points': 8,
                'lot_size': 50,
                'margin_per_lot': 125000,
                'max_lots': 2,
                'tick_size': 0.05,
                'slippage_std': 0.0002,
                'min_movement_multiplier': 1.5
            },
            'BANKNIFTY': {
                'min_movement': 30,
                'initial_stop': 40,
                'target1': 50,
                'target2': 75,
                'trail_points': 15,
                'lot_size': 15,
                'margin_per_lot': 150000,
                'max_lots': 2,
                'tick_size': 0.1,
                'slippage_std': 0.0003,
                'min_movement_multiplier': 2
            },
            'FINNIFTY': {
                'min_movement': 20,
                'initial_stop': 25,
                'target1': 30,
                'target2': 45,
                'trail_points': 10,
                'lot_size': 40,
                'margin_per_lot': 100000,
                'max_lots': 2,
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
                self.capital,
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
        """Update the cache of symbols we're tracking"""
        try:
            symbols = self.data_collector.get_index_symbols()
            
            # Group symbols by stock name or symbol
            for s in symbols:
                if s['exchange'] == 'NSE':
                    # For cash segment, use symbol as key
                    key = s['symbol']
                    if key not in self.symbol_cache:
                        self.symbol_cache[key] = {}
                    self.symbol_cache[key]['CASH'] = s
                else:
                    # For F&O segment
                    key = s.get('stock_name', s['symbol'])  # Use stock_name for options/futures, fallback to symbol
                    if key not in self.symbol_cache:
                        self.symbol_cache[key] = {}
                    
                    if s['instrument'] == 'FUTSTK':
                        self.symbol_cache[key]['FUTURES'] = s
                    elif s['instrument'] == 'OPTSTK':
                        option_key = f"{s['option_type']}_{s['strike']}"
                        if 'OPTIONS' not in self.symbol_cache[key]:
                            self.symbol_cache[key]['OPTIONS'] = {}
                        self.symbol_cache[key]['OPTIONS'][option_key] = s

            self.logger.info(f"Updated symbol cache with {len(self.symbol_cache)} symbols")
            
        except Exception as e:
            self.logger.error(f"Error updating symbol cache: {str(e)}")
            raise

    def get_position_value(self):
        """Calculate current value of all positions"""
        total_value = self.capital
        
        for symbol, position in self.positions.items():
            try:
                current_price = self.data_collector.get_last_price(
                    position['exchange'],
                    position['token']
                )
                
                if current_price:
                    position_value = position['quantity'] * current_price
                    total_value += position_value
                    
            except Exception as e:
                self.logger.error(f"Error calculating position value for {symbol}: {str(e)}")
                
        return total_value

    def get_symbol_price(self, symbol_key, instrument_type='CASH', option_key=None):
        """Get current price for a symbol"""
        try:
            if symbol_key not in self.symbol_cache:
                self.logger.warning(f"Symbol {symbol_key} not found in cache")
                return None
                
            symbol_data = self.symbol_cache[symbol_key]
            
            if instrument_type == 'CASH' and 'CASH' in symbol_data:
                symbol = symbol_data['CASH']
            elif instrument_type == 'FUTURES' and 'FUTURES' in symbol_data:
                symbol = symbol_data['FUTURES']
            elif instrument_type == 'OPTIONS' and 'OPTIONS' in symbol_data and option_key in symbol_data['OPTIONS']:
                symbol = symbol_data['OPTIONS'][option_key]
            else:
                self.logger.warning(f"Instrument type {instrument_type} not found for {symbol_key}")
                return None
                
            price = self.data_collector.get_last_price(
                symbol['exchange'],
                symbol['token']
            )
            
            return price
            
        except Exception as e:
            self.logger.error(f"Error getting price for {symbol_key}: {str(e)}")
            return None

    def place_order(self, symbol_key, instrument_type, quantity, order_type='MARKET', 
                   option_key=None, price=None):
        """Place a new order"""
        try:
            if symbol_key not in self.symbol_cache:
                raise ValueError(f"Symbol {symbol_key} not found")
                
            symbol_data = self.symbol_cache[symbol_key]
            
            # Get the correct symbol based on instrument type
            if instrument_type == 'CASH':
                if 'CASH' not in symbol_data:
                    raise ValueError(f"Cash segment not found for {symbol_key}")
                symbol = symbol_data['CASH']
            elif instrument_type == 'FUTURES':
                if 'FUTURES' not in symbol_data:
                    raise ValueError(f"Futures not found for {symbol_key}")
                symbol = symbol_data['FUTURES']
            elif instrument_type == 'OPTIONS':
                if 'OPTIONS' not in symbol_data or option_key not in symbol_data['OPTIONS']:
                    raise ValueError(f"Option {option_key} not found for {symbol_key}")
                symbol = symbol_data['OPTIONS'][option_key]
            else:
                raise ValueError(f"Invalid instrument type: {instrument_type}")
                
            # Get current price
            current_price = price or self.get_symbol_price(
                symbol_key,
                instrument_type,
                option_key
            )
            
            if not current_price:
                raise ValueError("Could not get current price")
                
            # Calculate order value
            order_value = quantity * current_price
            
            # Check if we have enough capital
            if order_value > self.capital:
                raise ValueError(f"Insufficient capital. Required: {order_value}, Available: {self.capital}")
                
            # Update positions
            position_key = f"{symbol['symbol']}_{instrument_type}"
            if option_key:
                position_key += f"_{option_key}"
                
            if position_key in self.positions:
                self.positions[position_key]['quantity'] += quantity
                if self.positions[position_key]['quantity'] == 0:
                    del self.positions[position_key]
            else:
                self.positions[position_key] = {
                    'symbol': symbol['symbol'],
                    'exchange': symbol['exchange'],
                    'token': symbol['token'],
                    'quantity': quantity,
                    'instrument_type': instrument_type,
                    'option_key': option_key
                }
                
            # Update capital
            self.capital -= order_value
            
            # Record trade
            trade = {
                'timestamp': datetime.now(),
                'symbol': symbol['symbol'],
                'instrument_type': instrument_type,
                'option_key': option_key,
                'quantity': quantity,
                'price': current_price,
                'value': order_value,
                'type': 'BUY' if quantity > 0 else 'SELL'
            }
            self.trades.append(trade)
            
            self.logger.info(
                f"Order placed: {trade['type']} {abs(quantity)} {symbol['symbol']} "
                f"@ {current_price} = {abs(order_value)}"
            )
            
            return True
            
        except Exception as e:
            self.logger.error(f"Error placing order: {str(e)}")
            return False

    def get_positions(self):
        """Get current positions"""
        positions = []
        for key, position in self.positions.items():
            try:
                current_price = self.data_collector.get_last_price(
                    position['exchange'],
                    position['token']
                )
                
                if current_price:
                    position_value = position['quantity'] * current_price
                    positions.append({
                        'symbol': position['symbol'],
                        'quantity': position['quantity'],
                        'current_price': current_price,
                        'value': position_value,
                        'instrument_type': position['instrument_type'],
                        'option_key': position.get('option_key')
                    })
                    
            except Exception as e:
                self.logger.error(f"Error getting position details for {key}: {str(e)}")
                
        return positions

    def calculate_position_size(self, symbol: str) -> int:
        """Calculate position size based on risk parameters"""
        params = self.index_params[symbol]
        available_margin = self.capital * 0.3  # Max 30% capital per trade
        max_lots = min(
            params['max_lots'],
            int(available_margin / params['margin_per_lot'])
        )
        return max_lots * params['lot_size']

    def can_take_new_trade(self) -> bool:
        """Check if new trades are allowed based on risk parameters"""
        today = date.today()
        
        # Check daily loss limit (2%)
        if abs(self.daily_pnl) > self.capital * 0.02:
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

    def get_latest_data(self, symbol_key):
        """Get latest market data for a symbol"""
        try:
            if symbol_key not in self.symbol_cache:
                self.logger.warning(f"Symbol {symbol_key} not found in cache")
                return None

            # Get data for all available segments
            data_points = []
            symbol_data = self.symbol_cache[symbol_key]

            # Check each segment (CASH, FUTURES, OPTIONS)
            for segment_type, segment_data in symbol_data.items():
                if isinstance(segment_data, dict):  # Handle OPTIONS dictionary
                    if segment_type == 'OPTIONS':
                        for option_key, option_data in segment_data.items():
                            quote = self.data_collector.api.get_quotes(
                                option_data['exchange'],
                                option_data['token']
                            )
                            if quote:
                                data_points.append({
                                    'timestamp': datetime.now(),
                                    'symbol': option_data['symbol'],
                                    'ltp': float(quote.get('lp', 0)),
                                    'volume': int(quote.get('v', 0)),
                                    'bid': float(quote.get('bp1', 0)),
                                    'ask': float(quote.get('sp1', 0)),
                                    'oi': int(quote.get('oi', 0)),
                                    'bid_qty': int(quote.get('bq1', 0)),
                                    'ask_qty': int(quote.get('sq1', 0)),
                                    'instrument': 'OPTSTK',
                                    'option_type': option_data['option_type'],
                                    'strike': option_data['strike']
                                })
                else:  # Handle CASH and FUTURES
                    quote = self.data_collector.api.get_quotes(
                        segment_data['exchange'],
                        segment_data['token']
                    )
                    if quote:
                        data_points.append({
                            'timestamp': datetime.now(),
                            'symbol': segment_data['symbol'],
                            'ltp': float(quote.get('lp', 0)),
                            'volume': int(quote.get('v', 0)),
                            'bid': float(quote.get('bp1', 0)),
                            'ask': float(quote.get('sp1', 0)),
                            'oi': int(quote.get('oi', 0)),
                            'bid_qty': int(quote.get('bq1', 0)),
                            'ask_qty': int(quote.get('sq1', 0)),
                            'instrument': segment_data.get('instrument', 'EQ')
                        })

            if not data_points:
                return None

            # Convert to DataFrame
            df = pd.DataFrame(data_points)
            df['timestamp'] = pd.to_datetime(df['timestamp'])
            return df

        except Exception as e:
            self.logger.error(f"Error getting latest data for {symbol_key}: {str(e)}")
            return None

    def check_entry_conditions(self, data: pd.DataFrame, symbol: str) -> Tuple[bool, str, float]:
        """Check entry conditions for a new trade using live data"""
        if data is None or len(data) == 0:
            return False, None, None
            
        try:
            # Get current price and parameters
            row = data.iloc[-1]  # Get the latest data point
            current_price = row['ltp']
            volume = row['volume']
            oi = row.get('oi', 0)  # Options and futures only
            
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
            oi_change = oi - last_data.get('oi', 0)
            
            # Get parameters for the symbol
            if symbol not in self.index_params:
                return False, None, None
                
            params = self.index_params[symbol]
            min_movement = params['min_movement']
            
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
            # 3. Open Interest is increasing (for F&O)
            # 4. Bid-Ask spread is reasonable
            if abs(price_change) >= min_movement and volume_change > 0:
                if row['instrument'] in ['FUTIDX', 'FUTSTK', 'OPTIDX', 'OPTSTK']:
                    if oi_change <= 0:  # Need increasing OI for F&O
                        return False, None, None
                
                # Check bid-ask spread
                if 'bid' in row and 'ask' in row:
                    spread = row['ask'] - row['bid']
                    if spread > min_movement:  # Spread too wide
                        return False, None, None
                
                # Determine direction based on price change
                direction = 'BUY' if price_change > 0 else 'SELL'
                return True, direction, current_price
                
            return False, None, None
            
        except Exception as e:
            self.logger.error(f"Error checking entry conditions for {symbol}: {str(e)}")
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
        
        self.positions[symbol] = position
        self.log_trade(symbol, 'ENTRY', direction, executed_price, quantity, 0, 'New position')
        self.log_position(symbol, position)

    def manage_position(self, symbol: str, data: pd.DataFrame):
        """Manage existing position"""
        if data is None or len(data) == 0:
            return
            
        position = self.positions[symbol]
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
        position = self.positions[symbol]
        
        # Calculate P&L for partial exit
        price_diff = price - position['entry_price']
        if position['direction'] == 'SELL':
            price_diff = -price_diff
        pnl = price_diff * quantity
        
        # Update position
        position['quantity'] -= quantity
        self.daily_pnl += pnl
        self.capital += pnl
        
        self.log_trade(symbol, 'PARTIAL_EXIT', position['direction'], 
                      price, quantity, pnl, reason)

    def exit_position(self, symbol: str, price: float, reason: str):
        """Exit entire position"""
        position = self.positions[symbol]
        
        # Calculate final P&L
        price_diff = price - position['entry_price']
        if position['direction'] == 'SELL':
            price_diff = -price_diff
        pnl = price_diff * position['quantity']
        
        # Update account
        self.daily_pnl += pnl
        self.capital += pnl
        
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
        del self.positions[symbol]

    def process_market_data(self):
        """Process latest market data and execute trading logic"""
        if not self.is_trading_time():
            return

        try:
            # Get all unique symbols from the cache
            symbols = list(self.symbol_cache.keys())
            
            for symbol in symbols:
                try:
                    data = self.get_latest_data(symbol)
                    if data is None or len(data) == 0:
                        continue
                        
                    # Manage existing position
                    if symbol in self.positions:
                        self.manage_position(symbol, data)
                        continue
                        
                    # Check for new entry if no position exists
                    if self.can_take_new_trade():
                        entry_signal, direction, price = self.check_entry_conditions(data, symbol)
                        if entry_signal and symbol in self.index_params:  # Only trade symbols with defined parameters
                            self.execute_trade(symbol, direction, price, self.index_params[symbol])
                            
                except Exception as e:
                    self.logger.error(f"Error processing market data for {symbol}: {str(e)}")
                    continue
                    
        except Exception as e:
            self.logger.error(f"Error in process_market_data: {str(e)}")
            return

    def get_position_summary(self) -> str:
        """Get current positions and P&L summary"""
        summary = []
        for symbol, pos in self.positions.items():
            summary.append(
                f"{symbol}: {pos['direction']} {pos['quantity']} @ {pos['entry_price']:.2f} "
                f"Current: {pos['current_price']:.2f} PnL: {pos['unrealized_pnl']:.2f}"
            )
        
        if not summary:
            summary = ["No active positions"]
            
        summary.append(f"Daily P&L: {self.daily_pnl:.2f}")
        summary.append(f"Current Capital: {self.capital:.2f}")
        return " | ".join(summary)

    def save_trading_stats(self):
        """Save trading statistics to file"""
        try:
            stats_dir = os.path.join('logs', 'trading_stats')
            if not os.path.exists(stats_dir):
                os.makedirs(stats_dir)
                
            today = datetime.now().strftime('%Y%m%d')
            stats_file = os.path.join(stats_dir, f'trading_stats_{today}.json')
            
            # Calculate statistics
            stats = {
                'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                'total_trades': len(self.trades),
                'daily_pnl': self.daily_pnl,
                'current_capital': self.capital,
                'win_rate': self._calculate_win_rate(),
                'avg_profit': self._calculate_avg_profit(),
                'max_drawdown': self._calculate_max_drawdown(),
                'active_positions': len(self.positions),
                'trades': self.trades  # Full trade history
            }
            
            # Save to file
            with open(stats_file, 'w') as f:
                json.dump(stats, f, indent=4)
                
            self.logger.info(f"Trading statistics saved to {stats_file}")
            
        except Exception as e:
            self.logger.error(f"Error saving trading statistics: {str(e)}")

    def _calculate_win_rate(self):
        """Calculate win rate from completed trades"""
        if not self.trades:
            return 0.0
            
        winning_trades = sum(1 for t in self.trades if t['pnl'] > 0)
        return (winning_trades / len(self.trades)) * 100

    def _calculate_avg_profit(self):
        """Calculate average profit per trade"""
        if not self.trades:
            return 0.0
            
        total_pnl = sum(t['pnl'] for t in self.trades)
        return total_pnl / len(self.trades)

    def _calculate_max_drawdown(self):
        """Calculate maximum drawdown"""
        if not self.trades:
            return 0.0
            
        peak = self.capital
        max_drawdown = 0
        current_capital = self.capital
        
        for trade in self.trades:
            current_capital += trade['pnl']
            if current_capital > peak:
                peak = current_capital
            drawdown = (peak - current_capital) / peak * 100
            max_drawdown = max(max_drawdown, drawdown)
            
        return max_drawdown 