"""
Shoonya API IV Fetcher Module

Uses Shoonya API's option_greek function to calculate IV from option prices.
This is more reliable than NSE scraping and uses the official API.
"""

import logging
from typing import Optional
from scipy.optimize import brentq
import numpy as np

logger = logging.getLogger('ShoonyaIVFetcher')


def calculate_iv_using_shoonya_greek(api, option_price: float, spot_price: float, 
                                      strike_price: float, expiry_date: str, 
                                      days_to_expiry: int, risk_free_rate: float = 0.06,
                                      option_type: str = 'CE') -> Optional[float]:
    """
    Calculate IV using Shoonya API's option_greek function.
    
    Uses binary search to find the IV that produces an option price matching
    the market price. The option_greek function calculates theoretical price
    given volatility, so we iterate to find the volatility that matches.
    
    Args:
        api: ShoonyaApiPy instance (must be logged in)
        option_price: Current market option price
        spot_price: Current spot price
        strike_price: Strike price
        expiry_date: Expiry date in format 'DD-MMM-YYYY' (e.g., '25-JAN-2024')
        days_to_expiry: Days to expiration (not used by API, but kept for consistency)
        risk_free_rate: Risk-free rate (default: 6%)
        option_type: 'CE' for call, 'PE' for put
    
    Returns:
        float: IV as decimal (e.g., 0.15 for 15%), or None if calculation fails
    """
    try:
        # Convert option_type to format expected by API
        api_option_type = 'CE' if option_type.upper() in ['CE', 'C', 'CALL'] else 'PE'
        
        # Convert risk-free rate to percentage for API (API expects percentage)
        interest_rate = risk_free_rate * 100
        
        def price_diff(volatility_percent):
            """Calculate difference between market price and theoretical price"""
            try:
                # Call Shoonya API option_greek function
                # Note: option_greek calculates theoretical price given volatility
                result = api.option_greek(
                    expiredate=expiry_date,
                    StrikePrice=str(int(strike_price)),
                    SpotPrice=str(spot_price),
                    InterestRate=str(interest_rate),
                    Volatility=str(volatility_percent),  # API expects percentage
                    OptionType=api_option_type
                )
                
                if result and isinstance(result, dict):
                    if result.get('stat') == 'Ok':
                        # Get theoretical price from response
                        # Field name might vary - check common names
                        theoretical_price = None
                        for field in ['TheoPrice', 'theoprice', 'TheoreticalPrice', 'price', 'Price']:
                            if field in result:
                                try:
                                    theoretical_price = float(result[field])
                                    break
                                except (ValueError, TypeError):
                                    continue
                        
                        if theoretical_price is not None and theoretical_price > 0:
                            diff = theoretical_price - option_price
                            logger.debug(f"Vol={volatility_percent}%, TheoPrice={theoretical_price}, MarketPrice={option_price}, Diff={diff:.4f}")
                            return diff
                        else:
                            logger.warning(f"Could not extract theoretical price from API response: {result}")
                            return float('inf')
                    else:
                        logger.warning(f"API returned error status: {result.get('stat')}, message: {result.get('emsg', 'N/A')}")
                        return float('inf')
                else:
                    logger.warning(f"Invalid API response: {result}")
                    return float('inf')
                    
            except Exception as e:
                logger.warning(f"Error calling option_greek: {e}")
                return float('inf')
        
        # Binary search for IV (0.1% to 500% in percentage terms)
        iv_low_percent = 0.1
        iv_high_percent = 500.0
        
        # Check bounds
        price_at_low = price_diff(iv_low_percent)
        price_at_high = price_diff(iv_high_percent)
        
        if price_at_low * price_at_high > 0:
            logger.warning(f"IV search bounds don't bracket the solution (low_diff={price_at_low:.4f}, high_diff={price_at_high:.4f})")
            return None
        
        # Use Brent's method to find IV
        iv_percent = brentq(price_diff, iv_low_percent, iv_high_percent, maxiter=50)
        
        # Convert from percentage to decimal
        iv = iv_percent / 100.0
        
        logger.debug(f"Calculated IV using Shoonya API: {iv:.4f} ({iv*100:.2f}%)")
        return iv
        
    except Exception as e:
        logger.error(f"Error calculating IV using Shoonya API: {e}")
        import traceback
        logger.debug(traceback.format_exc())
        return None


def get_atm_iv_from_shoonya(api, option_chain_df, spot_price: float, 
                            expiry_date: str, days_to_expiry: int,
                            risk_free_rate: float = 0.06) -> Optional[float]:
    """
    Get ATM IV from option chain using Shoonya API.
    
    Args:
        api: ShoonyaApiPy instance (must be logged in)
        option_chain_df: DataFrame with option chain data
        spot_price: Current spot price
        expiry_date: Expiry date in format 'DD-MMM-YYYY'
        days_to_expiry: Days to expiration
        risk_free_rate: Risk-free rate (default: 6%)
    
    Returns:
        float: ATM IV as percentage (e.g., 15.5 for 15.5%), or None if failed
    """
    logger.info(f"Attempting Shoonya IV calculation: spot={spot_price}, expiry={expiry_date}, DTE={days_to_expiry}")
    
    if option_chain_df.empty:
        logger.warning("Cannot calculate ATM IV: option chain DataFrame is empty")
        return None
    
    logger.debug(f"Option chain has {len(option_chain_df)} rows")
    
    # Find ATM strikes
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
            logger.debug(f"Calculating IV for ATM call: strike={call_row['strike']}, price={call_price}, expiry={expiry_date}")
            call_iv = calculate_iv_using_shoonya_greek(
                api=api,
                option_price=call_price,
                spot_price=spot_price,
                strike_price=call_row['strike'],
                expiry_date=expiry_date,
                days_to_expiry=days_to_expiry,
                risk_free_rate=risk_free_rate,
                option_type='CE'
            )
            if call_iv:
                ivs.append(call_iv)
            else:
                logger.warning(f"Failed to calculate IV for ATM call at strike {call_row['strike']}")
        else:
            logger.warning(f"ATM call has invalid price: {call_price}")
    else:
        logger.warning("No ATM call found in option chain")
    
    # Calculate IV for ATM put
    if not atm_put.empty:
        put_row = atm_put.iloc[0]
        put_price = put_row.get('mid_price', put_row.get('ltp', 0))
        if put_price > 0:
            logger.debug(f"Calculating IV for ATM put: strike={put_row['strike']}, price={put_price}, expiry={expiry_date}")
            put_iv = calculate_iv_using_shoonya_greek(
                api=api,
                option_price=put_price,
                spot_price=spot_price,
                strike_price=put_row['strike'],
                expiry_date=expiry_date,
                days_to_expiry=days_to_expiry,
                risk_free_rate=risk_free_rate,
                option_type='PE'
            )
            if put_iv:
                ivs.append(put_iv)
            else:
                logger.warning(f"Failed to calculate IV for ATM put at strike {put_row['strike']}")
        else:
            logger.warning(f"ATM put has invalid price: {put_price}")
    else:
        logger.warning("No ATM put found in option chain")
    
    if not ivs:
        logger.warning("Could not calculate ATM IV using Shoonya API - no valid IV values from call/put calculations")
        return None
    
    # Return average of call and put IV (convert to percentage)
    atm_iv = np.mean(ivs) * 100
    logger.debug(f"ATM IV from Shoonya API: {atm_iv:.2f}%")
    return atm_iv

