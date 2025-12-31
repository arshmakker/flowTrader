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


def calculate_probability_of_profit(spot_price: float, short_call_strike: float, 
                                     short_put_strike: float, iv: float, 
                                     days_to_expiry: int, risk_free_rate: float = RISK_FREE_RATE) -> float:
    """
    Calculate Probability of Profit (PoP) for Iron Condor strategy.
    
    PoP is the probability that the underlying price stays between the short strikes
    at expiration, resulting in maximum profit.
    
    Uses Black-Scholes normal distribution approach:
    PoP = N(d2_put) - N(d2_call)
    where N is cumulative normal distribution and d2 is from Black-Scholes formula.
    
    Args:
        spot_price: Current spot price of underlying
        short_call_strike: Short call strike price
        short_put_strike: Short put strike price
        iv: Implied volatility (annual, as decimal, e.g., 0.15 for 15%)
        days_to_expiry: Days to expiration
        risk_free_rate: Risk-free rate (annual, default 6%)
    
    Returns:
        float: Probability of profit as percentage (0-100)
    """
    try:
        if days_to_expiry <= 0:
            # At expiration, PoP is 1 if price is between strikes, 0 otherwise
            if short_put_strike <= spot_price <= short_call_strike:
                return 100.0
            else:
                return 0.0
        
        if iv <= 0:
            logger.warning("IV is zero or negative, cannot calculate PoP accurately")
            return 50.0  # Default to 50% if IV unavailable
        
        # Convert days to years
        T = days_to_expiry / 365.0
        
        # Calculate d2 for both strikes using Black-Scholes formula
        # d2 = (ln(S/K) + (r - 0.5*sigma^2)*T) / (sigma * sqrt(T))
        
        # For short put strike (lower bound)
        if short_put_strike > 0:
            d2_put = (np.log(spot_price / short_put_strike) + 
                      (risk_free_rate - 0.5 * iv ** 2) * T) / (iv * np.sqrt(T))
            prob_below_put = norm.cdf(d2_put)  # Probability price < put strike
        else:
            prob_below_put = 0.0
        
        # For short call strike (upper bound)
        if short_call_strike > 0:
            d2_call = (np.log(spot_price / short_call_strike) + 
                       (risk_free_rate - 0.5 * iv ** 2) * T) / (iv * np.sqrt(T))
            prob_below_call = norm.cdf(d2_call)  # Probability price < call strike
        else:
            prob_below_call = 1.0
        
        # PoP = Probability(put_strike < price < call_strike)
        # = P(price < call_strike) - P(price < put_strike)
        pop = prob_below_call - prob_below_put
        
        # Ensure PoP is between 0 and 1
        pop = max(0.0, min(1.0, pop))
        
        # Convert to percentage
        return pop * 100.0
        
    except Exception as e:
        logger.error(f"Error calculating PoP: {str(e)}")
        return 50.0  # Default to 50% on error


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


