"""
Regime Detection Validation Backtest

Validates regime detection using production logic (classify_regime_from_indicators).
Two-fork: only two regimes — TRENDING (ADX/ATR/EMA trend conditions) and SIDEWAYS (everything else).
IV from CSV, daily_metrics.json, or volatility proxy when no IV data.
Regime transitions and distribution report.
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

from regime.regime_detector import RegimeDetector, classify_regime_from_indicators
from technical_indicators import calculate_adx, calculate_ema
from backtest_trend_following import (
    load_backtest_iv_csv,
    load_daily_metrics,
    iv_percentile_from_vol_history,
)

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
    
    def _atr_percentile_and_range(self, candles_df: pd.DataFrame, atr_period: int = 14,
                                   range_lookback: int = 20) -> Tuple[Optional[float], Optional[float], Optional[float]]:
        """ATR percentile (0-100), last_range, rolling_avg_range from candles (production-style)."""
        if len(candles_df) < atr_period + 5 or 'high' not in candles_df.columns or 'low' not in candles_df.columns:
            return None, None, None
        highs = candles_df['high'].tolist()
        lows = candles_df['low'].tolist()
        closes = candles_df['close'].tolist()
        atr_values = []
        for i in range(atr_period - 1, len(closes)):
            h = highs[max(0, i - atr_period + 1):i + 1]
            l_ = lows[max(0, i - atr_period + 1):i + 1]
            c = closes[max(0, i - atr_period):i + 1]
            if len(h) >= atr_period and len(l_) >= atr_period and len(c) >= atr_period + 1:
                a = self.regime_detector.calculate_atr(h, l_, c, period=atr_period)
                if a is not None:
                    atr_values.append(a)
        if not atr_values:
            return None, None, None
        current_atr = atr_values[-1]
        atr_percentile = (sum(1 for a in atr_values if a <= current_atr) / len(atr_values)) * 100.0
        last_range = float(highs[-1] - lows[-1]) if highs and lows else None
        n = min(range_lookback, len(highs), len(lows))
        rolling_avg_range = (sum(float(highs[-i - 1] - lows[-i - 1]) for i in range(n)) / n) if n > 0 else None
        return atr_percentile, last_range, rolling_avg_range

    def _compute_daily_vol_proxy(self, candles: pd.DataFrame, atr_period: int = 14) -> Optional[float]:
        """Daily vol proxy = ATR(14)/close for IV proxy when no IV data."""
        if candles is None or len(candles) < atr_period + 1:
            return None
        if 'high' not in candles.columns or 'low' not in candles.columns or 'close' not in candles.columns:
            return None
        highs = candles['high'].tolist()
        lows = candles['low'].tolist()
        closes = candles['close'].tolist()
        atr = self.regime_detector.calculate_atr(highs, lows, closes, period=atr_period)
        if atr is None or not closes or closes[-1] <= 0:
            return None
        return float(atr) / float(closes[-1])

    def calculate_indicators(self, candles: pd.DataFrame, spot_price: float) -> Dict:
        """Calculate all indicators needed for production regime detection (needs >= 100 bars)."""
        indicators = {
            'spot_price': spot_price,
            'adx_14': None,
            'atr': None,
            'atr_percentile': None,
            'last_range': None,
            'rolling_avg_range': None,
            'ema_50': None,
            'ema_100': None,
        }
        if len(candles) < 100:
            return indicators
        highs = candles['high'].tolist()
        lows = candles['low'].tolist()
        closes = candles['close'].tolist()
        indicators['adx_14'] = calculate_adx(highs, lows, closes, period=14)
        indicators['atr'] = self.regime_detector.calculate_atr(highs, lows, closes, period=14)
        atr_pct, last_range, rolling_avg_range = self._atr_percentile_and_range(
            candles, atr_period=14, range_lookback=20
        )
        indicators['atr_percentile'] = atr_pct
        indicators['last_range'] = last_range
        indicators['rolling_avg_range'] = rolling_avg_range
        indicators['ema_50'] = calculate_ema(closes, period=50)
        indicators['ema_100'] = calculate_ema(closes, period=100)
        return indicators
    
    def _ema_direction(self, current_price: float, ema_50: float, ema_100: float) -> Optional[str]:
        """LONG / SHORT / None from price vs EMA50 vs EMA100 (production logic)."""
        if not all([current_price, ema_50, ema_100]):
            return None
        if current_price > ema_50 > ema_100:
            return 'LONG'
        if current_price < ema_50 < ema_100:
            return 'SHORT'
        return None

    def detect_regime_for_timestamp(self, indicators: Dict, iv_pct: Optional[float],
                                    timestamp: datetime,
                                    india_vix: Optional[float] = None) -> Dict:
        """Detect regime using production classify_regime_from_indicators."""
        adx = indicators.get('adx_14') or 0
        atr_pct = indicators.get('atr_percentile')
        last_range = indicators.get('last_range')
        rolling_avg = indicators.get('rolling_avg_range')
        range_compressed = (
            last_range is not None and rolling_avg is not None
            and rolling_avg > 0 and last_range < 0.6 * rolling_avg
        )
        current_price = indicators.get('spot_price')
        ema_50 = indicators.get('ema_50')
        ema_100 = indicators.get('ema_100')
        ema_direction = self._ema_direction(current_price, ema_50, ema_100)
        regime = classify_regime_from_indicators(
            iv_percentile=iv_pct,
            adx_14=adx,
            atr_percentile=atr_pct,
            range_compressed=range_compressed,
            ema_direction=ema_direction,
            india_vix=india_vix,
        )
        return {
            'timestamp': timestamp,
            'regime': regime,
            'indicators': {**indicators, 'iv_percentile': iv_pct},
            'regime_details': {
                'regime': regime,
                'iv_percentile': iv_pct,
                'adx': adx,
                'atr_percentile': atr_pct,
                'range_state': 'COMPRESSED' if range_compressed else 'NORMAL',
            },
        }
    
    def run_backtest(self, start_date: str, end_date: str, check_interval_minutes: int = 15,
                     iv_csv_path: Optional[str] = None):
        """Run regime detection validation using production logic. IV from CSV, daily_metrics, or vol proxy."""
        logger.info(f"Starting regime detection validation from {start_date} to {end_date}")
        self._iv_by_date = load_backtest_iv_csv(iv_csv_path) if iv_csv_path else {}
        self._india_vix_by_date = {}
        self._daily_vol_by_date = {}
        if self._iv_by_date:
            logger.info(f"Loaded IV for {len(self._iv_by_date)} dates from {iv_csv_path}")
        else:
            logger.info("No IV CSV: using daily_metrics.json when present, else volatility proxy")
        
        start = datetime.strptime(start_date, '%Y%m%d').date()
        end = datetime.strptime(end_date, '%Y%m%d').date()
        current_date = start
        check_interval = timedelta(minutes=check_interval_minutes)
        previous_regime = None
        historical_candles = []
        
        # Pre-load historical candles for EMA/ATR (need 100 bars)
        preload_start = start - timedelta(days=5)
        preload_date = preload_start
        while preload_date < start:
            date_str = preload_date.strftime('%Y%m%d')
            tick_data = self.load_futures_data(date_str)
            if not tick_data.empty:
                day_candles = self.aggregate_to_15min_candles(tick_data)
                if not day_candles.empty:
                    historical_candles.extend(day_candles.to_dict('records'))
            preload_date += timedelta(days=1)
        if len(historical_candles) > 100:
            historical_candles = historical_candles[-100:]
        
        while current_date <= end:
            date_str = current_date.strftime('%Y%m%d')
            logger.info(f"Processing {date_str}...")
            
            futures_data = self.load_futures_data(date_str)
            if futures_data.empty:
                logger.warning(f"No futures data for {date_str}")
                current_date += timedelta(days=1)
                continue
            
            candles = self.aggregate_to_15min_candles(futures_data)
            if candles.empty:
                logger.warning(f"No candles generated for {date_str}")
                current_date += timedelta(days=1)
                continue
            
            historical_candles.extend(candles.to_dict('records'))
            if len(historical_candles) > 100:
                historical_candles = historical_candles[-100:]
            
            # IV for this date: CSV / daily_metrics / vol proxy
            if date_str not in self._iv_by_date:
                daily_metrics = load_daily_metrics(date_str)
                if daily_metrics:
                    self._india_vix_by_date[date_str] = daily_metrics.get('india_vix')
                    if daily_metrics.get('iv_percentile') is not None:
                        self._iv_by_date[date_str] = float(daily_metrics['iv_percentile'])
                if date_str not in self._iv_by_date:
                    daily_vol = self._compute_daily_vol_proxy(candles)
                    if daily_vol is not None:
                        self._daily_vol_by_date[date_str] = daily_vol
                        historical_vols = [
                            self._daily_vol_by_date[d]
                            for d in sorted(self._daily_vol_by_date.keys())
                            if d < date_str
                        ]
                        self._iv_by_date[date_str] = iv_percentile_from_vol_history(
                            historical_vols, daily_vol
                        )
                    else:
                        self._iv_by_date[date_str] = 50.0
            iv_pct = self._iv_by_date.get(date_str)
            # Backtest: when India VIX not available, use synthetic 14 (two-fork regime ignores VIX for routing)
            if date_str not in self._india_vix_by_date:
                self._india_vix_by_date[date_str] = 14.0
            
            # Process each candle
            for idx, candle in candles.iterrows():
                timestamp = candle['timestamp']
                spot_price = candle['close']
                recent_df = pd.DataFrame(historical_candles[-100:])
                if len(recent_df) < 100:
                    continue
                indicators = self.calculate_indicators(recent_df, spot_price)
                if indicators.get('adx_14') is None and indicators.get('atr_percentile') is None:
                    continue
                india_vix = self._india_vix_by_date.get(date_str)
                regime_result = self.detect_regime_for_timestamp(indicators, iv_pct, timestamp, india_vix=india_vix)
                
                self.regime_history.append(regime_result)
                self.indicator_history.append({
                    'timestamp': timestamp,
                    'indicators': {**indicators, 'iv_percentile': iv_pct},
                })
                
                current_regime = regime_result['regime']
                if previous_regime and previous_regime != current_regime:
                    self.transitions.append({
                        'timestamp': timestamp,
                        'from_regime': previous_regime,
                        'to_regime': current_regime,
                        'indicators': regime_result['indicators'],
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
        range_compressed_count = 0
        for entry in self.regime_history:
            regime = entry['regime']
            regime_counts[regime] = regime_counts.get(regime, 0) + 1
            if entry.get('regime_details', {}).get('range_state') == 'COMPRESSED':
                range_compressed_count += 1

        # Two-fork: TRENDING (trend conditions) vs SIDEWAYS (else)
        trending_triggered = regime_counts.get('TRENDING', 0)
        sideways_triggered = regime_counts.get('SIDEWAYS', 0)
        trending_note = "TRENDING requires ADX/ATR/EMA trend conditions. SIDEWAYS = everything else."

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
            'trending_triggered': trending_triggered,
            'sideways_triggered': sideways_triggered,
            'range_compressed_bars': range_compressed_count,
            'trending_note': trending_note,
            'transitions': self.transitions,
            'transition_count': len(self.transitions),
            'indicator_stats': indicator_stats,
            'regime_history': self.regime_history[:100]  # First 100 entries for detail
        }


def main(start_date: str = '20251222', end_date: str = '20260116',
         iv_csv_path: Optional[str] = None) -> Dict:
    """Run regime detection backtest with production logic (two regimes: TRENDING, SIDEWAYS). Optional IV CSV."""
    backtester = RegimeDetectionBacktester()
    backtester.run_backtest(start_date, end_date, check_interval_minutes=15, iv_csv_path=iv_csv_path)
    report = backtester.generate_report()
    
    print("\n" + "="*60)
    print("REGIME DETECTION VALIDATION REPORT (production thresholds)")
    print("="*60)
    print(f"Total Detections: {report['total_detections']}")
    print(f"\nRegime Distribution:")
    for regime, count in report['regime_distribution'].items():
        pct = report['regime_percentages'].get(regime, 0)
        print(f"  {regime}: {count} ({pct:.1f}%)")
    trending_triggered = report.get('trending_triggered', 0)
    sideways_triggered = report.get('sideways_triggered', 0)
    print(f"\nTRENDING triggered: {trending_triggered} bars")
    print(f"SIDEWAYS triggered: {sideways_triggered} bars")
    print(f"  {report.get('trending_note', '')}")

    print(f"\nRegime Transitions: {report['transition_count']}")
    if report['transitions']:
        print("\nTransition Timeline (first 10):")
        for trans in report['transitions'][:10]:
            print(f"  {trans['timestamp']}: {trans['from_regime']} -> {trans['to_regime']}")
    
    print("\nIndicator Statistics by Regime:")
    for regime, stats in report['indicator_stats'].items():
        print(f"\n  {regime}:")
        if stats.get('iv_percentile') and stats['iv_percentile'].get('mean') is not None:
            print(f"    IV%: {stats['iv_percentile']['mean']:.1f} (min: {stats['iv_percentile']['min']:.1f}, max: {stats['iv_percentile']['max']:.1f})")
        if stats.get('adx_14') and stats['adx_14'].get('mean') is not None:
            print(f"    ADX: {stats['adx_14']['mean']:.1f} (min: {stats['adx_14']['min']:.1f}, max: {stats['adx_14']['max']:.1f})")
        if stats.get('atr_percentile') and stats['atr_percentile'].get('mean') is not None:
            print(f"    ATR%: {stats['atr_percentile']['mean']:.1f} (min: {stats['atr_percentile']['min']:.1f}, max: {stats['atr_percentile']['max']:.1f})")
    
    print("="*60)
    
    report_file = f"backtest_regime_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    with open(report_file, 'w') as f:
        json.dump(report, f, indent=2, default=str)
    print(f"\nDetailed report saved to: {report_file}")
    return report


if __name__ == '__main__':
    import sys
    start = sys.argv[1] if len(sys.argv) > 1 else '20251222'
    end = sys.argv[2] if len(sys.argv) > 2 else '20260116'
    iv_csv = sys.argv[3] if len(sys.argv) > 3 else None
    main(start_date=start, end_date=end, iv_csv_path=iv_csv)
