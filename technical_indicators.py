"""
Technical Indicators Module

Provides calculations for:
- Implied Volatility (IV) from option prices using Black-Scholes
- IV Percentile from historical IV data
- ADX (Average Directional Index) from price data
"""

import pandas as pd
import numpy as np
import logging
from datetime import datetime, timedelta
from scipy.stats import norm
from scipy.optimize import brentq
import os
import json

logger = logging.getLogger('TechnicalIndicators')


# Risk-free rate (approximate for Indian market, can be updated)
RISK_FREE_RATE = 0.06  # 6% annual


def black_scholes_price(S, K, T, r, sigma, option_type='call'):
    """
    Calculate Black-Scholes option price.
    
    Args:
        S: Current stock/index price
        K: Strike price
        T: Time to expiration (in years)
        r: Risk-free rate (annual)
        sigma: Volatility (annual)
        option_type: 'call' or 'put'
    
    Returns:
        float: Option price
    """
    if T <= 0:
        return max(S - K, 0) if option_type == 'call' else max(K - S, 0)
    
    d1 = (np.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * np.sqrt(T))
    d2 = d1 - sigma * np.sqrt(T)
    
    if option_type == 'call':
        price = S * norm.cdf(d1) - K * np.exp(-r * T) * norm.cdf(d2)
    else:  # put
        price = K * np.exp(-r * T) * norm.cdf(-d2) - S * norm.cdf(-d1)
    
    return max(price, 0)


def calculate_implied_volatility(option_price, S, K, T, r, option_type='call', max_iter=100):
    """
    Calculate implied volatility from option price using Black-Scholes.
    
    Uses binary search to find IV that matches the option price.
    
    Args:
        option_price: Current option market price
        S: Current stock/index price
        K: Strike price
        T: Time to expiration (in years)
        r: Risk-free rate (annual)
        option_type: 'call' or 'put'
        max_iter: Maximum iterations for search
    
    Returns:
        float: Implied volatility (annual), or None if calculation fails
    """
    if option_price <= 0 or S <= 0 or K <= 0 or T <= 0:
        return None
    
    # Intrinsic value check
    if option_type == 'call':
        intrinsic = max(S - K, 0)
    else:
        intrinsic = max(K - S, 0)
    
    if option_price < intrinsic:
        logger.debug(f"Option price {option_price} < intrinsic {intrinsic}")
        return None
    
    # Bounds for IV search (0.01% to 500%)
    iv_low = 0.0001
    iv_high = 5.0
    
    try:
        def price_diff(sigma):
            bs_price = black_scholes_price(S, K, T, r, sigma, option_type)
            return bs_price - option_price
        
        # Use Brent's method for root finding
        iv = brentq(price_diff, iv_low, iv_high, maxiter=max_iter)
        return iv
        
    except (ValueError, RuntimeError) as e:
        logger.debug(f"IV calculation failed for K={K}, price={option_price}: {str(e)}")
        return None


