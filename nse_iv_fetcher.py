"""
NSE IV Fetcher Module

Fetches Implied Volatility (IV) data directly from NSE website's option chain API.
This is faster and more accurate than calculating IV from option prices.
"""

import requests
import logging
import time
from datetime import datetime
from typing import Optional, Dict, List
import pandas as pd

logger = logging.getLogger('NSEIVFetcher')

# NSE API endpoints
NSE_OPTION_CHAIN_URL = "https://www.nseindia.com/api/option-chain-indices"
NSE_BASE_URL = "https://www.nseindia.com"
NSE_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'application/json, text/plain, */*',
    'Accept-Language': 'en-US,en;q=0.9',
    'Accept-Encoding': 'gzip, deflate, br',
    'Connection': 'keep-alive',
    'Upgrade-Insecure-Requests': '1',
    'Sec-Fetch-Dest': 'empty',
    'Sec-Fetch-Mode': 'cors',
    'Sec-Fetch-Site': 'same-origin',
    'Referer': 'https://www.nseindia.com/option-chain',
}

# Session to maintain cookies
_session = None


def _get_session():
    """Get or create a requests session with cookies"""
    global _session
    if _session is None:
        _session = requests.Session()
        # Initialize session by visiting NSE pages first (required for cookies)
        try:
            # Visit homepage to get initial cookies
            _session.get(NSE_BASE_URL, timeout=10, headers={'User-Agent': NSE_HEADERS['User-Agent']})
            time.sleep(0.3)
            # Visit option chain page to get proper session cookies
            _session.get(f'{NSE_BASE_URL}/option-chain', timeout=10, headers={'User-Agent': NSE_HEADERS['User-Agent']})
            time.sleep(0.5)  # Brief delay to ensure cookies are set
            # Now set full headers
            _session.headers.update(NSE_HEADERS)
        except Exception as e:
            logger.debug(f"Error initializing NSE session: {e}")
            # Still set headers even if initialization fails
            _session.headers.update(NSE_HEADERS)
    return _session


def fetch_nse_option_chain(symbol: str = 'NIFTY', retry_count: int = 3) -> Optional[Dict]:
    """
    Fetch option chain data from NSE API.
    
    Args:
        symbol: Index symbol (default: 'NIFTY')
        retry_count: Number of retry attempts
    
    Returns:
        dict: Option chain data from NSE, or None if failed
    """
    session = _get_session()
    
    for attempt in range(retry_count):
        try:
            # NSE API parameters
            params = {
                'symbol': symbol
            }
            
            response = session.get(
                NSE_OPTION_CHAIN_URL,
                params=params,
                timeout=15
            )
            
            if response.status_code == 200:
                try:
                    data = response.json()
                    # Check if data is valid
                    if data and isinstance(data, dict) and 'records' in data:
                        logger.debug(f"Successfully fetched NSE option chain for {symbol}")
                        return data
                    else:
                        logger.warning(f"NSE API returned invalid data structure")
                        logger.info(f"Response content length: {len(response.content)}")
                        logger.info(f"Response status: {response.status_code}")
                        # Log what keys/data we actually got
                        if isinstance(data, dict):
                            logger.info(f"Response keys: {list(data.keys())}")
                            logger.info(f"Response sample: {str(data)[:500]}")
                        else:
                            logger.info(f"Response type: {type(data)}, value: {str(data)[:500]}")
                        # Log full response for debugging
                        if len(response.text) < 2000:
                            logger.info(f"Full response: {response.text}")
                        else:
                            logger.info(f"Response (first 1000 chars): {response.text[:1000]}")
                            logger.info(f"Response (last 500 chars): {response.text[-500:]}")
                except ValueError as e:
                    logger.warning(f"Failed to parse JSON response: {e}")
                    logger.debug(f"Response content: {response.text[:500]}")
            elif response.status_code == 403:
                # Session expired, reinitialize
                logger.debug("NSE session expired, reinitializing...")
                global _session
                _session = None
                session = _get_session()
                time.sleep(1)  # Brief delay before retry
            else:
                logger.warning(f"NSE API returned status {response.status_code}")
                if attempt < retry_count - 1:
                    time.sleep(2 ** attempt)  # Exponential backoff
                    
        except requests.exceptions.RequestException as e:
            logger.warning(f"Error fetching NSE option chain (attempt {attempt + 1}/{retry_count}): {e}")
            if attempt < retry_count - 1:
                time.sleep(2 ** attempt)  # Exponential backoff
        except Exception as e:
            logger.error(f"Unexpected error fetching NSE option chain: {e}")
            break
    
    logger.error(f"Failed to fetch NSE option chain for {symbol} after {retry_count} attempts")
    return None


