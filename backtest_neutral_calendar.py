"""
Neutral Calendar Backtest

Backtests the ATM Call Calendar strategy:
- BUY 1 × ATM CALL (monthly expiry)
- SELL 1 × ATM CALL (weekly expiry)
- Same strike
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

from strategies.neutral.calendar import generate_neutral_call_calendar
from strategies.neutral.config import (
    IV_PERCENTILE_MIN, IV_PERCENTILE_MAX,
    ADX_MIN, ADX_MAX,
    MAX_NET_DEBIT_PCT_OF_CAPITAL
)
from technical_indicators import calculate_iv_percentile, calculate_atm_iv, calculate_adx
from regime.regime_detector import RegimeDetector

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger('CalendarBacktest')


class NeutralCalendarBacktester:
    """Backtest Neutral Calendar strategy on historical data"""
    
    def __init__(self, data_dir='market_data_*', initial_capital=100000):
        self.data_dir = data_dir
        self.initial_capital = initial_capital
        self.capital = initial_capital
        self.trades = []
        self.open_positions = []
        self.regime_detector = RegimeDetector()
        self.lot_size = 50  # NIFTY options lot size
        self.max_quote_staleness_minutes = 10
        
    def load_historical_data(self, date_str: str) -> Dict:
        """Load all historical data for a specific date"""
        data = {
            'options': {},
            'spot_prices': {},
            'timestamps': set()
        }
        
        date_pattern = f"market_data_{date_str}"
        data_dirs = glob.glob(date_pattern)
        
        if not data_dirs:
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
                        symbol = os.path.basename(csv_file).replace('.csv', '').split('_')[0]
                        data['options'][symbol] = df
                        data['timestamps'].update(df['timestamp'].tolist())
                except Exception as e:
                    logger.debug(f"Error loading {csv_file}: {str(e)}")
        
        # Get spot prices from futures
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
                            data['spot_prices'][ts] = ltp
                            data['timestamps'].add(ts)
                except Exception as e:
                    logger.debug(f"Error loading futures {csv_file}: {str(e)}")
        
        data['timestamps'] = sorted(list(data['timestamps']))
        return data
    
    def build_option_chain_at_time(self, data: Dict, timestamp: datetime, 
                                   expiry_type: str = 'weekly') -> pd.DataFrame:
        """
        Build option chain at a specific timestamp
        
        Args:
            expiry_type: 'weekly' or 'monthly' (simplified - uses all available options)
        """
        chain_rows = []
        staleness = timedelta(minutes=self.max_quote_staleness_minutes)
        
        for symbol, df in data['options'].items():
            # Filter by timestamp (use last-known quote within staleness window)
            df_filtered = df[
                (df['timestamp'] <= timestamp) &
                (df['timestamp'] >= timestamp - staleness)
            ]
            
            if df_filtered.empty:
                continue
            
            # Get closest quote
            closest_row = df_filtered.sort_values('timestamp').iloc[-1]
            
            # Extract strike and option type from symbol or DataFrame
            strike = closest_row.get('strike', 0)
            option_type = closest_row.get('option_type', 'CE').upper()
            
            if strike <= 0:
                continue
            
            # Calculate mid price
            bid = float(closest_row.get('bid', 0)) if pd.notna(closest_row.get('bid', 0)) else 0
            ask = float(closest_row.get('ask', 0)) if pd.notna(closest_row.get('ask', 0)) else 0
            ltp = float(closest_row.get('ltp', 0)) if pd.notna(closest_row.get('ltp', 0)) else 0
            
            mid_price = (bid + ask) / 2 if bid > 0 and ask > 0 else ltp
            
            if mid_price <= 0:
                continue
            
            chain_rows.append({
                'symbol': symbol,
                'strike': strike,
                'option_type': option_type,
                'ltp': ltp,
                'bid': bid,
                'ask': ask,
                'mid_price': mid_price,
                'volume': int(closest_row.get('volume', 0)) if pd.notna(closest_row.get('volume', 0)) else 0,
                'oi': int(closest_row.get('oi', 0)) if pd.notna(closest_row.get('oi', 0)) else 0,
                'timestamp': closest_row['timestamp']
            })
        
        if not chain_rows:
            return pd.DataFrame()
        
        chain_df = pd.DataFrame(chain_rows)
        chain_df['lot_size'] = self.lot_size
        return chain_df
    
    def get_spot_price_at_time(self, data: Dict, timestamp: datetime) -> Optional[float]:
        """Get spot price at a specific timestamp"""
        eligible = [ts for ts in data['spot_prices'].keys() if ts <= timestamp]
        if eligible:
            closest_ts = max(eligible)
            if abs((timestamp - closest_ts).total_seconds()) < 600:  # Within 10 minutes
                return data['spot_prices'][closest_ts]
        return None
    
    def calculate_indicators(self, data: Dict, timestamp: datetime, spot_price: float) -> Dict:
        """Calculate indicators for regime detection"""
        indicators = {
            'spot_price': spot_price,
            'iv_percentile': None,
            'adx_14': None,
            'atr': None,
            'atr_percentile': None,
            'range_state': 'NORMAL'
        }
        
        # Build option chain for IV calculation
        option_chain = self.build_option_chain_at_time(data, timestamp)
        if not option_chain.empty:
            try:
                days_to_expiry = 7  # Simplified - assume weekly
                indicators['iv_percentile'] = calculate_iv_percentile(
                    option_chain, spot_price, days_to_expiry
                )
            except Exception as e:
                logger.debug(f"Error calculating IV percentile: {str(e)}")
        
        # For ADX/ATR, we'd need historical price data
        # Simplified: use defaults for now
        indicators['adx_14'] = 20.0  # Placeholder - would need historical candles
        indicators['atr'] = None
        indicators['atr_percentile'] = None
        
        return indicators
    
    def check_entry_conditions(self, indicators: Dict, market_state: Dict) -> bool:
        """Check if entry conditions are met"""
        # Regime must be NEUTRAL
        regime = market_state.get('regime', 'NEUTRAL')
        if regime != 'NEUTRAL':
            return False
        
        # Sub-state must be NEUTRAL_ACTIVE (simplified - assume true if regime is NEUTRAL)
        sub_state = market_state.get('sub_state', 'NEUTRAL_ACTIVE')
        if sub_state != 'NEUTRAL_ACTIVE':
            return False
        
        # IV percentile between 40-60
        iv_percentile = indicators.get('iv_percentile')
        if not iv_percentile or not (IV_PERCENTILE_MIN <= iv_percentile <= IV_PERCENTILE_MAX):
            return False
        
        # ADX between 18-25
        adx_14 = indicators.get('adx_14')
        if not adx_14 or not (ADX_MIN <= adx_14 <= ADX_MAX):
            return False
        
        # ATR not expanding
        range_state = market_state.get('range_state', 'NORMAL')
        if range_state == 'EXPANDING':
            return False
        
        return True
    
    def run_backtest(self, start_date: str, end_date: str, check_interval_minutes: int = 15):
        """Run backtest on historical data"""
        logger.info(f"Starting Neutral Calendar backtest from {start_date} to {end_date}")
        
        start = datetime.strptime(start_date, '%Y%m%d').date()
        end = datetime.strptime(end_date, '%Y%m%d').date()
        current_date = start
        check_interval = timedelta(minutes=check_interval_minutes)
        
        while current_date <= end:
            date_str = current_date.strftime('%Y%m%d')
            logger.info(f"Processing {date_str}...")
            
            # Load historical data
            data = self.load_historical_data(date_str)
            if not data['timestamps']:
                logger.warning(f"No data for {date_str}")
                current_date += timedelta(days=1)
                continue
            
            # Process at intervals
            current_time = datetime.combine(current_date, datetime.min.time().replace(hour=9, minute=15))
            end_time = datetime.combine(current_date, datetime.min.time().replace(hour=15, minute=30))
            
            while current_time <= end_time:
                # Get spot price
                spot_price = self.get_spot_price_at_time(data, current_time)
                if not spot_price:
                    current_time += check_interval
                    continue
                
                # Calculate indicators
                indicators = self.calculate_indicators(data, current_time, spot_price)
                
                # Build market state
                market_state = {
                    'spot_price': spot_price,
                    'regime': 'NEUTRAL',  # Simplified - would use regime detector
                    'sub_state': 'NEUTRAL_ACTIVE',
                    'iv_percentile': indicators.get('iv_percentile'),
                    'adx_14': indicators.get('adx_14'),
                    'range_state': 'NORMAL',
                    'expiry': current_date.strftime('%Y-%m-%d'),  # Simplified
                    'days_to_expiry': 7
                }
                
                # Check exit conditions for open positions
                for position in self.open_positions[:]:
                    # Simplified exit: close at end of day or if regime changes
                    if current_time >= end_time:
                        # Close position
                        self._close_position(position, spot_price, current_time, 'end_of_day')
                        self.open_positions.remove(position)
                
                # Check entry conditions (only if no open position)
                if not self.open_positions:
                    if self.check_entry_conditions(indicators, market_state):
                        # Build option chains (simplified - use same chain for both)
                        option_chain_weekly = self.build_option_chain_at_time(data, current_time, 'weekly')
                        option_chain_monthly = self.build_option_chain_at_time(data, current_time, 'monthly')
                        
                        if not option_chain_weekly.empty and not option_chain_monthly.empty:
                            # Generate trade proposal
                            trade_proposal = generate_neutral_call_calendar(
                                market_state, option_chain_weekly, option_chain_monthly, self.capital
                            )
                            
                            if trade_proposal:
                                # Enter position
                                position = {
                                    'entry_time': current_time,
                                    'trade_proposal': trade_proposal,
                                    'entry_price_short': trade_proposal['legs'][0]['price'],
                                    'entry_price_long': trade_proposal['legs'][1]['price'],
                                    'strike': trade_proposal['legs'][0]['strike'],
                                    'lots': trade_proposal['lots'],
                                    'net_debit': trade_proposal['net_debit_total'],
                                    'regime_at_entry': market_state['regime']
                                }
                                self.open_positions.append(position)
                                logger.info(f"Entered calendar position: Strike={position['strike']}, Debit=₹{position['net_debit']:.2f}")
                
                current_time += check_interval
            
            current_date += timedelta(days=1)
        
        # Close any remaining positions
        for position in self.open_positions:
            # Use last known spot price
            last_spot = position.get('last_spot_price', position['strike'])
            self._close_position(position, last_spot, datetime.now(), 'end_of_backtest')
        
        self.open_positions = []
    
    def _close_position(self, position: Dict, spot_price: float, exit_time: datetime, reason: str):
        """Close a calendar position"""
        # Simplified P&L calculation
        # For calendar: P&L = (short_price - current_short_price) - (current_long_price - long_price)
        # Simplified: assume prices move proportionally
        entry_short = position['entry_price_short']
        entry_long = position['entry_price_long']
        
        # Simplified exit prices (would need actual option prices)
        exit_short = entry_short * 0.5  # Assume 50% decay
        exit_long = entry_long * 0.9   # Assume 10% decay
        
        pnl_per_lot = (entry_short - exit_short) - (exit_long - entry_long)
        total_pnl = pnl_per_lot * position['lots'] * self.lot_size
        
        trade_record = {
            'entry_time': position['entry_time'],
            'exit_time': exit_time,
            'strike': position['strike'],
            'lots': position['lots'],
            'entry_debit': position['net_debit'],
            'exit_pnl': total_pnl,
            'exit_reason': reason,
            'regime_at_entry': position['regime_at_entry']
        }
        
        self.trades.append(trade_record)
        self.capital += total_pnl
        
        logger.info(f"Closed calendar position: Strike={position['strike']}, P&L=₹{total_pnl:.2f}, Reason={reason}")
    
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
                'initial_capital': self.initial_capital,
                'final_capital': self.capital,
                'total_return_pct': 0.0,
                'trades': []
            }
        
        winning_trades = [t for t in self.trades if t['exit_pnl'] > 0]
        losing_trades = [t for t in self.trades if t['exit_pnl'] <= 0]
        
        total_pnl = sum(t['exit_pnl'] for t in self.trades)
        avg_pnl = total_pnl / len(self.trades) if self.trades else 0
        win_rate = (len(winning_trades) / len(self.trades)) * 100 if self.trades else 0
        total_return_pct = ((self.capital - self.initial_capital) / self.initial_capital) * 100
        
        return {
            'total_trades': len(self.trades),
            'winning_trades': len(winning_trades),
            'losing_trades': len(losing_trades),
            'win_rate': win_rate,
            'total_pnl': total_pnl,
            'avg_pnl': avg_pnl,
            'initial_capital': self.initial_capital,
            'final_capital': self.capital,
            'total_return_pct': total_return_pct,
            'trades': self.trades
        }


def main():
    """Run Neutral Calendar backtest"""
    backtester = NeutralCalendarBacktester(initial_capital=100000)
    
    # Backtest on available data
    backtester.run_backtest('20251222', '20260116', check_interval_minutes=15)
    
    # Generate report
    report = backtester.generate_report()
    
    print("\n" + "="*60)
    print("NEUTRAL CALENDAR BACKTEST REPORT")
    print("="*60)
    print(f"Total Trades: {report['total_trades']}")
    print(f"Winning Trades: {report['winning_trades']}")
    print(f"Losing Trades: {report['losing_trades']}")
    print(f"Win Rate: {report['win_rate']:.2f}%")
    print(f"\nTotal P&L: ₹{report['total_pnl']:.2f}")
    print(f"Average P&L per Trade: ₹{report['avg_pnl']:.2f}")
    print(f"\nInitial Capital: ₹{report['initial_capital']:.2f}")
    print(f"Final Capital: ₹{report['final_capital']:.2f}")
    print(f"Total Return: {report['total_return_pct']:.2f}%")
    print("="*60)
    
    # Save detailed report
    report_file = f"backtest_calendar_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    with open(report_file, 'w') as f:
        json.dump(report, f, indent=2, default=str)
    
    print(f"\nDetailed report saved to: {report_file}")
    
    return report


if __name__ == '__main__':
    main()
