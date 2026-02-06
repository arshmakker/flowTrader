"""
Backtesting framework for Iron Condor strategy using historical data
"""

import pandas as pd
import numpy as np
import os
import json
import logging
from datetime import datetime, date, timedelta
from typing import Dict, List, Optional, Tuple
from pathlib import Path
import glob

from strategies.iron_condor import generate_iron_condor_trade
from technical_indicators import calculate_iv_percentile, calculate_atm_iv
from symbol_manager import SymbolManager

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger('Backtest')


class IronCondorBacktester:
    """Backtest Iron Condor strategy on historical data"""
    
    def __init__(self, data_dir='market_data_*', initial_capital=100000):
        self.data_dir = data_dir
        self.initial_capital = initial_capital
        self.capital = initial_capital
        self.trades = []
        self.open_positions = []
        self.daily_pnl = []
        # Backtest-only: allow using last-known quotes to build a usable option chain.
        # Real tick streams often have sparse/unaligned timestamps across strikes; requiring
        # a quote within ±1 minute per strike can lead to empty/one-strike chains.
        self.max_quote_staleness_minutes = 10
        
    def load_historical_data(self, date_str: str) -> Dict:
        """
        Load all historical data for a specific date
        
        Returns:
            dict with keys:
                - options: dict of {symbol: DataFrame}
                - spot_prices: dict of {timestamp: float}
        """
        data = {
            'options': {},
            'spot_prices': {},
            'timestamps': set()
        }
        
        # Find data directory for this date
        date_pattern = f"market_data_{date_str}"
        data_dirs = glob.glob(date_pattern)
        
        if not data_dirs:
            logger.warning(f"No data directory found for {date_str}")
            return data
        
        data_dir = data_dirs[0]
        options_dir = os.path.join(data_dir, 'raw_data', 'options', 'NIFTY')
        
        # Load all option CSV files
        for option_type in ['ce', 'pe']:
            option_path = os.path.join(options_dir, option_type)
            if not os.path.exists(option_path):
                continue
                
            for csv_file in glob.glob(os.path.join(option_path, '*.csv')):
                try:
                    df = pd.read_csv(csv_file)
                    if 'timestamp' in df.columns:
                        df['timestamp'] = pd.to_datetime(df['timestamp'])
                        # Extract symbol from filename
                        symbol = os.path.basename(csv_file).replace('.csv', '').split('_')[0]
                        data['options'][symbol] = df
                        data['timestamps'].update(df['timestamp'].tolist())
                except Exception as e:
                    logger.debug(f"Error loading {csv_file}: {str(e)}")
        
        # Try to get spot prices from futures data
        futures_dir = os.path.join(data_dir, 'raw_data', 'futures')
        if os.path.exists(futures_dir):
            for csv_file in glob.glob(os.path.join(futures_dir, 'NIFTY*.csv')):
                try:
                    df = pd.read_csv(csv_file)
                    if 'timestamp' in df.columns and 'ltp' in df.columns:
                        df['timestamp'] = pd.to_datetime(df['timestamp'])
                        for _, row in df.iterrows():
                            ts = row['timestamp']
                            ltp = float(row['ltp'])
                            # Futures price is close to spot, use as proxy
                            data['spot_prices'][ts] = ltp
                            data['timestamps'].add(ts)
                except Exception as e:
                    logger.debug(f"Error loading futures {csv_file}: {str(e)}")
        
        # Convert timestamps to sorted list
        data['timestamps'] = sorted(list(data['timestamps']))
        
        logger.info(f"Loaded data for {date_str}: {len(data['options'])} options, "
                   f"{len(data['timestamps'])} timestamps")
        
        return data
    
    def build_option_chain_at_time(self, data: Dict, timestamp: datetime, 
                                   expiry_date: date, spot_price: float) -> pd.DataFrame:
        """
        Build option chain DataFrame at a specific timestamp
        
        Args:
            data: Historical data dictionary
            timestamp: Target timestamp
            expiry_date: Expiry date to filter
            spot_price: Current spot price
            
        Returns:
            DataFrame with option chain data
        """
        chain_data = []
        expiry_str = expiry_date.strftime('%d-%b-%Y').upper()
        
        # Find options matching the expiry
        for symbol, df in data['options'].items():
            # Check if this option matches the expiry
            # First check if expiry column exists
            if 'expiry' in df.columns:
                df_expiry = df[df['expiry'] == expiry_str]
                if df_expiry.empty:
                    continue
            elif expiry_str not in symbol:
                continue
            
            # Backtest-friendly quote selection:
            # take the last-known quote at or before `timestamp`, but not older than a staleness window.
            staleness = timedelta(minutes=self.max_quote_staleness_minutes)
            df_filtered = df[
                (df['timestamp'] <= timestamp) &
                (df['timestamp'] >= timestamp - staleness)
            ]
            if df_filtered.empty:
                continue
            closest_row = df_filtered.sort_values('timestamp').iloc[-1]
            
            # Extract option details
            try:
                strike = float(closest_row.get('strike', 0))
                option_type = closest_row.get('option_type', '').upper()
                ltp = float(closest_row.get('ltp', 0))
                bid = float(closest_row.get('bid', 0))
                ask = float(closest_row.get('ask', 0))
                
                if strike > 0 and option_type in ['CE', 'PE']:
                    mid_price = (bid + ask) / 2 if bid > 0 and ask > 0 else ltp
                    
                    chain_data.append({
                        'strike': strike,
                        'option_type': option_type,
                        'ltp': ltp,
                        'bid': bid,
                        'ask': ask,
                        'mid_price': mid_price,
                        'delta': 0.0,  # Delta not available in historical data
                        'oi': int(closest_row.get('oi', 0)),
                        'volume': int(closest_row.get('volume', 0))
                    })
            except Exception as e:
                logger.debug(f"Error processing {symbol}: {str(e)}")
                continue
        
        if not chain_data:
            return pd.DataFrame()
        
        chain_df = pd.DataFrame(chain_data)
        return chain_df
    
    def get_spot_price_at_time(self, data: Dict, timestamp: datetime) -> Optional[float]:
        """Get spot price at a specific timestamp"""
        # First try futures data
        if data['spot_prices']:
            # Prefer last-known futures price at/before timestamp (more realistic than nearest).
            eligible = [ts for ts in data['spot_prices'].keys() if ts <= timestamp]
            if eligible:
                closest_ts = max(eligible)
                if abs((timestamp - closest_ts).total_seconds()) < 600:  # Within 10 minutes
                    return data['spot_prices'][closest_ts]
        
        # Fallback: estimate from ATM options
        # Find ATM call and put, average their strikes adjusted by premium
        atm_calls = []
        atm_puts = []
        
        for symbol, df in data['options'].items():
            df_filtered = df[
                (df['timestamp'] >= timestamp - timedelta(minutes=1)) &
                (df['timestamp'] <= timestamp + timedelta(minutes=1))
            ]
            
            if df_filtered.empty:
                continue
            
            closest_row = df_filtered.iloc[0]
            strike = float(closest_row.get('strike', 0))
            option_type = closest_row.get('option_type', '').upper()
            mid_price = (float(closest_row.get('bid', 0)) + float(closest_row.get('ask', 0))) / 2
            if mid_price == 0:
                mid_price = float(closest_row.get('ltp', 0))
            
            if strike > 0 and mid_price > 0:
                if option_type == 'CE':
                    atm_calls.append((strike, mid_price))
                elif option_type == 'PE':
                    atm_puts.append((strike, mid_price))
        
        # Estimate spot from ATM options (simplified: use average of closest strikes)
        if atm_calls and atm_puts:
            # Rough estimate: spot is between ATM call and put strikes
            call_strikes = [s for s, _ in atm_calls]
            put_strikes = [s for s, _ in atm_puts]
            if call_strikes and put_strikes:
                estimated_spot = (min(call_strikes) + max(put_strikes)) / 2
                return estimated_spot
        
        return None
    
    def _load_historical_iv_for_backtest(self, spot_price: float, days_to_expiry: int,
                                         backtest_date: date, data_dir: str) -> List[float]:
        """
        Load historical IV data for backtesting (only data before backtest_date)
        """
        historical_ivs = []
        
        try:
            if not os.path.exists(data_dir):
                return []
            
            # Load data from all dates before the backtest date
            backtest_datetime = datetime.combine(backtest_date, datetime.min.time())
            
            for days_back in range(1, 90):
                date_to_check = backtest_datetime - timedelta(days=days_back)
                date_str = date_to_check.strftime('%Y%m%d')
                filename = os.path.join(data_dir, f"iv_data_{date_str}.json")
                
                if os.path.exists(filename):
                    try:
                        with open(filename, 'r') as f:
                            data = json.load(f)
                            for entry in data:
                                # Filter by similar DTE range (±2 days)
                                if abs(entry.get('days_to_expiry', 0) - days_to_expiry) <= 2:
                                    # Only include data from before backtest date
                                    entry_timestamp = entry.get('timestamp')
                                    if entry_timestamp:
                                        try:
                                            entry_time = datetime.fromisoformat(entry_timestamp)
                                            if entry_time < backtest_datetime:
                                                iv = entry.get('iv', None)
                                                if iv:
                                                    historical_ivs.append(iv)
                                        except (ValueError, AttributeError):
                                            # If timestamp parsing fails, include it anyway
                                            iv = entry.get('iv', None)
                                            if iv:
                                                historical_ivs.append(iv)
                    except Exception as e:
                        logger.debug(f"Error loading IV data from {filename}: {str(e)}")
                        continue
            
            logger.debug(f"Loaded {len(historical_ivs)} historical IV values for backtest (before {backtest_date})")
            return historical_ivs
            
        except Exception as e:
            logger.error(f"Error loading historical IV for backtest: {str(e)}")
            return []
    
    def calculate_market_state(self, option_chain: pd.DataFrame, spot_price: float,
                              expiry_date: date, backtest_date: date = None, 
                              data_dir='market_data_iv') -> Dict:
        """
        Calculate market state for strategy eligibility
        
        Args:
            option_chain: Option chain DataFrame
            spot_price: Current spot price
            expiry_date: Expiry date
            backtest_date: Date for backtesting (if None, uses today)
            data_dir: Directory for IV historical data
            
        Returns:
            Market state dictionary
        """
        # Use backtest date if provided, otherwise use today
        if backtest_date is None:
            backtest_date = date.today()
        
        # Calculate days to expiry
        days_to_expiry = (expiry_date - backtest_date).days
        
        # Calculate IV percentile (using historical data if available)
        # For backtesting, we need to use historical data up to but not including the backtest date
        iv_percentile = None
        try:
            # Create a custom IV percentile calculation for backtesting
            # that only uses data up to the backtest date
            from technical_indicators import calculate_atm_iv
            import numpy as np
            
            # Calculate current IV
            current_iv = calculate_atm_iv(option_chain, spot_price, days_to_expiry)
            if current_iv is None:
                iv_percentile = 65.0
            else:
                # Load historical IVs up to (but not including) the backtest date
                historical_ivs = self._load_historical_iv_for_backtest(
                    spot_price, days_to_expiry, backtest_date, data_dir
                )
                
                if len(historical_ivs) < 20:
                    # Use heuristic based on current IV
                    if current_iv < 12:
                        iv_percentile = 40.0
                    elif current_iv < 18:
                        iv_percentile = 55.0
                    elif current_iv < 25:
                        iv_percentile = 70.0
                    else:
                        iv_percentile = 85.0
                    logger.debug(f"Using IV heuristic: {iv_percentile:.1f}% (samples: {len(historical_ivs)})")
                else:
                    # Calculate percentile
                    historical_ivs = np.array(historical_ivs)
                    iv_percentile = (np.sum(historical_ivs < current_iv) / len(historical_ivs)) * 100
                    logger.debug(f"IV Percentile: {iv_percentile:.1f}% (Current IV: {current_iv:.2f}%, Historical samples: {len(historical_ivs)})")
                    
        except Exception as e:
            logger.debug(f"Error calculating IV percentile: {str(e)}")
            iv_percentile = 65.0
        
        # ADX not available in backtest (would need historical price data)
        adx_14 = 18.0  # Default fallback
        
        # Check for major events (simplified - assume none)
        has_major_event = False
        
        market_state = {
            'iv_percentile': iv_percentile,
            'days_to_expiry': days_to_expiry,
            'adx_14': adx_14,
            'has_major_event': has_major_event,
            'instrument': 'NIFTY',
            'instrument_type': 'WEEKLY',
            'spot_price': spot_price,
            'expiry': expiry_date.strftime('%Y-%m-%d')
        }
        
        return market_state
    
    def calculate_position_value(self, position: Dict, current_prices: Dict) -> float:
        """
        Calculate current value of a position
        
        Args:
            position: Trade position dictionary
            current_prices: Dict of {symbol: current_price}
            
        Returns:
            Current position value (negative for short, positive for long)
        """
        total_value = 0
        
        for leg in position['legs']:
            # Build symbol from leg info
            expiry_str = position['expiry'].replace('-', '')
            # Format: NIFTY30DEC25C26200
            option_type = leg['option_type']
            strike = int(leg['strike'])
            
            # Try to find matching price
            symbol_key = f"{option_type}{strike}"
            current_price = current_prices.get(symbol_key, leg.get('price', 0))
            
            # Get lot size from position (default to 50 if not available)
            lot_size = position.get('lot_size', 50)
            
            # Calculate value
            if leg['position'] == 'SHORT':
                # Short: we received premium, so value decreases as price increases
                value = (leg['price'] - current_price) * position['lots'] * lot_size
            else:
                # Long: we paid premium, so value increases as price increases
                value = (current_price - leg['price']) * position['lots'] * lot_size
            
            total_value += value
        
        return total_value
    
    def check_exit_conditions(self, position: Dict, current_prices: Dict, 
                             current_time: datetime, expiry_date: date) -> Tuple[bool, str]:
        """
        Check if position should be exited. TSL only (no hardcoded profit target):
        trailing PnL lock (₹300 then trail ₹200), stop loss, mandatory DTE/time.
        """
        # Calculate current P&L
        entry_credit = position['net_credit_total']
        current_value = self.calculate_position_value(position, current_prices)
        current_pnl = current_value - entry_credit

        # Trailing PnL lock (same as production: MIN_PNL_LOCK_INR 300, PNL_TRAIL_DISTANCE_INR 200)
        MIN_PNL_LOCK_INR = 300
        PNL_TRAIL_DISTANCE_INR = 200
        current_lock = position.get('profit_locked_inr', 0)
        if current_pnl >= MIN_PNL_LOCK_INR:
            if current_lock == 0:
                new_lock = MIN_PNL_LOCK_INR
            else:
                new_lock = max(current_lock, current_pnl - PNL_TRAIL_DISTANCE_INR)
            position['profit_locked_inr'] = new_lock
            current_lock = new_lock
        if current_lock > 0 and current_pnl < current_lock:
            return True, "trailing_stop_pnl"

        # Check stop loss (1.2x max loss)
        max_loss = position['max_loss']
        stop_loss = max_loss * 1.2
        
        if current_pnl <= -stop_loss:
            return True, "stop_loss"
        
        # Check mandatory exit DTE
        days_to_expiry = (expiry_date - current_time.date()).days
        if days_to_expiry <= 1:
            return True, "mandatory_exit_dte"
        
        # Check mandatory exit time (14:30 on expiry day)
        if days_to_expiry == 0 and current_time.time() >= datetime.strptime("14:30", "%H:%M").time():
            return True, "mandatory_exit_time"
        
        return False, ""
    
    def run_backtest(self, start_date: str, end_date: str, 
                    check_interval_minutes: int = 5):
        """
        Run backtest on historical data
        
        Args:
            start_date: Start date (YYYYMMDD)
            end_date: End date (YYYYMMDD)
            check_interval_minutes: How often to check for trades (minutes)
        """
        logger.info(f"Starting backtest from {start_date} to {end_date}")
        
        # Get all dates in range
        start = datetime.strptime(start_date, '%Y%m%d').date()
        end = datetime.strptime(end_date, '%Y%m%d').date()
        
        current_date = start
        while current_date <= end:
            date_str = current_date.strftime('%Y%m%d')
            
            # Skip weekends
            if current_date.weekday() >= 5:
                current_date += timedelta(days=1)
                continue
            
            logger.info(f"Processing {date_str}")
            
            # Load data for this date
            data = self.load_historical_data(date_str)
            
            if not data['timestamps']:
                logger.warning(f"No data found for {date_str}")
                current_date += timedelta(days=1)
                continue
            
            # Process timestamps at intervals
            timestamps_to_check = []
            for ts in data['timestamps']:
                if ts.minute % check_interval_minutes == 0:
                    timestamps_to_check.append(ts)
            
            # Get available expiries from option data
            expiries = set()
            for symbol, df in data['options'].items():
                if df.empty:
                    continue
                
                # Try to get expiry from dataframe column first (most reliable)
                if 'expiry' in df.columns:
                    expiry_str = df.iloc[0]['expiry']
                    try:
                        # Handle format like "30-DEC-2025"
                        expiry_date = datetime.strptime(expiry_str, '%d-%b-%Y').date()
                        if expiry_date >= current_date:
                            expiries.add(expiry_date)
                            continue
                    except Exception as e:
                        logger.debug(f"Error parsing expiry {expiry_str}: {str(e)}")
                
                # Fallback: try to extract from symbol name (e.g., NIFTY30DEC25C26200)
                import re
                # Pattern: NIFTY + date + C/P + strike
                match = re.match(r'NIFTY(\d{2}[A-Z]{3}\d{2})[CP]', symbol)
                if match:
                    expiry_part = match.group(1)
                    try:
                        expiry_date = datetime.strptime(expiry_part, '%d%b%y').date()
                        if expiry_date >= current_date:
                            expiries.add(expiry_date)
                    except:
                        pass
            
            if not expiries:
                logger.warning(f"No valid expiries found for {date_str}")
                current_date += timedelta(days=1)
                continue
            
            # Use nearest expiry
            nearest_expiry = min(expiries)
            
            # Process each timestamp
            for timestamp in timestamps_to_check:
                # Get spot price
                spot_price = self.get_spot_price_at_time(data, timestamp)
                if not spot_price or spot_price <= 0:
                    continue
                
                # Build option chain
                option_chain = self.build_option_chain_at_time(
                    data, timestamp, nearest_expiry, spot_price
                )
                
                if option_chain.empty:
                    continue
                
                # Check for new trades (if no open positions)
                if not self.open_positions:
                    # Calculate market state (pass backtest date for proper IV calculation)
                    market_state = self.calculate_market_state(
                        option_chain, spot_price, nearest_expiry, backtest_date=current_date
                    )
                    
                    # Generate trade proposal
                    trade_proposal = generate_iron_condor_trade(market_state, option_chain)
                    
                    # Log why trade was rejected if applicable
                    if not trade_proposal:
                        logger.debug(f"Trade rejected at {timestamp}: "
                                   f"IV={market_state.get('iv_percentile', 'N/A')}%, "
                                   f"DTE={market_state.get('days_to_expiry', 'N/A')}, "
                                   f"ADX={market_state.get('adx_14', 'N/A')}")
                    
                    if trade_proposal:
                        # Enter trade
                        trade_proposal['entry_time'] = timestamp
                        trade_proposal['entry_spot'] = spot_price
                        self.open_positions.append(trade_proposal)
                        logger.info(f"Entered trade at {timestamp}: {trade_proposal['lots']} lots, "
                                  f"credit={trade_proposal['net_credit_total']:.2f}")
                
                # Check exit conditions for open positions
                for position in self.open_positions[:]:
                    # Get current prices for position legs
                    current_prices = {}
                    for leg in position['legs']:
                        option_type = leg['option_type']
                        strike = int(leg['strike'])
                        symbol_key = f"{option_type}{strike}"
                        
                        # Find current price in option chain
                        leg_chain = option_chain[
                            (option_chain['option_type'] == option_type) &
                            (option_chain['strike'] == strike)
                        ]
                        if not leg_chain.empty:
                            current_prices[symbol_key] = leg_chain.iloc[0]['mid_price']
                        else:
                            current_prices[symbol_key] = leg.get('price', 0)
                    
                    # Check exit conditions
                    should_exit, reason = self.check_exit_conditions(
                        position, current_prices, timestamp, nearest_expiry
                    )
                    
                    if should_exit:
                        # Calculate final P&L
                        entry_credit = position['net_credit_total']
                        exit_value = self.calculate_position_value(position, current_prices)
                        final_pnl = exit_value - entry_credit
                        
                        # Record trade
                        trade_record = {
                            'entry_time': position['entry_time'],
                            'exit_time': timestamp,
                            'entry_spot': position['entry_spot'],
                            'exit_spot': spot_price,
                            'expiry': position['expiry'],
                            'lots': position['lots'],
                            'net_credit': position['net_credit_total'],
                            'max_profit': position['max_profit'],
                            'max_loss': position['max_loss'],
                            'final_pnl': final_pnl,
                            'exit_reason': reason
                        }
                        
                        self.trades.append(trade_record)
                        self.capital += final_pnl
                        self.open_positions.remove(position)
                        
                        logger.info(f"Exited trade at {timestamp}: P&L={final_pnl:.2f}, reason={reason}")
            
            current_date += timedelta(days=1)
        
        # Close any remaining positions at end
        logger.info("Closing remaining positions...")
        for position in self.open_positions:
            # Use last known prices (simplified)
            trade_record = {
                'entry_time': position['entry_time'],
                'exit_time': datetime.now(),
                'entry_spot': position['entry_spot'],
                'exit_spot': position['entry_spot'],  # Simplified
                'expiry': position['expiry'],
                'lots': position['lots'],
                'net_credit': position['net_credit_total'],
                'max_profit': position['max_profit'],
                'max_loss': position['max_loss'],
                'final_pnl': -position['max_loss'],  # Assume max loss
                'exit_reason': 'end_of_backtest'
            }
            self.trades.append(trade_record)
            self.capital -= position['max_loss']
        
        self.open_positions = []
    
    def generate_report(self) -> Dict:
        """Generate backtest performance report"""
        if not self.trades:
            return {
                'total_trades': 0,
                'winning_trades': 0,
                'losing_trades': 0,
                'win_rate': 0.0,
                'total_pnl': 0.0,
                'avg_pnl': 0.0,
                'max_profit': 0.0,
                'max_loss': 0.0,
                'initial_capital': self.initial_capital,
                'final_capital': self.capital,
                'total_return_pct': 0.0,
                'trades': [],
                'message': 'No trades executed during backtest period'
            }
        
        df = pd.DataFrame(self.trades)
        
        total_trades = len(df)
        winning_trades = len(df[df['final_pnl'] > 0])
        losing_trades = len(df[df['final_pnl'] <= 0])
        win_rate = (winning_trades / total_trades * 100) if total_trades > 0 else 0
        
        total_pnl = df['final_pnl'].sum()
        avg_pnl = df['final_pnl'].mean()
        max_profit = df['final_pnl'].max()
        max_loss = df['final_pnl'].min()
        
        # Calculate return
        total_return = ((self.capital - self.initial_capital) / self.initial_capital) * 100
        
        report = {
            'total_trades': total_trades,
            'winning_trades': winning_trades,
            'losing_trades': losing_trades,
            'win_rate': win_rate,
            'total_pnl': total_pnl,
            'avg_pnl': avg_pnl,
            'max_profit': max_profit,
            'max_loss': max_loss,
            'initial_capital': self.initial_capital,
            'final_capital': self.capital,
            'total_return_pct': total_return,
            'trades': self.trades
        }
        
        return report


