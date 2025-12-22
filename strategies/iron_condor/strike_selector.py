"""
Strike selection logic for Iron Condor strategy
"""

import pandas as pd
import numpy as np
import logging
from typing import Dict, Optional
from .config import (
    SHORT_CALL_DELTA_MIN,
    SHORT_CALL_DELTA_MAX,
    SHORT_PUT_DELTA_MIN,
    SHORT_PUT_DELTA_MAX,
    SPOT_DISTANCE_MIN_PCT,
    SPOT_DISTANCE_MAX_PCT,
    WING_WIDTH_MIN,
    WING_WIDTH_MAX
)

logger = logging.getLogger(__name__)


def select_strikes(option_chain: pd.DataFrame, spot_price: float) -> Dict:
    """
    Select strikes for Iron Condor strategy.
    
    Logic:
    1. Select short call with delta ∈ [0.15, 0.20]
    2. Select short put with delta ∈ [-0.20, -0.15]
    3. If delta missing, use distance from spot = 0.8%–1.2%
    4. Hedges: Wing width = 100–150 points, pick nearest liquid strikes
    
    Args:
        option_chain: DataFrame with columns:
            - strike: float
            - option_type: str ('CE' or 'PE')
            - ltp: float (last traded price)
            - bid: float
            - ask: float
            - delta: float (optional)
            - oi: int (open interest, for liquidity)
            - volume: int (for liquidity)
        spot_price: Current spot price of underlying
    
    Returns:
        Dictionary with keys:
            - short_call: dict with strike, option_type, price, etc.
            - short_put: dict with strike, option_type, price, etc.
            - long_call: dict with strike, option_type, price, etc.
            - long_put: dict with strike, option_type, price, etc.
    """
    try:
        if option_chain.empty:
            raise ValueError("Empty option chain provided")
        
        if spot_price <= 0:
            raise ValueError(f"Invalid spot price: {spot_price}")
        
        # Separate calls and puts
        calls = option_chain[option_chain['option_type'].str.upper() == 'CE'].copy()
        puts = option_chain[option_chain['option_type'].str.upper() == 'PE'].copy()
        
        if calls.empty or puts.empty:
            raise ValueError("Missing calls or puts in option chain")
        
        # Sort by strike
        calls = calls.sort_values('strike')
        puts = puts.sort_values('strike', ascending=False)
        
        # Select short call
        short_call = _select_short_call(calls, spot_price)
        if short_call is None:
            raise ValueError("Could not select short call strike")
        
        # Select short put
        short_put = _select_short_put(puts, spot_price)
        if short_put is None:
            raise ValueError("Could not select short put strike")
        
        # Select long call hedge (wing)
        long_call = _select_long_call_hedge(calls, short_call['strike'], spot_price)
        if long_call is None:
            raise ValueError("Could not select long call hedge")
        
        # Select long put hedge (wing)
        long_put = _select_long_put_hedge(puts, short_put['strike'], spot_price)
        if long_put is None:
            raise ValueError("Could not select long put hedge")
        
        return {
            "short_call": short_call,
            "short_put": short_put,
            "long_call": long_call,
            "long_put": long_put
        }
        
    except Exception as e:
        logger.error(f"Error in strike selection: {str(e)}")
        raise


def _select_short_call(calls: pd.DataFrame, spot_price: float) -> Optional[Dict]:
    """Select short call strike based on delta or distance from spot"""
    # Filter calls above spot
    calls_above = calls[calls['strike'] > spot_price].copy()
    
    if calls_above.empty:
        return None
    
    # Try delta-based selection first
    if 'delta' in calls_above.columns:
        calls_above['delta'] = pd.to_numeric(calls_above['delta'], errors='coerce')
        valid_delta = calls_above[
            (calls_above['delta'] >= SHORT_CALL_DELTA_MIN) &
            (calls_above['delta'] <= SHORT_CALL_DELTA_MAX)
        ]
        
        if not valid_delta.empty:
            # Select most liquid strike in delta range
            valid_delta['liquidity'] = (
                valid_delta.get('oi', 0).fillna(0) +
                valid_delta.get('volume', 0).fillna(0)
            )
            best = valid_delta.nlargest(1, 'liquidity').iloc[0]
            return _format_strike_dict(best, 'CE')
    
    # Fallback: distance from spot
    calls_above['distance_pct'] = (
        (calls_above['strike'] - spot_price) / spot_price * 100
    )
    valid_distance = calls_above[
        (calls_above['distance_pct'] >= SPOT_DISTANCE_MIN_PCT) &
        (calls_above['distance_pct'] <= SPOT_DISTANCE_MAX_PCT)
    ]
    
    if not valid_distance.empty:
        valid_distance['liquidity'] = (
            valid_distance.get('oi', 0).fillna(0) +
            valid_distance.get('volume', 0).fillna(0)
        )
        best = valid_distance.nlargest(1, 'liquidity').iloc[0]
        return _format_strike_dict(best, 'CE')
    
    return None


