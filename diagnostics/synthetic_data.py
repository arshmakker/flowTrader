"""
Synthetic Market Data Generator

Generates deterministic synthetic OHLC and IV data for testing regime detection.
All data generation uses fixed random seeds for reproducibility.
"""

import numpy as np
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

# Fixed seed for deterministic output
SYNTHETIC_DATA_SEED = 42


class SyntheticMarketGenerator:
    """Generate synthetic market data for testing regime detection"""
    
    def __init__(self, seed: int = SYNTHETIC_DATA_SEED):
        """
        Initialize synthetic data generator
        
        Args:
            seed: Random seed for deterministic output (default: 42)
        """
        self.seed = seed
        np.random.seed(seed)
    
    def generate_sideways_market(
        self,
        length: int,
        base_price: float = 26000.0,
        volatility: float = 0.005,  # 0.5% daily volatility
        iv_level: float = 70.0  # High IV (70th percentile)
    ) -> Dict:
        """
        Generate sideways/range-bound market data (INCOME regime)
        
        Characteristics:
        - Flat price movement (mean-reverting)
        - Low ADX (< 15)
        - Stable ATR
        - High IV (> 65)
        
        Args:
            length: Number of candles to generate
            base_price: Starting price
            volatility: Daily volatility (as decimal)
            iv_level: IV percentile level (0-100)
        
        Returns:
            Dictionary with candles, iv_series, and expected_regime
        """
        np.random.seed(self.seed)
        
        candles = []
        iv_series = []
        current_price = base_price
        
        # Generate IV series (high, stable)
        # For high IV percentile (70), we need IV values that are higher than historical average
        # Generate historical IVs first (lower), then current IVs (higher)
        historical_base_iv = 15.0  # Lower historical IV
        current_base_iv = 25.0 + (iv_level / 100.0) * 5.0  # Higher current IV (25-30%)
        
        # Generate historical IVs (for percentile calculation)
        historical_ivs = [historical_base_iv + np.random.normal(0, 1.5) for _ in range(length * 2)]
        historical_ivs = [max(12.0, min(22.0, iv)) for iv in historical_ivs]
        
        # Generate current IVs (higher than historical)
        iv_series = [current_base_iv + np.random.normal(0, 1.0) for _ in range(length)]
        iv_series = [max(20.0, min(35.0, iv)) for iv in iv_series]
        
        # Store historical IVs for percentile calculation
        iv_series = {
            'current': iv_series,
            'historical': historical_ivs
        }
        
        # Generate sideways price movement (low ADX < 20)
        # For low ADX, we need alternating small moves with strong mean reversion
        for i in range(length):
            # Strong mean reversion to keep price near base
            drift = (base_price - current_price) * 0.25  # Very strong mean reversion
            
            # Alternating small moves (no sustained trend) to keep ADX low
            # Change direction frequently to prevent trend
            cycle = i % 7  # 7-day cycle
            if cycle < 3:
                direction = 1
            elif cycle < 5:
                direction = -1
            else:
                direction = 0
            
            # Very small directional component (keeps ADX low)
            small_trend = direction * 0.00005 * base_price  # Tiny trend
            random_move = np.random.normal(0, volatility * base_price * 0.8)  # Reduced volatility
            price_change = drift + small_trend + random_move
            
            open_price = current_price
            close_price = open_price + price_change
            
            # Generate high/low with consistent small range (sideways, not expanding)
            # Keep range consistent to avoid EXPANDING state
            range_size = volatility * base_price * 0.4  # Small, consistent range
            high = max(open_price, close_price) + np.random.uniform(0, range_size * 0.8)
            low = min(open_price, close_price) - np.random.uniform(0, range_size * 0.8)
            
            candles.append({
                'open': float(open_price),
                'high': float(high),
                'low': float(low),
                'close': float(close_price),
                'timestamp': datetime.now() - timedelta(days=length - i)
            })
            
            current_price = close_price
        
        return {
            "candles": candles,
            "iv_series": iv_series,
            "expected_regime": "INCOME"
        }
    
    def generate_trending_market(
        self,
        length: int,
        base_price: float = 26000.0,
        trend_strength: float = 0.002,  # 0.2% per day trend
        volatility: float = 0.008,  # Higher volatility in trends
        iv_level: float = 65.0  # High IV
    ) -> Dict:
        """
        Generate strong trending market data (NEUTRAL_PASSIVE regime)
        
        Characteristics:
        - Monotonic price movement
        - ADX > 35
        - Rising ATR
        - High IV (> 60)
        
        Args:
            length: Number of candles to generate
            base_price: Starting price
            trend_strength: Daily trend strength (as decimal)
            volatility: Daily volatility (as decimal)
            iv_level: IV percentile level (0-100)
        
        Returns:
            Dictionary with candles, iv_series, and expected_regime
        """
        np.random.seed(self.seed + 100)  # Different seed for different pattern
        
        candles = []
        iv_series = []
        current_price = base_price
        
        # Generate IV series (high)
        # For STRONG_TREND (NEUTRAL), we need high IV but ADX > 20 (so it doesn't match INCOME)
        # INCOME requires: IV > 60, ADX < 20, ATR% < 50
        # So for NEUTRAL with high ADX, we need IV > 60 but ADX will be > 20, so it won't match INCOME
        historical_base_iv = 15.0
        current_base_iv = 25.0 + (iv_level / 100.0) * 5.0
        
        historical_ivs = [historical_base_iv + np.random.normal(0, 1.5) for _ in range(length * 2)]
        historical_ivs = [max(12.0, min(22.0, iv)) for iv in historical_ivs]
        
        iv_series = [current_base_iv + np.random.normal(0, 1.5) for _ in range(length)]
        iv_series = [max(20.0, min(35.0, iv)) for iv in iv_series]
        
        iv_series = {
            'current': iv_series,
            'historical': historical_ivs
        }
        
        # Generate trending price movement (high ADX, expanding ATR)
        # For NEUTRAL with high ADX: need ADX > 20 (so it doesn't match INCOME which requires ADX < 20)
        # Also need ATR% >= 50 (expanding) so it doesn't match INCOME which requires ATR% < 50
        for i in range(length):
            # Strong directional move (sustained trend for high ADX)
            trend_move = trend_strength * base_price * (1.0 + i * 0.01)  # Increasing trend strength
            random_move = np.random.normal(0, volatility * base_price * 0.7)  # Less noise for cleaner trend
            price_change = trend_move + random_move
            
            open_price = current_price
            close_price = open_price + price_change
            
            # Generate high/low with progressively larger range (expanding ATR)
            # Start smaller, end larger to ensure ATR is expanding
            range_progress = i / length
            range_size = volatility * base_price * (0.8 + range_progress * 0.8)  # 0.8 -> 1.6 (expanding)
            high = max(open_price, close_price) + np.random.uniform(0, range_size)
            low = min(open_price, close_price) - np.random.uniform(0, range_size)
            
            candles.append({
                'open': float(open_price),
                'high': float(high),
                'low': float(low),
                'close': float(close_price),
                'timestamp': datetime.now() - timedelta(days=length - i)
            })
            
            current_price = close_price
        
        return {
            "candles": candles,
            "iv_series": iv_series,
            "expected_regime": "NEUTRAL"  # High ADX + High IV = NEUTRAL_PASSIVE
        }
    
    def generate_compression_then_breakout(
        self,
        compression_length: int,
        breakout_length: int,
        base_price: float = 26000.0,
        compression_vol: float = 0.002,  # Very low volatility during compression
        breakout_vol: float = 0.010,  # High volatility on breakout
        iv_start: float = 20.0,  # Low IV at start (for < 40 percentile)
        iv_end: float = 25.0  # Slightly higher IV after breakout
    ) -> Dict:
        """
        Generate compression then breakout market data (CONVEX regime)
        
        Characteristics:
        - Tight range during compression
        - ATR percentile < 25
        - IV < 35
        - Range compressed
        
        Args:
            compression_length: Number of candles in compression phase
            breakout_length: Number of candles in breakout phase (not used for CONVEX detection)
            base_price: Starting price
            compression_vol: Volatility during compression (low)
            breakout_vol: Volatility during breakout (high)
            iv_start: Starting IV level
            iv_end: Ending IV level
        
        Returns:
            Dictionary with candles, iv_series, and expected_regime
        """
        np.random.seed(self.seed + 200)  # Different seed
        
        candles = []
        iv_series = []
        current_price = base_price
        
        total_length = compression_length + breakout_length
        
        # Generate IV series (low, increasing slightly)
        # For CONVEX, we need low IV (< 40 percentile)
        # Generate high historical IVs (24-28%), then low current IVs (18-19%)
        historical_base_iv = 26.0  # Higher historical IV
        historical_ivs = [historical_base_iv + np.random.normal(0, 1.5) for _ in range((total_length + 15) * 3)]
        historical_ivs = [max(24.0, min(28.0, iv)) for iv in historical_ivs]
        
        iv_series = []
        for i in range(total_length + 15):  # Include pre-compression period
            if i < 15:
                # Pre-compression: slightly higher IV
                iv = 24.0 + np.random.normal(0, 0.8)
                iv = max(22.0, min(26.0, iv))
            elif i < 15 + compression_length:
                # Low IV during compression (18-19%) - must be < 40 percentile
                iv = iv_start + np.random.normal(0, 0.5)
                iv = max(17.5, min(19.5, iv))  # Keep it very low
            else:
                # Transition to slightly higher IV
                progress = (i - 15 - compression_length) / breakout_length
                iv = iv_start + (iv_end - iv_start) * progress + np.random.normal(0, 0.8)
                iv = max(18.0, min(25.0, iv))
            iv_series.append(iv)
        
        iv_series = {
            'current': iv_series,
            'historical': historical_ivs
        }
        
        # Generate compression phase (tight range, low ADX, low IV)
        # For CONVEX: IV < 40%, ATR < 25%, range COMPRESSED, low ADX
        # Strategy: Generate larger ranges first, then compress to ensure last_range < rolling_avg * 0.6
        
        # First, generate some pre-compression data with much higher ATR (for historical ATR calculation)
        # This ensures we have historical ATR values for percentile calculation
        # For ATR% < 25, we need current ATR (low) to be lower than at least 75% of historical ATRs
        # Strategy: Generate mix of very high ATRs (15 days) and medium ATRs (5 days)
        # So current low ATR is in bottom 25% (5 out of 20 historical values)
        pre_compression_candles = []
        pre_price = base_price
        # Generate pre-compression data with timestamps in the past (for historical ATR)
        for i in range(20):  # Generate 20 days of pre-compression data
            if i < 15:
                # First 15 days: very high volatility (for high historical ATR baseline)
                price_change = np.random.normal(0, compression_vol * base_price * 5.0)  # 5x volatility
                range_size = compression_vol * base_price * 4.5  # Very large range
            else:
                # Last 5 days: medium-high volatility (still higher than compression)
                price_change = np.random.normal(0, compression_vol * base_price * 2.5)  # 2.5x volatility
                range_size = compression_vol * base_price * 2.0  # Medium-high range
            
            open_price = pre_price
            close_price = open_price + price_change
            high = max(open_price, close_price) + np.random.uniform(0, range_size)
            low = min(open_price, close_price) - np.random.uniform(0, range_size)
            
            # Timestamps in the past (for historical data)
            pre_compression_candles.append({
                'open': float(open_price),
                'high': float(high),
                'low': float(low),
                'close': float(close_price),
                'timestamp': datetime.now() - timedelta(days=total_length + 20 - i)
            })
            pre_price = close_price
        
        # Now generate compression phase
        # For COMPRESSED range: last_range (last 60 min = ~4 candles) must be < rolling_avg_range (last 20) * 0.6
        # Strategy: 
        # - First (compression_length - 4) candles: larger ranges (for rolling average)
        # - Last 4 candles: very small ranges (compressed), with timestamps within last 60 minutes
        # - Set timestamps so last 4 are recent (within 60 min), rest are older
        
        now = datetime.now()
        candles = []
        for i in range(compression_length):
            # Very strong mean reversion (keeps ADX low)
            mean_reversion = (base_price - current_price) * 0.4
            
            # Alternating tiny moves (no trend) to keep ADX very low
            cycle = i % 6
            if cycle < 2:
                direction = 1
            elif cycle < 4:
                direction = -1
            else:
                direction = 0
            
            tiny_trend = direction * 0.00001 * base_price  # Extremely small
            
            # Very small random moves (for low ATR in compression phase)
            price_change = mean_reversion + tiny_trend + np.random.normal(0, compression_vol * base_price * 0.15)  # Very low volatility
            
            open_price = current_price
            close_price = open_price + price_change
            
            # Generate ranges: larger at start, smaller at end (to ensure compression)
            # For ATR% < 25, compression phase ATR must be much lower than historical
            # Historical has 2.0-5.0x volatility, so compression should be 0.1-0.2x
            # ATR(14) uses last 14 candles, so last 14 must all have very small ranges
            # For COMPRESSED range: last 4 candles (last_range) must be < 60% of rolling_avg (last 20)
            # Strategy: First 16 candles have much larger ranges, last 4 have very small ranges
            # Rolling avg will be high (from first 16), last_range will be low (from last 4)
            if i < compression_length - 4:
                # First part: much larger ranges (for rolling average baseline)
                # These will be in rolling_avg, making it high
                range_size = compression_vol * base_price * 3.5  # Much larger range for baseline
            else:
                # Last 4 candles: very small ranges (for last_range calculation and compression)
                # These must be < 60% of rolling average to be COMPRESSED
                # If rolling_avg is ~3.5x, then last_range should be < 2.1x, so use 0.05x
                range_size = compression_vol * base_price * 0.05  # Very small (compressed, for low last_range)
            
            high = max(open_price, close_price) + np.random.uniform(0, range_size * 0.3)
            low = min(open_price, close_price) - np.random.uniform(0, range_size * 0.3)
            
            # Set timestamps: last 4 candles within last 60 minutes, rest older
            if i < compression_length - 4:
                # Older candles (for rolling average, but not in last 60 min window)
                candle_timestamp = now - timedelta(hours=2 + (compression_length - 4 - i) * 0.25)  # 2-6 hours ago
            else:
                # Last 4 candles: within last 60 minutes (for last_range calculation)
                minutes_ago = (compression_length - 1 - i) * 15  # 0, 15, 30, 45 minutes ago
                candle_timestamp = now - timedelta(minutes=minutes_ago)
            
            candles.append({
                'open': float(open_price),
                'high': float(high),
                'low': float(low),
                'close': float(close_price),
                'timestamp': candle_timestamp
            })
            
            current_price = close_price
        
        # Combine pre-compression and compression candles (exclude breakout from ATR calculation)
        # Put pre-compression first (older), then compression (newer)
        # Breakout is generated separately and NOT included in candles for ATR calculation
        all_candles = pre_compression_candles + candles
        candles = all_candles  # Use combined candles for ATR calculation (without breakout)
        
        # Generate breakout phase (for completeness, but CONVEX is detected during compression)
        # Breakout candles are NOT added to candles list to avoid inflating ATR
        # They're only used for completeness of the scenario
        breakout_candles = []
        for i in range(breakout_length):
            # Larger price movements
            price_change = np.random.normal(0, breakout_vol * base_price)
            
            open_price = current_price
            close_price = open_price + price_change
            
            # Larger range
            range_size = breakout_vol * base_price * 1.0
            high = max(open_price, close_price) + np.random.uniform(0, range_size)
            low = min(open_price, close_price) - np.random.uniform(0, range_size)
            
            # Store breakout candles separately (not added to main candles list)
            breakout_candles.append({
                'open': float(open_price),
                'high': float(high),
                'low': float(low),
                'close': float(close_price),
                'timestamp': datetime.now() + timedelta(hours=i + 1)  # Future timestamps
            })
            
            current_price = close_price
        
        return {
            "candles": candles,
            "iv_series": iv_series,
            "expected_regime": "CONVEX"
        }
    
    def generate_transition_market(
        self,
        length: int,
        base_price: float = 26000.0,
        volatility: float = 0.006,
        iv_level: float = 50.0  # Mid-range IV
    ) -> Dict:
        """
        Generate transition/ambiguous market data (NEUTRAL regime)
        
        Characteristics:
        - ADX 20-25 (ambiguous)
        - ATR ambiguous
        - IV 45-55 (mid-range)
        
        Args:
            length: Number of candles to generate
            base_price: Starting price
            volatility: Daily volatility (as decimal)
            iv_level: IV percentile level (0-100)
        
        Returns:
            Dictionary with candles, iv_series, and expected_regime
        """
        np.random.seed(self.seed + 300)  # Different seed
        
        candles = []
        iv_series = []
        current_price = base_price
        
        # Generate IV series (mid-range, 45-55 percentile)
        # For NEUTRAL: IV should be 45-55 (not < 40 for CONVEX, not > 60 for INCOME)
        # Generate lower historical IVs, then current IVs in middle range
        historical_base_iv = 16.0  # Lower historical IV
        historical_ivs = [historical_base_iv + np.random.normal(0, 1.5) for _ in range(length * 3)]
        historical_ivs = [max(14.0, min(20.0, iv)) for iv in historical_ivs]
        
        # Current IVs should be higher to get 45-55 percentile
        current_base_iv = 23.0  # Higher current IV
        iv_series = [current_base_iv + np.random.normal(0, 1.2) for _ in range(length)]
        iv_series = [max(21.0, min(25.0, iv)) for iv in iv_series]
        
        iv_series = {
            'current': iv_series,
            'historical': historical_ivs
        }
        
        # Generate ambiguous price movement (not clearly trending or sideways)
        # For NEUTRAL: ADX should be 20-25, ATR% should be 30-50, Range should be NORMAL
        # Generate pre-transition data with moderate ATR (for historical ATR baseline)
        # For ATR% 30-50, we need current ATR to be in the middle of historical range
        pre_transition_candles = []
        pre_price = base_price
        for i in range(20):  # Generate 20 days of pre-transition data
            # Moderate volatility pre-transition (for historical ATR baseline)
            # Mix of higher and lower volatility to create a range
            if i < 10:
                # First half: higher volatility
                price_change = np.random.normal(0, volatility * base_price * 2.0)
                range_size = volatility * base_price * 1.8
            else:
                # Second half: lower volatility
                price_change = np.random.normal(0, volatility * base_price * 1.2)
                range_size = volatility * base_price * 1.0
            
            open_price = pre_price
            close_price = open_price + price_change
            high = max(open_price, close_price) + np.random.uniform(0, range_size)
            low = min(open_price, close_price) - np.random.uniform(0, range_size)
            
            pre_transition_candles.append({
                'open': float(open_price),
                'high': float(high),
                'low': float(low),
                'close': float(close_price),
                'timestamp': datetime.now() - timedelta(days=length + 20 - i)
            })
            pre_price = close_price
        
        # Generate transition phase with moderate ATR (30-50 percentile)
        # Current ATR should be in the middle of historical range (not too low, not too high)
        # Strategy: Generate historical ATRs with mix of high and low, then current ATR in middle
        for i in range(length):
            # Weak alternating trends (creates ADX 20-25)
            trend_direction = 1 if (i // 7) % 2 == 0 else -1  # Change direction every 7 candles
            trend_move = trend_direction * 0.0005 * base_price  # Very small trend
            # Use moderate volatility - higher than compression, lower than expansion
            random_move = np.random.normal(0, volatility * base_price * 1.4)  # Moderate-high volatility
            price_change = trend_move + random_move
            
            open_price = current_price
            close_price = open_price + price_change
            
            # Moderate, consistent range (NORMAL, not compressed, not expanding)
            # Keep range in middle-upper part of historical range (for ATR% 30-50)
            # Historical has mix of 1.0-1.8, so use 1.4-1.5 for current (middle-upper)
            range_size = volatility * base_price * 1.5  # Moderate-high range (middle-upper of historical)
            high = max(open_price, close_price) + np.random.uniform(0, range_size * 0.6)
            low = min(open_price, close_price) - np.random.uniform(0, range_size * 0.6)
            
            candles.append({
                'open': float(open_price),
                'high': float(high),
                'low': float(low),
                'close': float(close_price),
                'timestamp': datetime.now() - timedelta(days=length - i)
            })
            
            current_price = close_price
        
        # Combine pre-transition and transition candles
        all_candles = pre_transition_candles + candles
        candles = all_candles
        
        return {
            "candles": candles,
            "iv_series": iv_series,
            "expected_regime": "NEUTRAL"
        }
