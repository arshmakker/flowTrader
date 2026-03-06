"""
Convex Backspread Backtest

For a backtest that mimics the full main flow (Convex + Iron Condor together), see backtest_main_flow.py.

Backtests the Call Backspread strategy:
- Sell 1 ATM Call
- Buy 2 OTM Calls (~ +1% strike)
- Same weekly expiry

Two-fork: Entry when regime is TRENDING only. Exit: aligned with production (regime change to SIDEWAYS after confirmation,
time >40%, no ATR expansion, re-compression, CONVEX_MAX_LOSS (-30%), Convex TSL).
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

from strategies.convex.call_backspread import generate_nifty_call_backspread
from technical_indicators import calculate_iv_percentile, calculate_atm_iv, calculate_adx, calculate_ema
from regime.regime_detector import RegimeDetector, classify_regime_from_indicators
from backtest_trend_following import load_daily_metrics
from strategies.iron_condor.position_tracker import (
    CONVEX_REGIME_CHANGE_CONFIRMATION_CHECKS,
    CONVEX_TSL_ACTIVATION_MTM_PCT,
    CONVEX_TSL_ACTIVATION_TIME_PCT,
    CONVEX_TSL_TRAIL_PCT,
    CONVEX_TSL_TRAIL_TIGHT_PCT,
    CONVEX_TSL_TIGHT_TIME_PCT,
    CONVEX_TSL_ATR_TIGHT_THRESHOLD,
    CONVEX_MAX_LOSS_MTM_PCT,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger('ConvexBacktest')


class ConvexBackspreadBacktester:
    """Backtest Convex Backspread strategy on historical data"""
    
    def __init__(self, data_dir='market_data_*', initial_capital=100000, vix_low_override=None, vix_high_override=None, time_exit_pct=0.40):
        self.data_dir = data_dir
        self.initial_capital = initial_capital
        self.capital = initial_capital
        self.trades = []
        self.open_positions = []
        self.regime_detector = RegimeDetector()
        self.lot_size = 50  # NIFTY options lot size
        self.max_quote_staleness_minutes = 10
        self.vix_low_override = vix_low_override
        self.vix_high_override = vix_high_override
        self.time_exit_pct = time_exit_pct  # Time-based exit threshold (e.g. 0.40 = 40% of expiry life)
        
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

    def load_futures_data(self, date_str: str) -> pd.DataFrame:
        """Load NIFTY futures tick data for a date (for 15m candles)."""
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
        combined = pd.concat(all_data, ignore_index=True)
        return combined.sort_values('timestamp')

    def aggregate_to_15min_candles(self, tick_data: pd.DataFrame) -> pd.DataFrame:
        """Aggregate tick data into 15-minute candles."""
        if tick_data.empty:
            return pd.DataFrame()
        tick_data = tick_data.set_index('timestamp')
        candles = tick_data['ltp'].resample('15min').agg({
            'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last'
        }).reset_index()
        return candles.dropna()

    def _atr_percentile_and_range(self, candles_df: pd.DataFrame, atr_period: int = 14,
                                  range_lookback: int = 20) -> Tuple[Optional[float], Optional[float], Optional[float]]:
        """ATR percentile (0-100), last_range, rolling_avg_range (production-style)."""
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
        atr_pct = (sum(1 for a in atr_values if a <= current_atr) / len(atr_values)) * 100.0
        last_range = float(highs[-1] - lows[-1]) if highs and lows else None
        n = min(range_lookback, len(highs), len(lows))
        rolling_avg_range = (sum(float(highs[-i - 1] - lows[-i - 1]) for i in range(n)) / n) if n > 0 else None
        return atr_pct, last_range, rolling_avg_range

    def _regime_from_candles(self, candles_df: pd.DataFrame, spot_price: float,
                             iv_pct: Optional[float], india_vix: Optional[float]) -> Tuple[str, bool]:
        """Production regime (two-fork: TRENDING or SIDEWAYS) and range_compressed from last 100 candles. Needs len(candles_df) >= 100."""
        if len(candles_df) < 100:
            return 'SIDEWAYS', False
        adx = calculate_adx(
            candles_df['high'].tolist(), candles_df['low'].tolist(), candles_df['close'].tolist(), period=14
        )
        atr_pct, last_range, rolling_avg_range = self._atr_percentile_and_range(
            candles_df, atr_period=14, range_lookback=20
        )
        range_compressed = (
            last_range is not None and rolling_avg_range is not None
            and rolling_avg_range > 0 and last_range < 0.6 * rolling_avg_range
        )
        closes = candles_df['close'].tolist()
        ema_50 = calculate_ema(closes, period=50)
        ema_100 = calculate_ema(closes, period=100)
        ema_direction = None
        if spot_price and ema_50 and ema_100:
            if spot_price > ema_50 > ema_100:
                ema_direction = 'LONG'
            elif spot_price < ema_50 < ema_100:
                ema_direction = 'SHORT'
        regime = classify_regime_from_indicators(
            iv_percentile=iv_pct,
            adx_14=adx,
            atr_percentile=atr_pct,
            range_compressed=range_compressed,
            ema_direction=ema_direction,
            india_vix=india_vix,
            vix_low_override=self.vix_low_override,
            vix_high_override=self.vix_high_override,
        )
        return regime, range_compressed
    
    def build_option_chain_at_time(self, data: Dict, timestamp: datetime) -> pd.DataFrame:
        """Build option chain at a specific timestamp. Backtest-only: if only one CE strike, adds a synthetic OTM (strike+50, price 0.85×ATM) so strategy can form a valid backspread; production uses real chains only."""
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
            
            # Extract strike and option type
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
        
        # Backtest-only: ensure at least 2 CE strikes so OTM > ATM (avoid "no valid backspread")
        ce = chain_df[chain_df['option_type'].str.upper() == 'CE']
        if not ce.empty:
            ce_strikes = sorted(ce['strike'].unique())
            if len(ce_strikes) < 2:
                # One CE strike only -> add synthetic OTM (strike + 50, NIFTY step); OTM typically cheaper
                base = ce.iloc[0].to_dict()
                atm_price = float(base.get('mid_price', 0) or base.get('ltp', 0))
                synth_strike = int(ce_strikes[0]) + 50
                otm_price = max(1.0, atm_price * 0.85)  # OTM call cheaper; not too cheap so backtest stays plausible
                chain_df = pd.concat([
                    chain_df,
                    pd.DataFrame([{
                        'symbol': base.get('symbol', '') + f'_syn_{synth_strike}',
                        'strike': synth_strike,
                        'option_type': 'CE',
                        'ltp': otm_price,
                        'bid': otm_price * 0.98,
                        'ask': otm_price * 1.02,
                        'mid_price': otm_price,
                        'volume': int(base.get('volume', 0)),
                        'oi': int(base.get('oi', 0)),
                        'timestamp': base.get('timestamp'),
                        'lot_size': self.lot_size,
                    }])
                ], ignore_index=True)
        
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
        indicators['adx_14'] = 15.0  # Placeholder - would need historical candles
        indicators['atr'] = None
        indicators['atr_percentile'] = None
        
        return indicators
    
    def check_entry_conditions(self, indicators: Dict, market_state: Dict) -> bool:
        """Allow Convex entry when regime is TRENDING (two-fork: Convex only in TRENDING)."""
        regime = market_state.get('regime', 'SIDEWAYS')
        return regime == 'TRENDING'

    def _estimate_convex_mtm(self, position: Dict, spot_price: float) -> float:
        """Estimate current MTM (PnL) for Convex backspread using same simplified formula as _close_position."""
        entry_atm = position['entry_price_atm']
        entry_otm = position['entry_price_otm']
        strike_atm = position['strike_atm']
        spot_move_pct = (spot_price - strike_atm) / strike_atm if strike_atm else 0
        if spot_move_pct > 0.01:
            exit_atm = entry_atm * 0.3
            exit_otm = entry_otm * (1 + spot_move_pct * 2)
        else:
            exit_atm = entry_atm * 0.5
            exit_otm = entry_otm * 0.7
        pnl_per_lot = (entry_atm - exit_atm) + 2 * (exit_otm - entry_otm)
        return pnl_per_lot * position['lots'] * self.lot_size

    def _check_convex_exit_conditions_backtest(
        self,
        position: Dict,
        current_regime: str,
        spot_price: float,
        days_to_expiry: int,
        entry_days_to_expiry: int,
        current_atr_percentile: Optional[float],
        current_range_state: str,
        current_mtm: float,
    ) -> Tuple[bool, Optional[str]]:
        """Mirror production check_convex_exit_conditions: regime flip to SIDEWAYS, time 40%, no ATR expansion, re-compression, max loss, TSL."""
        regime_at_entry = position.get('regime_at_entry') or 'TRENDING'
        entry_spot = position.get('entry_spot')
        entry_range_state = position.get('entry_range_state')
        entry_premium = abs(position.get('entry_credit') or position.get('net_debit') or 0)
        if entry_days_to_expiry <= 0:
            time_elapsed_pct = 0.0
        else:
            time_elapsed_pct = (entry_days_to_expiry - days_to_expiry) / entry_days_to_expiry

        # 1. Regime change: exit when regime flips from TRENDING to SIDEWAYS (skip when TSL active)
        # Ignore regime change when in profit: let TSL or other exits handle (avoid crystallising profit on flicker)
        entry_is_trending = regime_at_entry in ('TRENDING', 'CONVEX')
        current_is_trending = current_regime in ('TRENDING', 'CONVEX')
        if not position.get('convex_tsl_active', False):
            if entry_is_trending and not current_is_trending:
                if current_mtm is not None and current_mtm > 0:
                    # In profit: ignore regime change; let TSL or other exits handle
                    if position.get('convex_regime_change_count', 0) > 0:
                        position['convex_regime_change_count'] = 0
                    return False, None
                count = position.get('convex_regime_change_count', 0) + 1
                position['convex_regime_change_count'] = count
                if count >= CONVEX_REGIME_CHANGE_CONFIRMATION_CHECKS:
                    return True, "REGIME_CHANGED"
                return False, None
            else:
                if position.get('convex_regime_change_count', 0) > 0:
                    position['convex_regime_change_count'] = 0

        # 2. Time elapsed > threshold — only exit on time when in loss (don't cut winners)
        threshold = getattr(self, 'time_exit_pct', 0.40)
        if entry_days_to_expiry > 0 and time_elapsed_pct > threshold:
            if current_mtm is None or current_mtm <= 0:
                return True, "TIME_ELAPSED_40PCT"

        # 3. No ATR expansion within threshold — only when in loss (don't cut winners)
        if entry_days_to_expiry > 0 and time_elapsed_pct >= threshold and current_atr_percentile is not None:
            if current_atr_percentile < 30 and (current_mtm is None or current_mtm <= 0):
                return True, "NO_ATR_EXPANSION"

        # 4. Re-compression — only when in loss (don't cut winners)
        if entry_range_state == "COMPRESSED" and current_range_state == "COMPRESSED" and entry_spot and entry_spot > 0:
            price_change_pct = abs(spot_price - entry_spot) / entry_spot
            if time_elapsed_pct > 0.30 and price_change_pct < 0.005 and (current_mtm is None or current_mtm <= 0):
                return True, "RE_COMPRESSION"

        # 5. Max loss & TSL
        if entry_premium > 0 and current_mtm <= -CONVEX_MAX_LOSS_MTM_PCT * entry_premium:
            return True, "CONVEX_MAX_LOSS"

        if 'convex_peak_mtm' not in position:
            position['convex_peak_mtm'] = float(current_mtm)
        if not position.get('convex_tsl_active', False):
            mtm_pct = (current_mtm / entry_premium) if entry_premium > 0 else 0.0
            if (entry_premium > 0 and mtm_pct >= CONVEX_TSL_ACTIVATION_MTM_PCT) or time_elapsed_pct >= CONVEX_TSL_ACTIVATION_TIME_PCT:
                position['convex_tsl_active'] = True
        if position.get('convex_tsl_active', False):
            old_peak = position.get('convex_peak_mtm', current_mtm)
            peak = max(old_peak, current_mtm)
            position['convex_peak_mtm'] = float(peak)
            trailing_pct = CONVEX_TSL_TRAIL_PCT
            if time_elapsed_pct > CONVEX_TSL_TIGHT_TIME_PCT or (current_atr_percentile is not None and current_atr_percentile < CONVEX_TSL_ATR_TIGHT_THRESHOLD):
                trailing_pct = CONVEX_TSL_TRAIL_TIGHT_PCT
            if current_mtm <= peak * (1.0 - trailing_pct) and current_mtm > 0:
                return True, "CONVEX_TSL_HIT"
        return False, None

    def run_backtest(self, start_date: str, end_date: str, check_interval_minutes: int = 15):
        """Run backtest using production regime (two-fork: TRENDING → Convex entry only)."""
        logger.info(f"Starting Convex Backspread backtest from {start_date} to {end_date}")
        avail_start, avail_end = get_available_date_range()
        if avail_start and avail_end:
            logger.info(f"Data available: market_data_* from {avail_start} to {avail_end}")
        start = datetime.strptime(start_date, '%Y%m%d').date()
        end = datetime.strptime(end_date, '%Y%m%d').date()
        current_date = start
        check_interval = timedelta(minutes=check_interval_minutes)
        historical_candles = []
        _india_vix_by_date = {}
        # Diagnostics: why we might get few trades
        self._diag_eligible_bars = 0   # regime TRENDING, no position
        self._diag_chain_empty = 0
        self._diag_proposal_none = 0
        
        while current_date <= end:
            date_str = current_date.strftime('%Y%m%d')
            logger.info(f"Processing {date_str}...")
            
            # Load options/spot for trades
            data = self.load_historical_data(date_str)
            # Load futures for regime (15m candles)
            futures_df = self.load_futures_data(date_str)
            if not futures_df.empty:
                day_candles = self.aggregate_to_15min_candles(futures_df)
                if not day_candles.empty:
                    historical_candles.extend(day_candles.to_dict('records'))
            if len(historical_candles) > 100:
                historical_candles = historical_candles[-100:]
            
            # India VIX: daily_metrics or synthetic 14 for backtest
            if date_str not in _india_vix_by_date:
                daily = load_daily_metrics(date_str)
                _india_vix_by_date[date_str] = daily.get('india_vix') if daily else None
            if _india_vix_by_date[date_str] is None:
                _india_vix_by_date[date_str] = 14.0
            
            if not data['timestamps']:
                current_date += timedelta(days=1)
                continue
            
            current_time = datetime.combine(current_date, datetime.min.time().replace(hour=9, minute=15))
            end_time = datetime.combine(current_date, datetime.min.time().replace(hour=15, minute=30))
            
            while current_time <= end_time:
                spot_price = self.get_spot_price_at_time(data, current_time)
                if not spot_price:
                    current_time += check_interval
                    continue
                
                indicators = self.calculate_indicators(data, current_time, spot_price)
                
                # Production regime from last 100 candles up to current_time
                candles_up_to_now = [c for c in historical_candles if c.get('timestamp') <= current_time]
                if len(candles_up_to_now) >= 100:
                    recent_df = pd.DataFrame(candles_up_to_now[-100:])
                    regime, range_compressed = self._regime_from_candles(
                        recent_df, spot_price, iv_pct=indicators.get('iv_percentile') or 50.0,
                        india_vix=_india_vix_by_date.get(date_str)
                    )
                else:
                    regime, range_compressed = 'SIDEWAYS', False
                
                market_state = {
                    'spot_price': spot_price,
                    'regime': regime,
                    'iv_percentile': indicators.get('iv_percentile'),
                    'adx_14': indicators.get('adx_14'),
                    'atr_percentile': indicators.get('atr_percentile'),
                    'range_state': 'COMPRESSED' if range_compressed else 'NORMAL',
                    'expiry': current_date.strftime('%Y-%m-%d'),
                    'days_to_expiry': 7
                }
                
                # Check exit conditions for open positions (production-aligned: regime, time 40%, ATR, re-compression, max loss, TSL)
                for position in self.open_positions[:]:
                    if current_time >= end_time:
                        self._close_position(position, spot_price, current_time, 'end_of_day')
                        self.open_positions.remove(position)
                        continue
                    expiry_date = position.get('expiry_date') or current_date
                    exp_date = expiry_date.date() if isinstance(expiry_date, datetime) else expiry_date
                    days_to_expiry = (exp_date - current_time.date()).days
                    entry_days = position.get('entry_days_to_expiry', 7)
                    current_mtm = self._estimate_convex_mtm(position, spot_price)
                    should_exit, exit_reason = self._check_convex_exit_conditions_backtest(
                        position,
                        regime,
                        spot_price,
                        days_to_expiry,
                        entry_days,
                        indicators.get('atr_percentile'),
                        market_state.get('range_state', 'NORMAL'),
                        current_mtm,
                    )
                    if should_exit and exit_reason:
                        self._close_position(position, spot_price, current_time, exit_reason)
                        self.open_positions.remove(position)

                # Check entry conditions (only if no open position)
                if not self.open_positions:
                    if self.check_entry_conditions(indicators, market_state):
                        self._diag_eligible_bars += 1
                        # Build option chain
                        option_chain = self.build_option_chain_at_time(data, current_time)
                        
                        if option_chain.empty:
                            self._diag_chain_empty += 1
                        else:
                            # Generate trade proposal (backtest-only: relax net debit + max loss % + max 5 lots)
                            trade_proposal = generate_nifty_call_backspread(
                                market_state, option_chain, self.capital,
                                max_net_debit_pct=0.030,  # 3% of spot for backtest (production uses 0.32%)
                                max_loss_pct_of_capital_override=0.25,  # 25% for backtest (production uses 10%)
                                max_lots_override=5,  # backtest-only: allow up to 5 lots (production uses 1)
                            )
                            if not trade_proposal:
                                self._diag_proposal_none += 1
                            
                            if trade_proposal:
                                # Don't enter within 45 min of market close (avoid end_of_day exit immediately)
                                market_close = current_time.replace(hour=15, minute=30, second=0, microsecond=0)
                                if current_time >= market_close - timedelta(minutes=45):
                                    pass  # skip entry this bar
                                else:
                                    # Enter position (state for production-aligned exit checks)
                                    expiry_date = current_date  # weekly expiry on backtest date
                                    position = {
                                    'entry_time': current_time,
                                    'trade_proposal': trade_proposal,
                                    'entry_price_atm': trade_proposal['legs'][0]['price'],
                                    'entry_price_otm': trade_proposal['legs'][1]['price'],
                                    'strike_atm': trade_proposal['legs'][0]['strike'],
                                    'strike_otm': trade_proposal['legs'][1]['strike'],
                                    'lots': trade_proposal['lots'],
                                    'net_debit': trade_proposal['net_debit_total'],
                                    'max_loss': trade_proposal['max_loss'],
                                    'regime_at_entry': market_state['regime'],
                                    'entry_spot': spot_price,
                                    'entry_days_to_expiry': 7,
                                    'entry_range_state': market_state.get('range_state', 'NORMAL'),
                                    'entry_credit': abs(trade_proposal['net_debit_total']),
                                    'expiry_date': expiry_date,
                                    'convex_regime_change_count': 0,
                                    'convex_tsl_active': False,
                                    'iv_percentile_at_entry': indicators.get('iv_percentile') or market_state.get('iv_percentile'),
                                    'adx_at_entry': indicators.get('adx_14') or market_state.get('adx_14'),
                                }
                                    self.open_positions.append(position)
                                    logger.info(
                                        f"Entered backspread position: "
                                        f"ATM={position['strike_atm']}, OTM={position['strike_otm']}, "
                                        f"Debit=₹{position['net_debit']:.2f}"
                                    )
                
                current_time += check_interval
            
            current_date += timedelta(days=1)
        
        # Close any remaining positions
        for position in self.open_positions:
            # Use last known spot price
            last_spot = position.get('last_spot_price', position['strike_atm'])
            self._close_position(position, last_spot, datetime.now(), 'end_of_backtest')
        
        self.open_positions = []
    
    def _close_position(self, position: Dict, spot_price: float, exit_time: datetime, reason: str):
        """Close a backspread position"""
        # Simplified P&L calculation
        # For backspread: P&L depends on final spot price
        # Simplified: assume prices move proportionally
        entry_atm = position['entry_price_atm']
        entry_otm = position['entry_price_otm']
        
        # Simplified exit prices (would need actual option prices)
        # If spot moved up, OTM calls gain more value
        spot_move_pct = (spot_price - position['strike_atm']) / position['strike_atm']
        
        if spot_move_pct > 0.01:  # Spot moved up > 1%
            # OTM calls gain value, ATM call loses value
            exit_atm = entry_atm * 0.3  # ATM call loses 70%
            exit_otm = entry_otm * (1 + spot_move_pct * 2)  # OTM calls gain
        else:
            # Both lose value (time decay)
            exit_atm = entry_atm * 0.5
            exit_otm = entry_otm * 0.7
        
        # P&L = (ATM entry - ATM exit) + 2 * (OTM exit - OTM entry)
        pnl_per_lot = (entry_atm - exit_atm) + 2 * (exit_otm - entry_otm)
        total_pnl = pnl_per_lot * position['lots'] * self.lot_size
        
        # Enrich for pattern analysis (all from existing backtest data)
        entry_t = position['entry_time']
        if isinstance(entry_t, str):
            entry_dt = datetime.fromisoformat(entry_t.replace(' ', 'T'))
        else:
            entry_dt = entry_t
        exit_dt = exit_time if isinstance(exit_time, datetime) else datetime.fromisoformat(str(exit_time).replace(' ', 'T'))
        hold_minutes = (exit_dt - entry_dt).total_seconds() / 60.0 if exit_dt and entry_dt else None
        
        trade_record = {
            'entry_time': position['entry_time'],
            'exit_time': exit_time,
            'strike_atm': position['strike_atm'],
            'strike_otm': position['strike_otm'],
            'lots': position['lots'],
            'entry_debit': position['net_debit'],
            'exit_pnl': total_pnl,
            'exit_reason': reason,
            'regime_at_entry': position['regime_at_entry'],
            'entry_spot': position.get('entry_spot'),
            'iv_percentile_at_entry': position.get('iv_percentile_at_entry'),
            'adx_at_entry': position.get('adx_at_entry'),
            'entry_range_state': position.get('entry_range_state', 'NORMAL'),
            'entry_hour': entry_dt.hour if entry_dt else None,
            'hold_minutes': hold_minutes,
        }
        
        self.trades.append(trade_record)
        self.capital += total_pnl
        
        logger.info(
            f"Closed backspread position: "
            f"ATM={position['strike_atm']}, OTM={position['strike_otm']}, "
            f"P&L=₹{total_pnl:.2f}, Reason={reason}"
        )
    
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
                'trades': [],
                'entry_diagnostics': {
                    'eligible_bars': getattr(self, '_diag_eligible_bars', 0),
                    'chain_empty': getattr(self, '_diag_chain_empty', 0),
                    'proposal_none': getattr(self, '_diag_proposal_none', 0),
                }
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
            'trades': self.trades,
            'entry_diagnostics': {
                'eligible_bars': getattr(self, '_diag_eligible_bars', 0),
                'chain_empty': getattr(self, '_diag_chain_empty', 0),
                'proposal_none': getattr(self, '_diag_proposal_none', 0),
            }
        }


def get_available_date_range():
    """Return (start_date_str, end_date_str) from market_data_* dirs, or (None, None)."""
    dirs = glob.glob("market_data_[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]")
    dates = sorted([d.replace("market_data_", "") for d in dirs if len(d.replace("market_data_", "")) == 8])
    if not dates:
        return None, None
    return dates[0], dates[-1]


def run_comparison(start_date: str, end_date: str, check_interval_minutes: int = 15):
    """Run backtest with VIX_LOW=12 and VIX_LOW=14 and print comparison."""
    print("\n" + "=" * 64)
    print("CONVEX BACKTEST COMPARISON: VIX_LOW=12 vs VIX_LOW=14")
    print("=" * 64)
    print(f"Date range: {start_date} to {end_date}\n")

    results = {}
    for vix_low, label in [(12.0, "VIX_LOW=12"), (14.0, "VIX_LOW=14")]:
        backtester = ConvexBackspreadBacktester(initial_capital=100000, vix_low_override=vix_low)
        backtester.run_backtest(start_date, end_date, check_interval_minutes=check_interval_minutes)
        report = backtester.generate_report()
        results[label] = report
        print(f"  {label}: trades={report['total_trades']}, win_rate={report['win_rate']:.1f}%, "
              f"total_pnl=₹{report['total_pnl']:.2f}, avg_pnl=₹{report['avg_pnl']:.2f}")

    r12 = results["VIX_LOW=12"]
    r14 = results["VIX_LOW=14"]
    print("\n--- Summary ---")
    print(f"  VIX_LOW=12  →  Trades: {r12['total_trades']},  Total P&L: ₹{r12['total_pnl']:.2f},  Win rate: {r12['win_rate']:.1f}%")
    print(f"  VIX_LOW=14  →  Trades: {r14['total_trades']},  Total P&L: ₹{r14['total_pnl']:.2f},  Win rate: {r14['win_rate']:.1f}%")
    if r12['total_pnl'] > r14['total_pnl']:
        print("  → VIX_LOW=12 had better total P&L in this period.")
    elif r14['total_pnl'] > r12['total_pnl']:
        print("  → VIX_LOW=14 had better total P&L in this period.")
    else:
        print("  → Same total P&L (or zero trades).")
    print("=" * 64 + "\n")

    comparison = {
        "start_date": start_date,
        "end_date": end_date,
        "VIX_LOW_12": r12,
        "VIX_LOW_14": r14,
    }
    out_file = f"backtest_convex_vix_comparison_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    with open(out_file, "w") as f:
        json.dump(comparison, f, indent=2, default=str)
    print(f"Comparison saved to: {out_file}\n")
    return results


def main():
    """Run Convex Backspread backtest. Use --compare to run VIX_LOW=12 vs 14. Use --time-exit 0.30 or 0.50 to override time-exit threshold."""
    import sys
    do_compare = "--compare" in sys.argv
    time_exit_pct = 0.40
    argv = sys.argv[1:]
    args = []
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--time-exit" and i + 1 < len(argv):
            try:
                time_exit_pct = float(argv[i + 1])
            except ValueError:
                pass
            i += 2
            continue
        if a != "--compare" and not a.startswith("-"):
            args.append(a)
        i += 1

    start_date = args[0] if len(args) >= 1 else None
    end_date = args[1] if len(args) >= 2 else None
    if not start_date or not end_date:
        start_date, end_date = get_available_date_range()
        if not start_date:
            start_date, end_date = "20251222", "20260116"
            logger.warning(f"No market_data_* dirs found; using default range {start_date}–{end_date}")

    if do_compare:
        return run_comparison(start_date, end_date, check_interval_minutes=15)

    if time_exit_pct != 0.40:
        logger.info("Time-exit threshold override: %.0f%% of expiry life", time_exit_pct * 100)
    backtester = ConvexBackspreadBacktester(initial_capital=100000, time_exit_pct=time_exit_pct)
    backtester.run_backtest(start_date, end_date, check_interval_minutes=15)
    report = backtester.generate_report()

    print("\n" + "=" * 60)
    print("CONVEX BACKSPREAD BACKTEST REPORT")
    print("=" * 60)
    print(f"Total Trades: {report['total_trades']}")
    print(f"Winning Trades: {report['winning_trades']}")
    print(f"Losing Trades: {report['losing_trades']}")
    print(f"Win Rate: {report['win_rate']:.2f}%")
    print(f"\nTotal P&L: ₹{report['total_pnl']:.2f}")
    print(f"Average P&L per Trade: ₹{report['avg_pnl']:.2f}")
    print(f"\nInitial Capital: ₹{report['initial_capital']:.2f}")
    print(f"Final Capital: ₹{report['final_capital']:.2f}")
    print(f"Total Return: {report['total_return_pct']:.2f}%")
    diag = report.get('entry_diagnostics', {})
    if diag:
        print("\nEntry diagnostics (why only N trades):")
        print(f"  Bars with TRENDING + no position: {diag.get('eligible_bars', 0)}")
        print(f"  Of those, option chain empty: {diag.get('chain_empty', 0)}")
        print(f"  Of those, no valid backspread (e.g. OTM<=ATM): {diag.get('proposal_none', 0)}")
    print("=" * 60)

    report_file = f"backtest_convex_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    with open(report_file, "w") as f:
        json.dump(report, f, indent=2, default=str)
    print(f"\nDetailed report saved to: {report_file}")
    return report


if __name__ == "__main__":
    main()
