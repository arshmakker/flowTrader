"""
Backtest comparison: Fixed Trailing Stop vs Hybrid Profit-Protection Trailing Stop

This script compares two trailing stop approaches:
1. FIXED: Standard 2× ATR trailing (current implementation)
2. HYBRID: Phased profit-protection trailing
   - Phase 1: Not in profit → 2× ATR trailing
   - Phase 2: Small profit (< 1× ATR) → Move to breakeven
   - Phase 3: Good profit (1-2× ATR) → 1.5× ATR trailing
   - Phase 4: Large profit (> 2× ATR) → 1× ATR trailing

Usage:
    python backtest_trailing_stop_comparison.py
"""

import pandas as pd
import numpy as np
import os
import json
import logging
import copy
from datetime import datetime, date, timedelta
from typing import Dict, List, Optional, Tuple
from pathlib import Path
import glob

from technical_indicators import calculate_ema, calculate_adx
from regime.regime_detector import RegimeDetector
from strategies.trend.config import (
    MAX_RISK_PCT_OF_CAPITAL,
    MAX_POSITION_SIZE,
    INITIAL_STOP_LOSS_ATR_MULTIPLIER,
    TRAILING_STOP_LOSS_ATR_MULTIPLIER,
    EMA_FAST_PERIOD,
    EMA_SLOW_PERIOD
)

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger('TrailingStopBacktest')


# Trailing Stop Modes
class TrailingStopMode:
    FIXED = 'FIXED'      # Standard 2× ATR trailing
    HYBRID = 'HYBRID'    # Phased profit-protection


# Hybrid trailing stop configuration
HYBRID_CONFIG = {
    'breakeven_threshold_atr': 0.5,    # Move to breakeven after 0.5× ATR profit
    'phase2_threshold_atr': 1.0,       # Use 1.5× ATR after 1× ATR profit
    'phase3_threshold_atr': 2.0,       # Use 1× ATR after 2× ATR profit
    'phase1_multiplier': 2.0,          # Not in profit: 2× ATR
    'phase2_multiplier': 1.5,          # Small profit: 1.5× ATR
    'phase3_multiplier': 1.0,          # Large profit: 1× ATR (tight)
}