def load_historical_iv(spot_price, days_to_expiry, data_dir='market_data_iv', exclude_current_timestamp=None):
    """
    Load historical IV data for percentile calculation.
    Includes today's earlier calculations but excludes the current one to avoid circular logic.
    
    Args:
        spot_price: Current spot price
        days_to_expiry: Days to expiration
        data_dir: Directory where historical IV data is stored
        exclude_current_timestamp: ISO format timestamp to exclude (e.g., '2025-12-23T13:53:22.123456')
    
    Returns:
        list: Historical IV values, or empty list if no data
    """
    historical_ivs = []
    
    try:
        if not os.path.exists(data_dir):
            os.makedirs(data_dir)
            return []
        
        # Parse exclude timestamp if provided
        exclude_time = None
        if exclude_current_timestamp:
            try:
                exclude_time = datetime.fromisoformat(exclude_current_timestamp)
            except (ValueError, AttributeError):
                logger.debug(f"Could not parse exclude timestamp: {exclude_current_timestamp}")
        
        # Look for IV data files (stored by date)
        # Format: iv_data_YYYYMMDD.json
        today = datetime.now()
        today_date_str = today.strftime('%Y%m%d')
        
        # First, load today's data (if exists) - include earlier calculations but exclude current
        today_filename = os.path.join(data_dir, f"iv_data_{today_date_str}.json")
        if os.path.exists(today_filename):
            try:
                with open(today_filename, 'r') as f:
                    data = json.load(f)
                    # Filter by similar DTE range (±2 days) and exclude current timestamp
                    for entry in data:
                        # Check DTE match
                        if abs(entry.get('days_to_expiry', 0) - days_to_expiry) <= 2:
                            # Exclude current calculation if timestamp matches (within 5 seconds)
                            entry_timestamp = entry.get('timestamp')
                            if entry_timestamp and exclude_time:
                                try:
                                    entry_time = datetime.fromisoformat(entry_timestamp)
                                    time_diff = abs((entry_time - exclude_time).total_seconds())
                                    if time_diff < 5:  # Exclude if within 5 seconds
                                        continue
                                except (ValueError, AttributeError):
                                    pass  # If can't parse, include it
                            
                            iv = entry.get('iv', None)
                            if iv:
                                historical_ivs.append(iv)
            except Exception as e:
                logger.debug(f"Error loading today's IV data from {today_filename}: {str(e)}")
        
        # Then load historical data from previous days
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
        
        logger.debug(f"Loaded {len(historical_ivs)} historical IV values (including today's earlier calculations)")
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
        
        # Get current timestamp before saving (to exclude it from historical data)
        current_timestamp = datetime.now().isoformat()
        
        # Save current IV for future percentile calculations
        save_iv_data(current_iv, spot_price, days_to_expiry, data_dir)
        
        # Load historical IV data (exclude current calculation to avoid circular logic)
        historical_ivs = load_historical_iv(spot_price, days_to_expiry, data_dir, exclude_current_timestamp=current_timestamp)
        
        # If we don't have enough historical data, use intelligent estimate based on current IV
        if len(historical_ivs) < 20:
            logger.info(f"Only {len(historical_ivs)} historical IV values available (need 20+), using current IV as proxy. This is normal for new systems.")
            
            # Use current IV to estimate percentile
            # This allows trading when IV is actually high, even without historical data
            # Typical NIFTY IV range: 10-40%
            if current_iv < 12:
                # Very low IV → Low percentile (below threshold, won't trade - safe)
                estimated_percentile = 40.0
                logger.info(f"Estimated IV percentile: {estimated_percentile:.1f}% (current IV: {current_iv:.2f}% is very low)")
            elif current_iv < 18:
                # Low-normal IV → Moderate percentile (at threshold, allows trading but cautious)
                estimated_percentile = 55.0  # Just at threshold
                logger.info(f"Estimated IV percentile: {estimated_percentile:.1f}% (current IV: {current_iv:.2f}% is low-normal)")
            elif current_iv < 25:
                # Normal-high IV → High percentile (good for selling options)
                estimated_percentile = 70.0  # Well above threshold
                logger.info(f"Estimated IV percentile: {estimated_percentile:.1f}% (current IV: {current_iv:.2f}% is normal-high)")
            else:
                # Very high IV → Very high percentile (excellent for selling options)
                estimated_percentile = 85.0  # At upper limit
                logger.info(f"Estimated IV percentile: {estimated_percentile:.1f}% (current IV: {current_iv:.2f}% is very high)")
            
            return estimated_percentile
        
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