def _select_short_put(puts: pd.DataFrame, spot_price: float) -> Optional[Dict]:
    """Select short put strike based on delta or distance from spot"""
    # Filter puts below spot
    puts_below = puts[puts['strike'] < spot_price].copy()
    
    if puts_below.empty:
        return None
    
    # Try delta-based selection first
    if 'delta' in puts_below.columns:
        puts_below = puts_below.copy()
        puts_below['delta'] = pd.to_numeric(puts_below['delta'], errors='coerce')
        valid_delta = puts_below[
            (puts_below['delta'] >= SHORT_PUT_DELTA_MIN) &
            (puts_below['delta'] <= SHORT_PUT_DELTA_MAX)
        ]
        
        if not valid_delta.empty:
            # Select most liquid strike in delta range
            valid_delta = valid_delta.copy()
            valid_delta['liquidity'] = (
                valid_delta.get('oi', 0).fillna(0) +
                valid_delta.get('volume', 0).fillna(0)
            )
            best = valid_delta.nlargest(1, 'liquidity').iloc[0]
            return _format_strike_dict(best, 'PE')
    
    # Fallback: distance from spot
    puts_below = puts_below.copy()
    puts_below['distance_pct'] = (
        (spot_price - puts_below['strike']) / spot_price * 100
    )
    valid_distance = puts_below[
        (puts_below['distance_pct'] >= SPOT_DISTANCE_MIN_PCT) &
        (puts_below['distance_pct'] <= SPOT_DISTANCE_MAX_PCT)
    ]
    
    if not valid_distance.empty:
        valid_distance = valid_distance.copy()
        valid_distance['liquidity'] = (
            valid_distance.get('oi', 0).fillna(0) +
            valid_distance.get('volume', 0).fillna(0)
        )
        best = valid_distance.nlargest(1, 'liquidity').iloc[0]
        return _format_strike_dict(best, 'PE')
    
    return None


def _select_long_call_hedge(
    calls: pd.DataFrame,
    short_call_strike: float,
    spot_price: float
) -> Optional[Dict]:
    """Select long call hedge with wing width 100-150 points"""
    # Filter calls above short call strike
    hedges = calls[calls['strike'] > short_call_strike].copy()
    
    if hedges.empty:
        return None
    
    # Calculate distance from short call
    hedges = hedges.copy()
    hedges['wing_width'] = hedges['strike'] - short_call_strike
    
    # Filter by wing width range
    valid_hedges = hedges[
        (hedges['wing_width'] >= WING_WIDTH_MIN) &
        (hedges['wing_width'] <= WING_WIDTH_MAX)
    ]
    
    if valid_hedges.empty:
        # If no exact match, pick nearest
        hedges['wing_width'] = hedges['strike'] - short_call_strike
        valid_hedges = hedges[
            hedges['wing_width'] >= WING_WIDTH_MIN
        ]
        if valid_hedges.empty:
            return None
        # Pick closest to min wing width
        valid_hedges = valid_hedges.nsmallest(1, 'wing_width')
    
    # Select most liquid
    valid_hedges = valid_hedges.copy()
    valid_hedges['liquidity'] = (
        valid_hedges.get('oi', 0).fillna(0) +
        valid_hedges.get('volume', 0).fillna(0)
    )
    best = valid_hedges.nlargest(1, 'liquidity').iloc[0]
    return _format_strike_dict(best, 'CE')


def _select_long_put_hedge(
    puts: pd.DataFrame,
    short_put_strike: float,
    spot_price: float
) -> Optional[Dict]:
    """Select long put hedge with wing width 100-150 points"""
    # Filter puts below short put strike
    hedges = puts[puts['strike'] < short_put_strike].copy()
    
    if hedges.empty:
        return None
    
    # Calculate distance from short put
    hedges = hedges.copy()
    hedges['wing_width'] = short_put_strike - hedges['strike']
    
    # Filter by wing width range
    valid_hedges = hedges[
        (hedges['wing_width'] >= WING_WIDTH_MIN) &
        (hedges['wing_width'] <= WING_WIDTH_MAX)
    ]
    
    if valid_hedges.empty:
        # If no exact match, pick nearest
        hedges['wing_width'] = short_put_strike - hedges['strike']
        valid_hedges = hedges[
            hedges['wing_width'] >= WING_WIDTH_MIN
        ]
        if valid_hedges.empty:
            return None
        # Pick closest to min wing width
        valid_hedges = valid_hedges.nsmallest(1, 'wing_width')
    
    # Select most liquid
    valid_hedges = valid_hedges.copy()
    valid_hedges['liquidity'] = (
        valid_hedges.get('oi', 0).fillna(0) +
        valid_hedges.get('volume', 0).fillna(0)
    )
    best = valid_hedges.nlargest(1, 'liquidity').iloc[0]
    return _format_strike_dict(best, 'PE')


def _format_strike_dict(row: pd.Series, option_type: str) -> Dict:
    """Format a DataFrame row into a strike dictionary"""
    return {
        "strike": float(row['strike']),
        "option_type": option_type,
        "ltp": float(row.get('ltp', 0)),
        "bid": float(row.get('bid', 0)),
        "ask": float(row.get('ask', 0)),
        "delta": float(row.get('delta', 0)) if 'delta' in row else None,
        "oi": int(row.get('oi', 0)),
        "volume": int(row.get('volume', 0)),
        # Use mid price for calculations if available
        "mid_price": float((row.get('bid', 0) + row.get('ask', 0)) / 2) if row.get('bid') and row.get('ask') else float(row.get('ltp', 0))
    }

