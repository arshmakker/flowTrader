"""
Strategy Runner Module

Handles integration of Iron Condor strategy with the main system.
Provides helper functions to fetch option chains, calculate market state,
and run strategy checks.
"""

import pandas as pd
import logging
import os
import json
import re
from datetime import datetime, timedelta
from typing import Dict, Optional, List
from strategies.iron_condor import generate_iron_condor_trade
from technical_indicators import (
    calculate_iv_percentile,
    calculate_adx,
    get_historical_price_data
)

logger = logging.getLogger('StrategyRunner')


def _get_date_object(date_or_datetime):
    """
    Helper function to safely convert date or datetime to date object.
    
    Args:
        date_or_datetime: datetime.date, datetime.datetime, or None
    
    Returns:
        datetime.date object
    """
    if date_or_datetime is None:
        return datetime.now().date()
    elif isinstance(date_or_datetime, datetime):
        return date_or_datetime.date()
    elif isinstance(date_or_datetime, type(datetime.now().date())):
        return date_or_datetime
    else:
        return datetime.now().date()


def get_weekly_expiry(date=None):
    """
    Get the next weekly expiry date (Thursday) for NIFTY.
    
    If today is Thursday:
    - Before 3:30 PM: Use today's expiry
    - After 3:30 PM: Use next week's expiry
    
    Args:
        date: Reference date (default: today)
    
    Returns:
        datetime object for the weekly expiry
    """
    if date is None:
        now = datetime.now()
        reference_date = now.date()
        reference_datetime = now
    else:
        # If date is provided, convert to date if it's datetime
        if isinstance(date, datetime):
            reference_datetime = date
            reference_date = date.date()
        else:
            reference_date = date
            reference_datetime = datetime.combine(date, datetime.min.time())
    
    # If today is Thursday, check if market is still open
    if reference_date.weekday() == 3:  # Thursday
        market_close = reference_datetime.replace(hour=15, minute=30, second=0, microsecond=0)
        if reference_datetime > market_close:
            # Market closed, use next week's Thursday
            expiry = reference_date + timedelta(days=7)
            return expiry
        else:
            # Market still open, use today's expiry
            return reference_date
    
    # Find next Thursday
    days_ahead = 3 - reference_date.weekday()  # Thursday is weekday 3
    if days_ahead <= 0:  # Shouldn't happen now, but handle edge case
        days_ahead += 7
    
    expiry = reference_date + timedelta(days=days_ahead)
    return expiry


def get_nifty_spot_price(api, symbol_manager):
    """
    Get current NIFTY spot price.
    
    Args:
        api: ShoonyaApiPy instance
        symbol_manager: SymbolManager instance
    
    Returns:
        float: Current spot price or None if error
    """
    try:
        # Try to get NIFTY index token - NIFTY is stored as "Nifty 50" in NSE.csv
        nifty_info = symbol_manager.get_token_info('Nifty 50', exchange='NSE')
        
        # Fallback: If not found, use direct token (NIFTY INDEX token is 26000)
        if not nifty_info:
            logger.debug("NIFTY not found by symbol, using direct token 26000")
            nifty_info = {
                'token': '26000',
                'exchange': 'NSE'
            }
        
        if not nifty_info or 'token' not in nifty_info:
            logger.error("Could not find NIFTY token")
            return None
        
        # Get quote
        quote = api.get_quotes(exchange='NSE', token=nifty_info['token'])
        if quote and 'lp' in quote:
            spot_price = float(quote['lp'])
            logger.debug(f"NIFTY spot price: {spot_price}")
            return spot_price
        else:
            logger.error("Could not get NIFTY quote")
            return None
            
    except Exception as e:
        logger.error(f"Error getting NIFTY spot price: {str(e)}")
        return None