def main():
    """Run backtest"""
    backtester = IronCondorBacktester(initial_capital=100000)
    
    # Backtest on available data
    # Dec 22, 23, 24, 26 (skipping 25 which is Christmas)
    # Use 2-minute intervals for more frequent checks (more data points)
    backtester.run_backtest('20251222', '20251226', check_interval_minutes=2)
    
    # Generate report
    report = backtester.generate_report()
    
    print("\n" + "="*60)
    print("IRON CONDOR BACKTEST REPORT")
    print("="*60)
    print(f"Total Trades: {report['total_trades']}")
    print(f"Winning Trades: {report['winning_trades']}")
    print(f"Losing Trades: {report['losing_trades']}")
    print(f"Win Rate: {report['win_rate']:.2f}%")
    print(f"\nTotal P&L: ₹{report['total_pnl']:.2f}")
    print(f"Average P&L per Trade: ₹{report['avg_pnl']:.2f}")
    print(f"Max Profit: ₹{report['max_profit']:.2f}")
    print(f"Max Loss: ₹{report['max_loss']:.2f}")
    print(f"\nInitial Capital: ₹{report['initial_capital']:.2f}")
    print(f"Final Capital: ₹{report['final_capital']:.2f}")
    print(f"Total Return: {report['total_return_pct']:.2f}%")
    print("="*60)
    
    # Save detailed report
    report_file = f"backtest_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    with open(report_file, 'w') as f:
        json.dump(report, f, indent=2, default=str)
    
    print(f"\nDetailed report saved to: {report_file}")
    
    return report


if __name__ == '__main__':
    main()