def get_historical_price_data_from_stored(api, symbol_manager, symbol_name, days=30):
    """
    Get historical price data from stored data collector files.
    Uses NIFTY futures data as a proxy for NIFTY index.
    
    Args:
        api: ShoonyaApiPy instance (not used, kept for compatibility)
        symbol_manager: SymbolManager instance
        symbol_name: Symbol name (e.g., 'Nifty 50')
        days: Number of days of historical data to fetch
    
    Returns:
        tuple: (high_prices, low_prices, close_prices) or (None, None, None) if error
    """
    try:
        import glob
        from pathlib import Path
        
        # Use NIFTY futures as proxy for NIFTY index
        # Find the most recent NIFTY futures contract
        if symbol_manager.nse_fo is None:
            return None, None, None
        
        # Get active NIFTY futures
        nifty_futures = symbol_manager.nse_fo[
            (symbol_manager.nse_fo['instrument'] == 'FUTIDX') &
            (symbol_manager.nse_fo['symbol'] == 'NIFTY') &
            (symbol_manager.nse_fo['optiontype'] == 'XX')
        ].copy()
        
        if nifty_futures.empty:
            logger.debug("No NIFTY futures found in symbol manager")
            return None, None, None
        
        # Get the nearest expiry future
        nifty_futures['expiry_date'] = pd.to_datetime(nifty_futures['expiry'], format='%d-%b-%Y', errors='coerce')
        today = datetime.now().date()
        active_futures = nifty_futures[nifty_futures['expiry_date'].dt.date >= today]
        
        if active_futures.empty:
            logger.debug("No active NIFTY futures found")
            return None, None, None
        
        # Get the nearest expiry
        nearest_future = active_futures.sort_values('expiry_date').iloc[0]
        future_symbol = nearest_future['tradingsymbol']
        
        # Look for stored data files
        base_dir = Path('.')
        data_dirs = sorted([d for d in base_dir.glob('market_data_*') if d.is_dir()], reverse=True)
        
        if not data_dirs:
            logger.debug("No stored data directories found")
            return None, None, None
        
        # Collect data from multiple days
        # Note: We look for ANY NIFTY future file in each directory, not just the current contract,
        # because historical data might have been collected with different future contracts
        all_data = []
        for data_dir in data_dirs[:days]:  # Check up to 'days' number of directories
            futures_dir = data_dir / 'raw_data' / 'futures'
            if not futures_dir.exists():
                continue
            
            # Look for ANY NIFTY future file in this directory (not just current contract)
            # Historical data might have been collected with different future contracts
            nifty_futures_files = list(futures_dir.glob('NIFTY*FUT_*.csv'))
            if not nifty_futures_files:
                # Also try without FUT suffix (in case naming is different)
                nifty_futures_files = list(futures_dir.glob('NIFTY*_*.csv'))
            
            # Use the first NIFTY future file found for this date
            if nifty_futures_files:
                futures_file = nifty_futures_files[0]
                try:
                    df = pd.read_csv(futures_file)
                    if 'timestamp' in df.columns and 'ltp' in df.columns:
                        df['timestamp'] = pd.to_datetime(df['timestamp'])
                        all_data.append(df)
                        logger.debug(f"Loaded data from {futures_file.name} ({len(df)} rows)")
                except Exception as e:
                    logger.debug(f"Error reading {futures_file}: {str(e)}")
                    continue
        
        if not all_data:
            logger.debug("No stored futures data found")
            return None, None, None
        
        # Combine all data
        combined_df = pd.concat(all_data, ignore_index=True)
        
        # Group by date and calculate daily OHLC
        combined_df['date'] = combined_df['timestamp'].dt.date
        daily_bars = combined_df.groupby('date').agg({
            'ltp': ['max', 'min', 'last']  # High, Low, Close
        }).reset_index()
        
        daily_bars.columns = ['date', 'high', 'low', 'close']
        daily_bars = daily_bars.sort_values('date')
        
        if len(daily_bars) < 15:
            logger.info(f"Only {len(daily_bars)} days of stored data available (need at least 15 for ADX). System will accumulate more data over time.")
            return None, None, None
        
        highs = daily_bars['high'].tolist()
        lows = daily_bars['low'].tolist()
        closes = daily_bars['close'].tolist()
        
        logger.info(f"Using {len(daily_bars)} days of stored NIFTY futures data for ADX calculation")
        return highs, lows, closes
        
    except Exception as e:
        logger.debug(f"Error getting historical data from stored files: {str(e)}")
        return None, None, None