def get_option_chain_data(api, symbol_manager, spot_price, expiry_date, count=50):
    """
    Get option chain data for NIFTY weekly expiry.
    
    Args:
        api: ShoonyaApiPy instance
        symbol_manager: SymbolManager instance
        spot_price: Current spot price
        expiry_date: Expiry date (datetime)
        count: Number of strikes on each side (default: 50)
    
    Returns:
        pd.DataFrame: Option chain data with columns:
            ['strike', 'option_type', 'ltp', 'bid', 'ask', 'delta', 'oi', 'volume']
    """
    try:
        # Get the correct futures symbol from symbol manager
        expiry_date_obj = _get_date_object(expiry_date)
        
        # Get all NIFTY futures and find the one matching our expiry
        futures_list = symbol_manager.get_index_futures()
        nifty_future = None
        
        for future in futures_list:
            if future.get('index_name') == 'NIFTY':
                # Check if expiry matches
                future_expiry = pd.to_datetime(future.get('expiry', ''), format='%d-%b-%Y')
                if future_expiry.date() == expiry_date_obj:
                    nifty_future = future
                    break
        
        if not nifty_future:
            # Fallback: try to construct symbol manually
            expiry_str = expiry_date_obj.strftime('%d%b%y').upper()
            futures_symbol = f"NIFTY{expiry_str}F"
            logger.warning(f"Could not find NIFTY future for expiry {expiry_date_obj}, trying {futures_symbol}")
        else:
            futures_symbol = nifty_future['symbol']  # This is the tradingsymbol
            logger.info(f"Found NIFTY future: {futures_symbol} (Expiry: {nifty_future.get('expiry')})")
        
        logger.info(f"Fetching option chain for {futures_symbol} at strike {int(spot_price)}")
        
        # Get option chain from API
        option_chain_raw = api.get_option_chain(
            exchange='NFO',
            tradingsymbol=futures_symbol,
            strikeprice=int(spot_price),
            count=count
        )
        
        if not option_chain_raw or 'values' not in option_chain_raw:
            logger.warning(f"No option chain data returned for {futures_symbol}")
            # Try alternative: maybe the API needs the symbol without 'F' suffix
            if futures_symbol.endswith('F'):
                alt_symbol = futures_symbol[:-1]  # Remove 'F'
                logger.info(f"Trying alternative symbol: {alt_symbol}")
                option_chain_raw = api.get_option_chain(
                    exchange='NFO',
                    tradingsymbol=alt_symbol,
                    strikeprice=int(spot_price),
                    count=count
                )
                if not option_chain_raw or 'values' not in option_chain_raw:
                    logger.warning(f"No option chain data returned for {alt_symbol} either")
                    return pd.DataFrame()
            else:
                return pd.DataFrame()
        
        # Process option chain data
        chain_data = []
        for option in option_chain_raw.get('values', []):
            try:
                # Get quote for this option
                quote = api.get_quotes(option['exch'], option['token'])
                if not quote:
                    continue
                
                # Determine option type
                tsym = option.get('tsym', '')
                option_type = 'CE' if 'CE' in tsym else 'PE' if 'PE' in tsym else None
                if not option_type:
                    continue
                
                # Extract strike price - try multiple methods
                strike = 0.0
                
                # Method 1: Direct strike field
                if 'strprc' in option and option['strprc']:
                    try:
                        strike = float(option['strprc'])
                    except:
                        pass
                
                # Method 2: Extract from trading symbol (NIFTY format: NIFTY24JAN20200CE or NIFTY24JAN20200PE)
                if strike == 0 and tsym:
                    try:
                        # Remove NIFTY prefix
                        symbol_part = tsym.replace('NIFTY', '')
                        # Remove option type suffix (CE/PE)
                        symbol_part = symbol_part.replace('CE', '').replace('PE', '')
                        # Remove 'F' if present (futures suffix)
                        symbol_part = symbol_part.replace('F', '')
                        # Extract numeric part - this should be the strike
                        # Format is typically: DDMMMYYSTRIKE or DDMMMYY
                        # Try to extract the last numeric sequence as strike
                        # Find all numeric sequences
                        numbers = re.findall(r'\d+', symbol_part)
                        if numbers:
                            # The last number is usually the strike
                            strike_str = numbers[-1]
                            if len(strike_str) >= 4:  # Strike should be at least 4 digits
                                strike = float(strike_str)
                    except:
                        pass
                
                # Method 3: Try 'strike' field (alternative naming)
                if strike == 0 and 'strike' in option:
                    try:
                        strike = float(option['strike'])
                    except:
                        pass
                
                if strike == 0:
                    logger.debug(f"Could not extract strike price for {tsym}")
                    continue
                
                # Calculate mid price if bid/ask available
                bid = float(quote.get('bp1', 0))
                ask = float(quote.get('sp1', 0))
                ltp = float(quote.get('lp', 0))
                mid_price = (bid + ask) / 2 if bid > 0 and ask > 0 else ltp
                
                chain_data.append({
                    'strike': strike,
                    'option_type': option_type,
                    'ltp': ltp,
                    'bid': bid,
                    'ask': ask,
                    'mid_price': mid_price,
                    'delta': float(option.get('delta', 0)) if option.get('delta') else 0.0,  # May not be available
                    'oi': int(quote.get('oi', 0)),
                    'volume': int(quote.get('v', 0))
                })
                
            except Exception as e:
                logger.debug(f"Error processing option {option.get('tsym', 'unknown')}: {str(e)}")
                continue
        
        if not chain_data:
            logger.warning("No valid option chain data processed")
            return pd.DataFrame()
        
        option_chain_df = pd.DataFrame(chain_data)
        logger.info(f"Processed {len(option_chain_df)} option contracts")
        
        return option_chain_df
        
    except Exception as e:
        logger.error(f"Error getting option chain data: {str(e)}", exc_info=True)
        return pd.DataFrame()


