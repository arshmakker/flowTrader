"""
Regime Detector for Market Regime Classification (Two-Fork Model)

Detects two regimes only:
- TRENDING: Strong trend (ADX >= 30, ATR% >= 50, EMA alignment). Route to Convex Backspread.
- SIDEWAYS: Not trending. Route to Iron Condor.

Features:
- Regime persistence (anti-whipsaw): Requires N=3 consecutive detections
- ATR history storage: Maintains 10-15 sessions for robust percentile calculation
"""

import pandas as pd
import numpy as np
import logging
import os
import json
from datetime import datetime, timedelta, date
from typing import Dict, List, Optional, Tuple
from technical_indicators import get_historical_price_data, calculate_ema, get_15min_candle_data

logger = logging.getLogger(__name__)

# ATR history storage directory
ATR_HISTORY_DIR = 'market_data_atr'
ATR_HISTORY_FILE = os.path.join(ATR_HISTORY_DIR, 'atr_history.json')

# Production thresholds (used by classify_regime_from_indicators and detect_regime)
CONVEX_IV_PCT_MAX = 40
CONVEX_VIX_MAX = 15  # India VIX < 15 (convexcall-style) as alternative to IV% for CONVEX
CONVEX_ATR_PCT_MAX = 25
INCOME_IV_PCT_MIN = 60
INCOME_VIX_MIN = 20   # India VIX >= 20 (elevated fear) as alternative to IV% > 60 for INCOME
INCOME_ADX_MAX = 20
INCOME_ATR_PCT_MAX = 50
TREND_ADX_MIN = 30
TREND_ATR_PCT_MIN = 50

# India VIX benchmarks for volatility regimes (used only when NOT TREND)
VIX_LOW = 12.0   # Below this → CONVEX eligible
VIX_HIGH = 18.0  # At or above this → INCOME eligible


def classify_regime_from_indicators(
    iv_percentile: Optional[float],
    adx_14: Optional[float],
    atr_percentile: Optional[float],
    range_compressed: bool,
    ema_direction: Optional[str],
    india_vix: Optional[float] = None,
    vix_low_override: Optional[float] = None,
    vix_high_override: Optional[float] = None,
) -> str:
    """
    Stateless regime classification (two-fork). Use in backtest when you have
    bar-level ADX, ATR%, and EMA direction.

    Returns TRENDING if trend conditions pass, else SIDEWAYS.
    VIX/iv_percentile args kept for API compatibility but not used for routing.

    Args:
        iv_percentile: 0-100 (unused in two-fork; kept for compatibility).
        adx_14: ADX(14) value.
        atr_percentile: 0-100 (ATR percentile).
        range_compressed: Unused; kept for compatibility.
        ema_direction: 'LONG' | 'SHORT' | None (from price vs EMA50 vs EMA100).
        india_vix, vix_low_override, vix_high_override: Unused; kept for compatibility.

    Returns:
        'TRENDING' | 'SIDEWAYS'
    """
    adx = adx_14 if adx_14 is not None else 0.0
    atr_pct = atr_percentile if atr_percentile is not None else 50.0

    if adx >= TREND_ADX_MIN and atr_pct >= TREND_ATR_PCT_MIN and ema_direction in ("LONG", "SHORT"):
        return "TRENDING"
    return "SIDEWAYS"