def calculate_atm_iv(option_chain_df, spot_price, days_to_expiry, risk_free_rate=RISK_FREE_RATE):
    """
    Calculate ATM (At-The-Money) implied volatility from option chain.
    
    Args:
        option_chain_df: DataFrame with option chain data
        spot_price: Current spot price
        days_to_expiry: Days to expiration
        risk_free_rate: Risk-free rate (default: 6%)
    
    Returns:
        float: ATM IV (annual), or None if calculation fails
    """
    if option_chain_df.empty:
        return None
    
    # Convert days to years
    T = days_to_expiry / 365.0
    
    # Find ATM strikes (closest to spot)
    option_chain_df = option_chain_df.copy()
    option_chain_df['strike_diff'] = abs(option_chain_df['strike'] - spot_price)
    
    # Get ATM call and put
    atm_call = option_chain_df[
        (option_chain_df['option_type'] == 'CE') &
        (option_chain_df['strike_diff'] == option_chain_df[option_chain_df['option_type'] == 'CE']['strike_diff'].min())
    ]
    
    atm_put = option_chain_df[
        (option_chain_df['option_type'] == 'PE') &
        (option_chain_df['strike_diff'] == option_chain_df[option_chain_df['option_type'] == 'PE']['strike_diff'].min())
    ]
    
    ivs = []
    
    # Calculate IV for ATM call
    if not atm_call.empty:
        call_row = atm_call.iloc[0]
        call_price = call_row.get('mid_price', call_row.get('ltp', 0))
        if call_price > 0:
            call_iv = calculate_implied_volatility(
                call_price, spot_price, call_row['strike'], T, risk_free_rate, 'call'
            )
            if call_iv:
                ivs.append(call_iv)
    
    # Calculate IV for ATM put
    if not atm_put.empty:
        put_row = atm_put.iloc[0]
        put_price = put_row.get('mid_price', put_row.get('ltp', 0))
        if put_price > 0:
            put_iv = calculate_implied_volatility(
                put_price, spot_price, put_row['strike'], T, risk_free_rate, 'put'
            )
            if put_iv:
                ivs.append(put_iv)
    
    if not ivs:
        logger.warning("Could not calculate ATM IV from option chain")
        return None
    
    # Return average of call and put IV (or single value if only one available)
    atm_iv = np.mean(ivs) * 100  # Convert to percentage
    logger.debug(f"Calculated ATM IV: {atm_iv:.2f}%")
    return atm_iv


def load_historical_iv(spot_price, days_to_expiry, data_dir='market_data_iv'):
    """
    Load historical IV data for percentile calculation.
    
    Args:
        spot_price: Current spot price
        days_to_expiry: Days to expiration
        data_dir: Directory where historical IV data is stored
    
    Returns:
        list: Historical IV values, or empty list if no data
    """
    historical_ivs = []
    
    try:
        if not os.path.exists(data_dir):
            os.makedirs(data_dir)
            return []
        
        # Look for IV data files (stored by date)
        # Format: iv_data_YYYYMMDD.json
        today = datetime.now()
        for days_back in range(1, 90):  # Look back 90 days
            date = today - timedelta(days=days_back)
            filename = os.path.join(data_dir, f"iv_data_{date.strftime('%Y%m%d')}.json")
            
            if os.path.exists(filename):
                try:
                    with open(filename, 'r') as f:
                        data = json.load(f)
                        # Filter by similar DTE range (±2 days)
                        for entry in data:
                            if abs(entry.get('days_to_expiry', 0) - days_to_expiry) <= 2:
                                iv = entry.get('iv', None)
                                if iv:
                                    historical_ivs.append(iv)
                except Exception as e:
                    logger.debug(f"Error loading IV data from {filename}: {str(e)}")
                    continue
        
        logger.debug(f"Loaded {len(historical_ivs)} historical IV values")
        return historical_ivs
        
    except Exception as e:
        logger.error(f"Error loading historical IV: {str(e)}")
        return []


def save_iv_data(current_iv, spot_price, days_to_expiry, data_dir='market_data_iv'):
    """
    Save current IV data for historical percentile calculation.
    
    Args:
        current_iv: Current IV value
        spot_price: Current spot price
        days_to_expiry: Days to expiration
        data_dir: Directory to save IV data
    """
    try:
        os.makedirs(data_dir, exist_ok=True)
        
        filename = os.path.join(data_dir, f"iv_data_{datetime.now().strftime('%Y%m%d')}.json")
        
        # Load existing data for today
        data = []
        if os.path.exists(filename):
            try:
                with open(filename, 'r') as f:
                    data = json.load(f)
            except:
                data = []
        
        # Add new entry
        data.append({
            'timestamp': datetime.now().isoformat(),
            'iv': current_iv,
            'spot_price': spot_price,
            'days_to_expiry': days_to_expiry
        })
        
        # Save
        with open(filename, 'w') as f:
            json.dump(data, f, indent=2)
        
        logger.debug(f"Saved IV data: {current_iv:.2f}%")
        
    except Exception as e:
        logger.error(f"Error saving IV data: {str(e)}")


