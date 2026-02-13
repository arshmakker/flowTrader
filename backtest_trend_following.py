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
from regime.regime_detector import RegimeDetector, classify_regime_from_indicators
from strategies.trend.config import (
    MAX_RISK_PCT_OF_CAPITAL,
    MAX_POSITION_SIZE,
    INITIAL_STOP_LOSS_ATR_MULTIPLIER,
    TRAILING_STOP_LOSS_ATR_MULTIPLIER,
    EMA_FAST_PERIOD,
    EMA_SLOW_PERIOD,
    PROFIT_TARGET_ATR_MULTIPLIER,
    USE_HYBRID_TRAILING_STOP,
    HYBRID_MIN_PNL_LOCK_INR,
    LOCK_PROFIT_MIN_INR,
    HYBRID_BREAKEVEN_THRESHOLD_ATR,  # legacy: for __init__ hybrid_breakeven_threshold_atr override
    HYBRID_BREAKEVEN_THRESHOLD_INR,
    HYBRID_PHASE2_THRESHOLD_INR,
    HYBRID_PHASE3_THRESHOLD_INR,
    HYBRID_PHASE4_THRESHOLD_INR,
    HYBRID_PHASE5_THRESHOLD_INR,
    HYBRID_PHASE6_THRESHOLD_INR,
    HYBRID_PHASE6_PLUS_THRESHOLD_INR,
    HYBRID_PHASE1_MULTIPLIER,
    HYBRID_PHASE2_MULTIPLIER,
    HYBRID_PHASE3_MULTIPLIER,
    HYBRID_PHASE4_MULTIPLIER,
    HYBRID_PHASE5_MULTIPLIER,
    HYBRID_PHASE6_MULTIPLIER,
    EXIT_BEFORE_MARKET_CLOSE,
    MARKET_CLOSE_EXIT_MINUTES,
    MAX_INTRADAY_LOSS_INR,
    MAX_TIME_IN_LOSS_MINUTES,
    EXIT_ON_REGIME_CHANGE,
    REGIME_CHANGE_CONFIRMATION_CHECKS,
    IGNORE_REGIME_CHANGE_WHEN_IN_PROFIT,
    EXIT_ON_EMA_BREAK,
    EMA_BREAK_CONFIRMATION_CHECKS,
    EMA_BREAK_CONFIRMATION_CHECKS_WHEN_IN_LOSS,
    EMA_BREAK_TOLERANCE_PCT,
    PRIORITIZE_TRAILING_STOP_IN_PROFIT,
    TRAILING_STOP_PRIORITY_DISTANCE_ATR,
    REENTRY_COOLDOWN_MINUTES,
    HIGH_VOL_ATR_PERCENTILE_THRESHOLD,
    HIGH_VOL_MIN_ADX,
    MAX_TREND_TRADES_PER_DAY,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger('Backtest')

# Default lot size if NFO.csv not found or no NIFTY FUTIDX row
DEFAULT_NIFTY_LOT_SIZE = 50

# Futures brokerage/transaction charges (per order unless noted)
BROKERAGE_PCT = 0.0003  # 0.03%
BROKERAGE_MAX_PER_ORDER = 5.0  # Rs 5
TX_CHARGES_PCT = 0.0000173  # 0.00173%
SEBI_PER_CRORE = 10.0  # Rs 10 per crore turnover
STT_PCT_SELL = 0.0005  # 0.05% on sell side
GST_PCT = 0.18  # 18% on (brokerage + SEBI + transaction charges)
IPFT_PER_LAKH = 0.10  # Rs 0.10 per lakh turnover


def charges_per_order_futures(notional: float, is_sell: bool) -> float:
    """
    Transaction cost for one futures order (entry or exit).
    Brokerage: 0.03% or Rs 5 whichever low; STT 0.02% on sell; tx 0.00173%; SEBI Rs 10/cr; GST 18%.
    """
    brokerage = min(BROKERAGE_PCT * notional, BROKERAGE_MAX_PER_ORDER)
    tx = TX_CHARGES_PCT * notional
    sebi = (notional / 1e7) * SEBI_PER_CRORE
    stt = STT_PCT_SELL * notional if is_sell else 0.0
    base_gst = brokerage + tx + sebi
    gst = GST_PCT * base_gst
    return brokerage + tx + sebi + stt + gst


def charges_per_trade_futures(entry_price: float, exit_price: float, quantity: float) -> float:
    """
    Total transaction charges for one round-trip futures trade (entry + exit + IPFT).
    """
    entry_notional = entry_price * quantity
    exit_notional = exit_price * quantity
    entry_cost = charges_per_order_futures(entry_notional, is_sell=False)
    exit_cost = charges_per_order_futures(exit_notional, is_sell=True)
    turnover = entry_notional + exit_notional
    ipft = (turnover / 1e5) * IPFT_PER_LAKH
    return entry_cost + exit_cost + ipft


def load_backtest_iv_csv(csv_path: str) -> Dict[str, float]:
    """
    Load IV percentile by date from a CSV for regime testing in backtest.
    CSV columns: date (YYYYMMDD), iv_percentile (0-100).
    Returns dict date_str -> iv_percentile. Empty dict if file missing or invalid.
    """
    if not csv_path or not os.path.exists(csv_path):
        return {}
    try:
        df = pd.read_csv(csv_path)
        if 'date' not in df.columns or 'iv_percentile' not in df.columns:
            logger.warning(f"IV CSV must have columns 'date' and 'iv_percentile'; got {list(df.columns)}")
            return {}
        df['date'] = df['date'].astype(str).str.replace('-', '').str.strip()
        return dict(zip(df['date'], df['iv_percentile'].astype(float)))
    except Exception as e:
        logger.warning(f"Error loading IV CSV {csv_path}: {e}")
        return {}


def load_iv_from_daily_metrics(date_str: str) -> Optional[float]:
    """
    Load iv_percentile from market_data_YYYYMMDD/daily_metrics.json if present.
    Used when backtest has no IV CSV: prefer stored IV from when it was calculated (e.g. in production).
    Returns iv_percentile (0-100) or None if file missing or no iv_percentile.
    """
    m = load_daily_metrics(date_str)
    return float(m['iv_percentile']) if m and m.get('iv_percentile') is not None else None


def load_daily_metrics(date_str: str) -> Optional[Dict]:
    """
    Load full daily_metrics from market_data_YYYYMMDD/daily_metrics.json if present.
    Returns dict with iv_percentile, india_vix, adx_14, atr_percentile, regime, etc. or None.
    """
    data_dirs = glob.glob(f"market_data_{date_str}")
    if not data_dirs:
        return None
    metrics_path = os.path.join(data_dirs[0], 'daily_metrics.json')
    if not os.path.exists(metrics_path):
        return None
    try:
        with open(metrics_path, 'r') as f:
            return json.load(f)
    except Exception:
        return None


def iv_percentile_from_vol_history(historical_vols: List[float], current_vol: float,
                                  min_samples: int = 20) -> float:
    """
    Compute IV-percentile-like value from volatility proxy history (same formula as production).
    If fewer than min_samples historical values, return 50 (neutral) for percentile rank.
    """
    if not historical_vols or len(historical_vols) < min_samples:
        return 50.0
    count_below = sum(1 for v in historical_vols if v < current_vol)
    return (count_below / len(historical_vols)) * 100.0


def get_nifty_lot_size_from_nfo(symbols_dir: str = 'symbols') -> int:
    """
    Read NIFTY futures lot size from symbols/NFO.csv (LotSize for Symbol=NIFTY, Instrument=FUTIDX).
    Returns DEFAULT_NIFTY_LOT_SIZE if file missing or no matching row.
    """
    try:
        nfo_path = os.path.join(symbols_dir, 'NFO.csv')
        if not os.path.exists(nfo_path):
            logger.warning(f"NFO.csv not found at {nfo_path}, using default lot size {DEFAULT_NIFTY_LOT_SIZE}")
            return DEFAULT_NIFTY_LOT_SIZE
        df = pd.read_csv(nfo_path)
        # NFO.csv columns: Exchange, Token, LotSize, Symbol, TradingSymbol, Expiry, Instrument, ...
        if 'Symbol' not in df.columns or 'LotSize' not in df.columns or 'Instrument' not in df.columns:
            logger.warning(f"NFO.csv missing required columns (Symbol, LotSize, Instrument), using default {DEFAULT_NIFTY_LOT_SIZE}")
            return DEFAULT_NIFTY_LOT_SIZE
        nifty_fut = df[(df['Symbol'].str.strip() == 'NIFTY') & (df['Instrument'] == 'FUTIDX')]
        if nifty_fut.empty:
            logger.warning("No NIFTY FUTIDX row in NFO.csv, using default lot size %s", DEFAULT_NIFTY_LOT_SIZE)
            return DEFAULT_NIFTY_LOT_SIZE
        lot_size = int(nifty_fut.iloc[0]['LotSize'])
        logger.info(f"NIFTY futures lot size from NFO.csv: {lot_size}")
        return lot_size
    except Exception as e:
        logger.warning(f"Error reading NIFTY lot size from NFO.csv: {e}, using default {DEFAULT_NIFTY_LOT_SIZE}")
        return DEFAULT_NIFTY_LOT_SIZE


class TrendFollowingBacktester:
    """Backtest Futures Trend Following strategy on historical data"""
    
    def __init__(self, data_dir='market_data_*', initial_capital=1000000,
                 profit_target_atr_multiplier=None, hybrid_breakeven_threshold_atr=None,
                 scenario_name=None, max_position_size=None, max_risk_pct_of_capital=None,
                 force_max_lots=False, min_adx_entry=None):
        self.data_dir = data_dir
        self.initial_capital = initial_capital
        self.capital = initial_capital
        self.trades = []
        self.open_positions = []
        self.daily_pnl = []
        self.regime_detector = RegimeDetector()
        self.lot_size = get_nifty_lot_size_from_nfo()
        # Overrides for profit target vs breakeven comparison (default = config)
        self._profit_target_atr = (
            profit_target_atr_multiplier if profit_target_atr_multiplier is not None
            else PROFIT_TARGET_ATR_MULTIPLIER
        )
        self._hybrid_breakeven_atr = (
            hybrid_breakeven_threshold_atr if hybrid_breakeven_threshold_atr is not None
            else HYBRID_BREAKEVEN_THRESHOLD_ATR
        )
        self._max_position_size = (
            max_position_size if max_position_size is not None else MAX_POSITION_SIZE
        )
        self._max_risk_pct = (
            max_risk_pct_of_capital if max_risk_pct_of_capital is not None
            else MAX_RISK_PCT_OF_CAPITAL
        )
        self._force_max_lots = bool(force_max_lots)  # True = always take max_position_size lots
        self._min_adx_entry = min_adx_entry  # If set, require ADX >= this for entry (stricter filter)
        self.scenario_name = scenario_name or 'default'
        
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
    
    def _atr_percentile_and_range(self, candles_df: pd.DataFrame, atr_period: int = 14,
                                   range_lookback: int = 20) -> Tuple[Optional[float], Optional[float], Optional[float]]:
        """
        From last 100 candles, compute: atr_percentile (0-100), last_range (high-low), rolling_avg_range.
        Used for simplified four-regime detection (no IV).
        """
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
        if n > 0:
            ranges = [float(highs[-i - 1] - lows[-i - 1]) for i in range(n)]
            rolling_avg_range = sum(ranges) / len(ranges)
        else:
            rolling_avg_range = None
        return atr_percentile, last_range, rolling_avg_range

    def _compute_daily_vol_proxy(self, candles: pd.DataFrame, atr_period: int = 14) -> Optional[float]:
        """
        Compute a daily volatility proxy from 15m candles (ATR(14) / close) for IV proxy in backtest.
        Used when no IV data or daily_metrics: allows regime testing via percentile rank (two-fork: TRENDING/SIDEWAYS).
        Returns volatility as fraction (e.g. 0.02 for 2%) or None if insufficient data.
        """
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
        # Check regime (two-fork: TRENDING only for trend strategy)
        regime = market_state.get('regime')
        if regime != 'TRENDING':
            return False, None
        
        # Check ADX (stricter on high-vol days: require ADX >= 40 when ATR% >= 90; or use min_adx_entry override)
        adx = indicators.get('adx_14')
        atr_percentile = market_state.get('atr_percentile')
        if getattr(self, '_min_adx_entry', None) is not None:
            min_adx = self._min_adx_entry
        else:
            min_adx = (
                HIGH_VOL_MIN_ADX
                if (atr_percentile is not None and atr_percentile >= HIGH_VOL_ATR_PERCENTILE_THRESHOLD)
                else 30
            )
        if not adx or adx < min_adx:
            return False, None
        
        # Check ATR percentile
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
        max_risk_amount = self.capital * self._max_risk_pct
        
        # Calculate maximum quantity based on risk
        max_quantity_by_risk = int(max_risk_amount / risk_per_share) if risk_per_share > 0 else 0
        
        # Limit to max position size (or use full lots when force_max_lots is set)
        cap_quantity = self._max_position_size * self.lot_size
        if getattr(self, '_force_max_lots', False):
            max_quantity = cap_quantity  # Always take max lots (ignore risk limit for comparison)
        else:
            max_quantity = min(max_quantity_by_risk, cap_quantity)
        
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
                            market_state: Dict, current_timestamp: Optional[datetime] = None,
                            bar_low: Optional[float] = None, bar_high: Optional[float] = None) -> Tuple[bool, str]:
        """
        Check if exit conditions are met. Mirrors main.py: market close, stop loss,
        max loss cap, time in loss, regime change (with confirmation), ATR profit target,
        EMA break (with tolerance and confirmation); then update hybrid trailing stop.
        
        Stop loss: when bar_low/bar_high are provided, treat stop as a LIMIT order at the stop
        price — stop is "hit" when price traded at that level (bar low <= stop for LONG,
        bar high >= stop for SHORT). Otherwise use close (market-order semantics).
        Returns:
            (should_exit, exit_reason)
        """
        direction = position['direction']
        entry_price = position['entry_price']
        current_stop = position['current_stop_price']
        quantity = position['quantity']
        atr = indicators.get('atr_14', 0)

        # Current P&L in rupees and in points
        if direction == 'LONG':
            current_pnl = (current_price - entry_price) * quantity
            unrealized_pnl_points = current_price - entry_price
        else:  # SHORT:
            current_pnl = (entry_price - current_price) * quantity
            unrealized_pnl_points = entry_price - current_price

        # Position age (for time-in-loss; need current_timestamp)
        position_age_minutes = 0.0
        if current_timestamp is not None and position.get('entry_time') is not None:
            delta = current_timestamp - position['entry_time']
            position_age_minutes = delta.total_seconds() / 60.0

        # Exit condition 0: Market close approaching (same as production)
        if current_timestamp is not None and EXIT_BEFORE_MARKET_CLOSE:
            market_close_time = current_timestamp.replace(hour=15, minute=30, second=0, microsecond=0)
            exit_before_close_time = market_close_time - timedelta(minutes=MARKET_CLOSE_EXIT_MINUTES)
            if current_timestamp.weekday() < 5 and current_timestamp >= exit_before_close_time:
                return True, 'MARKET_CLOSE_APPROACHING'

        # Exit condition 1: Stop loss hit (limit order at stop — hit when price traded at stop)
        if bar_low is not None and bar_high is not None:
            if direction == 'LONG':
                if bar_low <= current_stop:
                    return True, 'STOP_LOSS_HIT'
            else:  # SHORT
                if bar_high >= current_stop:
                    return True, 'STOP_LOSS_HIT'
        else:
            if direction == 'LONG':
                if current_price <= current_stop:
                    return True, 'STOP_LOSS_HIT'
            else:  # SHORT
                if current_price >= current_stop:
                    return True, 'STOP_LOSS_HIT'
        
        # Exit condition 1b: Max intraday loss (circuit breaker)
        if current_pnl <= -MAX_INTRADAY_LOSS_INR:
            return True, 'MAX_LOSS_CAP'

        # Exit condition 1c: Time in loss
        if current_pnl < 0 and position_age_minutes >= MAX_TIME_IN_LOSS_MINUTES:
            return True, 'TIME_IN_LOSS'

        # Exit condition 2: Regime change (with confirmation to reduce whipsaw; skip when in profit if configured)
        regime = market_state.get('regime')
        if EXIT_ON_REGIME_CHANGE and not (IGNORE_REGIME_CHANGE_WHEN_IN_PROFIT and current_pnl > 0):
            if 'regime_change_count' not in position:
                position['regime_change_count'] = 0
            if regime != 'TRENDING':
                position['regime_change_count'] = position.get('regime_change_count', 0) + 1
                if position['regime_change_count'] >= REGIME_CHANGE_CONFIRMATION_CHECKS:
                    return True, 'REGIME_CHANGE'
            else:
                if position.get('regime_change_count', 0) > 0:
                    position['regime_change_count'] = 0

        # Exit condition 2b: ATR profit target (optional; None = trailing only)
        if self._profit_target_atr is not None and atr > 0 and quantity > 0:
            if current_pnl > 0 and unrealized_pnl_points >= (atr * self._profit_target_atr):
                return True, 'PROFIT_TARGET_ATR'

        # Exit condition 4: EMA structure breaks (with tolerance, confirmation, in-profit ignore)
        if EXIT_ON_EMA_BREAK:
            ema_50 = indicators.get('ema_50')
            ema_100 = indicators.get('ema_100')
            if ema_50 and ema_100:
                if 'ema_break_count' not in position:
                    position['ema_break_count'] = 0
                # Structure valid with tolerance (same as production)
                tol = EMA_BREAK_TOLERANCE_PCT / 100.0
                if direction == 'LONG':
                    price_above_ema50 = current_price > ema_50 * (1 - tol)
                    ema50_above_ema100 = ema_50 > ema_100 * (1 - tol)
                    structure_valid = price_above_ema50 and ema50_above_ema100
                else:  # SHORT
                    price_below_ema50 = current_price < ema_50 * (1 + tol)
                    ema50_below_ema100 = ema_50 < ema_100 * (1 + tol)
                    structure_valid = price_below_ema50 and ema50_below_ema100

                if not structure_valid:
                    position['ema_break_count'] = position.get('ema_break_count', 0) + 1
                    in_profit = current_pnl > 0
                    if direction == 'LONG':
                        distance_to_stop = current_price - current_stop
                    else:
                        distance_to_stop = current_stop - current_price
                    close_to_stop = (atr > 0 and
                                     distance_to_stop < (atr * TRAILING_STOP_PRIORITY_DISTANCE_ATR))
                    ema_required_checks = (EMA_BREAK_CONFIRMATION_CHECKS_WHEN_IN_LOSS
                                         if current_pnl < 0 else EMA_BREAK_CONFIRMATION_CHECKS)
                    if PRIORITIZE_TRAILING_STOP_IN_PROFIT and in_profit and not close_to_stop:
                        position['ema_break_count'] = 0
                    elif position['ema_break_count'] >= ema_required_checks:
                        return True, 'EMA_STRUCTURE_BROKEN'
                else:
                    if position.get('ema_break_count', 0) > 0:
                        position['ema_break_count'] = 0
        
        # Update trailing stop loss (hybrid phases 1–6 in PnL terms, aligned with main.py)
        pnl_inr = current_pnl if current_pnl is not None else 0.0
        if atr > 0:
            if USE_HYBRID_TRAILING_STOP:
                if pnl_inr <= 0:
                    trailing_multiplier = HYBRID_PHASE1_MULTIPLIER
                elif pnl_inr < HYBRID_BREAKEVEN_THRESHOLD_INR:
                    trailing_multiplier = HYBRID_PHASE1_MULTIPLIER
                elif pnl_inr < HYBRID_PHASE3_THRESHOLD_INR:
                    trailing_multiplier = HYBRID_PHASE2_MULTIPLIER
                elif pnl_inr < HYBRID_PHASE4_THRESHOLD_INR:
                    trailing_multiplier = HYBRID_PHASE3_MULTIPLIER
                elif pnl_inr < HYBRID_PHASE5_THRESHOLD_INR:
                    trailing_multiplier = HYBRID_PHASE4_MULTIPLIER
                elif pnl_inr < HYBRID_PHASE6_THRESHOLD_INR:
                    trailing_multiplier = HYBRID_PHASE5_MULTIPLIER
                elif pnl_inr < HYBRID_PHASE6_PLUS_THRESHOLD_INR:
                    trailing_multiplier = HYBRID_PHASE5_MULTIPLIER
                else:
                    trailing_multiplier = HYBRID_PHASE6_MULTIPLIER
            else:
                trailing_multiplier = TRAILING_STOP_LOSS_ATR_MULTIPLIER

            # Lock at least LOCK_PROFIT_MIN_INR when PnL >= ₹300 (same as production)
            lock_be = (USE_HYBRID_TRAILING_STOP and
                       (current_pnl is not None and current_pnl >= HYBRID_MIN_PNL_LOCK_INR))
            trailing_stop_distance = atr * trailing_multiplier
            if direction == 'LONG':
                new_trailing_stop = current_price - trailing_stop_distance
                if lock_be and quantity > 0:
                    min_stop_lock = entry_price + (LOCK_PROFIT_MIN_INR / quantity)
                    new_trailing_stop = max(new_trailing_stop, min_stop_lock)
                updated_stop = max(current_stop, new_trailing_stop)
                # Never relax once in profit: keep at least LOCK_PROFIT_MIN_INR locked
                if current_stop >= entry_price and quantity > 0:
                    min_lock = entry_price + (LOCK_PROFIT_MIN_INR / quantity)
                    updated_stop = max(updated_stop, min_lock)
                position['current_stop_price'] = updated_stop
            else:  # SHORT
                new_trailing_stop = current_price + trailing_stop_distance
                if lock_be and quantity > 0:
                    min_stop_lock = entry_price - (LOCK_PROFIT_MIN_INR / quantity)
                    new_trailing_stop = min(new_trailing_stop, min_stop_lock)
                updated_stop = min(current_stop, new_trailing_stop)
                # Never relax once in profit: keep at least LOCK_PROFIT_MIN_INR locked
                if current_stop <= entry_price and quantity > 0:
                    max_lock = entry_price - (LOCK_PROFIT_MIN_INR / quantity)
                    updated_stop = min(updated_stop, max_lock)
                position['current_stop_price'] = updated_stop
        
        return False, None
    
    def run_backtest(self, start_date: str, end_date: str, check_interval_minutes: int = 15,
                     iv_csv_path: Optional[str] = None):
        """
        Run backtest on historical data.
        
        Args:
            start_date: Start date in YYYYMMDD format
            end_date: End date in YYYYMMDD format
            check_interval_minutes: How often to check for entry/exit (default: 15 minutes)
            iv_csv_path: Optional path to CSV with columns date (YYYYMMDD), iv_percentile (0-100).
                         Two-fork regime: TRENDING (trend conditions) or SIDEWAYS (else).
        """
        logger.info(f"Starting backtest from {start_date} to {end_date}")
        self._iv_by_date = load_backtest_iv_csv(iv_csv_path) if iv_csv_path else {}
        self._india_vix_by_date = {}  # date_str -> India VIX (from daily_metrics when present)
        self._daily_vol_by_date = {}  # date_str -> daily vol proxy (for IV proxy when no IV data)
        if self._iv_by_date:
            logger.info(f"Loaded IV for {len(self._iv_by_date)} dates from {iv_csv_path}")
        else:
            logger.info(
                "No IV CSV: will use daily_metrics.json when present, else compute IV proxy from volatility"
            )
        
        start = datetime.strptime(start_date, '%Y%m%d').date()
        end = datetime.strptime(end_date, '%Y%m%d').date()
        
        self._market_close_exit_dates = set()  # No new entries after market-close exit that day
        self._cooldown_until = None  # After STOP_LOSS_HIT, block new entry until this timestamp
        self._trades_entered_today = 0  # Entries on current day (reset each date_str)
        current_date = start
        check_interval = timedelta(minutes=check_interval_minutes)
        last_processed_price = None  # Track last candle close price for end-of-backtest closing
        # Per-day regime tracking: two regimes (TRENDING, SIDEWAYS)
        self._days_with_data = set()
        self._days_with_trending = set()
        self._days_with_sideways = set()
        # ATR and ADX range over the backtest data (all bars where indicators were computed)
        self._atr_values = []
        self._adx_values = []
        
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
            
            self._days_with_data.add(date_str)
            self._trades_entered_today = 0  # Reset per day for MAX_TREND_TRADES_PER_DAY
            had_trending_today = False
            had_sideways_today = False
            # IV for regime: use CSV if provided; else daily_metrics if present; else volatility proxy
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
                        self._iv_by_date[date_str] = 50.0  # neutral fallback

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
                # Collect ATR and ADX for range stats
                atr_val = indicators.get('atr_14')
                adx_val = indicators.get('adx_14')
                if atr_val is not None:
                    self._atr_values.append(float(atr_val))
                if adx_val is not None:
                    self._adx_values.append(float(adx_val))
                # ATR percentile and range for four-regime detection (no IV in backtest)
                atr_pct, last_range, rolling_avg_range = self._atr_percentile_and_range(recent_candles_df)
                if atr_pct is None:
                    atr_pct = 75.0  # fallback
                adx = indicators.get('adx_14', 0)
                direction = self.detect_trend_direction(
                    current_price,
                    indicators.get('ema_50'),
                    indicators.get('ema_100')
                )
                range_compressed = (last_range is not None and rolling_avg_range is not None and
                                    rolling_avg_range > 0 and last_range < 0.6 * rolling_avg_range)
                # Use production regime classifier (optional IV / India VIX from CSV or daily_metrics)
                iv_pct = self._iv_by_date.get(date_str) if getattr(self, '_iv_by_date', None) else None
                india_vix = getattr(self, '_india_vix_by_date', {}).get(date_str)
                # Backtest: when India VIX not available, use synthetic 14 (two-fork ignores VIX for regime)
                if india_vix is None:
                    india_vix = 14.0
                regime = classify_regime_from_indicators(
                    iv_percentile=iv_pct,
                    adx_14=adx,
                    atr_percentile=atr_pct,
                    range_compressed=range_compressed,
                    ema_direction=direction,
                    india_vix=india_vix,
                )
                if regime == 'TRENDING':
                    had_trending_today = True
                else:
                    had_sideways_today = True

                market_state = {
                    'spot_price': current_price,
                    'adx_14': adx,
                    'atr': indicators.get('atr_14', 0),
                    'atr_percentile': atr_pct,
                    'regime': regime
                }

                # Check exit conditions for open positions (pass bar low/high for limit-order-at-stop semantics)
                bar_low = candle.get('low')
                bar_high = candle.get('high')
                for position in self.open_positions[:]:  # Copy list to allow modification
                    should_exit, exit_reason = self.check_exit_conditions(
                        position, current_price, indicators, market_state,
                        current_timestamp=timestamp,
                        bar_low=bar_low, bar_high=bar_high
                    )
                    
                    if should_exit:
                        if exit_reason == 'MARKET_CLOSE_APPROACHING':
                            self._market_close_exit_dates.add(date_str)
                        # Exit price: for STOP_LOSS_HIT with limit order at stop, use stop price; else close
                        exit_price = position['current_stop_price'] if exit_reason == 'STOP_LOSS_HIT' else current_price
                        # Calculate gross P&L
                        if position['direction'] == 'LONG':
                            pnl_gross = (exit_price - position['entry_price']) * position['quantity']
                        else:  # SHORT
                            pnl_gross = (position['entry_price'] - exit_price) * position['quantity']
                        charges = charges_per_trade_futures(
                            position['entry_price'], exit_price, position['quantity']
                        )
                        pnl = pnl_gross - charges
                        # Record trade (pnl is net of charges)
                        trade_record = {
                            'entry_time': position['entry_time'],
                            'exit_time': timestamp,
                            'direction': position['direction'],
                            'entry_price': position['entry_price'],
                            'exit_price': exit_price,
                            'quantity': position['quantity'],
                            'lots': position['lots'],
                            'pnl': pnl,
                            'pnl_gross': pnl_gross,
                            'charges': charges,
                            'exit_reason': exit_reason,
                            'regime_at_entry': position['regime_at_entry']
                        }
                        
                        self.trades.append(trade_record)
                        self.capital += pnl
                        self.open_positions.remove(position)
                        if exit_reason == 'STOP_LOSS_HIT':
                            self._cooldown_until = timestamp + timedelta(minutes=REENTRY_COOLDOWN_MINUTES)
                        
                        logger.info(
                            f"Exited {position['direction']} position: "
                            f"Entry={position['entry_price']:.2f}, Exit={exit_price:.2f}, "
                            f"P&L=₹{pnl:.2f} (gross ₹{pnl_gross:.2f}, charges ₹{charges:.2f}), Reason={exit_reason}"
                        )
                
                # Check entry conditions (only if no open position, no market-close exit today, past cooldown, under max trades/day)
                in_cooldown = (
                    getattr(self, '_cooldown_until', None) is not None
                    and timestamp < self._cooldown_until
                )
                at_max_trades_today = getattr(self, '_trades_entered_today', 0) >= MAX_TREND_TRADES_PER_DAY
                if (not self.open_positions
                    and date_str not in getattr(self, '_market_close_exit_dates', set())
                    and not in_cooldown
                    and not at_max_trades_today):
                    can_enter, direction = self.check_entry_conditions(indicators, market_state)
                    
                    if can_enter and direction:
                        # Calculate position size
                        position_info = self.calculate_position_size(
                            current_price, indicators.get('atr_14', 0), direction
                        )
                        # When regime allows trade but risk gives 0 lots, take at least 1 lot (ignore max risk for this trade)
                        if position_info['quantity'] == 0:
                            atr = indicators.get('atr_14', 0)
                            stop_loss_atr = atr * INITIAL_STOP_LOSS_ATR_MULTIPLIER
                            risk_per_share = stop_loss_atr
                            if direction == 'LONG':
                                stop_loss_price = current_price - stop_loss_atr
                            else:
                                stop_loss_price = current_price + stop_loss_atr
                            position_info = {
                                'lots': 1,
                                'quantity': self.lot_size,
                                'risk_amount': self.lot_size * risk_per_share,
                                'stop_loss_price': stop_loss_price,
                                'risk_per_share': risk_per_share,
                            }
                            logger.info(
                                f"Backtest: regime allowed trade but risk gave 0 lots; taking 1 lot (ignoring max risk). "
                                f"Risk for this trade: ₹{position_info['risk_amount']:.2f}"
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
                                'ema_100_at_entry': indicators.get('ema_100', 0),
                                'regime_change_count': 0,
                                'ema_break_count': 0,
                            }
                            
                            self.open_positions.append(position)
                            self._trades_entered_today = getattr(self, '_trades_entered_today', 0) + 1
                            
                            logger.info(
                                f"Entered {direction} position: "
                                f"Price={current_price:.2f}, Quantity={position_info['quantity']}, "
                                f"Lots={position_info['lots']}, SL={position_info['stop_loss_price']:.2f}"
                            )
            
            if had_trending_today:
                self._days_with_trending.add(date_str)
            if had_sideways_today:
                self._days_with_sideways.add(date_str)

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
                pnl_gross = (last_price - position['entry_price']) * position['quantity']
            else:  # SHORT
                pnl_gross = (position['entry_price'] - last_price) * position['quantity']
            charges = charges_per_trade_futures(
                position['entry_price'], last_price, position['quantity']
            )
            pnl = pnl_gross - charges
            trade_record = {
                'entry_time': position['entry_time'],
                'exit_time': datetime.now(),
                'direction': position['direction'],
                'entry_price': position['entry_price'],
                'exit_price': last_price,
                'quantity': position['quantity'],
                'lots': position['lots'],
                'pnl': pnl,
                'pnl_gross': pnl_gross,
                'charges': charges,
                'exit_reason': 'end_of_backtest',
                'regime_at_entry': position['regime_at_entry']
            }
            self.trades.append(trade_record)
            self.capital += pnl
        
        self.open_positions = []
    
    def generate_report(self) -> Dict:
        """Generate backtest performance report"""
        if not self.trades:
            r = {
                'total_trades': 0,
                'winning_trades': 0,
                'losing_trades': 0,
                'win_rate': 0.0,
                'total_pnl': 0.0,
                'total_pnl_gross': 0.0,
                'total_charges': 0.0,
                'total_quantity': 0,
                'avg_pnl': 0.0,
                'max_profit': 0.0,
                'max_loss': 0.0,
                'initial_capital': self.initial_capital,
                'final_capital': self.capital,
                'total_return_pct': 0.0,
                'trades': [],
                'exit_reasons': {},
            }
            if hasattr(self, 'scenario_name'):
                r['scenario_name'] = self.scenario_name
                r['profit_target_atr_multiplier'] = self._profit_target_atr
                r['hybrid_breakeven_threshold_atr'] = self._hybrid_breakeven_atr
                r['max_position_size'] = self._max_position_size
                r['max_risk_pct_of_capital'] = self._max_risk_pct
            return r
        
        winning_trades = [t for t in self.trades if t['pnl'] > 0]
        losing_trades = [t for t in self.trades if t['pnl'] <= 0]
        
        total_pnl = sum(t['pnl'] for t in self.trades)
        total_charges = sum(t.get('charges', 0) for t in self.trades)
        total_pnl_gross = sum(t.get('pnl_gross', t['pnl']) for t in self.trades)
        total_quantity = sum(t.get('quantity', 0) for t in self.trades)
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
        
        report = {
            'total_trades': len(self.trades),
            'winning_trades': len(winning_trades),
            'losing_trades': len(losing_trades),
            'win_rate': win_rate,
            'total_pnl': total_pnl,
            'total_pnl_gross': total_pnl_gross,
            'total_charges': total_charges,
            'total_quantity': total_quantity,
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
        if hasattr(self, 'scenario_name'):
            report['scenario_name'] = self.scenario_name
            report['profit_target_atr_multiplier'] = self._profit_target_atr
            report['hybrid_breakeven_threshold_atr'] = self._hybrid_breakeven_atr
            report['max_position_size'] = self._max_position_size
            report['max_risk_pct_of_capital'] = self._max_risk_pct
        # Regime-per-day stats (two regimes: TRENDING, SIDEWAYS)
        if hasattr(self, '_days_with_data'):
            n = len(self._days_with_data)
            report['days_with_data'] = n
            if n:
                report['days_with_trending'] = len(getattr(self, '_days_with_trending', set()))
                report['days_with_sideways'] = len(getattr(self, '_days_with_sideways', set()))
                report['prob_trending_per_day_pct'] = report['days_with_trending'] / n * 100.0
                report['prob_sideways_per_day_pct'] = report['days_with_sideways'] / n * 100.0
                report['prob_at_least_one_regime_per_day'] = 100.0  # every day has at least one bar with some regime
        # ATR and ADX range in the data (all bars where indicators were computed)
        if hasattr(self, '_atr_values') and self._atr_values:
            report['atr_min'] = min(self._atr_values)
            report['atr_max'] = max(self._atr_values)
            report['atr_mean'] = sum(self._atr_values) / len(self._atr_values)
            report['atr_count'] = len(self._atr_values)
        if hasattr(self, '_adx_values') and self._adx_values:
            report['adx_min'] = min(self._adx_values)
            report['adx_max'] = max(self._adx_values)
            report['adx_mean'] = sum(self._adx_values) / len(self._adx_values)
            report['adx_count'] = len(self._adx_values)
        return report


def run_comparison(start_date: str = '20251222', end_date: str = '20260116',
                   check_interval_minutes: int = 15) -> Dict:
    """
    Run sixteen scenarios and compare:
    1. Baseline (0.1×), 2. Lower (0.05×), 3. Earlier breakeven, 4. 0.05× 5 lots,
    5–10. 0.15×–0.40×, 11. 0.45×, 12. 0.50×, 13. 0.56×, 14. 0.60×, 15. 0.65×, 16. 0.70× ATR target
    """
    scenarios = [
        {
            'name': 'Baseline (0.1× target, 0.5× breakeven)',
            'profit_target_atr_multiplier': 0.1,
            'hybrid_breakeven_threshold_atr': 0.5,
        },
        {
            'name': 'Lower ATR target (0.05× target, 0.5× breakeven)',
            'profit_target_atr_multiplier': 0.05,
            'hybrid_breakeven_threshold_atr': 0.5,
        },
        {
            'name': 'Earlier breakeven (0.1× target, 0.25× breakeven)',
            'profit_target_atr_multiplier': 0.1,
            'hybrid_breakeven_threshold_atr': 0.25,
        },
        {
            'name': '0.05× ATR, 5 lots (0.05× target, force 5 lots)',
            'profit_target_atr_multiplier': 0.05,
            'hybrid_breakeven_threshold_atr': 0.5,
            'max_position_size': 5,
            'max_risk_pct_of_capital': 0.25,
            'force_max_lots': True,
        },
        {
            'name': '0.15× ATR target (0.5× breakeven)',
            'profit_target_atr_multiplier': 0.15,
            'hybrid_breakeven_threshold_atr': 0.5,
        },
        {
            'name': '0.20× ATR target (0.5× breakeven)',
            'profit_target_atr_multiplier': 0.2,
            'hybrid_breakeven_threshold_atr': 0.5,
        },
        {
            'name': '0.25× ATR target (0.5× breakeven)',
            'profit_target_atr_multiplier': 0.25,
            'hybrid_breakeven_threshold_atr': 0.5,
        },
        {
            'name': '0.30× ATR target (0.5× breakeven)',
            'profit_target_atr_multiplier': 0.30,
            'hybrid_breakeven_threshold_atr': 0.5,
        },
        {
            'name': '0.35× ATR target (0.5× breakeven)',
            'profit_target_atr_multiplier': 0.35,
            'hybrid_breakeven_threshold_atr': 0.5,
        },
        {
            'name': '0.40× ATR target (0.5× breakeven)',
            'profit_target_atr_multiplier': 0.40,
            'hybrid_breakeven_threshold_atr': 0.5,
        },
        {
            'name': '0.45× ATR target (0.5× breakeven)',
            'profit_target_atr_multiplier': 0.45,
            'hybrid_breakeven_threshold_atr': 0.5,
        },
        {
            'name': '0.50× ATR target (0.5× breakeven)',
            'profit_target_atr_multiplier': 0.50,
            'hybrid_breakeven_threshold_atr': 0.5,
        },
        {
            'name': '0.56× ATR target (0.5× breakeven)',
            'profit_target_atr_multiplier': 0.56,
            'hybrid_breakeven_threshold_atr': 0.5,
        },
        {
            'name': '0.60× ATR target (0.5× breakeven)',
            'profit_target_atr_multiplier': 0.60,
            'hybrid_breakeven_threshold_atr': 0.5,
        },
        {
            'name': '0.65× ATR target (0.5× breakeven)',
            'profit_target_atr_multiplier': 0.65,
            'hybrid_breakeven_threshold_atr': 0.5,
        },
        {
            'name': '0.70× ATR target (0.5× breakeven)',
            'profit_target_atr_multiplier': 0.70,
            'hybrid_breakeven_threshold_atr': 0.5,
        },
    ]
    results = []
    for s in scenarios:
        logger.info(f"Running scenario: {s['name']}")
        kwargs = {
            'initial_capital': 1000000,
            'profit_target_atr_multiplier': s['profit_target_atr_multiplier'],
            'hybrid_breakeven_threshold_atr': s['hybrid_breakeven_threshold_atr'],
            'scenario_name': s['name'],
        }
        if s.get('max_position_size') is not None:
            kwargs['max_position_size'] = s['max_position_size']
        if s.get('max_risk_pct_of_capital') is not None:
            kwargs['max_risk_pct_of_capital'] = s['max_risk_pct_of_capital']
        if s.get('force_max_lots'):
            kwargs['force_max_lots'] = True
        backtester = TrendFollowingBacktester(**kwargs)
        backtester.run_backtest(start_date, end_date, check_interval_minutes=check_interval_minutes)
        report = backtester.generate_report()
        results.append(report)

    # Comparison table
    print("\n" + "=" * 95)
    print("PROFIT TARGET vs EARLIER BREAKEVEN — COMPARISON")
    print("=" * 95)
    print(f"Period: {start_date} to {end_date}  |  Check interval: {check_interval_minutes} min")
    print("=" * 95)

    headers = [
        'Scenario', 'Trades', 'Wins', 'Losses', 'Win%', 'Net P&L (₹)', 'Gross P&L (₹)', 'Charges (₹)', 'Total Qty',
        'PROFIT_TARGET_ATR', 'STOP_LOSS_HIT', 'REGIME_CHANGE', 'Other'
    ]
    col_widths = [38, 6, 4, 5, 5, 11, 11, 9, 9, 8, 8, 8, 5]
    fmt = "  ".join(f"{{:<{w}}}" for w in col_widths)
    print(fmt.format(*headers))
    print("-" * 95)

    for r in results:
        name = r.get('scenario_name', 'default')[:38]
        total = r['total_trades']
        wins = r['winning_trades']
        loss = r['losing_trades']
        win_pct = r['win_rate']
        net_pnl = r['total_pnl']
        gross = r.get('total_pnl_gross', net_pnl)
        charges = r.get('total_charges', 0)
        total_qty = r.get('total_quantity', 0)
        er = r.get('exit_reasons', {})
        pt = er.get('PROFIT_TARGET_ATR', {}).get('count', 0)
        sl = er.get('STOP_LOSS_HIT', {}).get('count', 0)
        rc = er.get('REGIME_CHANGE', {}).get('count', 0)
        other = total - pt - sl - rc
        print(fmt.format(
            name[:38], str(total), str(wins), str(loss), f"{win_pct:.1f}",
            f"{net_pnl:,.0f}", f"{gross:,.0f}", f"{charges:,.0f}", str(total_qty),
            str(pt), str(sl), str(rc), str(other)
        ))

    print("=" * 95)
    best = max(results, key=lambda x: x['total_pnl'])
    print(f"Best net P&L: {best.get('scenario_name', 'default')} — ₹{best['total_pnl']:,.2f}")
    print("=" * 95)

    # Probability of at least one of each regime per day (two regimes: TRENDING, SIDEWAYS)
    r0 = results[0] if results else {}
    if r0.get('days_with_data') is not None:
        n_days = r0['days_with_data']
        print("\nRegime per day (backtest, two regimes):")
        print(f"  Trading days with data: {n_days}")
        print(f"  Days with ≥1 TRENDING:  {r0.get('days_with_trending', 0):3d}  → P(TRENDING per day)  = {r0.get('prob_trending_per_day_pct', 0):.1f}%")
        print(f"  Days with ≥1 SIDEWAYS: {r0.get('days_with_sideways', 0):3d}  → P(SIDEWAYS per day) = {r0.get('prob_sideways_per_day_pct', 0):.1f}%")
        print(f"  P(at least one of any regime per day): 100.0% (every bar has a regime)")
    # ATR and ADX range in the backtest data
    if r0.get('atr_min') is not None:
        print("\nATR and ADX range in backtest data:")
        print(f"  ATR(14): min = {r0['atr_min']:.2f}, max = {r0['atr_max']:.2f}, mean = {r0['atr_mean']:.2f}  (n = {r0.get('atr_count', 0)})")
    if r0.get('adx_min') is not None:
        print(f"  ADX(14): min = {r0['adx_min']:.2f}, max = {r0['adx_max']:.2f}, mean = {r0['adx_mean']:.2f}  (n = {r0.get('adx_count', 0)})")
    print()

    # Profitability summary for 0.5× ATR target (config default)
    half_atr = next((r for r in results if r.get('profit_target_atr_multiplier') == 0.5), None)
    if half_atr:
        win_pct = half_atr['win_rate']
        ret_pct = half_atr.get('total_return_pct', 0)
        net_pnl = half_atr['total_pnl']
        total = half_atr['total_trades']
        wins = half_atr['winning_trades']
        print("\n0.5× ATR target — profitability (backtest):")
        print(f"  Win percentage: {win_pct:.1f}% ({wins} winning trades / {total} total)")
        print(f"  Total return: {ret_pct:.2f}% on capital")
        print(f"  Net P&L: ₹{net_pnl:,.2f}")
        print("  ATR percentile: backtest computes atr_percentile from recent 100 candles for four-regime stats.")
    print()

    # Save comparison (summaries only, no full trade lists)
    comparison = {
        'start_date': start_date,
        'end_date': end_date,
        'check_interval_minutes': check_interval_minutes,
        'regime_per_day': {
            'days_with_data': results[0].get('days_with_data'),
            'days_with_trending': results[0].get('days_with_trending'),
            'days_with_sideways': results[0].get('days_with_sideways'),
            'prob_convex_per_day_pct': results[0].get('prob_convex_per_day_pct'),
            'prob_income_per_day_pct': results[0].get('prob_income_per_day_pct'),
            'prob_trend_per_day_pct': results[0].get('prob_trend_per_day_pct'),
            'prob_neutral_per_day_pct': results[0].get('prob_neutral_per_day_pct'),
            'prob_at_least_one_regime_per_day_pct': 100.0,
        } if results and results[0].get('days_with_data') is not None else None,
        'atr_adx_range': {
            'atr_min': results[0].get('atr_min'),
            'atr_max': results[0].get('atr_max'),
            'atr_mean': results[0].get('atr_mean'),
            'atr_count': results[0].get('atr_count'),
            'adx_min': results[0].get('adx_min'),
            'adx_max': results[0].get('adx_max'),
            'adx_mean': results[0].get('adx_mean'),
            'adx_count': results[0].get('adx_count'),
        } if results and results[0].get('atr_min') is not None else None,
        'scenarios': [
            {
                'scenario_name': r.get('scenario_name'),
                'profit_target_atr_multiplier': r.get('profit_target_atr_multiplier'),
                'hybrid_breakeven_threshold_atr': r.get('hybrid_breakeven_threshold_atr'),
                'max_position_size': r.get('max_position_size'),
                'max_risk_pct_of_capital': r.get('max_risk_pct_of_capital'),
                'total_trades': r['total_trades'],
                'winning_trades': r['winning_trades'],
                'losing_trades': r['losing_trades'],
                'win_rate': r['win_rate'],
                'total_pnl': r['total_pnl'],
                'total_pnl_gross': r.get('total_pnl_gross'),
                'total_charges': r.get('total_charges'),
                'total_quantity': r.get('total_quantity'),
                'total_return_pct': r.get('total_return_pct'),
                'exit_reasons': r.get('exit_reasons'),
            }
            for r in results
        ],
    }
    comparison_file = f"backtest_trend_comparison_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    with open(comparison_file, 'w') as f:
        json.dump(comparison, f, indent=2, default=str)
    print(f"\nComparison saved to: {comparison_file}")
    return comparison


def run_trailing_vs_035_comparison(start_date: str = '20251222', end_date: str = '20260116',
                                   check_interval_minutes: int = 15) -> Dict:
    """
    Run two scenarios on the same period: (1) trailing only (no profit target),
    (2) 0.35× ATR profit target. Return and print side-by-side comparison.
    """
    # Trailing only (production default)
    bt_trailing = TrendFollowingBacktester(
        initial_capital=1000000,
        scenario_name='Trailing only (no target)',
        profit_target_atr_multiplier=None,
    )
    bt_trailing.run_backtest(start_date, end_date, check_interval_minutes=check_interval_minutes)
    r_trailing = bt_trailing.generate_report()

    # 0.35× ATR profit target
    bt_035 = TrendFollowingBacktester(
        initial_capital=1000000,
        scenario_name='0.35× ATR profit target',
        profit_target_atr_multiplier=0.35,
    )
    bt_035.run_backtest(start_date, end_date, check_interval_minutes=check_interval_minutes)
    r_035 = bt_035.generate_report()

    # Comparison table
    print("\n" + "=" * 85)
    print("TRAILING ONLY vs 0.35× ATR PROFIT TARGET")
    print("=" * 85)
    print(f"Period: {start_date} to {end_date}  |  Check interval: {check_interval_minutes} min")
    print("=" * 85)
    headers = ['Metric', 'Trailing only', '0.35× target']
    col_w = [32, 24, 24]
    fmt = "  ".join(f"{{:<{w}}}" for w in col_w)
    print(fmt.format(*headers))
    print("-" * 85)

    def row(label, v_t, v_035, fmt_num=lambda x: f"{x:,.2f}" if isinstance(x, (int, float)) else str(x)):
        print(fmt.format(label[:32], fmt_num(v_t), fmt_num(v_035)))

    row('Total trades', r_trailing['total_trades'], r_035['total_trades'], lambda x: str(int(x)))
    row('Winning / Losing', f"{r_trailing['winning_trades']} / {r_trailing['losing_trades']}",
        f"{r_035['winning_trades']} / {r_035['losing_trades']}")
    row('Win rate (%)', r_trailing['win_rate'], r_035['win_rate'])
    row('Net P&L (₹)', r_trailing['total_pnl'], r_035['total_pnl'])
    row('Gross P&L (₹)', r_trailing.get('total_pnl_gross', 0), r_035.get('total_pnl_gross', 0))
    row('Charges (₹)', r_trailing.get('total_charges', 0), r_035.get('total_charges', 0))
    row('Total return (%)', r_trailing.get('total_return_pct', 0), r_035.get('total_return_pct', 0))

    print("-" * 85)
    print("Exit reasons (count / P&L):")
    all_reasons = set(r_trailing.get('exit_reasons', {})) | set(r_035.get('exit_reasons', {}))
    for reason in sorted(all_reasons):
        et = r_trailing.get('exit_reasons', {}).get(reason, {'count': 0, 'pnl': 0})
        e35 = r_035.get('exit_reasons', {}).get(reason, {'count': 0, 'pnl': 0})
        print(fmt.format(f"  {reason}", f"{et['count']} / ₹{et['pnl']:,.0f}", f"{e35['count']} / ₹{e35['pnl']:,.0f}"))
    print("=" * 85 + "\n")

    out = {
        'start_date': start_date,
        'end_date': end_date,
        'check_interval_minutes': check_interval_minutes,
        'trailing_only': {
            'total_trades': r_trailing['total_trades'],
            'winning_trades': r_trailing['winning_trades'],
            'losing_trades': r_trailing['losing_trades'],
            'win_rate': r_trailing['win_rate'],
            'total_pnl': r_trailing['total_pnl'],
            'total_pnl_gross': r_trailing.get('total_pnl_gross'),
            'total_charges': r_trailing.get('total_charges'),
            'total_return_pct': r_trailing.get('total_return_pct'),
            'exit_reasons': r_trailing.get('exit_reasons'),
        },
        '035_atr_target': {
            'total_trades': r_035['total_trades'],
            'winning_trades': r_035['winning_trades'],
            'losing_trades': r_035['losing_trades'],
            'win_rate': r_035['win_rate'],
            'total_pnl': r_035['total_pnl'],
            'total_pnl_gross': r_035.get('total_pnl_gross'),
            'total_charges': r_035.get('total_charges'),
            'total_return_pct': r_035.get('total_return_pct'),
            'exit_reasons': r_035.get('exit_reasons'),
        },
    }
    out_file = f"backtest_trailing_vs_035_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    with open(out_file, 'w') as f:
        json.dump(out, f, indent=2, default=str)
    print(f"Comparison saved to: {out_file}")
    return out


def run_single_day_1_lot(date_str: str, check_interval_minutes: int = 15) -> Dict:
    """
    Run trend backtest for a single day with 1 lot forced (ignore risk-based position sizing).
    Use to answer: "If we had traded with 1 lot today, what would have been the PnL?"

    Args:
        date_str: Date in YYYYMMDD (e.g. '20260203').
        check_interval_minutes: Bar interval in minutes.

    Returns:
        Report dict with total_pnl, trades, exit_reasons, etc.
    """
    backtester = TrendFollowingBacktester(
        initial_capital=1000000,
        scenario_name=f'1 lot fixed — {date_str}',
        max_position_size=1,
        force_max_lots=True,
    )
    backtester.run_backtest(date_str, date_str, check_interval_minutes=check_interval_minutes)
    report = backtester.generate_report()

    # Print summary
    print("\n" + "=" * 60)
    print(f"SINGLE-DAY 1-LOT PnL — {date_str}")
    print("=" * 60)
    print(f"Total trades:     {report['total_trades']}")
    print(f"Winning / Losing: {report['winning_trades']} / {report['losing_trades']}")
    print(f"Win rate:         {report['win_rate']:.1f}%")
    print(f"Net P&L:          ₹{report['total_pnl']:,.2f}")
    print(f"Gross P&L:        ₹{report.get('total_pnl_gross', report['total_pnl']):,.2f}")
    print(f"Charges:          ₹{report.get('total_charges', 0):,.2f}")
    if report.get('exit_reasons'):
        print("Exit reasons:")
        for reason, data in report['exit_reasons'].items():
            print(f"  {reason}: {data.get('count', 0)} trades, P&L ₹{data.get('pnl', 0):,.2f}")
    if report.get('trades'):
        print("\nTrades:")
        for i, t in enumerate(report['trades'], 1):
            direction = t.get('direction', '')
            entry = t.get('entry_price', 0)
            exit_p = t.get('exit_price', 0)
            pnl = t.get('pnl', 0)
            reason = t.get('exit_reason', '')
            print(f"  {i}. {direction} entry={entry:.2f} exit={exit_p:.2f} PnL=₹{pnl:,.2f} [{reason}]")
    print("=" * 60 + "\n")
    return report


def main():
    """Run comparison, or single-day 1-lot PnL if --date and --force-1-lot are given."""
    import argparse
    parser = argparse.ArgumentParser(description='Trend following backtest')
    parser.add_argument('--date', type=str, help='Single date YYYYMMDD (e.g. 20260203)')
    parser.add_argument('--force-1-lot', action='store_true', dest='force_1_lot',
                        help='Run single-day with 1 lot fixed')
    parser.add_argument('--comparison', action='store_true', help='Run full comparison (default if no --date)')
    parser.add_argument('--trailing-vs-035', action='store_true', dest='trailing_vs_035',
                        help='Compare trailing only vs 0.35× ATR profit target')
    args = parser.parse_args()

    if args.trailing_vs_035:
        return run_trailing_vs_035_comparison(
            start_date='20251222',
            end_date='20260116',
            check_interval_minutes=15,
        )
    if args.date and args.force_1_lot:
        return run_single_day_1_lot(args.date)
    if args.comparison or (not args.date and not args.force_1_lot):
        return run_comparison(
            start_date='20251222',
            end_date='20260116',
            check_interval_minutes=15,
        )
    if args.date:
        print("Use --force-1-lot to run single-day 1-lot backtest for --date")
    return None


if __name__ == '__main__':
    main()