class RegimeDetector:
    """Detect market regime based on IV, ADX, ATR, and price range"""
    
    # Class variables for persistence (shared across all instances)
    _last_confirmed_regime = None
    _candidate_regime = None
    _confirmation_count_current = 0
    
    def __init__(self, cache_duration_minutes=15, confirmation_count=3):
        """
        Initialize RegimeDetector
        
        Args:
            cache_duration_minutes: How long to cache regime before recalculating
            confirmation_count: Number of consecutive detections required for regime change (default: 3)
        """
        self.cache_duration_minutes = cache_duration_minutes
        self.cached_regime = None
        self.cached_time = None
        
        # Regime persistence (anti-whipsaw) - use class variables
        self.confirmation_count = confirmation_count
        # Note: persistence state is stored as class variables above
        
        # Ensure ATR history directory exists
        if not os.path.exists(ATR_HISTORY_DIR):
            os.makedirs(ATR_HISTORY_DIR)
    
    def calculate_atr(self, high_prices: List[float], low_prices: List[float], 
                     close_prices: List[float], period: int = 14) -> Optional[float]:
        """
        Calculate Average True Range (ATR) over specified period
        
        Args:
            high_prices: List of high prices
            low_prices: List of low prices
            close_prices: List of close prices
            period: ATR period (default: 14)
        
        Returns:
            ATR value or None if calculation fails
        """
        try:
            if len(high_prices) < period + 1 or len(low_prices) < period + 1 or len(close_prices) < period + 1:
                logger.debug(f"Insufficient data for ATR calculation: need {period + 1} periods, got {len(close_prices)}")
                return None
            
            # Calculate True Range (TR) for each period
            true_ranges = []
            for i in range(1, len(close_prices)):
                tr1 = high_prices[i] - low_prices[i]  # Current high - current low
                tr2 = abs(high_prices[i] - close_prices[i-1])  # Current high - previous close
                tr3 = abs(low_prices[i] - close_prices[i-1])  # Current low - previous close
                tr = max(tr1, tr2, tr3)
                true_ranges.append(tr)
            
            if len(true_ranges) < period:
                logger.debug(f"Insufficient true ranges for ATR: need {period}, got {len(true_ranges)}")
                return None
            
            # Calculate ATR as simple moving average of TR
            atr = np.mean(true_ranges[-period:])
            return atr
            
        except Exception as e:
            logger.error(f"Error calculating ATR: {str(e)}", exc_info=True)
            return None
    
    def save_atr_data(self, atr_value: float, date_str: str = None):
        """
        Save ATR value to history for percentile calculation
        
        Args:
            atr_value: Current ATR value
            date_str: Date string (YYYY-MM-DD), defaults to today
        """
        try:
            if date_str is None:
                date_str = datetime.now().strftime('%Y-%m-%d')
            
            # Load existing history
            history = self.load_atr_history()
            
            # Add or update entry
            entry = {
                'date': date_str,
                'atr': atr_value,
                'timestamp': datetime.now().isoformat()
            }
            
            # Remove old entry for same date if exists
            history = [h for h in history if h.get('date') != date_str]
            history.append(entry)
            
            # Keep only last 20 sessions (for robustness)
            history = sorted(history, key=lambda x: x.get('date', ''), reverse=True)[:20]
            
            # Save to file
            with open(ATR_HISTORY_FILE, 'w') as f:
                json.dump(history, f, indent=2)
            
            logger.debug(f"Saved ATR data: {atr_value:.2f} for {date_str}")
            
        except Exception as e:
            logger.error(f"Error saving ATR data: {str(e)}")
    
    def load_atr_history(self) -> List[Dict]:
        """
        Load ATR history from storage
        
        Returns:
            List of ATR entries with 'date' and 'atr' keys
        """
        try:
            if os.path.exists(ATR_HISTORY_FILE):
                with open(ATR_HISTORY_FILE, 'r') as f:
                    history = json.load(f)
                    # Ensure we have at least 10-15 sessions
                    if len(history) >= 10:
                        return history
                    else:
                        logger.debug(f"ATR history has only {len(history)} sessions (target: 10-15)")
                        return history
            return []
        except Exception as e:
            logger.error(f"Error loading ATR history: {str(e)}")
            return []
    
    def calculate_atr_percentile(self, current_atr: float, historical_atrs: List[float] = None) -> Optional[float]:
        """
        Calculate ATR percentile relative to historical daily ATR values
        
        Uses stored daily ATR history (excludes today's entry for comparison).
        Only uses stored history - does NOT use intraday rolling ATR values.
        
        Args:
            current_atr: Current ATR value (today's intraday ATR)
            historical_atrs: DEPRECATED - ignored. Only stored daily history is used.
        
        Returns:
            ATR percentile (0-100) or None if calculation fails
        """
        try:
            if current_atr <= 0:
                return None
            
            # Load stored daily ATR history
            stored_history = self.load_atr_history()
            if not stored_history:
                logger.debug("No stored ATR history available")
                return None
            
            # Get today's date to exclude it from historical comparison
            today_str = datetime.now().strftime('%Y-%m-%d')
            
            # Extract ATR values from stored history, excluding today's entry
            historical_daily_atrs = []
            for entry in stored_history:
                entry_date = entry.get('date', '')
                entry_atr = entry.get('atr', 0)
                # Only include historical entries (not today) with valid ATR
                if entry_atr > 0 and entry_date != today_str:
                    historical_daily_atrs.append(entry_atr)
            
            if not historical_daily_atrs:
                logger.debug("No historical ATR values available (excluding today)")
                return None
            
            # Warn if we have less than 10 entries (less robust)
            if len(historical_daily_atrs) < 10:
                logger.warning(
                    f"ATR percentile using only {len(historical_daily_atrs)} historical daily values "
                    f"(recommended: 10+ for robustness)"
                )
            else:
                logger.debug(f"Using stored daily ATR history: {len(historical_daily_atrs)} sessions")
            
            # Calculate percentile
            sorted_atrs = sorted(historical_daily_atrs)
            count_below = sum(1 for atr in sorted_atrs if atr < current_atr)
            percentile = (count_below / len(sorted_atrs)) * 100.0
            
            logger.debug(
                f"ATR percentile: {percentile:.1f}% (current: {current_atr:.2f}, "
                f"historical daily samples: {len(sorted_atrs)})"
            )
            
            return percentile
            
        except Exception as e:
            logger.error(f"Error calculating ATR percentile: {str(e)}")
            return None
    
    def calculate_recent_range(self, recent_candles: List[Dict], minutes: int = 60) -> Optional[float]:
        """
        Calculate price range over last N minutes
        
        Args:
            recent_candles: List of candle dictionaries with 'high', 'low', 'timestamp'
            minutes: Number of minutes to look back (default: 60)
        
        Returns:
            Price range (high - low) or None if calculation fails
        """
        try:
            if not recent_candles:
                return None
            
            # Filter candles within time window
            cutoff_time = datetime.now() - timedelta(minutes=minutes)
            filtered_candles = [
                c for c in recent_candles
                if isinstance(c.get('timestamp'), datetime) and c['timestamp'] >= cutoff_time
            ]
            
            if not filtered_candles:
                # If no recent candles, use all available
                filtered_candles = recent_candles
            
            if not filtered_candles:
                return None
            
            # Find high and low over the period
            highs = [c.get('high', c.get('ltp', 0)) for c in filtered_candles if c.get('high') or c.get('ltp')]
            lows = [c.get('low', c.get('ltp', 0)) for c in filtered_candles if c.get('low') or c.get('ltp')]
            
            if not highs or not lows:
                return None
            
            price_range = max(highs) - min(lows)
            return price_range
            
        except Exception as e:
            logger.debug(f"Error calculating recent range: {str(e)}")
            return None
    
    def get_recent_candles(self, api, symbol_manager, spot_price: float, 
                          lookback_days: int = 1) -> List[Dict]:
        """
        Get recent candles for range calculation
        
        Args:
            api: ShoonyaApiPy instance
            symbol_manager: SymbolManager instance
            spot_price: Current spot price
            lookback_days: Days to look back
        
        Returns:
            List of candle dictionaries
        """
        try:
            # #region agent log
            import json
            try:
                with open('/Users/arshdeep/git/regimetrader/.cursor/debug.log', 'a') as f:
                    f.write(json.dumps({"location":"regime_detector.py:250","message":"get_recent_candles entry","data":{"lookback_days":lookback_days,"spot_price":spot_price,"has_api":api is not None,"has_symbol_manager":symbol_manager is not None},"timestamp":int(datetime.now().timestamp()*1000),"sessionId":"debug-session","runId":"regime-debug","hypothesisId":"R3h"})+"\n")
            except: pass
            # #endregion
            
            # Get historical price data (same source as ADX calculation)
            highs, lows, closes = get_historical_price_data(
                api, symbol_manager, 'Nifty 50', days=lookback_days
            )
            
            # #region agent log
            try:
                with open('/Users/arshdeep/git/regimetrader/.cursor/debug.log', 'a') as f:
                    f.write(json.dumps({"location":"regime_detector.py:266","message":"get_historical_price_data result","data":{"highs_count":len(highs) if highs else 0,"lows_count":len(lows) if lows else 0,"closes_count":len(closes) if closes else 0,"has_data":highs is not None and lows is not None and closes is not None},"timestamp":int(datetime.now().timestamp()*1000),"sessionId":"debug-session","runId":"regime-debug","hypothesisId":"R3i"})+"\n")
            except: pass
            # #endregion
            
            if not highs or not lows or not closes:
                # Fallback: create synthetic candles from spot price
                logger.debug("No historical data, using spot price as fallback")
                # #region agent log
                try:
                    with open('/Users/arshdeep/git/regimetrader/.cursor/debug.log', 'a') as f:
                        f.write(json.dumps({"location":"regime_detector.py:270","message":"Using fallback synthetic candles","data":{"spot_price":spot_price},"timestamp":int(datetime.now().timestamp()*1000),"sessionId":"debug-session","runId":"regime-debug","hypothesisId":"R3j"})+"\n")
                except: pass
                # #endregion
                return [{
                    'high': spot_price * 1.001,
                    'low': spot_price * 0.999,
                    'ltp': spot_price,
                    'timestamp': datetime.now()
                }]
            
            # Convert to candle format
            candles = []
            for i in range(len(closes)):
                candles.append({
                    'high': highs[i] if i < len(highs) else closes[i],
                    'low': lows[i] if i < len(lows) else closes[i],
                    'ltp': closes[i],
                    'timestamp': datetime.now() - timedelta(days=len(closes) - i)
                })
            
            # #region agent log
            try:
                with open('/Users/arshdeep/git/regimetrader/.cursor/debug.log', 'a') as f:
                    f.write(json.dumps({"location":"regime_detector.py:290","message":"get_recent_candles returning","data":{"candles_count":len(candles),"needs_20_for_rolling_avg":True,"has_enough":len(candles) >= 20},"timestamp":int(datetime.now().timestamp()*1000),"sessionId":"debug-session","runId":"regime-debug","hypothesisId":"R3k"})+"\n")
            except: pass
            # #endregion
            
            return candles
            
        except Exception as e:
            logger.debug(f"Error getting recent candles: {str(e)}")
            # #region agent log
            try:
                with open('/Users/arshdeep/git/regimetrader/.cursor/debug.log', 'a') as f:
                    f.write(json.dumps({"location":"regime_detector.py:293","message":"get_recent_candles exception","data":{"error":str(e)},"timestamp":int(datetime.now().timestamp()*1000),"sessionId":"debug-session","runId":"regime-debug","hypothesisId":"R3l"})+"\n")
            except: pass
            # #endregion
            return []
    
    def detect_regime(self, market_state: Dict, recent_candles: Optional[List[Dict]] = None,
                     api=None, symbol_manager=None) -> Dict:
        """
        Two-fork regime detection:

        1. TRENDING: ADX >= 30, ATR% >= 50, EMA directional alignment (price vs EMA50 vs EMA100).
        2. SIDEWAYS: Otherwise.

        NOTE: Exit logic and trailing stop-loss rules are handled elsewhere.

        Args:
            market_state: Market state with iv_percentile, adx_14, spot_price.
            recent_candles: Optional; used for range_state.
            api, symbol_manager: For fetching 15m candles (EMA) if needed.

        Returns:
            Dict with "regime": "TRENDING" | "SIDEWAYS", "detected_regime", iv_percentile,
            adx, atr, atr_percentile, range_state, etc.
        """
        try:
            # Check cache
            if self.cached_regime and self.cached_time:
                time_diff = (datetime.now() - self.cached_time).total_seconds() / 60.0
                if time_diff < self.cache_duration_minutes:
                    logger.debug(f"Using cached regime: {self.cached_regime['regime']} (cached {time_diff:.1f} min ago)")
                    return self.cached_regime
            
            # Extract inputs
            iv_percentile = market_state.get('iv_percentile')
            adx_14 = market_state.get('adx_14')
            india_vix = market_state.get('india_vix')
            spot_price = market_state.get('spot_price')
            
            # #region agent log (H-A: india_vix; H-B: adx, atr inputs)
            import json
            try:
                with open('/Users/arshdeep/git/regimetrader/.cursor/debug.log', 'a') as f:
                    f.write(json.dumps({"location":"regime_detector.py:329","message":"Regime detection inputs","data":{"iv_percentile":iv_percentile,"adx_14":adx_14,"india_vix":india_vix,"spot_price":spot_price,"has_all_inputs":iv_percentile is not None and adx_14 is not None and spot_price is not None},"timestamp":int(datetime.now().timestamp()*1000),"sessionId":"debug-session","runId":"regime-debug","hypothesisId":"H-A,H-B"})+"\n")
            except: pass
            # #endregion
            
            if iv_percentile is None or adx_14 is None or spot_price is None:
                logger.warning("Missing required market_state fields for regime detection")
                # #region agent log
                try:
                    with open('/Users/arshdeep/git/regimetrader/.cursor/debug.log', 'a') as f:
                        f.write(json.dumps({"location":"regime_detector.py:334","message":"Missing inputs - returning SIDEWAYS","data":{"iv_percentile":iv_percentile,"adx_14":adx_14,"spot_price":spot_price},"timestamp":int(datetime.now().timestamp()*1000),"sessionId":"debug-session","runId":"regime-debug","hypothesisId":"R1"})+"\n")
                except: pass
                # #endregion
                regime_result = {
                    "regime": "SIDEWAYS",
                    "detected_regime": "SIDEWAYS",
                    "iv_percentile": iv_percentile or 0,
                    "adx": adx_14 or 0,
                    "atr": None,
                    "atr_percentile": None,
                    "range_state": "NORMAL"
                }
                self._cache_regime(regime_result)
                return regime_result
            
            # Get recent candles if not provided
            if recent_candles is None:
                if api and symbol_manager:
                    # #region agent log
                    try:
                        with open('/Users/arshdeep/git/regimetrader/.cursor/debug.log', 'a') as f:
                            f.write(json.dumps({"location":"regime_detector.py:348","message":"Calling get_recent_candles","data":{"has_api":api is not None,"has_symbol_manager":symbol_manager is not None,"spot_price":spot_price,"lookback_days":20},"timestamp":int(datetime.now().timestamp()*1000),"sessionId":"debug-session","runId":"regime-debug","hypothesisId":"R3e"})+"\n")
                    except: pass
                    # #endregion
                    # Use 20 days lookback to get enough candles for rolling average (needs 20+)
                    recent_candles = self.get_recent_candles(api, symbol_manager, spot_price, lookback_days=20)
                    # #region agent log
                    try:
                        with open('/Users/arshdeep/git/regimetrader/.cursor/debug.log', 'a') as f:
                            f.write(json.dumps({"location":"regime_detector.py:355","message":"get_recent_candles result","data":{"recent_candles_count":len(recent_candles) if recent_candles else 0,"has_candles":recent_candles is not None and len(recent_candles) > 0 if recent_candles else False},"timestamp":int(datetime.now().timestamp()*1000),"sessionId":"debug-session","runId":"regime-debug","hypothesisId":"R3f"})+"\n")
                    except: pass
                    # #endregion
                else:
                    recent_candles = []
                    # #region agent log
                    try:
                        with open('/Users/arshdeep/git/regimetrader/.cursor/debug.log', 'a') as f:
                            f.write(json.dumps({"location":"regime_detector.py:362","message":"get_recent_candles skipped - no API","data":{"has_api":api is not None,"has_symbol_manager":symbol_manager is not None},"timestamp":int(datetime.now().timestamp()*1000),"sessionId":"debug-session","runId":"regime-debug","hypothesisId":"R3g"})+"\n")
                    except: pass
                    # #endregion
            
            # Calculate ATR
            atr = None
            atr_percentile = None
            range_state = "NORMAL"
            
            if api and symbol_manager:
                # Get historical price data for ATR calculation
                highs, lows, closes = get_historical_price_data(
                    api, symbol_manager, 'Nifty 50', days=30
                )
                
                if highs and lows and closes and len(highs) >= 15:
                    # Calculate current ATR
                    atr = self.calculate_atr(highs, lows, closes, period=14)
                    
                    if atr:
                        # Calculate percentile using stored daily ATR history only
                        # (NOT using intraday rolling ATR values - those are incorrect for percentile)
                        atr_percentile = self.calculate_atr_percentile(atr)
                        
                        # #region agent log
                        try:
                            with open('/Users/arshdeep/git/regimetrader/.cursor/debug.log', 'a') as f:
                                f.write(json.dumps({"location":"regime_detector.py:383","message":"ATR calculation results","data":{"atr":atr,"atr_percentile":atr_percentile,"historical_atrs_count":len(historical_atrs)},"timestamp":int(datetime.now().timestamp()*1000),"sessionId":"debug-session","runId":"regime-debug","hypothesisId":"R2"})+"\n")
                        except: pass
                        # #endregion
            else:
                # #region agent log
                try:
                    with open('/Users/arshdeep/git/regimetrader/.cursor/debug.log', 'a') as f:
                        f.write(json.dumps({"location":"regime_detector.py:359","message":"ATR calculation skipped","data":{"has_api":api is not None,"has_symbol_manager":symbol_manager is not None},"timestamp":int(datetime.now().timestamp()*1000),"sessionId":"debug-session","runId":"regime-debug","hypothesisId":"R2"})+"\n")
                except: pass
                # #endregion
            
            # Calculate recent price range
            last_range = self.calculate_recent_range(recent_candles, minutes=60)
            
            # #region agent log
            try:
                with open('/Users/arshdeep/git/regimetrader/.cursor/debug.log', 'a') as f:
                    f.write(json.dumps({"location":"regime_detector.py:413","message":"Recent candles for range calculation","data":{"recent_candles_count":len(recent_candles) if recent_candles else 0,"last_range":last_range,"has_recent_candles":recent_candles is not None and len(recent_candles) > 0 if recent_candles else False},"timestamp":int(datetime.now().timestamp()*1000),"sessionId":"debug-session","runId":"regime-debug","hypothesisId":"R3a"})+"\n")
            except: pass
            # #endregion
            
            # Calculate rolling average range (for comparison)
            # Require at least 15 candles so we can compute range when API returns ~17 days (e.g. 20 calendar days)
            MIN_CANDLES_FOR_ROLLING = 15
            rolling_avg_range = None
            if recent_candles and len(recent_candles) >= MIN_CANDLES_FOR_ROLLING:
                # Use last N candles (up to 20) for average range
                n_use = min(20, len(recent_candles))
                recent_ranges = []
                for i in range(max(0, len(recent_candles) - n_use), len(recent_candles)):
                    candle = recent_candles[i]
                    high = candle.get('high', candle.get('ltp', spot_price))
                    low = candle.get('low', candle.get('ltp', spot_price))
                    if high and low:
                        recent_ranges.append(high - low)
                
                if recent_ranges:
                    rolling_avg_range = np.mean(recent_ranges)
                    # #region agent log
                    try:
                        with open('/Users/arshdeep/git/regimetrader/.cursor/debug.log', 'a') as f:
                            f.write(json.dumps({"location":"regime_detector.py:428","message":"Rolling avg range calculated","data":{"rolling_avg_range":rolling_avg_range,"recent_ranges_count":len(recent_ranges)},"timestamp":int(datetime.now().timestamp()*1000),"sessionId":"debug-session","runId":"regime-debug","hypothesisId":"R3b"})+"\n")
                    except: pass
                    # #endregion
                else:
                    # #region agent log
                    try:
                        with open('/Users/arshdeep/git/regimetrader/.cursor/debug.log', 'a') as f:
                            f.write(json.dumps({"location":"regime_detector.py:435","message":"Rolling avg range - no valid ranges","data":{"recent_candles_count":len(recent_candles),"recent_ranges_count":0},"timestamp":int(datetime.now().timestamp()*1000),"sessionId":"debug-session","runId":"regime-debug","hypothesisId":"R3c"})+"\n")
                    except: pass
                    # #endregion
            else:
                # #region agent log
                try:
                    with open('/Users/arshdeep/git/regimetrader/.cursor/debug.log', 'a') as f:
                        f.write(json.dumps({"location":"regime_detector.py:442","message":"Rolling avg range - insufficient candles","data":{"recent_candles_count":len(recent_candles) if recent_candles else 0,"needs_min":MIN_CANDLES_FOR_ROLLING,"has_enough":recent_candles is not None and len(recent_candles) >= MIN_CANDLES_FOR_ROLLING if recent_candles else False},"timestamp":int(datetime.now().timestamp()*1000),"sessionId":"debug-session","runId":"regime-debug","hypothesisId":"R3d"})+"\n")
                except: pass
                # #endregion
            
            # Determine range state
            if last_range and rolling_avg_range:
                if last_range < (rolling_avg_range * 0.6):
                    range_state = "COMPRESSED"
                elif last_range > (rolling_avg_range * 1.4):
                    range_state = "EXPANDING"
                else:
                    range_state = "NORMAL"
            
            # #region agent log
            try:
                with open('/Users/arshdeep/git/regimetrader/.cursor/debug.log', 'a') as f:
                    f.write(json.dumps({"location":"regime_detector.py:403","message":"Range state calculation","data":{"last_range":last_range,"rolling_avg_range":rolling_avg_range,"range_state":range_state,"compressed_threshold":rolling_avg_range * 0.6 if rolling_avg_range else None,"expanding_threshold":rolling_avg_range * 1.4 if rolling_avg_range else None},"timestamp":int(datetime.now().timestamp()*1000),"sessionId":"debug-session","runId":"regime-debug","hypothesisId":"R3"})+"\n")
            except: pass
            # #endregion
            
            # --- STEP 1: TREND DETECTION (HARD OVERRIDE) ---
            # TREND is determined only by price structure (ADX, EMA alignment, ATR expansion).
            # No VIX/IV/range checks before this; if TREND, return immediately.
            is_trend = False
            if (adx_14 is not None and adx_14 >= TREND_ADX_MIN and
                    atr_percentile is not None and atr_percentile >= TREND_ATR_PCT_MIN):
                # #region agent log (H-B: TREND branch entered; H-E: why INCOME didn't trigger)
                import json
                income_vol_ok = (iv_percentile is not None and iv_percentile > 60) or (india_vix is not None and india_vix >= INCOME_VIX_MIN)
                try:
                    with open('/Users/arshdeep/git/regimetrader/.cursor/debug.log', 'a') as f:
                        f.write(json.dumps({"location":"regime_detector.py:431","message":"TREND branch entered","data":{"adx":adx_14,"atr_percentile":atr_percentile,"income_vol_ok":income_vol_ok,"adx_lt_20":adx_14 is not None and adx_14 < 20,"atr_lt_50":atr_percentile is not None and atr_percentile < 50,"has_api":api is not None,"has_symbol_manager":symbol_manager is not None},"timestamp":int(datetime.now().timestamp()*1000),"sessionId":"debug-session","runId":"regime-debug","hypothesisId":"H-B,H-E"})+"\n")
                except: pass
                # #endregion
                
                # Check directional bias using EMA structure
                # Use 15-minute candles for EMA calculation (same timeframe as ADX/ATR)
                # EMA(50) = 50 × 15min = 12.5 hours, EMA(100) = 100 × 15min = 25 hours
                directional_bias_stable = False
                direction = None
                
                # Fetch 15-minute candle data for EMA calculation
                if api and symbol_manager:
                    # #region agent log
                    try:
                        with open('/Users/arshdeep/git/regimetrader/.cursor/debug.log', 'a') as f:
                            f.write(json.dumps({"location":"regime_detector.py:437","message":"Fetching 15-minute candles for EMA","data":{"lookback_hours":30},"timestamp":int(datetime.now().timestamp()*1000),"sessionId":"debug-session","runId":"post-fix","hypothesisId":"F"})+"\n")
                    except: pass
                    # #endregion
                    
                    # Fetch 30 hours of 15-minute candles (120 candles = enough for EMA(100))
                    # #region agent log
                    try:
                        with open('/Users/arshdeep/git/regimetrader/.cursor/debug.log', 'a') as f:
                            f.write(json.dumps({"location":"regime_detector.py:457","message":"Calling get_15min_candle_data","data":{"lookback_hours":30,"has_api":api is not None,"has_symbol_manager":symbol_manager is not None},"timestamp":int(datetime.now().timestamp()*1000),"sessionId":"debug-session","runId":"check-15min","hypothesisId":"G1"})+"\n")
                    except: pass
                    # #endregion
                    
                    ema_closes = get_15min_candle_data(api, symbol_manager, 'Nifty 50', lookback_hours=30)
                    
                    # #region agent log
                    try:
                        with open('/Users/arshdeep/git/regimetrader/.cursor/debug.log', 'a') as f:
                            f.write(json.dumps({"location":"regime_detector.py:462","message":"15-minute candles fetch result","data":{"ema_closes_is_none":ema_closes is None,"ema_closes_len":len(ema_closes) if ema_closes else 0,"has_data":ema_closes is not None and len(ema_closes) > 0 if ema_closes else False},"timestamp":int(datetime.now().timestamp()*1000),"sessionId":"debug-session","runId":"check-15min","hypothesisId":"G2"})+"\n")
                    except: pass
                    # #endregion
                else:
                    ema_closes = None
                
                if api and symbol_manager and ema_closes and len(ema_closes) >= 100:
                    # #region agent log
                    try:
                        with open('/Users/arshdeep/git/regimetrader/.cursor/debug.log', 'a') as f:
                            f.write(json.dumps({"location":"regime_detector.py:450","message":"Starting EMA calculation","data":{"closes_count":len(ema_closes),"spot_price":spot_price},"timestamp":int(datetime.now().timestamp()*1000),"sessionId":"debug-session","runId":"post-fix","hypothesisId":"B"})+"\n")
                    except: pass
                    # #endregion
                    
                    # Calculate EMAs
                    ema_50 = calculate_ema(ema_closes, period=50)
                    ema_100 = calculate_ema(ema_closes, period=100)
                    current_price = ema_closes[-1] if ema_closes else spot_price
                    
                    # #region agent log
                    try:
                        with open('/Users/arshdeep/git/regimetrader/.cursor/debug.log', 'a') as f:
                            f.write(json.dumps({"location":"regime_detector.py:455","message":"EMA calculation results","data":{"ema_50":ema_50,"ema_100":ema_100,"current_price":current_price,"has_all_values":ema_50 is not None and ema_100 is not None and current_price is not None},"timestamp":int(datetime.now().timestamp()*1000),"sessionId":"debug-session","runId":"post-fix","hypothesisId":"C"})+"\n")
                    except: pass
                    # #endregion
                    
                    if ema_50 and ema_100 and current_price:
                        # Check if EMA structure is aligned (trending)
                        # LONG: price > EMA50 > EMA100
                        # SHORT: price < EMA50 < EMA100
                        long_structure = current_price > ema_50 > ema_100
                        short_structure = current_price < ema_50 < ema_100
                        directional_bias_stable = long_structure or short_structure
                        
                        # #region agent log
                        try:
                            with open('/Users/arshdeep/git/regimetrader/.cursor/debug.log', 'a') as f:
                                f.write(json.dumps({"location":"regime_detector.py:461","message":"EMA structure check","data":{"long_structure":long_structure,"short_structure":short_structure,"directional_bias_stable":directional_bias_stable,"price_ema50":current_price > ema_50,"ema50_ema100":ema_50 > ema_100,"price_ema50_val":current_price - ema_50,"ema50_ema100_val":ema_50 - ema_100},"timestamp":int(datetime.now().timestamp()*1000),"sessionId":"debug-session","runId":"post-fix","hypothesisId":"D"})+"\n")
                        except: pass
                        # #endregion
                        
                        if long_structure:
                            direction = "LONG"
                        elif short_structure:
                            direction = "SHORT"
                else:
                    # #region agent log
                    try:
                        with open('/Users/arshdeep/git/regimetrader/.cursor/debug.log', 'a') as f:
                            f.write(json.dumps({"location":"regime_detector.py:468","message":"EMA check skipped - insufficient data","data":{"has_api":api is not None,"has_symbol_manager":symbol_manager is not None,"has_ema_closes":ema_closes is not None if 'ema_closes' in locals() else False,"ema_closes_len":len(ema_closes) if 'ema_closes' in locals() and ema_closes else 0,"needs_100":len(ema_closes) >= 100 if 'ema_closes' in locals() and ema_closes else False},"timestamp":int(datetime.now().timestamp()*1000),"sessionId":"debug-session","runId":"post-fix","hypothesisId":"E"})+"\n")
                    except: pass
                    # #endregion
                
                if directional_bias_stable:
                    is_trend = True
                    confirmed_trend = self._apply_regime_persistence("TRENDING")
                    if atr and atr > 0:
                        self.save_atr_data(atr)
                    regime_result = {
                        "regime": confirmed_trend,
                        "detected_regime": "TRENDING",
                        "iv_percentile": iv_percentile,
                        "india_vix": india_vix,
                        "adx": adx_14,
                        "atr": atr,
                        "atr_percentile": atr_percentile,
                        "range_state": range_state,
                        "confirmation_count": RegimeDetector._confirmation_count_current,
                        "last_confirmed_regime": RegimeDetector._last_confirmed_regime
                    }
                    self._cache_regime(regime_result)
                    logger.info(f"Regime detected: TRENDING (ADX={adx_14:.1f}, ATR%={atr_percentile:.1f}%, Direction={direction})")
                    return regime_result
            
            # --- STEP 2: NOT TRENDING → SIDEWAYS ---
            detected_regime = "SIDEWAYS"
            logger.info(f"Regime detected: SIDEWAYS (no trend)")
            
            # Apply regime persistence (anti-whipsaw)
            confirmed_regime = self._apply_regime_persistence(detected_regime)
            # #region agent log (H-C: persistence may keep TREND)
            try:
                with open('/Users/arshdeep/git/regimetrader/.cursor/debug.log', 'a') as f:
                    f.write(json.dumps({"location":"regime_detector.py:persist","message":"After persistence","data":{"detected_regime":detected_regime,"confirmed_regime":confirmed_regime,"confirmation_count_current":RegimeDetector._confirmation_count_current,"last_confirmed_regime":RegimeDetector._last_confirmed_regime},"timestamp":int(datetime.now().timestamp()*1000),"sessionId":"debug-session","runId":"regime-debug","hypothesisId":"H-C"})+"\n")
            except: pass
            # #endregion

            # Save ATR to history if calculated
            if atr and atr > 0:
                self.save_atr_data(atr)
            
            # Build result
            regime_result = {
                "regime": confirmed_regime,  # Use confirmed regime
                "detected_regime": detected_regime,  # Also include raw detection
                "iv_percentile": iv_percentile,
                "india_vix": india_vix,
                "adx": adx_14,
                "atr": atr,
                "atr_percentile": atr_percentile,
                "range_state": range_state,
                "confirmation_count": RegimeDetector._confirmation_count_current,  # Use class variable
                "last_confirmed_regime": RegimeDetector._last_confirmed_regime  # Use class variable
            }
            
            # #region agent log
            try:
                with open('/Users/arshdeep/git/regimetrader/.cursor/debug.log', 'a') as f:
                    f.write(json.dumps({"location":"regime_detector.py:592","message":"Final regime result","data":{"regime":confirmed_regime,"detected_regime":detected_regime,"iv_percentile":iv_percentile,"adx":adx_14,"atr":atr,"atr_percentile":atr_percentile,"range_state":range_state,"confirmation_count":RegimeDetector._confirmation_count_current},"timestamp":int(datetime.now().timestamp()*1000),"sessionId":"debug-session","runId":"regime-debug","hypothesisId":"R6"})+"\n")
            except: pass
            # #endregion
            
            # Cache result
            self._cache_regime(regime_result)
            
            return regime_result
            
        except Exception as e:
            logger.error(f"Error detecting regime: {str(e)}", exc_info=True)
            return {
                "regime": "SIDEWAYS",
                "detected_regime": "SIDEWAYS",
                "iv_percentile": market_state.get('iv_percentile', 0),
                "india_vix": market_state.get('india_vix'),
                "adx": market_state.get('adx_14', 0),
                "atr": None,
                "atr_percentile": None,
                "range_state": "NORMAL"
            }
    
    def _apply_regime_persistence(self, detected_regime: str) -> str:
        """
        Apply regime persistence logic (anti-whipsaw)
        
        A regime change is accepted ONLY if the same regime is detected
        N consecutive times (default: N=3).
        
        Uses class variables to maintain state across instances.
        
        Args:
            detected_regime: Currently detected regime
        
        Returns:
            Confirmed regime (may be previous if not yet confirmed)
        """
        # Initialize if first detection
        if RegimeDetector._last_confirmed_regime is None:
            RegimeDetector._last_confirmed_regime = detected_regime
            RegimeDetector._candidate_regime = detected_regime
            RegimeDetector._confirmation_count_current = 1
            logger.debug(f"Initial regime confirmed: {detected_regime}")
            return detected_regime
        
        # If same as last confirmed, no change needed
        if detected_regime == RegimeDetector._last_confirmed_regime:
            RegimeDetector._confirmation_count_current = 0  # Reset counter
            return RegimeDetector._last_confirmed_regime
        
        # If different from last confirmed, check if it matches candidate
        if detected_regime == RegimeDetector._candidate_regime:
            # Same candidate as before, increment count
            RegimeDetector._confirmation_count_current += 1
        else:
            # New candidate, reset count
            RegimeDetector._candidate_regime = detected_regime
            RegimeDetector._confirmation_count_current = 1
        
        # Check if confirmation threshold reached
        if RegimeDetector._confirmation_count_current >= self.confirmation_count:
            # Regime change confirmed
            old_regime = RegimeDetector._last_confirmed_regime
            RegimeDetector._last_confirmed_regime = detected_regime
            RegimeDetector._confirmation_count_current = 0
            logger.info(f"Regime change confirmed: {old_regime} → {detected_regime} (after {self.confirmation_count} confirmations)")
            return detected_regime
        else:
            # Not yet confirmed, return last confirmed
            logger.debug(
                f"Regime change pending: {RegimeDetector._last_confirmed_regime} → {detected_regime} "
                f"({RegimeDetector._confirmation_count_current}/{self.confirmation_count} confirmations)"
            )
            return RegimeDetector._last_confirmed_regime
    
    def _cache_regime(self, regime_result: Dict):
        """Cache regime result"""
        self.cached_regime = regime_result
        self.cached_time = datetime.now()