def calculate_iv_percentile(option_chain_df, spot_price, days_to_expiry, data_dir='market_data_iv'):
    """
    Calculate IV percentile from current IV and historical data.
    
    Args:
        option_chain_df: DataFrame with option chain data
        spot_price: Current spot price
        days_to_expiry: Days to expiration
        data_dir: Directory for historical IV data
    
    Returns:
        float: IV percentile (0-100), or None if calculation fails
    """
    try:
        # Calculate current ATM IV
        current_iv = calculate_atm_iv(option_chain_df, spot_price, days_to_expiry)
        if current_iv is None:
            logger.warning("Could not calculate current IV")
            return None
        
        # Save current IV for future percentile calculations
        save_iv_data(current_iv, spot_price, days_to_expiry, data_dir)
        
        # Load historical IV data
        historical_ivs = load_historical_iv(spot_price, days_to_expiry, data_dir)
        
        # If we don't have enough historical data, use a simple estimate
        if len(historical_ivs) < 20:
            logger.warning(f"Only {len(historical_ivs)} historical IV values, using estimate")
            # Use a simple heuristic: if current IV is in typical range (15-30%), assume 50-70 percentile
            if 15 <= current_iv <= 30:
                return 65.0  # Default to middle range
            elif current_iv < 15:
                return 30.0  # Low IV
            else:
                return 80.0  # High IV
        
        # Calculate percentile
        historical_ivs = np.array(historical_ivs)
        percentile = (np.sum(historical_ivs < current_iv) / len(historical_ivs)) * 100
        
        logger.info(f"IV Percentile: {percentile:.1f}% (Current IV: {current_iv:.2f}%, Historical samples: {len(historical_ivs)})")
        
        return percentile
        
    except Exception as e:
        logger.error(f"Error calculating IV percentile: {str(e)}", exc_info=True)
        return None


def calculate_adx(high_prices, low_prices, close_prices, period=14):
    """
    Calculate ADX (Average Directional Index) from price data.
    
    Args:
        high_prices: Series of high prices
        low_prices: Series of low prices
        close_prices: Series of close prices
        period: Period for ADX calculation (default: 14)
    
    Returns:
        float: ADX value, or None if calculation fails
    """
    try:
        if len(high_prices) < period + 1:
            logger.warning(f"Not enough data for ADX calculation (need {period + 1}, have {len(high_prices)})")
            return None
        
        # Convert to numpy arrays
        high = np.array(high_prices)
        low = np.array(low_prices)
        close = np.array(close_prices)
        
        # Calculate True Range (TR)
        tr1 = high - low
        tr2 = np.abs(high - np.roll(close, 1))
        tr3 = np.abs(low - np.roll(close, 1))
        tr = np.maximum(tr1, np.maximum(tr2, tr3))
        tr[0] = tr1[0]  # First value is just high - low
        
        # Calculate Directional Movement
        up_move = high - np.roll(high, 1)
        down_move = np.roll(low, 1) - low
        
        # +DM and -DM
        plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0)
        minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0)
        
        # Smooth TR, +DM, -DM using Wilder's smoothing
        atr = tr.copy()
        plus_di_smooth = plus_dm.copy()
        minus_di_smooth = minus_dm.copy()
        
        for i in range(1, len(tr)):
            atr[i] = (atr[i-1] * (period - 1) + tr[i]) / period
            plus_di_smooth[i] = (plus_di_smooth[i-1] * (period - 1) + plus_dm[i]) / period
            minus_di_smooth[i] = (minus_di_smooth[i-1] * (period - 1) + minus_dm[i]) / period
        
        # Calculate +DI and -DI
        plus_di = 100 * (plus_di_smooth / atr)
        minus_di = 100 * (minus_di_smooth / atr)
        
        # Calculate DX
        dx = 100 * np.abs(plus_di - minus_di) / (plus_di + minus_di)
        dx = np.where(np.isnan(dx) | np.isinf(dx), 0, dx)
        
        # Calculate ADX (smoothed DX)
        adx = dx.copy()
        for i in range(period, len(dx)):
            adx[i] = (adx[i-1] * (period - 1) + dx[i]) / period
        
        # Return the latest ADX value
        adx_value = adx[-1]
        logger.debug(f"Calculated ADX({period}): {adx_value:.2f}")
        
        return adx_value
        
    except Exception as e:
        logger.error(f"Error calculating ADX: {str(e)}", exc_info=True)
        return None


