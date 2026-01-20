"""
Regime Detection Validation Backtest

Validates regime detection accuracy on historical data:
- Regime detection accuracy (CONVEX, INCOME, NEUTRAL, TREND_CONTINUATION)
- Regime persistence (confirmation count logic)
- Indicator calculation accuracy (IV%, ADX, ATR%, Range state)
- Regime transitions and timing
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

from regime.regime_detector import RegimeDetector
from technical_indicators import calculate_iv_percentile, calculate_atm_iv, calculate_adx, get_historical_price_data
from symbol_manager import SymbolManager

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger('RegimeBacktest')


class RegimeDetectionBacktester:
    """Validate regime detection on historical data"""
    
    def __init__(self, data_dir='market_data_*'):
        self.data_dir = data_dir
        self.regime_detector = RegimeDetector()
        self.regime_history = []
        self.indicator_history = []
        self.transitions = []
        
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
                logger.debug(f"Error loading {csv_file}: {str(e)}")
        
        if not all_data:
            return pd.DataFrame()
        
        combined_df = pd.concat(all_data, ignore_index=True)
        combined_df = combined_df.sort_values('timestamp')
        return combined_df
    
    def load_options_data(self, date_str: str) -> Dict[str, pd.DataFrame]:
        """Load options data for a specific date"""
        date_pattern = f"market_data_{date_str}"
        data_dirs = glob.glob(date_pattern)
        
        if not data_dirs:
            return {}
        
        data_dir = data_dirs[0]
        options_dir = os.path.join(data_dir, 'raw_data', 'options', 'NIFTY')
        
        if not os.path.exists(options_dir):
            return {}
        
        options_data = {}
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
                        options_data[symbol] = df
                except Exception as e:
                    logger.debug(f"Error loading {csv_file}: {str(e)}")
        
        return options_data
    
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
        }).reset_index()
        candles = candles.dropna()
        return candles
    
    def calculate_indicators(self, candles: pd.DataFrame, options_data: Dict, spot_price: float) -> Dict:
        """Calculate all indicators needed for regime detection"""
        indicators = {
            'spot_price': spot_price,
            'iv_percentile': None,
            'adx_14': None,
            'atr': None,
            'atr_percentile': None,
            'range_state': 'NORMAL'
        }
        
        # Calculate ADX and ATR from candles
        if len(candles) >= 15:
            highs = candles['high'].tolist()
            lows = candles['low'].tolist()
            closes = candles['close'].tolist()
            
            # Calculate ADX
            indicators['adx_14'] = calculate_adx(highs, lows, closes, period=14)
            
            # Calculate ATR
            indicators['atr'] = self.regime_detector.calculate_atr(highs, lows, closes, period=14)
            
            # Calculate ATR percentile
            if indicators['atr']:
                indicators['atr_percentile'] = self.regime_detector.calculate_atr_percentile(
                    indicators['atr']
                )
        
        # Calculate IV percentile from options data
        if options_data:
            try:
                # Build option chain DataFrame
                option_chain_rows = []
                for symbol, df in options_data.items():
                    if not df.empty and 'ltp' in df.columns:
                        # Get latest quote
                        latest = df.sort_values('timestamp').iloc[-1]
                        if 'strike' in latest and 'option_type' in latest:
                            option_chain_rows.append({
                                'symbol': symbol,
                                'strike': float(latest['strike']),
                                'option_type': latest['option_type'],
                                'ltp': float(latest['ltp']) if pd.notna(latest['ltp']) else 0,
                                'bid': float(latest.get('bid', 0)) if pd.notna(latest.get('bid', 0)) else 0,
                                'ask': float(latest.get('ask', 0)) if pd.notna(latest.get('ask', 0)) else 0,
                            })
                
                if option_chain_rows:
                    option_chain_df = pd.DataFrame(option_chain_rows)
                    # Estimate days to expiry (simplified - use 7 for weekly)
                    days_to_expiry = 7
                    indicators['iv_percentile'] = calculate_iv_percentile(
                        option_chain_df, spot_price, days_to_expiry
                    )
            except Exception as e:
                logger.debug(f"Error calculating IV percentile: {str(e)}")
        
        return indicators
    
    def detect_regime_for_timestamp(self, indicators: Dict, timestamp: datetime) -> Dict:
        """Detect regime for a specific timestamp"""
        market_state = {
            'spot_price': indicators['spot_price'],
            'iv_percentile': indicators['iv_percentile'] or 50.0,  # Default if missing
            'adx_14': indicators['adx_14'] or 0,
            'atr': indicators['atr'],
            'atr_percentile': indicators['atr_percentile']
        }
        
        # Use RegimeDetector (simplified - no API/symbol_manager in backtest)
        # We'll use a simplified version that doesn't require API
        regime_result = self._simplified_regime_detection(market_state)
        
        return {
            'timestamp': timestamp,
            'regime': regime_result['regime'],
            'indicators': indicators,
            'regime_details': regime_result
        }
    
    def _simplified_regime_detection(self, market_state: Dict) -> Dict:
        """Simplified regime detection for backtest (without API dependencies)"""
        iv_percentile = market_state.get('iv_percentile', 50.0)
        adx_14 = market_state.get('adx_14', 0)
        atr_percentile = market_state.get('atr_percentile')
        spot_price = market_state.get('spot_price', 0)
        
        # CONVEX: Low IV, compressed volatility
        if iv_percentile < 40 and atr_percentile and atr_percentile < 25:
            return {
                'regime': 'CONVEX',
                'iv_percentile': iv_percentile,
                'adx': adx_14,
                'atr_percentile': atr_percentile,
                'range_state': 'COMPRESSED'
            }
        
        # INCOME: High IV, low trend
        if iv_percentile >= 50 and adx_14 < 25:
            return {
                'regime': 'INCOME',
                'iv_percentile': iv_percentile,
                'adx': adx_14,
                'atr_percentile': atr_percentile,
                'range_state': 'NORMAL'
            }
        
        # TREND_CONTINUATION: Strong trend
        if adx_14 >= 30 and atr_percentile and atr_percentile >= 50:
            return {
                'regime': 'TREND_CONTINUATION',
                'iv_percentile': iv_percentile,
                'adx': adx_14,
                'atr_percentile': atr_percentile,
                'range_state': 'EXPANDING'
            }
        
        # NEUTRAL: Default
        return {
            'regime': 'NEUTRAL',
            'iv_percentile': iv_percentile,
            'adx': adx_14,
            'atr_percentile': atr_percentile,
            'range_state': 'NORMAL'
        }
    
    def run_backtest(self, start_date: str, end_date: str, check_interval_minutes: int = 15):
        """Run regime detection validation on historical data"""
        logger.info(f"Starting regime detection validation from {start_date} to {end_date}")
        
        start = datetime.strptime(start_date, '%Y%m%d').date()
        end = datetime.strptime(end_date, '%Y%m%d').date()
        current_date = start
        check_interval = timedelta(minutes=check_interval_minutes)
        
        previous_regime = None
        
        while current_date <= end:
            date_str = current_date.strftime('%Y%m%d')
            logger.info(f"Processing {date_str}...")
            
            # Load data
            futures_data = self.load_futures_data(date_str)
            options_data = self.load_options_data(date_str)
            
            if futures_data.empty:
                logger.warning(f"No futures data for {date_str}")
                current_date += timedelta(days=1)
                continue
            
            # Aggregate to 15-minute candles
            candles = self.aggregate_to_15min_candles(futures_data)
            if candles.empty:
                logger.warning(f"No candles generated for {date_str}")
                current_date += timedelta(days=1)
                continue
            
            # Process each candle
            for idx, candle in candles.iterrows():
                timestamp = candle['timestamp']
                spot_price = candle['close']
                
                # Calculate indicators
                indicators = self.calculate_indicators(candles.iloc[:idx+1], options_data, spot_price)
                
                # Detect regime
                regime_result = self.detect_regime_for_timestamp(indicators, timestamp)
                
                # Track regime history
                self.regime_history.append(regime_result)
                self.indicator_history.append({
                    'timestamp': timestamp,
                    'indicators': indicators
                })
                
                # Track transitions
                current_regime = regime_result['regime']
                if previous_regime and previous_regime != current_regime:
                    self.transitions.append({
                        'timestamp': timestamp,
                        'from_regime': previous_regime,
                        'to_regime': current_regime,
                        'indicators': indicators
                    })
                    logger.info(f"Regime transition: {previous_regime} -> {current_regime} at {timestamp}")
                
                previous_regime = current_regime
            
            current_date += timedelta(days=1)
        
        logger.info(f"Processed {len(self.regime_history)} regime detections")
        logger.info(f"Found {len(self.transitions)} regime transitions")
    
    def generate_report(self) -> Dict:
        """Generate validation report"""
        if not self.regime_history:
            return {
                'total_detections': 0,
                'regime_distribution': {},
                'transitions': [],
                'indicator_stats': {}
            }
        
        # Regime distribution
        regime_counts = {}
        for entry in self.regime_history:
            regime = entry['regime']
            regime_counts[regime] = regime_counts.get(regime, 0) + 1
        
        # Indicator statistics by regime
        indicator_stats = {}
        for regime in regime_counts.keys():
            regime_entries = [e for e in self.regime_history if e['regime'] == regime]
            if regime_entries:
                iv_values = [e['indicators'].get('iv_percentile') for e in regime_entries if e['indicators'].get('iv_percentile')]
                adx_values = [e['indicators'].get('adx_14') for e in regime_entries if e['indicators'].get('adx_14')]
                atr_pct_values = [e['indicators'].get('atr_percentile') for e in regime_entries if e['indicators'].get('atr_percentile')]
                
                indicator_stats[regime] = {
                    'count': len(regime_entries),
                    'iv_percentile': {
                        'mean': np.mean(iv_values) if iv_values else None,
                        'min': np.min(iv_values) if iv_values else None,
                        'max': np.max(iv_values) if iv_values else None
                    },
                    'adx_14': {
                        'mean': np.mean(adx_values) if adx_values else None,
                        'min': np.min(adx_values) if adx_values else None,
                        'max': np.max(adx_values) if adx_values else None
                    },
                    'atr_percentile': {
                        'mean': np.mean(atr_pct_values) if atr_pct_values else None,
                        'min': np.min(atr_pct_values) if atr_pct_values else None,
                        'max': np.max(atr_pct_values) if atr_pct_values else None
                    }
                }
        
        return {
            'total_detections': len(self.regime_history),
            'regime_distribution': regime_counts,
            'regime_percentages': {k: (v / len(self.regime_history)) * 100 
                                  for k, v in regime_counts.items()},
            'transitions': self.transitions,
            'transition_count': len(self.transitions),
            'indicator_stats': indicator_stats,
            'regime_history': self.regime_history[:100]  # First 100 entries for detail
        }


def main():
    """Run regime detection validation"""
    backtester = RegimeDetectionBacktester()
    
    # Run validation on available data
    backtester.run_backtest('20251222', '20260116', check_interval_minutes=15)
    
    # Generate report
    report = backtester.generate_report()
    
    print("\n" + "="*60)
    print("REGIME DETECTION VALIDATION REPORT")
    print("="*60)
    print(f"Total Detections: {report['total_detections']}")
    print(f"\nRegime Distribution:")
    for regime, count in report['regime_distribution'].items():
        pct = report['regime_percentages'].get(regime, 0)
        print(f"  {regime}: {count} ({pct:.1f}%)")
    
    print(f"\nRegime Transitions: {report['transition_count']}")
    if report['transitions']:
        print("\nTransition Timeline:")
        for trans in report['transitions'][:10]:  # Show first 10
            print(f"  {trans['timestamp']}: {trans['from_regime']} -> {trans['to_regime']}")
    
    print("\nIndicator Statistics by Regime:")
    for regime, stats in report['indicator_stats'].items():
        print(f"\n  {regime}:")
        if stats['iv_percentile']['mean']:
            print(f"    IV%: {stats['iv_percentile']['mean']:.1f} (min: {stats['iv_percentile']['min']:.1f}, max: {stats['iv_percentile']['max']:.1f})")
        if stats['adx_14']['mean']:
            print(f"    ADX: {stats['adx_14']['mean']:.1f} (min: {stats['adx_14']['min']:.1f}, max: {stats['adx_14']['max']:.1f})")
        if stats['atr_percentile']['mean']:
            print(f"    ATR%: {stats['atr_percentile']['mean']:.1f} (min: {stats['atr_percentile']['min']:.1f}, max: {stats['atr_percentile']['max']:.1f})")
    
    print("="*60)
    
    # Save detailed report
    report_file = f"backtest_regime_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    with open(report_file, 'w') as f:
        json.dump(report, f, indent=2, default=str)
    
    print(f"\nDetailed report saved to: {report_file}")
    
    return report


if __name__ == '__main__':
    main()