def get_historical_price_data(api, symbol_manager, symbol_name, days=30):
    """
    Get historical price data for ADX calculation.
    First tries stored data from data collector, then falls back to API.
    
    Args:
        api: ShoonyaApiPy instance
        symbol_manager: SymbolManager instance
        symbol_name: Symbol name (e.g., 'Nifty 50')
        days: Number of days of historical data to fetch
    
    Returns:
        tuple: (high_prices, low_prices, close_prices) or (None, None, None) if error
    """
    # Method 1: Try stored data first (preferred)
    highs, lows, closes = get_historical_price_data_from_stored(
        api, symbol_manager, symbol_name, days
    )
    if highs is not None and lows is not None and closes is not None:
        return highs, lows, closes
    
    # Method 2: Fallback to API (Note: Shoonya API does not support historical data)
    # This is expected to fail for new systems or when API doesn't support historical data
    logger.debug(f"Stored data insufficient, attempting API (expected to fail if API doesn't support historical data)")
    try:
        # Get symbol token
        symbol_info = symbol_manager.get_token_info(symbol_name, exchange='NSE')
        if not symbol_info:
            logger.debug(f"Could not find token for {symbol_name}")
            return None, None, None
        
        token = symbol_info['token']
        
        # Calculate date range
        end_date = datetime.now()
        start_date = end_date - timedelta(days=days)
        
        # Try to fetch daily price series using token
        price_data = None
        error_messages = []
        
        # Method 1: Try get_time_price_series
        try:
            price_data = api.get_time_price_series(
                exchange='NSE',
                token=token,
                starttime=int(start_date.timestamp()),
                endtime=int(end_date.timestamp()),
                interval=1440  # Daily interval (1440 minutes = 1 day)
            )
            if price_data:
                logger.debug(f"Successfully fetched price data using get_time_price_series")
        except Exception as e:
            error_messages.append(f"get_time_price_series: {str(e)}")
            logger.debug(f"get_time_price_series failed: {str(e)}")
        
        # Method 2: Fallback to get_daily_price_series if available
        if not price_data:
            try:
                tradingsymbol = symbol_info.get('tradingsymbol', symbol_name)
                price_data = api.get_daily_price_series(
                    exchange='NSE',
                    tradingsymbol=tradingsymbol,
                    startdate=int(start_date.timestamp()),
                    enddate=int(end_date.timestamp())
                )
                if price_data:
                    logger.debug(f"Successfully fetched price data using get_daily_price_series")
            except Exception as e:
                error_messages.append(f"get_daily_price_series: {str(e)}")
                logger.debug(f"get_daily_price_series failed: {str(e)}")
        
        if not price_data:
            # This is expected - Shoonya API does not support historical price data
            logger.debug(f"API does not support historical price data for {symbol_name} (expected behavior). Will use stored data as it accumulates.")
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
                # Check if entry is a dictionary, skip if it's a string or other type
                if not isinstance(entry, dict):
                    logger.debug(f"Skipping non-dict entry: {type(entry)}")
                    continue
                
                # Try different field names
                high = float(entry.get('h', entry.get('high', entry.get('High', 0))))
                low = float(entry.get('l', entry.get('low', entry.get('Low', 0))))
                close = float(entry.get('c', entry.get('close', entry.get('Close', entry.get('intc', 0)))))
                
                if high > 0 and low > 0 and close > 0:
                    highs.append(high)
                    lows.append(low)
                    closes.append(close)
            except (ValueError, TypeError, KeyError, AttributeError) as e:
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

