"""
Regime Detector for Market Regime Classification

Detects three market regimes:
- CONVEX: Low IV, compressed volatility, suitable for convex strategies
- INCOME: High IV, low trend, suitable for income strategies (Iron Condor)
- NEUTRAL: Transitional state, no new trades
"""

import pandas as pd
import numpy as np
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
from technical_indicators import get_historical_price_data

logger = logging.getLogger(__name__)


class RegimeDetector:
    """Detect market regime based on IV, ADX, ATR, and price range"""
    
    def __init__(self, cache_duration_minutes=15):
        """
        Initialize RegimeDetector
        
        Args:
            cache_duration_minutes: How long to cache regime before recalculating
        """
        self.cache_duration_minutes = cache_duration_minutes
        self.cached_regime = None
        self.cached_time = None
    
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
    
    def calculate_atr_percentile(self, current_atr: float, historical_atrs: List[float]) -> Optional[float]:
        """
        Calculate ATR percentile relative to historical ATR values
        
        Args:
            current_atr: Current ATR value
            historical_atrs: List of historical ATR values (last ~20 trading days)
        
        Returns:
            ATR percentile (0-100) or None if calculation fails
        """
        try:
            # Lowered minimum from 5 to 2 to work with limited data (17 days = 3 historical ATRs)
            if not historical_atrs or len(historical_atrs) < 2:
                logger.debug(f"Insufficient historical ATR data: {len(historical_atrs)} values (need at least 2)")
                return None
            
            if current_atr <= 0:
                return None
            
            # Calculate percentile
            sorted_atrs = sorted(historical_atrs)
            count_below = sum(1 for atr in sorted_atrs if atr < current_atr)
            percentile = (count_below / len(sorted_atrs)) * 100.0
            
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
            # Get historical price data (same source as ADX calculation)
            highs, lows, closes = get_historical_price_data(
                api, symbol_manager, 'Nifty 50', days=lookback_days
            )
            
            if not highs or not lows or not closes:
                # Fallback: create synthetic candles from spot price
                logger.debug("No historical data, using spot price as fallback")
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
            
            return candles
            
        except Exception as e:
            logger.debug(f"Error getting recent candles: {str(e)}")
            return []
    
    def detect_regime(self, market_state: Dict, recent_candles: Optional[List[Dict]] = None,
                     api=None, symbol_manager=None) -> Dict:
        """
        Detect current market regime
        
        Args:
            market_state: Market state dictionary with:
                - iv_percentile: float (0-100)
                - adx_14: float
                - spot_price: float
            recent_candles: Optional list of recent candle data
            api: ShoonyaApiPy instance (for fetching candles if not provided)
            symbol_manager: SymbolManager instance (for fetching candles if not provided)
        
        Returns:
            Dictionary with regime information:
            {
                "regime": "CONVEX" | "INCOME" | "NEUTRAL",
                "iv_percentile": float,
                "adx": float,
                "atr": float,
                "atr_percentile": float,
                "range_state": "COMPRESSED" | "NORMAL" | "EXPANDING"
            }
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
            spot_price = market_state.get('spot_price')
            
            if iv_percentile is None or adx_14 is None or spot_price is None:
                logger.warning("Missing required market_state fields for regime detection")
                regime_result = {
                    "regime": "NEUTRAL",
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
                    recent_candles = self.get_recent_candles(api, symbol_manager, spot_price)
                else:
                    recent_candles = []
            
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
                        # Calculate historical ATRs for percentile
                        historical_atrs = []
                        for i in range(14, len(highs)):
                            # Slice to get 15 elements (period + 1) for ATR(14) calculation
                            # When i=14, we need indices 0-14 (15 elements), so slice is [i-14:i+1]
                            period_highs = highs[i-14:i+1]
                            period_lows = lows[i-14:i+1]
                            period_closes = closes[i-14:i+1]
                            period_atr = self.calculate_atr(period_highs, period_lows, period_closes, period=14)
                            if period_atr:
                                historical_atrs.append(period_atr)
                        
                        if historical_atrs:
                            atr_percentile = self.calculate_atr_percentile(atr, historical_atrs)
            
            # Calculate recent price range
            last_range = self.calculate_recent_range(recent_candles, minutes=60)
            
            # Calculate rolling average range (for comparison)
            rolling_avg_range = None
            if recent_candles and len(recent_candles) >= 20:
                # Use last 20 candles to calculate average range
                recent_ranges = []
                for i in range(max(0, len(recent_candles) - 20), len(recent_candles)):
                    candle = recent_candles[i]
                    high = candle.get('high', candle.get('ltp', spot_price))
                    low = candle.get('low', candle.get('ltp', spot_price))
                    if high and low:
                        recent_ranges.append(high - low)
                
                if recent_ranges:
                    rolling_avg_range = np.mean(recent_ranges)
            
            # Determine range state
            if last_range and rolling_avg_range:
                if last_range < (rolling_avg_range * 0.6):
                    range_state = "COMPRESSED"
                elif last_range > (rolling_avg_range * 1.4):
                    range_state = "EXPANDING"
                else:
                    range_state = "NORMAL"
            
            # Regime detection rules (STRICT)
            regime = "NEUTRAL"
            
            # CONVEX regime
            if (iv_percentile is not None and iv_percentile < 40 and
                atr_percentile is not None and atr_percentile < 25 and
                last_range is not None and rolling_avg_range is not None and
                last_range < (rolling_avg_range * 0.6)):
                regime = "CONVEX"
                logger.info(f"Regime detected: CONVEX (IV={iv_percentile:.1f}%, ATR%={atr_percentile:.1f}%, Range=COMPRESSED)")
            
            # INCOME regime
            elif (iv_percentile is not None and iv_percentile > 60 and
                  adx_14 is not None and adx_14 < 20 and
                  atr_percentile is not None and atr_percentile < 50):  # ATR not expanding
                regime = "INCOME"
                logger.info(f"Regime detected: INCOME (IV={iv_percentile:.1f}%, ADX={adx_14:.1f}, ATR%={atr_percentile:.1f}%)")
            
            else:
                regime = "NEUTRAL"
                logger.debug(f"Regime: NEUTRAL (IV={iv_percentile:.1f}%, ADX={adx_14:.1f}, ATR%={atr_percentile or 'N/A'})")
            
            # Build result
            regime_result = {
                "regime": regime,
                "iv_percentile": iv_percentile,
                "adx": adx_14,
                "atr": atr,
                "atr_percentile": atr_percentile,
                "range_state": range_state
            }
            
            # Cache result
            self._cache_regime(regime_result)
            
            return regime_result
            
        except Exception as e:
            logger.error(f"Error detecting regime: {str(e)}", exc_info=True)
            return {
                "regime": "NEUTRAL",
                "iv_percentile": market_state.get('iv_percentile', 0),
                "adx": market_state.get('adx_14', 0),
                "atr": None,
                "atr_percentile": None,
                "range_state": "NORMAL"
            }
    
    def _cache_regime(self, regime_result: Dict):
        """Cache regime result"""
        self.cached_regime = regime_result
        self.cached_time = datetime.now()