class TrailingStopBacktester:
    """Backtest Futures Trend Following with configurable trailing stop modes"""
    
    def __init__(self, trailing_mode: str = TrailingStopMode.FIXED, 
                 initial_capital: float = 1000000,
                 hybrid_config: Dict = None):
        self.trailing_mode = trailing_mode
        self.initial_capital = initial_capital
        self.capital = initial_capital
        self.trades = []
        self.open_positions = []
        self.regime_detector = RegimeDetector()
        self.lot_size = 50  # NIFTY futures lot size
        self.hybrid_config = hybrid_config or HYBRID_CONFIG
        
        # Track additional metrics for hybrid mode
        self.breakeven_moves = 0
        self.profit_to_loss_trades = 0  # Trades that went from profit to loss
        self.max_unrealized_profits = []  # Track max unrealized profit per trade
        
    def load_futures_data(self, date_str: str) -> pd.DataFrame:
        """Load NIFTY futures data for a specific date"""
        date_pattern = f"market_data_{date_str}"
        data_dirs = glob.glob(date_pattern)
        
        if not data_dirs:
            return pd.DataFrame()
        
        data_dir = data_dirs[0]
        futures_dir = os.path.join(data_dir, 'raw_data', 'futures')
        
        if not os.path.exists(futures_dir):
            return pd.DataFrame()
        
        all_data = []
        for csv_file in glob.glob(os.path.join(futures_dir, 'NIFTY*.csv')):
            try:
                df = pd.read_csv(csv_file)
                if 'timestamp' in df.columns and 'ltp' in df.columns:
                    df['timestamp'] = pd.to_datetime(df['timestamp'])
                    df = df[df['ltp'] > 0]
                    all_data.append(df)
            except Exception as e:
                pass
        
        if not all_data:
            return pd.DataFrame()
        
        combined_df = pd.concat(all_data, ignore_index=True)
        combined_df = combined_df.sort_values('timestamp')
        return combined_df
    
    def aggregate_to_15min_candles(self, tick_data: pd.DataFrame) -> pd.DataFrame:
        """Aggregate tick data into 15-minute candles"""
        if tick_data.empty:
            return pd.DataFrame()
        
        tick_data = tick_data.set_index('timestamp')
        
        candles = tick_data['ltp'].resample('15min').agg({
            'open': 'first',
            'high': 'max',
            'low': 'min',
            'close': 'last'
        })
        
        if 'volume' in tick_data.columns:
            candles['volume'] = tick_data['volume'].resample('15min').sum()
        else:
            candles['volume'] = 0
        
        candles = candles.reset_index()
        candles.columns = ['timestamp', 'open', 'high', 'low', 'close', 'volume']
        candles = candles.dropna()
        
        return candles
    
    def calculate_indicators(self, candles: pd.DataFrame) -> Dict:
        """Calculate technical indicators from 15-minute candles"""
        if len(candles) < 100:
            return {}
        
        closes = candles['close'].tolist()
        highs = candles['high'].tolist()
        lows = candles['low'].tolist()
        
        ema_50 = calculate_ema(closes, period=EMA_FAST_PERIOD)
        ema_100 = calculate_ema(closes, period=EMA_SLOW_PERIOD)
        atr_14 = self.regime_detector.calculate_atr(highs, lows, closes, period=14)
        adx_14 = calculate_adx(highs, lows, closes, period=14)
        
        return {
            'ema_50': ema_50,
            'ema_100': ema_100,
            'atr_14': atr_14,
            'adx_14': adx_14,
            'closes': closes,
            'highs': highs,
            'lows': lows,
            'current_price': closes[-1] if closes else None
        }
    
    def detect_trend_direction(self, current_price: float, ema_50: float, ema_100: float) -> Optional[str]:
        """Detect trend direction from EMA structure"""
        if not all([current_price, ema_50, ema_100]):
            return None
        
        if current_price > ema_50 > ema_100:
            return 'LONG'
        elif current_price < ema_50 < ema_100:
            return 'SHORT'
        
        return None
    
    def check_entry_conditions(self, indicators: Dict, market_state: Dict) -> Tuple[bool, Optional[str]]:
        """Check if entry conditions are met"""
        regime = market_state.get('regime')
        if regime != 'TREND_CONTINUATION':
            return False, None
        
        adx = indicators.get('adx_14')
        if not adx or adx < 30:
            return False, None
        
        atr_percentile = market_state.get('atr_percentile')
        if not atr_percentile or atr_percentile < 50:
            return False, None
        
        current_price = indicators.get('current_price')
        ema_50 = indicators.get('ema_50')
        ema_100 = indicators.get('ema_100')
        
        direction = self.detect_trend_direction(current_price, ema_50, ema_100)
        if not direction:
            return False, None
        
        return True, direction
    
    def calculate_position_size(self, entry_price: float, atr: float, direction: str) -> Dict:
        """Calculate position size based on risk limits"""
        stop_loss_atr = atr * INITIAL_STOP_LOSS_ATR_MULTIPLIER
        risk_per_share = stop_loss_atr
        
        max_risk_amount = self.capital * MAX_RISK_PCT_OF_CAPITAL
        max_quantity_by_risk = int(max_risk_amount / risk_per_share) if risk_per_share > 0 else 0
        max_quantity = min(max_quantity_by_risk, MAX_POSITION_SIZE * self.lot_size)
        
        lots = max_quantity // self.lot_size
        quantity = lots * self.lot_size
        
        if quantity == 0:
            return {'lots': 0, 'quantity': 0, 'risk_amount': 0, 'stop_loss_price': 0}
        
        if direction == 'LONG':
            stop_loss_price = entry_price - stop_loss_atr
        else:
            stop_loss_price = entry_price + stop_loss_atr
        
        actual_risk_amount = quantity * risk_per_share
        
        return {
            'lots': lots,
            'quantity': quantity,
            'risk_amount': actual_risk_amount,
            'stop_loss_price': stop_loss_price,
            'risk_per_share': risk_per_share
        }
    
    def calculate_trailing_stop_fixed(self, position: Dict, current_price: float, atr: float) -> float:
        """Calculate trailing stop using FIXED 2× ATR method"""
        direction = position['direction']
        current_stop = position['current_stop_price']
        
        trailing_stop_atr = atr * TRAILING_STOP_LOSS_ATR_MULTIPLIER
        
        if direction == 'LONG':
            new_trailing_stop = current_price - trailing_stop_atr
            return max(current_stop, new_trailing_stop)
        else:  # SHORT
            new_trailing_stop = current_price + trailing_stop_atr
            return min(current_stop, new_trailing_stop)
    
    def calculate_trailing_stop_hybrid(self, position: Dict, current_price: float, atr: float) -> float:
        """
        Calculate trailing stop using HYBRID profit-protection method
        
        Phases:
        1. Not in profit: Standard 2× ATR trailing
        2. Small profit (< breakeven_threshold): Move to breakeven
        3. Medium profit (< phase2_threshold): Use 1.5× ATR
        4. Large profit (>= phase3_threshold): Use tight 1× ATR
        """
        direction = position['direction']
        entry_price = position['entry_price']
        current_stop = position['current_stop_price']
        
        # Calculate unrealized P&L in points
        if direction == 'LONG':
            unrealized_pnl_points = current_price - entry_price
        else:  # SHORT
            unrealized_pnl_points = entry_price - current_price
        
        # Determine which phase we're in
        cfg = self.hybrid_config
        
        if unrealized_pnl_points <= 0:
            # Phase 1: Not in profit - use standard trailing
            trailing_multiplier = cfg['phase1_multiplier']
            phase = 'PHASE1_NO_PROFIT'
            
        elif unrealized_pnl_points < (atr * cfg['breakeven_threshold_atr']):
            # Still use standard trailing, but track that we're getting close
            trailing_multiplier = cfg['phase1_multiplier']
            phase = 'PHASE1_APPROACHING_BE'
            
        elif unrealized_pnl_points < (atr * cfg['phase2_threshold_atr']):
            # Phase 2: Small profit - move to breakeven or better
            # Use standard trailing but ensure stop is at least at breakeven
            trailing_multiplier = cfg['phase1_multiplier']
            phase = 'PHASE2_BREAKEVEN'
            
        elif unrealized_pnl_points < (atr * cfg['phase3_threshold_atr']):
            # Phase 3: Medium profit - use tighter 1.5× ATR
            trailing_multiplier = cfg['phase2_multiplier']
            phase = 'PHASE3_TIGHT'
            
        else:
            # Phase 4: Large profit - use very tight 1× ATR
            trailing_multiplier = cfg['phase3_multiplier']
            phase = 'PHASE4_VERY_TIGHT'
        
        # Calculate new trailing stop based on phase
        trailing_distance = atr * trailing_multiplier
        
        if direction == 'LONG':
            new_trailing_stop = current_price - trailing_distance
            
            # In Phase 2+, ensure stop is at least at breakeven
            if phase in ['PHASE2_BREAKEVEN', 'PHASE3_TIGHT', 'PHASE4_VERY_TIGHT']:
                new_trailing_stop = max(new_trailing_stop, entry_price)
                if current_stop < entry_price and new_trailing_stop >= entry_price:
                    self.breakeven_moves += 1
            
            return max(current_stop, new_trailing_stop)
            
        else:  # SHORT
            new_trailing_stop = current_price + trailing_distance
            
            # In Phase 2+, ensure stop is at least at breakeven
            if phase in ['PHASE2_BREAKEVEN', 'PHASE3_TIGHT', 'PHASE4_VERY_TIGHT']:
                new_trailing_stop = min(new_trailing_stop, entry_price)
                if current_stop > entry_price and new_trailing_stop <= entry_price:
                    self.breakeven_moves += 1
            
            return min(current_stop, new_trailing_stop)
    
    def check_exit_conditions(self, position: Dict, current_price: float, indicators: Dict, 
                            market_state: Dict) -> Tuple[bool, str]:
        """Check if exit conditions are met and update trailing stop"""
        direction = position['direction']
        entry_price = position['entry_price']
        current_stop = position['current_stop_price']
        
        # Track max unrealized profit
        if direction == 'LONG':
            unrealized_pnl = (current_price - entry_price) * position['quantity']
        else:
            unrealized_pnl = (entry_price - current_price) * position['quantity']
        
        if 'max_unrealized_pnl' not in position:
            position['max_unrealized_pnl'] = unrealized_pnl
        else:
            position['max_unrealized_pnl'] = max(position['max_unrealized_pnl'], unrealized_pnl)
        
        # Exit condition 1: Stop loss hit
        if direction == 'LONG':
            if current_price <= current_stop:
                return True, 'STOP_LOSS_HIT'
        else:
            if current_price >= current_stop:
                return True, 'STOP_LOSS_HIT'
        
        # Exit condition 2: Regime change
        regime = market_state.get('regime')
        if regime != 'TREND_CONTINUATION':
            return True, 'REGIME_CHANGE'
        
        # Exit condition 3: EMA structure breaks
        ema_50 = indicators.get('ema_50')
        ema_100 = indicators.get('ema_100')
        
        if ema_50 and ema_100:
            if direction == 'LONG':
                if not (current_price > ema_50 > ema_100):
                    return True, 'EMA_STRUCTURE_BROKEN'
            else:
                if not (current_price < ema_50 < ema_100):
                    return True, 'EMA_STRUCTURE_BROKEN'
        
        # Update trailing stop based on mode
        atr = indicators.get('atr_14', 0)
        if atr > 0:
            if self.trailing_mode == TrailingStopMode.FIXED:
                position['current_stop_price'] = self.calculate_trailing_stop_fixed(
                    position, current_price, atr
                )
            elif self.trailing_mode == TrailingStopMode.HYBRID:
                position['current_stop_price'] = self.calculate_trailing_stop_hybrid(
                    position, current_price, atr
                )
        
        return False, None
    
    def run_backtest(self, start_date: str, end_date: str):
        """Run backtest on historical data"""
        logger.info(f"Starting {self.trailing_mode} backtest from {start_date} to {end_date}")
        
        start = datetime.strptime(start_date, '%Y%m%d').date()
        end = datetime.strptime(end_date, '%Y%m%d').date()
        
        current_date = start
        last_processed_price = None
        historical_candles = []
        
        # Pre-load historical candles
        preload_start = start - timedelta(days=5)
        preload_date = preload_start
        while preload_date < start:
            date_str = preload_date.strftime('%Y%m%d')
            tick_data = self.load_futures_data(date_str)
            if not tick_data.empty:
                candles = self.aggregate_to_15min_candles(tick_data)
                if not candles.empty:
                    historical_candles.extend(candles.to_dict('records'))
            preload_date += timedelta(days=1)
        
        if len(historical_candles) > 100:
            historical_candles = historical_candles[-100:]
        
        while current_date <= end:
            date_str = current_date.strftime('%Y%m%d')
            
            tick_data = self.load_futures_data(date_str)
            if tick_data.empty:
                current_date += timedelta(days=1)
                continue
            
            candles = self.aggregate_to_15min_candles(tick_data)
            if candles.empty:
                current_date += timedelta(days=1)
                continue
            
            historical_candles.extend(candles.to_dict('records'))
            
            if len(historical_candles) > 100:
                historical_candles = historical_candles[-100:]
            
            for idx, candle in candles.iterrows():
                timestamp = candle['timestamp']
                current_price = candle['close']
                last_processed_price = current_price
                
                recent_candles_df = pd.DataFrame(historical_candles[-100:])
                if len(recent_candles_df) < 100:
                    continue
                
                indicators = self.calculate_indicators(recent_candles_df)
                if not indicators:
                    continue
                
                market_state = {
                    'spot_price': current_price,
                    'adx_14': indicators.get('adx_14', 0),
                    'atr': indicators.get('atr_14', 0),
                    'atr_percentile': 75.0,
                    'regime': 'NEUTRAL'
                }
                
                adx = indicators.get('adx_14', 0)
                atr_percentile = market_state['atr_percentile']
                direction = self.detect_trend_direction(
                    current_price,
                    indicators.get('ema_50'),
                    indicators.get('ema_100')
                )
                
                if adx >= 30 and atr_percentile >= 50 and direction:
                    market_state['regime'] = 'TREND_CONTINUATION'
                
                # Check exit conditions
                for position in self.open_positions[:]:
                    should_exit, exit_reason = self.check_exit_conditions(
                        position, current_price, indicators, market_state
                    )
                    
                    if should_exit:
                        if position['direction'] == 'LONG':
                            pnl = (current_price - position['entry_price']) * position['quantity']
                        else:
                            pnl = (position['entry_price'] - current_price) * position['quantity']
                        
                        # Track if trade went from profit to loss
                        max_unrealized = position.get('max_unrealized_pnl', 0)
                        if max_unrealized > 0 and pnl < 0:
                            self.profit_to_loss_trades += 1
                        
                        trade_record = {
                            'entry_time': position['entry_time'],
                            'exit_time': timestamp,
                            'direction': position['direction'],
                            'entry_price': position['entry_price'],
                            'exit_price': current_price,
                            'quantity': position['quantity'],
                            'lots': position['lots'],
                            'pnl': pnl,
                            'exit_reason': exit_reason,
                            'regime_at_entry': position['regime_at_entry'],
                            'max_unrealized_pnl': max_unrealized,
                            'final_stop': position['current_stop_price']
                        }
                        
                        self.trades.append(trade_record)
                        self.max_unrealized_profits.append(max_unrealized)
                        self.capital += pnl
                        self.open_positions.remove(position)
                
                # Check entry conditions
                if not self.open_positions:
                    can_enter, direction = self.check_entry_conditions(indicators, market_state)
                    
                    if can_enter and direction:
                        position_info = self.calculate_position_size(
                            current_price, indicators.get('atr_14', 0), direction
                        )
                        
                        if position_info['quantity'] > 0:
                            position = {
                                'entry_time': timestamp,
                                'entry_price': current_price,
                                'direction': direction,
                                'quantity': position_info['quantity'],
                                'lots': position_info['lots'],
                                'initial_stop_price': position_info['stop_loss_price'],
                                'current_stop_price': position_info['stop_loss_price'],
                                'regime_at_entry': market_state['regime'],
                                'atr_at_entry': indicators.get('atr_14', 0),
                            }
                            
                            self.open_positions.append(position)
            
            current_date += timedelta(days=1)
        
        # Close remaining positions
        for position in self.open_positions:
            if last_processed_price is not None:
                last_price = last_processed_price
            else:
                last_price = position['entry_price']
            
            if position['direction'] == 'LONG':
                pnl = (last_price - position['entry_price']) * position['quantity']
            else:
                pnl = (position['entry_price'] - last_price) * position['quantity']
            
            max_unrealized = position.get('max_unrealized_pnl', 0)
            if max_unrealized > 0 and pnl < 0:
                self.profit_to_loss_trades += 1
            
            trade_record = {
                'entry_time': position['entry_time'],
                'exit_time': datetime.now(),
                'direction': position['direction'],
                'entry_price': position['entry_price'],
                'exit_price': last_price,
                'quantity': position['quantity'],
                'lots': position['lots'],
                'pnl': pnl,
                'exit_reason': 'END_OF_BACKTEST',
                'regime_at_entry': position['regime_at_entry'],
                'max_unrealized_pnl': max_unrealized,
                'final_stop': position['current_stop_price']
            }
            
            self.trades.append(trade_record)
            self.max_unrealized_profits.append(max_unrealized)
            self.capital += pnl
        
        self.open_positions = []
    
    def generate_report(self) -> Dict:
        """Generate backtest performance report"""
        if not self.trades:
            return {
                'mode': self.trailing_mode,
                'total_trades': 0,
                'total_pnl': 0.0,
                'final_capital': self.capital,
            }
        
        winning_trades = [t for t in self.trades if t['pnl'] > 0]
        losing_trades = [t for t in self.trades if t['pnl'] <= 0]
        
        total_pnl = sum(t['pnl'] for t in self.trades)
        avg_pnl = total_pnl / len(self.trades)
        
        max_profit = max((t['pnl'] for t in self.trades), default=0)
        max_loss = min((t['pnl'] for t in self.trades), default=0)
        
        win_rate = (len(winning_trades) / len(self.trades)) * 100
        total_return_pct = ((self.capital - self.initial_capital) / self.initial_capital) * 100
        
        # Average winner and loser
        avg_winner = sum(t['pnl'] for t in winning_trades) / len(winning_trades) if winning_trades else 0
        avg_loser = sum(t['pnl'] for t in losing_trades) / len(losing_trades) if losing_trades else 0
        
        # Profit given back (max unrealized - final pnl)
        profit_given_back = sum(
            max(0, t.get('max_unrealized_pnl', 0) - t['pnl']) 
            for t in self.trades
        )
        
        # Exit reasons
        exit_reasons = {}
        for trade in self.trades:
            reason = trade['exit_reason']
            if reason not in exit_reasons:
                exit_reasons[reason] = {'count': 0, 'pnl': 0}
            exit_reasons[reason]['count'] += 1
            exit_reasons[reason]['pnl'] += trade['pnl']
        
        return {
            'mode': self.trailing_mode,
            'total_trades': len(self.trades),
            'winning_trades': len(winning_trades),
            'losing_trades': len(losing_trades),
            'win_rate': win_rate,
            'total_pnl': total_pnl,
            'avg_pnl': avg_pnl,
            'avg_winner': avg_winner,
            'avg_loser': avg_loser,
            'max_profit': max_profit,
            'max_loss': max_loss,
            'initial_capital': self.initial_capital,
            'final_capital': self.capital,
            'total_return_pct': total_return_pct,
            'profit_to_loss_trades': self.profit_to_loss_trades,
            'profit_given_back': profit_given_back,
            'breakeven_moves': self.breakeven_moves,
            'exit_reasons': exit_reasons,
            'trades': self.trades
        }


