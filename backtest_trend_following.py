"""
Backtesting framework for Futures Trend Following strategy using historical data
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

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger('Backtest')


class TrendFollowingBacktester:
    """Backtest Futures Trend Following strategy on historical data"""
    
    def __init__(self, data_dir='market_data_*', initial_capital=1000000):
        self.data_dir = data_dir
        self.initial_capital = initial_capital
        self.capital = initial_capital
        self.trades = []
        self.open_positions = []
        self.daily_pnl = []
        self.regime_detector = RegimeDetector()
        self.lot_size = 50  # NIFTY futures lot size
        
    def load_futures_data(self, date_str: str) -> pd.DataFrame:
        """
        Load NIFTY futures data for a specific date
        
        Returns:
            DataFrame with columns: timestamp, ltp, bid, ask, volume, oi
        """
        # Find data directory for this date
        date_pattern = f"market_data_{date_str}"
        data_dirs = glob.glob(date_pattern)
        
        if not data_dirs:
            logger.warning(f"No data directory found for {date_str}")
            return pd.DataFrame()
        
        data_dir = data_dirs[0]
        futures_dir = os.path.join(data_dir, 'raw_data', 'futures')
        
        if not os.path.exists(futures_dir):
            logger.warning(f"No futures directory found for {date_str}")
            return pd.DataFrame()
        
        # Load all NIFTY futures files
        all_data = []
        for csv_file in glob.glob(os.path.join(futures_dir, 'NIFTY*.csv')):
            try:
                df = pd.read_csv(csv_file)
                if 'timestamp' in df.columns and 'ltp' in df.columns:
                    df['timestamp'] = pd.to_datetime(df['timestamp'])
                    # Filter out invalid prices
                    df = df[df['ltp'] > 0]
                    all_data.append(df)
            except Exception as e:
                logger.debug(f"Error loading {csv_file}: {str(e)}")
        
        if not all_data:
            return pd.DataFrame()
        
        # Combine all futures data
        combined_df = pd.concat(all_data, ignore_index=True)
        combined_df = combined_df.sort_values('timestamp')
        
        logger.info(f"Loaded {len(combined_df)} futures ticks for {date_str}")
        return combined_df
    
    def aggregate_to_15min_candles(self, tick_data: pd.DataFrame) -> pd.DataFrame:
        """
        Aggregate tick data into 15-minute candles
        
        Returns:
            DataFrame with columns: timestamp, open, high, low, close, volume
        """
        if tick_data.empty:
            return pd.DataFrame()
        
        # Set timestamp as index
        tick_data = tick_data.set_index('timestamp')
        
        # Resample to 15-minute candles
        candles = tick_data['ltp'].resample('15min').agg({
            'open': 'first',
            'high': 'max',
            'low': 'min',
            'close': 'last'
        })
        
        # Add volume if available
        if 'volume' in tick_data.columns:
            candles['volume'] = tick_data['volume'].resample('15min').sum()
        else:
            candles['volume'] = 0
        
        # Reset index
        candles = candles.reset_index()
        candles.columns = ['timestamp', 'open', 'high', 'low', 'close', 'volume']
        
        # Remove rows with NaN (incomplete candles)
        candles = candles.dropna()
        
        return candles
    
    def calculate_indicators(self, candles: pd.DataFrame) -> Dict:
        """
        Calculate technical indicators from 15-minute candles
        
        Returns:
            Dictionary with indicators:
            - ema_50: float
            - ema_100: float
            - atr_14: float
            - adx_14: float
            - closes: List[float] (for EMA calculation)
            - highs: List[float]
            - lows: List[float]
        """
        if len(candles) < 100:
            return {}
        
        closes = candles['close'].tolist()
        highs = candles['high'].tolist()
        lows = candles['low'].tolist()
        
        # Calculate EMAs
        ema_50 = calculate_ema(closes, period=EMA_FAST_PERIOD)
        ema_100 = calculate_ema(closes, period=EMA_SLOW_PERIOD)
        
        # Calculate ATR(14) using RegimeDetector method
        atr_14 = self.regime_detector.calculate_atr(highs, lows, closes, period=14)
        
        # Calculate ADX(14)
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
        """
        Detect trend direction from EMA structure
        
        Returns:
            'LONG', 'SHORT', or None
        """
        if not all([current_price, ema_50, ema_100]):
            return None
        
        if current_price > ema_50 > ema_100:
            return 'LONG'
        elif current_price < ema_50 < ema_100:
            return 'SHORT'
        
        return None
    
    def check_entry_conditions(self, indicators: Dict, market_state: Dict) -> Tuple[bool, Optional[str]]:
        """
        Check if entry conditions are met
        
        Returns:
            (can_enter, direction) or (False, None)
        """
        # Check regime
        regime = market_state.get('regime')
        if regime != 'TREND_CONTINUATION':
            return False, None
        
        # Check ADX
        adx = indicators.get('adx_14')
        if not adx or adx < 30:
            return False, None
        
        # Check ATR percentile
        atr_percentile = market_state.get('atr_percentile')
        if not atr_percentile or atr_percentile < 50:
            return False, None
        
        # Check EMA structure
        current_price = indicators.get('current_price')
        ema_50 = indicators.get('ema_50')
        ema_100 = indicators.get('ema_100')
        
        direction = self.detect_trend_direction(current_price, ema_50, ema_100)
        if not direction:
            return False, None
        
        return True, direction
    
    def calculate_position_size(self, entry_price: float, atr: float, direction: str) -> Dict:
        """
        Calculate position size based on risk limits
        
        Returns:
            Dictionary with position details
        """
        # Calculate initial stop loss
        stop_loss_atr = atr * INITIAL_STOP_LOSS_ATR_MULTIPLIER
        risk_per_share = stop_loss_atr
        
        # Maximum risk per trade
        max_risk_amount = self.capital * MAX_RISK_PCT_OF_CAPITAL
        
        # Calculate maximum quantity based on risk
        max_quantity_by_risk = int(max_risk_amount / risk_per_share) if risk_per_share > 0 else 0
        
        # Limit to max position size
        max_quantity = min(max_quantity_by_risk, MAX_POSITION_SIZE * self.lot_size)
        
        # Round down to lot size
        lots = max_quantity // self.lot_size
        quantity = lots * self.lot_size
        
        if quantity == 0:
            return {
                'lots': 0,
                'quantity': 0,
                'risk_amount': 0,
                'stop_loss_price': 0
            }
        
        # Calculate stop loss price
        if direction == 'LONG':
            stop_loss_price = entry_price - stop_loss_atr
        else:  # SHORT
            stop_loss_price = entry_price + stop_loss_atr
        
        # Calculate actual risk
        actual_risk_amount = quantity * risk_per_share
        
        return {
            'lots': lots,
            'quantity': quantity,
            'risk_amount': actual_risk_amount,
            'stop_loss_price': stop_loss_price,
            'risk_per_share': risk_per_share
        }
    
    def check_exit_conditions(self, position: Dict, current_price: float, indicators: Dict, 
                            market_state: Dict) -> Tuple[bool, str]:
        """
        Check if exit conditions are met
        
        Returns:
            (should_exit, exit_reason)
        """
        direction = position['direction']
        entry_price = position['entry_price']
        current_stop = position['current_stop_price']
        
        # Exit condition 1: Stop loss hit
        if direction == 'LONG':
            if current_price <= current_stop:
                return True, 'STOP_LOSS_HIT'
        else:  # SHORT
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
            else:  # SHORT
                if not (current_price < ema_50 < ema_100):
                    return True, 'EMA_STRUCTURE_BROKEN'
        
        # Update trailing stop loss
        atr = indicators.get('atr_14', 0)
        if atr > 0:
            trailing_stop_atr = atr * TRAILING_STOP_LOSS_ATR_MULTIPLIER
            if direction == 'LONG':
                new_trailing_stop = current_price - trailing_stop_atr
                position['current_stop_price'] = max(current_stop, new_trailing_stop)
            else:  # SHORT
                new_trailing_stop = current_price + trailing_stop_atr
                position['current_stop_price'] = min(current_stop, new_trailing_stop)
        
        return False, None
    
    def run_backtest(self, start_date: str, end_date: str, check_interval_minutes: int = 15):
        """
        Run backtest on historical data
        
        Args:
            start_date: Start date in YYYYMMDD format
            end_date: End date in YYYYMMDD format
            check_interval_minutes: How often to check for entry/exit (default: 15 minutes)
        """
        logger.info(f"Starting backtest from {start_date} to {end_date}")
        
        start = datetime.strptime(start_date, '%Y%m%d').date()
        end = datetime.strptime(end_date, '%Y%m%d').date()
        
        current_date = start
        check_interval = timedelta(minutes=check_interval_minutes)
        last_processed_price = None  # Track last candle close price for end-of-backtest closing
        
        # Load all historical data for regime detection (across multiple days)
        historical_candles = []
        
        # Pre-load historical candles from previous days (for EMA calculation)
        # Load up to 5 days before start date to get enough data for EMA(100)
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
        
        # Keep only last 100 candles
        if len(historical_candles) > 100:
            historical_candles = historical_candles[-100:]
        
        while current_date <= end:
            date_str = current_date.strftime('%Y%m%d')
            logger.info(f"Processing {date_str}...")
            
            # Load futures data
            tick_data = self.load_futures_data(date_str)
            if tick_data.empty:
                logger.warning(f"No futures data for {date_str}")
                current_date += timedelta(days=1)
                continue
            
            # Aggregate to 15-minute candles
            candles = self.aggregate_to_15min_candles(tick_data)
            if candles.empty:
                logger.warning(f"No candles generated for {date_str}")
                current_date += timedelta(days=1)
                continue
            
            # Add to historical candles for regime detection
            historical_candles.extend(candles.to_dict('records'))
            
            # Keep only last 100 candles for EMA calculation (need 100 for EMA(100))
            # This ensures we have enough data across days
            if len(historical_candles) > 100:
                historical_candles = historical_candles[-100:]
            
            # Process each candle
            for idx, candle in candles.iterrows():
                timestamp = candle['timestamp']
                current_price = candle['close']
                last_processed_price = current_price  # Track last processed price
                
                # Calculate indicators from recent candles
                recent_candles_df = pd.DataFrame(historical_candles[-100:])
                if len(recent_candles_df) < 100:
                    continue
                
                indicators = self.calculate_indicators(recent_candles_df)
                if not indicators:
                    continue
                
                # Build market state for regime detection
                # Note: For backtest, we'll use simplified regime detection
                # In production, regime detector uses more sophisticated logic
                market_state = {
                    'spot_price': current_price,
                    'adx_14': indicators.get('adx_14', 0),
                    'atr': indicators.get('atr_14', 0),
                    'atr_percentile': 75.0,  # Simplified - in production this is calculated from history
                    'regime': 'NEUTRAL'  # Will be updated below
                }
                
                # Simplified regime detection for backtest
                adx = indicators.get('adx_14', 0)
                atr_percentile = market_state['atr_percentile']
                direction = self.detect_trend_direction(
                    current_price,
                    indicators.get('ema_50'),
                    indicators.get('ema_100')
                )
                
                if adx >= 30 and atr_percentile >= 50 and direction:
                    market_state['regime'] = 'TREND_CONTINUATION'
                
                # Check exit conditions for open positions
                for position in self.open_positions[:]:  # Copy list to allow modification
                    should_exit, exit_reason = self.check_exit_conditions(
                        position, current_price, indicators, market_state
                    )
                    
                    if should_exit:
                        # Calculate P&L
                        if position['direction'] == 'LONG':
                            pnl = (current_price - position['entry_price']) * position['quantity']
                        else:  # SHORT
                            pnl = (position['entry_price'] - current_price) * position['quantity']
                        
                        # Record trade
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
                            'regime_at_entry': position['regime_at_entry']
                        }
                        
                        self.trades.append(trade_record)
                        self.capital += pnl
                        self.open_positions.remove(position)
                        
                        logger.info(
                            f"Exited {position['direction']} position: "
                            f"Entry={position['entry_price']:.2f}, Exit={current_price:.2f}, "
                            f"P&L=₹{pnl:.2f}, Reason={exit_reason}"
                        )
                
                # Check entry conditions (only if no open position)
                if not self.open_positions:
                    can_enter, direction = self.check_entry_conditions(indicators, market_state)
                    
                    if can_enter and direction:
                        # Calculate position size
                        position_info = self.calculate_position_size(
                            current_price, indicators.get('atr_14', 0), direction
                        )
                        
                        if position_info['quantity'] > 0:
                            # Create position
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
                                'ema_50_at_entry': indicators.get('ema_50', 0),
                                'ema_100_at_entry': indicators.get('ema_100', 0)
                            }
                            
                            self.open_positions.append(position)
                            
                            logger.info(
                                f"Entered {direction} position: "
                                f"Price={current_price:.2f}, Quantity={position_info['quantity']}, "
                                f"Lots={position_info['lots']}, SL={position_info['stop_loss_price']:.2f}"
                            )
            
            current_date += timedelta(days=1)
        
        # Close any remaining positions at end
        logger.info("Closing remaining positions...")
        for position in self.open_positions:
            # Use last processed candle close price (last candle of last processed day)
            if last_processed_price is not None:
                last_price = last_processed_price
                logger.info(f"Closing position with last candle price: {last_price:.2f}")
            else:
                # Fallback: use entry price if no candles were processed (shouldn't happen)
                last_price = position['entry_price']
                logger.warning(f"Using entry_price for end-of-backtest close (no last price available): {position['entry_time']}")
            
            if position['direction'] == 'LONG':
                pnl = (last_price - position['entry_price']) * position['quantity']
            else:  # SHORT
                pnl = (position['entry_price'] - last_price) * position['quantity']
            
            trade_record = {
                'entry_time': position['entry_time'],
                'exit_time': datetime.now(),
                'direction': position['direction'],
                'entry_price': position['entry_price'],
                'exit_price': last_price,
                'quantity': position['quantity'],
                'lots': position['lots'],
                'pnl': pnl,
                'exit_reason': 'end_of_backtest',
                'regime_at_entry': position['regime_at_entry']
            }
            
            self.trades.append(trade_record)
            self.capital += pnl
        
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
                'trades': []
            }
        
        winning_trades = [t for t in self.trades if t['pnl'] > 0]
        losing_trades = [t for t in self.trades if t['pnl'] <= 0]
        
        total_pnl = sum(t['pnl'] for t in self.trades)
        avg_pnl = total_pnl / len(self.trades) if self.trades else 0
        
        max_profit = max((t['pnl'] for t in self.trades), default=0)
        max_loss = min((t['pnl'] for t in self.trades), default=0)
        
        win_rate = (len(winning_trades) / len(self.trades)) * 100 if self.trades else 0
        total_return_pct = ((self.capital - self.initial_capital) / self.initial_capital) * 100
        
        # Calculate by direction
        long_trades = [t for t in self.trades if t['direction'] == 'LONG']
        short_trades = [t for t in self.trades if t['direction'] == 'SHORT']
        
        long_pnl = sum(t['pnl'] for t in long_trades)
        short_pnl = sum(t['pnl'] for t in short_trades)
        
        # Calculate by exit reason
        exit_reasons = {}
        for trade in self.trades:
            reason = trade['exit_reason']
            if reason not in exit_reasons:
                exit_reasons[reason] = {'count': 0, 'pnl': 0}
            exit_reasons[reason]['count'] += 1
            exit_reasons[reason]['pnl'] += trade['pnl']
        
        return {
            'total_trades': len(self.trades),
            'winning_trades': len(winning_trades),
            'losing_trades': len(losing_trades),
            'win_rate': win_rate,
            'total_pnl': total_pnl,
            'avg_pnl': avg_pnl,
            'max_profit': max_profit,
            'max_loss': max_loss,
            'initial_capital': self.initial_capital,
            'final_capital': self.capital,
            'total_return_pct': total_return_pct,
            'long_trades': len(long_trades),
            'short_trades': len(short_trades),
            'long_pnl': long_pnl,
            'short_pnl': short_pnl,
            'exit_reasons': exit_reasons,
            'trades': self.trades
        }


def main():
    """Run backtest"""
    backtester = TrendFollowingBacktester(initial_capital=1000000)
    
    # Backtest on available data
    # Test on a subset first, then expand
    backtester.run_backtest('20251222', '20260116', check_interval_minutes=15)
    
    # Generate report
    report = backtester.generate_report()
    
    print("\n" + "="*60)
    print("FUTURES TREND FOLLOWING BACKTEST REPORT")
    print("="*60)
    print(f"Total Trades: {report['total_trades']}")
    print(f"Winning Trades: {report['winning_trades']}")
    print(f"Losing Trades: {report['losing_trades']}")
    print(f"Win Rate: {report['win_rate']:.2f}%")
    print(f"\nTotal P&L: ₹{report['total_pnl']:.2f}")
    print(f"Average P&L per Trade: ₹{report['avg_pnl']:.2f}")
    print(f"Max Profit: ₹{report['max_profit']:.2f}")
    print(f"Max Loss: ₹{report['max_loss']:.2f}")
    print(f"\nLong Trades: {report['long_trades']} (P&L: ₹{report['long_pnl']:.2f})")
    print(f"Short Trades: {report['short_trades']} (P&L: ₹{report['short_pnl']:.2f})")
    print(f"\nInitial Capital: ₹{report['initial_capital']:.2f}")
    print(f"Final Capital: ₹{report['final_capital']:.2f}")
    print(f"Total Return: {report['total_return_pct']:.2f}%")
    
    print("\nExit Reasons:")
    for reason, stats in report['exit_reasons'].items():
        print(f"  {reason}: {stats['count']} trades, P&L: ₹{stats['pnl']:.2f}")
    
    print("="*60)
    
    # Save detailed report
    report_file = f"backtest_trend_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    with open(report_file, 'w') as f:
        json.dump(report, f, indent=2, default=str)
    
    print(f"\nDetailed report saved to: {report_file}")
    
    return report


if __name__ == '__main__':
    main()