def extract_iv_from_nse_data(nse_data: Dict, expiry_date: Optional[str] = None) -> Optional[float]:
    """
    Extract ATM IV from NSE option chain data.
    
    Args:
        nse_data: Raw data from NSE API
        expiry_date: Expiry date in format 'DD-MMM-YYYY' (e.g., '25-JAN-2024')
                     If None, uses the nearest expiry
    
    Returns:
        float: ATM IV as percentage (e.g., 15.5 for 15.5%), or None if not found
    """
    try:
        if not nse_data or 'records' not in nse_data:
            logger.warning("Invalid NSE data structure")
            return None
        
        records = nse_data.get('records', {})
        expiry_dates = records.get('expiryDates', [])
        
        if not expiry_dates:
            logger.warning("No expiry dates found in NSE data")
            return None
        
        # Select expiry date
        if expiry_date:
            # Find matching expiry
            selected_expiry = None
            for exp in expiry_dates:
                if exp.upper() == expiry_date.upper():
                    selected_expiry = exp
                    break
            if not selected_expiry:
                logger.warning(f"Expiry {expiry_date} not found, using nearest expiry")
                selected_expiry = expiry_dates[0]
        else:
            # Use nearest expiry
            selected_expiry = expiry_dates[0]
        
        logger.debug(f"Using expiry: {selected_expiry}")
        
        # Get data for this expiry
        data = records.get('data', [])
        
        # Find ATM option (strike closest to current spot)
        spot_price = records.get('underlyingValue', 0)
        if spot_price <= 0:
            logger.warning("Invalid spot price in NSE data")
            return None
        
        # Filter data for selected expiry
        expiry_data = [d for d in data if d.get('expiryDate') == selected_expiry]
        
        if not expiry_data:
            logger.warning(f"No data found for expiry {selected_expiry}")
            return None
        
        # Find ATM strike (closest to spot)
        atm_strike = None
        min_diff = float('inf')
        
        for option_data in expiry_data:
            strike = option_data.get('strikePrice', 0)
            if strike > 0:
                diff = abs(strike - spot_price)
                if diff < min_diff:
                    min_diff = diff
                    atm_strike = strike
        
        if atm_strike is None:
            logger.warning("Could not find ATM strike")
            return None
        
        logger.debug(f"ATM strike: {atm_strike}, Spot: {spot_price}")
        
        # Get IV for ATM call and put
        call_iv = None
        put_iv = None
        
        for option_data in expiry_data:
            if option_data.get('strikePrice') == atm_strike:
                # Get call IV
                ce_data = option_data.get('CE', {})
                if ce_data:
                    call_iv = ce_data.get('impliedVolatility')
                
                # Get put IV
                pe_data = option_data.get('PE', {})
                if pe_data:
                    put_iv = pe_data.get('impliedVolatility')
                break
        
        # Average call and put IV, or use whichever is available
        ivs = []
        if call_iv is not None and call_iv > 0:
            ivs.append(call_iv)
        if put_iv is not None and put_iv > 0:
            ivs.append(put_iv)
        
        if not ivs:
            logger.warning("No valid IV found in NSE data")
            return None
        
        atm_iv = sum(ivs) / len(ivs)
        logger.debug(f"Extracted ATM IV from NSE: {atm_iv:.2f}%")
        return atm_iv
        
    except Exception as e:
        logger.error(f"Error extracting IV from NSE data: {e}")
        return None


def get_nse_iv(symbol: str = 'NIFTY', expiry_date: Optional[str] = None) -> Optional[float]:
    """
    Get ATM IV directly from NSE website.
    
    This is a convenience function that combines fetching and extraction.
    
    Note: NSE has anti-bot protection that may block automated requests.
    This function will return None if NSE blocks the request, and the system
    will fall back to calculating IV from option prices.
    
    Args:
        symbol: Index symbol (default: 'NIFTY')
        expiry_date: Expiry date in format 'DD-MMM-YYYY' (e.g., '25-JAN-2024')
                     If None, uses the nearest expiry
    
    Returns:
        float: ATM IV as percentage (e.g., 15.5 for 15.5%), or None if failed
    """
    try:
        nse_data = fetch_nse_option_chain(symbol, retry_count=3)
        if nse_data:
            return extract_iv_from_nse_data(nse_data, expiry_date)
    except Exception as e:
        logger.debug(f"NSE IV fetch failed (this is expected if NSE blocks requests): {e}")
    return None


def get_nse_iv_for_strikes(nse_data: Dict, expiry_date: Optional[str] = None) -> pd.DataFrame:
    """
    Get IV data for all strikes from NSE option chain.
    
    Args:
        nse_data: Raw data from NSE API
        expiry_date: Expiry date in format 'DD-MMM-YYYY'
    
    Returns:
        pd.DataFrame: DataFrame with columns ['strike', 'call_iv', 'put_iv', 'avg_iv']
    """
    try:
        if not nse_data or 'records' not in nse_data:
            return pd.DataFrame()
        
        records = nse_data.get('records', {})
        expiry_dates = records.get('expiryDates', [])
        
        if not expiry_dates:
            return pd.DataFrame()
        
        # Select expiry
        if expiry_date:
            selected_expiry = None
            for exp in expiry_dates:
                if exp.upper() == expiry_date.upper():
                    selected_expiry = exp
                    break
            if not selected_expiry:
                selected_expiry = expiry_dates[0]
        else:
            selected_expiry = expiry_dates[0]
        
        # Get data for this expiry
        data = records.get('data', [])
        expiry_data = [d for d in data if d.get('expiryDate') == selected_expiry]
        
        if not expiry_data:
            return pd.DataFrame()
        
        # Extract IV data
        iv_data = []
        for option_data in expiry_data:
            strike = option_data.get('strikePrice', 0)
            if strike <= 0:
                continue
            
            ce_data = option_data.get('CE', {})
            pe_data = option_data.get('PE', {})
            
            call_iv = ce_data.get('impliedVolatility') if ce_data else None
            put_iv = pe_data.get('impliedVolatility') if pe_data else None
            
            # Calculate average IV
            ivs = []
            if call_iv is not None and call_iv > 0:
                ivs.append(call_iv)
            if put_iv is not None and put_iv > 0:
                ivs.append(put_iv)
            
            avg_iv = sum(ivs) / len(ivs) if ivs else None
            
            iv_data.append({
                'strike': strike,
                'call_iv': call_iv,
                'put_iv': put_iv,
                'avg_iv': avg_iv
            })
        
        df = pd.DataFrame(iv_data)
        df = df.sort_values('strike')
        return df
        
    except Exception as e:
        logger.error(f"Error extracting IV data for all strikes: {e}")
        return pd.DataFrame()