def calculate_iv_percentile_wrapper(option_chain_df, spot_price, days_to_expiry):
    """
    Calculate IV percentile from option chain data.
    
    Args:
        option_chain_df: Option chain DataFrame
        spot_price: Current spot price
        days_to_expiry: Days to expiration
    
    Returns:
        float: IV percentile (0-100), or None if calculation fails
    """
    try:
        iv_percentile = calculate_iv_percentile(
            option_chain_df, spot_price, days_to_expiry
        )
        return iv_percentile
    except Exception as e:
        logger.error(f"Error calculating IV percentile: {str(e)}", exc_info=True)
        # Fallback to default value if calculation fails
        logger.warning("Using fallback IV percentile value")
        return 65.0


def calculate_adx_wrapper(api, symbol_manager, period=14):
    """
    Calculate ADX(14) from historical price data.
    
    Args:
        api: ShoonyaApiPy instance
        symbol_manager: SymbolManager instance
        period: ADX period (default: 14)
    
    Returns:
        float: ADX value, or None if calculation fails
    """
    try:
        # Get historical price data for NIFTY
        highs, lows, closes = get_historical_price_data(
            api, symbol_manager, 'Nifty 50', days=30
        )
        
        if highs is None or lows is None or closes is None:
            logger.warning("Could not fetch historical price data, using fallback ADX")
            return 18.0  # Fallback value
        
        # Calculate ADX
        adx_value = calculate_adx(highs, lows, closes, period)
        
        if adx_value is None:
            logger.warning("ADX calculation returned None, using fallback")
            return 18.0  # Fallback value
        
        return adx_value
        
    except Exception as e:
        logger.error(f"Error calculating ADX: {str(e)}", exc_info=True)
        # Fallback to default value if calculation fails
        logger.warning("Using fallback ADX value")
        return 18.0