def run_comparison(start_date: str, end_date: str):
    """Run both backtests and compare results"""
    
    print("\n" + "="*70)
    print("TRAILING STOP COMPARISON BACKTEST")
    print(f"Period: {start_date} to {end_date}")
    print("="*70)
    
    # Run FIXED mode
    print("\n[1/2] Running FIXED (2× ATR) trailing stop backtest...")
    fixed_backtester = TrailingStopBacktester(
        trailing_mode=TrailingStopMode.FIXED,
        initial_capital=1000000
    )
    fixed_backtester.run_backtest(start_date, end_date)
    fixed_report = fixed_backtester.generate_report()
    
    # Run HYBRID mode
    print("[2/2] Running HYBRID (profit-protection) trailing stop backtest...")
    hybrid_backtester = TrailingStopBacktester(
        trailing_mode=TrailingStopMode.HYBRID,
        initial_capital=1000000
    )
    hybrid_backtester.run_backtest(start_date, end_date)
    hybrid_report = hybrid_backtester.generate_report()
    
    # Print comparison
    print("\n" + "="*70)
    print("COMPARISON RESULTS")
    print("="*70)
    
    metrics = [
        ('Total Trades', 'total_trades', ''),
        ('Winning Trades', 'winning_trades', ''),
        ('Losing Trades', 'losing_trades', ''),
        ('Win Rate', 'win_rate', '%'),
        ('Total P&L', 'total_pnl', '₹'),
        ('Avg P&L/Trade', 'avg_pnl', '₹'),
        ('Avg Winner', 'avg_winner', '₹'),
        ('Avg Loser', 'avg_loser', '₹'),
        ('Max Profit', 'max_profit', '₹'),
        ('Max Loss', 'max_loss', '₹'),
        ('Final Capital', 'final_capital', '₹'),
        ('Total Return', 'total_return_pct', '%'),
        ('Profit→Loss Trades', 'profit_to_loss_trades', ''),
        ('Profit Given Back', 'profit_given_back', '₹'),
        ('Breakeven Moves', 'breakeven_moves', ''),
    ]
    
    print(f"\n{'Metric':<25} {'FIXED':>18} {'HYBRID':>18} {'Diff':>15}")
    print("-" * 76)
    
    for label, key, unit in metrics:
        fixed_val = fixed_report.get(key, 0)
        hybrid_val = hybrid_report.get(key, 0)
        
        if isinstance(fixed_val, float):
            diff = hybrid_val - fixed_val
            if unit == '₹':
                print(f"{label:<25} {unit}{fixed_val:>15,.2f} {unit}{hybrid_val:>15,.2f} {'+' if diff >= 0 else ''}{diff:>13,.2f}")
            elif unit == '%':
                print(f"{label:<25} {fixed_val:>17.2f}{unit} {hybrid_val:>17.2f}{unit} {'+' if diff >= 0 else ''}{diff:>12.2f}{unit}")
            else:
                print(f"{label:<25} {fixed_val:>18.2f} {hybrid_val:>18.2f} {'+' if diff >= 0 else ''}{diff:>15.2f}")
        else:
            diff = hybrid_val - fixed_val
            print(f"{label:<25} {fixed_val:>18} {hybrid_val:>18} {'+' if diff >= 0 else ''}{diff:>15}")
    
    print("-" * 76)
    
    # Summary
    print("\n" + "="*70)
    print("SUMMARY")
    print("="*70)
    
    pnl_diff = hybrid_report['total_pnl'] - fixed_report['total_pnl']
    profit_saved = fixed_report.get('profit_given_back', 0) - hybrid_report.get('profit_given_back', 0)
    
    if pnl_diff > 0:
        print(f"✅ HYBRID outperformed FIXED by ₹{pnl_diff:,.2f}")
    elif pnl_diff < 0:
        print(f"❌ FIXED outperformed HYBRID by ₹{abs(pnl_diff):,.2f}")
    else:
        print("➖ Both approaches had identical P&L")
    
    if profit_saved > 0:
        print(f"✅ HYBRID saved ₹{profit_saved:,.2f} in profit that would have been given back")
    
    p2l_diff = fixed_report.get('profit_to_loss_trades', 0) - hybrid_report.get('profit_to_loss_trades', 0)
    if p2l_diff > 0:
        print(f"✅ HYBRID prevented {p2l_diff} trades from going profit→loss")
    
    print("="*70)
    
    # Save detailed reports
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    
    comparison_report = {
        'backtest_period': {'start': start_date, 'end': end_date},
        'fixed_mode': fixed_report,
        'hybrid_mode': hybrid_report,
        'comparison': {
            'pnl_difference': pnl_diff,
            'profit_saved': profit_saved,
            'profit_to_loss_prevented': p2l_diff
        },
        'hybrid_config': HYBRID_CONFIG
    }
    
    report_file = f"backtest_trailing_comparison_{timestamp}.json"
    with open(report_file, 'w') as f:
        json.dump(comparison_report, f, indent=2, default=str)
    
    print(f"\nDetailed report saved to: {report_file}")
    
    return comparison_report


def main():
    """Run the comparison backtest"""
    # Use available data range
    # Adjust these dates based on your available market_data_* directories
    run_comparison('20251222', '20260116')


if __name__ == '__main__':
    main()
