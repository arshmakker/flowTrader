"""
Technical Indicators Module

Provides calculations for:
- Implied Volatility (IV) calculated from option prices using Black-Scholes model
- IV Percentile from historical IV data
- ADX (Average Directional Index) from price data
- Probability of Profit (PoP) calculations
"""

import pandas as pd
import numpy as np
import logging
from datetime import datetime, timedelta
from scipy.stats import norm
from scipy.optimize import brentq
from typing import Optional
import os
import json

logger = logging.getLogger('TechnicalIndicators')


# Risk-free rate (approximate for Indian market, can be updated)
RISK_FREE_RATE = 0.06  # 6% annual


def calculate_probability_of_profit(spot_price: float, short_call_strike: float, 
                                     short_put_strike: float, iv: float, 
                                     days_to_expiry: int, risk_free_rate: float = RISK_FREE_RATE) -> float:
    """
    Calculate Probability of Profit (PoP) for Iron Condor strategy.
    
    PoP is the probability that the underlying price stays between the short strikes
    at expiration, resulting in maximum profit.
    
    Uses normal distribution approach based on option pricing theory:
    PoP = N(d2_put) - N(d2_call)
    where N is cumulative normal distribution and d2 uses the provided IV.
    
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
        
        # Calculate d2 for both strikes using option pricing formula
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


def black_scholes_price(spot_price: float, strike: float, time_to_expiry: float, 
                        risk_free_rate: float, volatility: float, option_type: str = 'CE') -> float:
    """
    Calculate Black-Scholes option price.
    
    Args:
        spot_price: Current spot price
        strike: Strike price
        time_to_expiry: Time to expiration in years
        risk_free_rate: Risk-free rate (annual, as decimal, e.g., 0.06 for 6%)
        volatility: Volatility (annual, as decimal, e.g., 0.20 for 20%)
        option_type: 'CE' for call, 'PE' for put
    
    Returns:
        float: Option price
    """
    if time_to_expiry <= 0:
        # Option expired - return intrinsic value
        if option_type.upper() in ['CE', 'C', 'CALL']:
            return max(0, spot_price - strike)
        else:
            return max(0, strike - spot_price)
    
    S = spot_price
    K = strike
    T = time_to_expiry
    r = risk_free_rate
    sigma = volatility
    
    # Calculate d1 and d2
    d1 = (np.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * np.sqrt(T))
    d2 = d1 - sigma * np.sqrt(T)
    
    if option_type.upper() in ['CE', 'C', 'CALL']:
        # Call option
        price = S * norm.cdf(d1) - K * np.exp(-r * T) * norm.cdf(d2)
    else:
        # Put option
        price = K * np.exp(-r * T) * norm.cdf(-d2) - S * norm.cdf(-d1)
    
    return max(0, price)  # Option price cannot be negative


def calculate_iv_from_price(option_price: float, spot_price: float, strike: float,
                            days_to_expiry: int, risk_free_rate: float = RISK_FREE_RATE,
                            option_type: str = 'CE') -> Optional[float]:
    """
    Calculate implied volatility from option price using Black-Scholes and root finding.
    
    Args:
        option_price: Current market option price
        spot_price: Current spot price
        strike: Strike price
        days_to_expiry: Days to expiration
        risk_free_rate: Risk-free rate (annual, as decimal)
        option_type: 'CE' for call, 'PE' for put
    
    Returns:
        float: Implied volatility as percentage (e.g., 18.5 for 18.5%), or None if calculation fails
    """
    try:
        # Convert days to years
        time_to_expiry = days_to_expiry / 365.0
        
        if time_to_expiry <= 0:
            logger.debug("Option expired, cannot calculate IV")
            return None
        
        # Calculate intrinsic value
        if option_type.upper() in ['CE', 'C', 'CALL']:
            intrinsic = max(0, spot_price - strike)
        else:
            intrinsic = max(0, strike - spot_price)
        
        # If market price is less than intrinsic, IV cannot be calculated
        if option_price < intrinsic:
            logger.debug(f"Option price {option_price} < intrinsic {intrinsic}, cannot calculate IV")
            return None
        
        # Define error function: BS_price(sigma) - market_price
        def price_error(sigma):
            bs_price = black_scholes_price(spot_price, strike, time_to_expiry, risk_free_rate, sigma, option_type)
            return bs_price - option_price
        
        # Try to find IV using Brent's method
        # IV bounds: 0.1% to 500% (as decimal: 0.001 to 5.0)
        iv_low = 0.001
        iv_high = 5.0
        
        # Check bounds
        error_low = price_error(iv_low)
        error_high = price_error(iv_high)
        
        # If both errors have same sign, solution may not be bracketed
        if error_low * error_high > 0:
            # Try narrower bounds
            iv_low = 0.05  # 5%
            iv_high = 2.0   # 200%
            error_low = price_error(iv_low)
            error_high = price_error(iv_high)
            
            if error_low * error_high > 0:
                logger.debug(f"IV search bounds don't bracket solution (low_error={error_low:.4f}, high_error={error_high:.4f})")
                return None
        
        # Use Brent's method to find root
        iv_decimal = brentq(price_error, iv_low, iv_high, maxiter=100, xtol=1e-6)
        
        # Convert to percentage
        iv_percent = iv_decimal * 100
        
        logger.debug(f"Calculated IV: {iv_percent:.2f}% (strike={strike}, price={option_price}, DTE={days_to_expiry})")
        return iv_percent
        
    except Exception as e:
        logger.debug(f"Error calculating IV from price: {str(e)}")
        return None


def calculate_atm_iv(option_chain_df, spot_price, days_to_expiry, risk_free_rate=RISK_FREE_RATE, 
                     expiry_date_str=None, api=None):
    """
    Calculate ATM (At-The-Money) implied volatility from option chain.
    
    Calculates IV from option prices using Black-Scholes model and root finding.
    Since Shoonya API doesn't provide IV, we calculate it from market prices.
    
    Args:
        option_chain_df: DataFrame with option chain data (must have 'strike', 'option_type', 'mid_price' or 'ltp')
        spot_price: Current spot price
        days_to_expiry: Days to expiration
        risk_free_rate: Risk-free rate (annual, as decimal, default: 6%)
        expiry_date_str: Expiry date (not used, kept for compatibility)
        api: ShoonyaApiPy instance (not used, kept for compatibility)
    
    Returns:
        float: ATM IV as percentage (e.g., 18.5 for 18.5%), or None if calculation fails
    """
    import pandas as pd
    import numpy as np
    
    try:
        if option_chain_df.empty:
            logger.debug("Option chain is empty, cannot calculate IV")
            return None
        
        # Make a copy to avoid modifying original
        df = option_chain_df.copy()
        
        # Find ATM strikes (closest to spot)
        df['strike_diff'] = abs(df['strike'] - spot_price)
        
        # Get ATM call and put
        atm_call = df[
            (df['option_type'] == 'CE') &
            (df['strike_diff'] == df[df['option_type'] == 'CE']['strike_diff'].min())
        ]
        
        atm_put = df[
            (df['option_type'] == 'PE') &
            (df['strike_diff'] == df[df['option_type'] == 'PE']['strike_diff'].min())
        ]
        
        ivs = []
        
        # Calculate IV for ATM call
        if not atm_call.empty:
            call_row = atm_call.iloc[0]
            call_price = call_row.get('mid_price', call_row.get('ltp', 0))
            if call_price > 0:
                call_iv = calculate_iv_from_price(
                    option_price=call_price,
                    spot_price=spot_price,
                    strike=call_row['strike'],
                    days_to_expiry=days_to_expiry,
                    risk_free_rate=risk_free_rate,
                    option_type='CE'
                )
                if call_iv is not None:
                    ivs.append(call_iv)
                else:
                    logger.debug(f"Failed to calculate IV for ATM call at strike {call_row['strike']}")
        
        # Calculate IV for ATM put
        if not atm_put.empty:
            put_row = atm_put.iloc[0]
            put_price = put_row.get('mid_price', put_row.get('ltp', 0))
            if put_price > 0:
                put_iv = calculate_iv_from_price(
                    option_price=put_price,
                    spot_price=spot_price,
                    strike=put_row['strike'],
                    days_to_expiry=days_to_expiry,
                    risk_free_rate=risk_free_rate,
                    option_type='PE'
                )
                if put_iv is not None:
                    ivs.append(put_iv)
                else:
                    logger.debug(f"Failed to calculate IV for ATM put at strike {put_row['strike']}")
        
        if not ivs:
            logger.debug("Could not calculate ATM IV - no valid IV values from call/put calculations")
            return None
        
        # Return average of call and put IV
        atm_iv = np.mean(ivs)
        logger.info(f"✅ Calculated ATM IV: {atm_iv:.2f}% (from {len(ivs)} option(s))")
        return atm_iv
        
    except Exception as e:
        logger.error(f"Error calculating ATM IV: {str(e)}", exc_info=True)
        return None


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
            logger.debug("IV calculation not available (Shoonya API does not provide IV) - skipping IV percentile calculation")
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


def calculate_ema(prices, period: int):
    """
    Calculate Exponential Moving Average (EMA)
    
    Args:
        prices: List of closing prices (most recent last)
        period: EMA period
    
    Returns:
        EMA value or None if insufficient data
    """
    try:
        if len(prices) < period:
            return None
        
        # Use pandas for EMA calculation (more reliable)
        import pandas as pd
        df = pd.DataFrame({'close': prices})
        ema = df['close'].ewm(span=period, adjust=False).mean().iloc[-1]
        return float(ema)
    except Exception as e:
        logger.error(f"Error calculating EMA: {str(e)}")
        return None


def calculate_adx(high_prices, low_prices, close_prices, period=14):
    """
    Calculate ADX (Average Directional Index) from price data.
    Works with available data, adjusting period if needed (similar to IV percentile).
    
    Args:
        high_prices: Series of high prices
        low_prices: Series of low prices
        close_prices: Series of close prices
        period: Period for ADX calculation (default: 14)
    
    Returns:
        float: ADX value, or None if calculation fails
    """
    try:
        data_length = len(high_prices)
        
        # Adjust period if we don't have enough data (similar to IV percentile approach)
        # Minimum 2 data points needed for any calculation
        if data_length < 2:
            logger.debug(f"Not enough data for ADX calculation (need at least 2, have {data_length})")
            return None
        
        # If we have less than period+1 data points, use a shorter period
        # This allows calculation with whatever data we have
        adjusted_period = min(period, max(1, data_length - 1))
        
        if adjusted_period < period:
            logger.info(f"Using adjusted ADX period {adjusted_period} (have {data_length} days, ideal is {period + 1}+). This is normal for new systems.")
        
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
            atr[i] = (atr[i-1] * (adjusted_period - 1) + tr[i]) / adjusted_period
            plus_di_smooth[i] = (plus_di_smooth[i-1] * (adjusted_period - 1) + plus_dm[i]) / adjusted_period
            minus_di_smooth[i] = (minus_di_smooth[i-1] * (adjusted_period - 1) + minus_dm[i]) / adjusted_period
        
        # Calculate +DI and -DI
        plus_di = 100 * (plus_di_smooth / atr)
        minus_di = 100 * (minus_di_smooth / atr)
        
        # Calculate DX
        dx = 100 * np.abs(plus_di - minus_di) / (plus_di + minus_di)
        dx = np.where(np.isnan(dx) | np.isinf(dx), 0, dx)
        
        # Calculate ADX (smoothed DX)
        adx = dx.copy()
        for i in range(adjusted_period, len(dx)):
            adx[i] = (adx[i-1] * (adjusted_period - 1) + dx[i]) / adjusted_period
        
        # Return the latest ADX value
        # If we have less data than ideal, use the last calculated value
        # For very short periods, we might not have a smoothed ADX yet, so use DX
        if len(adx) > adjusted_period:
            adx_value = adx[-1]
        elif len(dx) > 0:
            # Use the last DX value as approximation if ADX not yet available
            adx_value = dx[-1]
            logger.debug(f"Using DX as ADX approximation (period {adjusted_period}, have {data_length} days)")
        else:
            return None
        
        logger.info(f"Calculated ADX(period={adjusted_period}, ideal={period}): {adx_value:.2f} from {data_length} days of data")
        
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
        
        # Return whatever data we have (similar to IV percentile approach)
        # The calculate_adx function will adjust the period based on available data
        if len(daily_bars) < 2:
            logger.debug(f"Only {len(daily_bars)} days of stored data available (need at least 2 for ADX)")
            return None, None, None
        
        highs = daily_bars['high'].tolist()
        lows = daily_bars['low'].tolist()
        closes = daily_bars['close'].tolist()
        
        if len(daily_bars) < 15:
            logger.info(f"Using {len(daily_bars)} days of stored NIFTY futures data for ADX calculation (ideal is 15+ days). ADX will use adjusted period.")
        else:
            logger.info(f"Using {len(daily_bars)} days of stored NIFTY futures data for ADX calculation")
        
        return highs, lows, closes
        
    except Exception as e:
        logger.debug(f"Error getting historical data from stored files: {str(e)}")
        return None, None, None


def get_15min_candle_data(api, symbol_manager, symbol_name, lookback_hours=30):
    """
    Get 15-minute candle data for EMA calculation.
    Builds candles from stored tick data since Shoonya API doesn't provide 15-minute intervals.
    
    Args:
        api: ShoonyaApiPy instance (not used, kept for compatibility)
        symbol_manager: SymbolManager instance (not used, kept for compatibility)
        symbol_name: Symbol name (e.g., 'Nifty 50')
        lookback_hours: Hours to look back (default: 30 hours = ~120 candles, need 100+ for EMA(100))
    
    Returns:
        List of close prices (15-minute candles) or None if error
        Requires minimum 100 candles for EMA(100) calculation
    """
    try:
        # Find NIFTY futures symbol for data collection
        # We'll use NIFTY futures data as proxy for NIFTY index
        # Need to read multiple days to get enough candles (100+ for EMA(100))
        today = datetime.now()
        all_tick_data = []
        
        # Calculate how many days we need (100 candles / ~23 candles per day = ~4-5 trading days)
        # Account for weekends - need to check more calendar days to get enough trading days
        # Check up to 20 calendar days to ensure we get enough trading days with data
        days_to_check = 20  # Check last 20 calendar days to account for weekends and holidays
        
        # #region agent log
        import json
        try:
            with open('/Users/arshdeep/git/ironcondor/.cursor/debug.log', 'a') as f:
                f.write(json.dumps({"location":"technical_indicators.py:614","message":"Starting multi-day 15min candle build","data":{"days_to_check":days_to_check,"lookback_hours":lookback_hours,"today":today.strftime('%Y%m%d')},"timestamp":int(datetime.now().timestamp()*1000),"sessionId":"debug-session","runId":"check-15min","hypothesisId":"A"})+"\n")
        except: pass
        # #endregion
        
        # Read data from multiple days (most recent first)
        days_found = 0
        for days_back in range(days_to_check):
            check_date = today - timedelta(days=days_back)
            date_str = check_date.strftime('%Y%m%d')
            data_dir = f"market_data_{date_str}"
            futures_dir = os.path.join(data_dir, 'raw_data', 'futures')
            
            if not os.path.exists(futures_dir):
                continue
            
            # Find NIFTY futures file for this date
            futures_files = [f for f in os.listdir(futures_dir) 
                            if f.startswith('NIFTY') and f.endswith('F_' + date_str + '.csv')]
            
            if not futures_files:
                continue
            
            # Use the first matching file (typically current month expiry)
            futures_file = futures_files[0]
            futures_path = os.path.join(futures_dir, futures_file)
            
            # Read tick data for this day
            try:
                day_data = pd.read_csv(futures_path)
                if not day_data.empty:
                    day_data['timestamp'] = pd.to_datetime(day_data['timestamp'])
                    # Set timestamp as index for this day's data
                    day_data = day_data.set_index('timestamp')
                    all_tick_data.append(day_data)
                    days_found += 1
                    # #region agent log
                    try:
                        with open('/Users/arshdeep/git/ironcondor/.cursor/debug.log', 'a') as f:
                            f.write(json.dumps({"location":"technical_indicators.py:644","message":"Loaded day data","data":{"date_str":date_str,"tick_count":len(day_data),"futures_file":futures_file},"timestamp":int(datetime.now().timestamp()*1000),"sessionId":"debug-session","runId":"check-15min","hypothesisId":"B"})+"\n")
                    except: pass
                    # #endregion
            except Exception as e:
                logger.debug(f"Error reading futures file {futures_path}: {str(e)}")
                continue
        
        if not all_tick_data:
            # #region agent log
            try:
                with open('/Users/arshdeep/git/ironcondor/.cursor/debug.log', 'a') as f:
                    f.write(json.dumps({"location":"technical_indicators.py:650","message":"No data found","data":{"days_to_check":days_to_check,"days_found":days_found},"timestamp":int(datetime.now().timestamp()*1000),"sessionId":"debug-session","runId":"check-15min","hypothesisId":"C"})+"\n")
            except: pass
            # #endregion
            logger.debug(f"No NIFTY futures data found in last {days_to_check} days")
            return None
        
        # #region agent log
        try:
            total_ticks = sum(len(df) for df in all_tick_data)
            with open('/Users/arshdeep/git/ironcondor/.cursor/debug.log', 'a') as f:
                f.write(json.dumps({"location":"technical_indicators.py:653","message":"Combining multi-day data","data":{"days_found":days_found,"total_ticks":total_ticks,"days_to_check":days_to_check},"timestamp":int(datetime.now().timestamp()*1000),"sessionId":"debug-session","runId":"check-15min","hypothesisId":"D"})+"\n")
        except: pass
        # #endregion
        
        # Combine all days' data (all already have timestamp as index)
        tick_data = pd.concat(all_tick_data, axis=0)
        tick_data = tick_data.sort_index()  # Sort by timestamp index
        
        # #region agent log
        try:
            total_ticks = len(tick_data)
            time_range_hours = (tick_data.index.max() - tick_data.index.min()).total_seconds() / 3600 if not tick_data.empty else 0
            with open('/Users/arshdeep/git/ironcondor/.cursor/debug.log', 'a') as f:
                f.write(json.dumps({"location":"technical_indicators.py:655","message":"Combined all days data","data":{"total_ticks":total_ticks,"time_range_hours":time_range_hours,"days_combined":len(all_tick_data)},"timestamp":int(datetime.now().timestamp()*1000),"sessionId":"debug-session","runId":"check-15min","hypothesisId":"H"})+"\n")
        except: pass
        # #endregion
        
        if tick_data.empty:
            logger.debug(f"No tick data available across {days_to_check} days")
            return None
        
        # Resample to 15-minute candles
        candles = tick_data['ltp'].resample('15min').agg({
            'open': 'first',
            'high': 'max',
            'low': 'min',
            'close': 'last'
        })
        
        # Remove rows with NaN (incomplete candles)
        candles = candles.dropna()
        
        # #region agent log
        try:
            candles_before_limit = len(candles)
            with open('/Users/arshdeep/git/ironcondor/.cursor/debug.log', 'a') as f:
                f.write(json.dumps({"location":"technical_indicators.py:668","message":"After resampling","data":{"candles_count":candles_before_limit,"tick_data_points":len(tick_data),"time_range_hours":(tick_data.index.max() - tick_data.index.min()).total_seconds() / 3600 if not tick_data.empty else 0},"timestamp":int(datetime.now().timestamp()*1000),"sessionId":"debug-session","runId":"check-15min","hypothesisId":"E"})+"\n")
        except: pass
        # #endregion
        
        if candles.empty:
            logger.debug("No complete 15-minute candles after resampling")
            return None
        
        # Take the most recent candles (prioritize recent data)
        # If we have more than 100, take last 100 (most recent)
        # If we have less than 100, use what we have (but will fail check below)
        if len(candles) > 100:
            candles = candles.tail(100)  # Take last 100 candles (most recent)
            logger.debug(f"Taking last 100 candles from {candles_before_limit} total candles")
        
        # Extract close prices
        closes = candles['close'].tolist()
        
        # #region agent log
        try:
            with open('/Users/arshdeep/git/ironcondor/.cursor/debug.log', 'a') as f:
                f.write(json.dumps({"location":"technical_indicators.py:695","message":"Final candle count check","data":{"candles_count":len(closes),"needs_100":len(closes) >= 100,"needs_50":len(closes) >= 50,"can_ema50":len(closes) >= 50,"can_ema100":len(closes) >= 100},"timestamp":int(datetime.now().timestamp()*1000),"sessionId":"debug-session","runId":"check-15min","hypothesisId":"F"})+"\n")
        except: pass
        # #endregion
        
        if len(closes) < 100:
            logger.debug(f"Insufficient 15-minute candles: {len(closes)} (need at least 100 for EMA(100))")
            return None
        
        logger.debug(f"Built {len(closes)} 15-minute candles from stored tick data for EMA calculation")
        return closes
        
    except Exception as e:
        logger.debug(f"Error building 15-minute candles from stored data: {str(e)}", exc_info=True)
        return None


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