def check_major_events(expiry_date):
    """
    Check if there are major events (RBI meetings, etc.) in the next 48 hours.
    
    This is a placeholder - in production, you would check an event calendar.
    
    Args:
        expiry_date: Expiry date
    
    Returns:
        bool: True if major event detected, False otherwise
    """
    # TODO: Implement actual event calendar check
    # For now, assume no major events
    return False


def build_market_state(api, symbol_manager, spot_price, expiry_date):
    """
    Build market state dictionary for Iron Condor strategy.
    
    Args:
        api: ShoonyaApiPy instance
        symbol_manager: SymbolManager instance
        spot_price: Current spot price
        expiry_date: Expiry date (datetime)
    
    Returns:
        dict: Market state dictionary
    """
    try:
        # Calculate days to expiry
        expiry_date_obj = _get_date_object(expiry_date)
        days_to_expiry = (expiry_date_obj - datetime.now().date()).days
        
        # Get option chain for IV calculation
        option_chain = get_option_chain_data(api, symbol_manager, spot_price, expiry_date, count=20)
        
        return build_market_state_from_chain(api, symbol_manager, spot_price, expiry_date, option_chain)
        
    except Exception as e:
        logger.error(f"Error building market state: {str(e)}", exc_info=True)
        return None


def build_market_state_from_chain(api, symbol_manager, spot_price, expiry_date, option_chain_df):
    """
    Build market state dictionary from existing option chain DataFrame.
    More efficient than build_market_state when option chain is already available.
    
    Args:
        api: ShoonyaApiPy instance
        symbol_manager: SymbolManager instance
        spot_price: Current spot price
        expiry_date: Expiry date (datetime)
        option_chain_df: Option chain DataFrame
    
    Returns:
        dict: Market state dictionary
    """
    try:
        # Calculate days to expiry
        expiry_date_obj = _get_date_object(expiry_date)
        days_to_expiry = (expiry_date_obj - datetime.now().date()).days
        
        # Calculate market metrics
        iv_percentile = calculate_iv_percentile_wrapper(option_chain_df, spot_price, days_to_expiry)
        adx_14 = calculate_adx_wrapper(api, symbol_manager)
        has_major_event = check_major_events(expiry_date)
        
        market_state = {
            'iv_percentile': iv_percentile,
            'days_to_expiry': days_to_expiry,
            'adx_14': adx_14,
            'has_major_event': has_major_event,
            'instrument': 'NIFTY',
            'instrument_type': 'WEEKLY',
            'spot_price': spot_price,
            'expiry': _get_date_object(expiry_date).strftime('%Y-%m-%d')
        }
        
        logger.info(
            f"Market state: IV={iv_percentile:.1f}%, DTE={days_to_expiry}, "
            f"ADX={adx_14:.1f}, Event={has_major_event}"
        )
        
        return market_state
        
    except Exception as e:
        logger.error(f"Error building market state: {str(e)}", exc_info=True)
        return None


def save_trade_proposal(trade_proposal, output_dir='trade_proposals'):
    """
    Save trade proposal to JSON file.
    
    Args:
        trade_proposal: Trade proposal dictionary
        output_dir: Directory to save proposals
    """
    try:
        os.makedirs(output_dir, exist_ok=True)
        
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        filename = os.path.join(output_dir, f"iron_condor_{timestamp}.json")
        
        # Convert numpy types to native Python types for JSON serialization
        def convert_types(obj):
            if isinstance(obj, dict):
                return {k: convert_types(v) for k, v in obj.items()}
            elif isinstance(obj, list):
                return [convert_types(item) for item in obj]
            elif isinstance(obj, (pd.Timestamp, datetime)):
                return obj.isoformat()
            elif hasattr(obj, 'item'):  # numpy types
                return obj.item()
            else:
                return obj
        
        trade_proposal_serializable = convert_types(trade_proposal)
        
        with open(filename, 'w') as f:
            json.dump(trade_proposal_serializable, f, indent=2)
        
        logger.info(f"Saved trade proposal to {filename}")
        
    except Exception as e:
        logger.error(f"Error saving trade proposal: {str(e)}")