def get_historical_price_data(api, symbol_manager, symbol_name, days=30):
    """
    Get historical price data for ADX calculation.
    
    Args:
        api: ShoonyaApiPy instance
        symbol_manager: SymbolManager instance
        symbol_name: Symbol name (e.g., 'Nifty 50')
        days: Number of days of historical data to fetch
    
    Returns:
        tuple: (high_prices, low_prices, close_prices) or (None, None, None) if error
    """
    try:
        # Get symbol token
        symbol_info = symbol_manager.get_token_info(symbol_name, exchange='NSE')
        if not symbol_info:
            logger.error(f"Could not find token for {symbol_name}")
            return None, None, None
        
        token = symbol_info['token']
        
        # Calculate date range
        end_date = datetime.now()
        start_date = end_date - timedelta(days=days)
        
        # Try to fetch daily price series using token
        # Use get_time_price_series with daily interval (1440 minutes = 1 day)
        try:
            price_data = api.get_time_price_series(
                exchange='NSE',
                token=token,
                starttime=int(start_date.timestamp()),
                endtime=int(end_date.timestamp()),
                interval=1440  # Daily interval (1440 minutes = 1 day)
            )
        except:
            # Fallback: try get_daily_price_series if available
            try:
                tradingsymbol = symbol_info.get('tradingsymbol', symbol_name)
                price_data = api.get_daily_price_series(
                    exchange='NSE',
                    tradingsymbol=tradingsymbol,
                    startdate=int(start_date.timestamp()),
                    enddate=int(end_date.timestamp())
                )
            except Exception as e:
                logger.warning(f"Error fetching price data: {str(e)}")
                price_data = None
        
        if not price_data:
            logger.warning(f"No historical price data returned for {symbol_name}")
            return None, None, None
        
        # Parse price data - handle both formats
        highs = []
        lows = []
        closes = []
        
        # Check if it's a list or dict with 'values'
        if isinstance(price_data, list):
            entries = price_data
        elif isinstance(price_data, dict) and 'values' in price_data:
            entries = price_data['values']
        else:
            entries = [price_data] if price_data else []
        
        for entry in entries:
            try:
                # Try different field names
                high = float(entry.get('h', entry.get('high', entry.get('High', 0))))
                low = float(entry.get('l', entry.get('low', entry.get('Low', 0))))
                close = float(entry.get('c', entry.get('close', entry.get('Close', entry.get('intc', 0)))))
                
                if high > 0 and low > 0 and close > 0:
                    highs.append(high)
                    lows.append(low)
                    closes.append(close)
            except (ValueError, TypeError, KeyError) as e:
                logger.debug(f"Error parsing price entry: {str(e)}")
                continue
        
        if len(highs) < 15:  # Need at least 15 days for ADX(14)
            logger.warning(f"Not enough historical data: {len(highs)} days")
            return None, None, None
        
        logger.debug(f"Fetched {len(highs)} days of historical data for {symbol_name}")
        
        return highs, lows, closes
        
    except Exception as e:
        logger.error(f"Error getting historical price data: {str(e)}", exc_info=True)
        return None, None, None