def run_iron_condor_strategy(api, symbol_manager):
    """
    Run Iron Condor strategy check.
    
    This function:
    1. Gets NIFTY spot price
    2. Gets weekly expiry date
    3. Fetches option chain
    4. Builds market state
    5. Generates trade proposal
    6. Saves proposal if valid
    
    Args:
        api: ShoonyaApiPy instance
        symbol_manager: SymbolManager instance
    
    Returns:
        dict: Trade proposal or None if no valid trade
    """
    try:
        logger.info("=== Running Iron Condor Strategy Check ===")
        
        # Step 1: Get NIFTY spot price
        spot_price = get_nifty_spot_price(api, symbol_manager)
        if spot_price is None or spot_price <= 0:
            logger.warning("Could not get valid NIFTY spot price")
            return None
        
        logger.info(f"NIFTY spot price: {spot_price}")
        
        # Step 2: Get weekly expiry
        expiry_date = get_weekly_expiry()
        expiry_date_obj = _get_date_object(expiry_date)
        days_to_expiry = (expiry_date_obj - datetime.now().date()).days
        
        logger.info(f"Weekly expiry: {expiry_date_obj.strftime('%Y-%m-%d')} ({days_to_expiry} days)")
        
        # Step 3: Get option chain
        option_chain_df = get_option_chain_data(api, symbol_manager, spot_price, expiry_date, count=50)
        if option_chain_df.empty:
            logger.warning("Could not fetch option chain data")
            return None
        
        logger.info(f"Fetched {len(option_chain_df)} option contracts")
        
        # Step 4: Build market state (reuse option chain we already fetched)
        market_state = build_market_state_from_chain(api, symbol_manager, spot_price, expiry_date, option_chain_df)
        if not market_state:
            logger.warning("Could not build market state")
            return None
        
        # Step 5: Generate trade proposal
        trade_proposal = generate_iron_condor_trade(market_state, option_chain_df)
        
        # Step 6: Handle result
        if trade_proposal:
            logger.info("✅ Valid Iron Condor trade found!")
            logger.info(f"   Strategy: {trade_proposal['strategy']}")
            logger.info(f"   Expiry: {trade_proposal['expiry']}")
            logger.info(f"   Lots: {trade_proposal['lots']}")
            logger.info(f"   Net Credit: ₹{trade_proposal['net_credit']:.2f} per lot")
            logger.info(f"   Total Credit: ₹{trade_proposal['net_credit_total']:.2f}")
            logger.info(f"   Max Loss: ₹{trade_proposal['max_loss']:.2f}")
            logger.info(f"   Max Profit: ₹{trade_proposal['max_profit']:.2f}")
            logger.info(f"   Reward-to-Risk: {trade_proposal['reward_to_risk']:.2f}")
            
            # Log legs
            logger.info("   Legs:")
            for leg in trade_proposal['legs']:
                logger.info(
                    f"     {leg['position']} {leg['option_type']} @ {leg['strike']} "
                    f"(Price: ₹{leg['price']:.2f})"
                )
            
            # Save proposal
            save_trade_proposal(trade_proposal)
            
            return trade_proposal
        else:
            logger.info("❌ No valid trade found (market conditions not suitable)")
            return None
            
    except Exception as e:
        logger.error(f"Error running Iron Condor strategy: {str(e)}", exc_info=True)
        return None


def is_market_hours():
    """
    Check if current time is during market hours (9:15 AM - 3:30 PM IST).
    
    Returns:
        bool: True if market is open
    """
    now = datetime.now()
    market_open = now.replace(hour=9, minute=15, second=0, microsecond=0)
    market_close = now.replace(hour=15, minute=30, second=0, microsecond=0)
    
    # Check if it's a weekday (Monday=0, Sunday=6)
    is_weekday = now.weekday() < 5
    
    return is_weekday and market_open <= now <= market_close

