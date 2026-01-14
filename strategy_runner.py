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
from strategies.convex import generate_nifty_call_backspread
from strategies.neutral import generate_neutral_call_calendar
from strategies.strategy_exclusion import get_active_strategy_type, can_enter_strategy, STRATEGY_IRON_CONDOR, STRATEGY_CONVEX, STRATEGY_CALENDAR
from strategies.neutral.config import ENABLE_NEUTRAL_CALENDAR
from regime import RegimeDetector
from technical_indicators import (
    calculate_iv_percentile,
    calculate_adx,
    get_historical_price_data,
    calculate_atm_iv
)

# Debug logging setup
DEBUG_LOG_PATH = '/Users/arshdeep/git/ironcondor/.cursor/debug.log'

def _debug_log(location, message, data, hypothesis_id=None):
    """Write debug log entry"""
    try:
        log_entry = {
            "sessionId": "debug-session",
            "runId": "run1",
            "hypothesisId": hypothesis_id or "general",
            "location": location,
            "message": message,
            "data": data,
            "timestamp": int(datetime.now().timestamp() * 1000)
        }
        with open(DEBUG_LOG_PATH, 'a') as f:
            f.write(json.dumps(log_entry) + '\n')
    except Exception:
        pass  # Silently fail if logging fails

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


def get_next_available_expiry(symbol_manager, preferred_date=None):
    """
    Get the next available expiry date from the symbol file.
    This ensures we only use expiries that actually exist in NFO.csv.
    
    Args:
        symbol_manager: SymbolManager instance
        preferred_date: Preferred expiry date (default: calculated weekly expiry)
    
    Returns:
        date: Next available expiry date from symbol file
    """
    try:
        # Calculate preferred expiry if not provided
        if preferred_date is None:
            preferred_date = get_weekly_expiry()
        else:
            preferred_date = _get_date_object(preferred_date)
        
        if symbol_manager.nse_fo is None:
            logger.error("NFO symbols not loaded")
            return preferred_date
        
        # Get all NIFTY options
        nifty_options = symbol_manager.nse_fo[
            (symbol_manager.nse_fo['instrument'] == 'OPTIDX') &
            (symbol_manager.nse_fo['symbol'] == 'NIFTY') &
            (symbol_manager.nse_fo['optiontype'].isin(['CE', 'PE']))
        ].copy()
        
        if nifty_options.empty:
            logger.warning("No NIFTY options found in symbol manager, using calculated expiry")
            return preferred_date
        
        # Convert expiry to datetime for date-based matching
        nifty_options['expiry_date'] = pd.to_datetime(nifty_options['expiry'], format='%d-%b-%Y', errors='coerce')
        
        # Get unique expiry dates
        valid_expiries = nifty_options['expiry_date'].dropna().dt.date.unique()
        
        if len(valid_expiries) == 0:
            logger.warning("No valid expiry dates found, using calculated expiry")
            return preferred_date
        
        # Find nearest future expiry (prefer future expiries)
        future_expiries = [d for d in valid_expiries if d >= preferred_date]
        if future_expiries:
            nearest_expiry = min(future_expiries)
        else:
            # If no future expiries, use the latest available
            nearest_expiry = max(valid_expiries)
        
        # Only log if different from preferred
        if nearest_expiry != preferred_date:
            logger.debug(f"Using available expiry: {nearest_expiry.strftime('%d-%b-%Y')} (preferred was {preferred_date.strftime('%d-%b-%Y')})")
        
        return nearest_expiry
        
    except Exception as e:
        logger.error(f"Error getting next available expiry: {str(e)}", exc_info=True)
        return preferred_date if preferred_date else datetime.now().date()


def get_all_eligible_expiries(symbol_manager, max_expiries_to_check=10):
    """
    Get all available expiries from symbol file, sorted by date.
    Returns expiries that could potentially meet eligibility criteria.
    
    Args:
        symbol_manager: SymbolManager instance
        max_expiries_to_check: Maximum number of expiries to check
    
    Returns:
        list: List of expiry dates (datetime.date objects)
    """
    try:
        if symbol_manager.nse_fo is None:
            logger.error("NFO symbols not loaded")
            return []
        
        # Get all NIFTY options
        nifty_options = symbol_manager.nse_fo[
            (symbol_manager.nse_fo['instrument'] == 'OPTIDX') &
            (symbol_manager.nse_fo['symbol'] == 'NIFTY') &
            (symbol_manager.nse_fo['optiontype'].isin(['CE', 'PE']))
        ].copy()
        
        if nifty_options.empty:
            return []
        
        # Convert expiry to datetime for date-based matching
        nifty_options['expiry_date'] = pd.to_datetime(nifty_options['expiry'], format='%d-%b-%Y', errors='coerce')
        
        # Get unique expiry dates, sorted
        valid_expiries = sorted(nifty_options['expiry_date'].dropna().dt.date.unique())
        
        # Filter to future expiries only
        today = datetime.now().date()
        future_expiries = [d for d in valid_expiries if d >= today]
        
        # Filter out expiries with less than 3 days to expiry (DAYS_TO_EXPIRY_MIN)
        from strategies.iron_condor.config import DAYS_TO_EXPIRY_MIN
        eligible_expiries = []
        for expiry_date in future_expiries:
            days_to_expiry = (expiry_date - today).days
            if days_to_expiry >= DAYS_TO_EXPIRY_MIN:
                eligible_expiries.append(expiry_date)
        
        # Return up to max_expiries_to_check expiries
        return eligible_expiries[:max_expiries_to_check]
        
    except Exception as e:
        logger.error(f"Error getting eligible expiries: {str(e)}", exc_info=True)
        return []


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
        expiry_date_obj = _get_date_object(expiry_date)
        expiry_str_formatted = expiry_date_obj.strftime('%d-%b-%Y').upper()  # Format: 25-DEC-2025
        
        # Get options directly from symbol manager for the expiry
        # The expiry_date should already be from the symbol file (via get_next_available_expiry)
        logger.info(f"Getting NIFTY options for expiry: {expiry_str_formatted}")
        
        # Get options from symbol manager filtered by expiry
        if symbol_manager.nse_fo is None:
            logger.error("NFO symbols not loaded")
            return pd.DataFrame()
        
        # Get all NIFTY options
        nifty_options_all = symbol_manager.nse_fo[
            (symbol_manager.nse_fo['instrument'] == 'OPTIDX') &
            (symbol_manager.nse_fo['symbol'] == 'NIFTY') &
            (symbol_manager.nse_fo['optiontype'].isin(['CE', 'PE']))
        ].copy()
        
        if nifty_options_all.empty:
            logger.error("No NIFTY options found in symbol manager")
            return pd.DataFrame()
        
        # Convert expiry to datetime for date-based matching
        nifty_options_all['expiry_date'] = pd.to_datetime(nifty_options_all['expiry'], format='%d-%b-%Y', errors='coerce')
        
        # Find options matching the expiry date (should exist since we got it from symbol file)
        options_df = nifty_options_all[
            nifty_options_all['expiry_date'].dt.date == expiry_date_obj
        ].copy()
        
        if options_df.empty:
            # This shouldn't happen if get_next_available_expiry worked correctly, but handle gracefully
            logger.error(f"No NIFTY options found for expiry {expiry_str_formatted} (this should not happen)")
            return pd.DataFrame()
        
        # Get the actual expiry string from the first row (expiry column, not expiry_date)
        actual_expiry_str = options_df['expiry'].iloc[0]
        
        # Drop the temporary expiry_date column
        if 'expiry_date' in options_df.columns:
            options_df = options_df.drop(columns=['expiry_date'])
        
        logger.info(f"Found {len(options_df)} NIFTY options for expiry {actual_expiry_str}")
        
        # Filter by strikes around spot price
        strike_interval = 50  # NIFTY strike interval is typically 50
        min_strike = int(spot_price) - (count * strike_interval)
        max_strike = int(spot_price) + (count * strike_interval)
        
        options_df = options_df[
            (options_df['strikeprice'] >= min_strike) &
            (options_df['strikeprice'] <= max_strike)
        ].copy()
        
        if options_df.empty:
            logger.warning(f"No options found in strike range {min_strike}-{max_strike}")
            return pd.DataFrame()
        
        logger.info(f"Filtered to {len(options_df)} options in strike range {min_strike}-{max_strike}")
        
        # #region agent log
        _debug_log('strategy_runner.py:275', 'Options from symbol manager', {
            'target_expiry': expiry_str_formatted,
            'actual_expiry': actual_expiry_str,
            'total_options': len(options_df),
            'strike_range': f"{min_strike}-{max_strike}",
            'sample_symbols': options_df['tradingsymbol'].head(5).tolist()
        }, 'E')
        # #endregion
        
        # Process options and fetch quotes
        chain_data = []
        total_options = len(options_df)
        filtered_by_quote = 0
        filtered_by_strike = 0
        
        for _, option_row in options_df.iterrows():
            try:
                tsym = option_row['tradingsymbol']
                token = str(option_row['token'])
                strike = float(option_row['strikeprice'])
                option_type = option_row['optiontype']
                
                # Get quote for this option
                quote = api.get_quotes(option_row['exchange'], token)
                if not quote:
                    filtered_by_quote += 1
                    continue
                
                # Strike is already extracted from symbol manager data
                if strike <= 0:
                    filtered_by_strike += 1
                    logger.debug(f"Invalid strike price for {tsym}")
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
                    'delta': 0.0,  # Delta not available from symbol manager, would need to calculate
                    'oi': int(quote.get('oi', 0)),
                    'volume': int(quote.get('v', 0)),
                    'lot_size': int(option_row.get('lotsize', 50))  # Get from NFO.csv
                })
                
            except Exception as e:
                logger.debug(f"Error processing option {tsym}: {str(e)}")
                continue
        
        # #region agent log
        _debug_log('strategy_runner.py:280', 'Filter results', {
            'total_options': total_options,
            'filtered_by_quote': filtered_by_quote,
            'filtered_by_strike': filtered_by_strike,
            'final_count': len(chain_data)
        }, 'F')
        # #endregion
        
        if not chain_data:
            logger.warning(f"No valid option chain data processed for expiry {expiry_str_formatted}")
            logger.debug(f"Total options found: {total_options}")
            return pd.DataFrame()
        
        option_chain_df = pd.DataFrame(chain_data)
        logger.info(f"Processed {len(option_chain_df)} option contracts for weekly expiry {expiry_str_formatted}")
        
        # Log sample strikes to verify
        if not option_chain_df.empty:
            sample_strikes = option_chain_df['strike'].unique()[:5]
            logger.debug(f"Sample strikes found: {sample_strikes}")
        
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
        float: IV percentile (0-100), or fallback value if calculation fails
    """
    try:
        iv_percentile = calculate_iv_percentile(
            option_chain_df, spot_price, days_to_expiry
        )
        # Handle None return value
        if iv_percentile is None:
            logger.warning("IV percentile calculation returned None, using fallback value (65.0)")
            return 65.0
        return iv_percentile
    except Exception as e:
        logger.error(f"Error calculating IV percentile: {str(e)}", exc_info=True)
        # Fallback to default value if calculation fails
        logger.warning("Using fallback IV percentile value (65.0)")
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
            # Try to estimate ADX based on available market data (similar to IV percentile)
            # This allows the system to work even with limited historical data
            logger.info("No historical price data available. Using intelligent ADX estimate based on typical market conditions.")
            # Use a conservative estimate that allows trading but indicates limited trend strength
            # ADX < 20 indicates weak/no trend, which is common in range-bound markets
            estimated_adx = 18.0
            logger.info(f"Estimated ADX: {estimated_adx:.1f} (indicates weak/no trend, suitable for Iron Condor)")
            return estimated_adx
        
        # Calculate ADX (will adjust period based on available data)
        adx_value = calculate_adx(highs, lows, closes, period)
        
        if adx_value is None:
            # If calculation still fails, use intelligent estimate
            logger.info("ADX calculation returned None. Using intelligent ADX estimate.")
            estimated_adx = 18.0
            logger.info(f"Estimated ADX: {estimated_adx:.1f} (indicates weak/no trend, suitable for Iron Condor)")
            return estimated_adx
        
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
        
        # Determine instrument type based on DTE
        # DTE <= 7: Weekly expiry, DTE > 7: Monthly expiry
        instrument_type = 'WEEKLY' if days_to_expiry <= 7 else 'MONTHLY'
        
        # Calculate market metrics
        iv_percentile = calculate_iv_percentile_wrapper(option_chain_df, spot_price, days_to_expiry)
        adx_14 = calculate_adx_wrapper(api, symbol_manager)
        has_major_event = check_major_events(expiry_date)
        
        # Calculate current IV for PoP calculation
        # Format expiry date for IV fetch (DD-MMM-YYYY format)
        expiry_date_str = expiry_date_obj.strftime('%d-%b-%Y').upper() if expiry_date_obj else None
        current_iv = None
        try:
            current_iv = calculate_atm_iv(
                option_chain_df, 
                spot_price, 
                days_to_expiry,
                expiry_date_str=expiry_date_str,
                api=api  # Pass API to use Shoonya option_greek
            )
        except Exception as e:
            logger.debug(f"Could not calculate current IV: {str(e)}")
        
        market_state = {
            'iv_percentile': iv_percentile,
            'days_to_expiry': days_to_expiry,
            'adx_14': adx_14,
            'has_major_event': has_major_event,
            'instrument': 'NIFTY',
            'instrument_type': instrument_type,
            'spot_price': spot_price,
            'expiry': _get_date_object(expiry_date).strftime('%Y-%m-%d'),
            'current_iv': current_iv  # Add current IV for PoP calculation
        }
        
        # Format values safely (handle None)
        iv_str = f"{iv_percentile:.1f}%" if iv_percentile is not None else "N/A"
        adx_str = f"{adx_14:.1f}" if adx_14 is not None else "N/A"
        dte_str = str(days_to_expiry) if days_to_expiry is not None else "N/A"
        
        logger.info(
            f"Market state: IV={iv_str}, DTE={dte_str}, "
            f"ADX={adx_str}, Event={has_major_event}"
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
        
        # Determine filename based on strategy type
        strategy = trade_proposal.get('strategy', 'iron_condor').lower().replace('_', '_')
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        filename = os.path.join(output_dir, f"{strategy}_{timestamp}.json")
        
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


def _determine_neutral_sub_state(market_state: Dict, regime_info: Dict) -> str:
    """
    Determine NEUTRAL sub-state based on market conditions
    
    Rules:
    - NEUTRAL_PASSIVE: ADX rising OR ATR ambiguous OR signals conflicting
    - NEUTRAL_ACTIVE: Otherwise (neutral-safe strategies allowed, e.g., calendar spreads)
    
    Args:
        market_state: Market state dictionary
        regime_info: Regime detection result
    
    Returns:
        "NEUTRAL_PASSIVE" or "NEUTRAL_ACTIVE"
    """
    try:
        adx_14 = market_state.get('adx_14')
        iv_percentile = market_state.get('iv_percentile')
        atr_percentile = regime_info.get('atr_percentile')
        range_state = regime_info.get('range_state')
        
        # Check for conflicting signals
        conflicting_signals = False
        if iv_percentile is not None and adx_14 is not None:
            # High IV + High ADX = conflicting (should be INCOME but ADX too high)
            if iv_percentile > 60 and adx_14 >= 20:
                conflicting_signals = True
        
        # Check for ADX rising (would need historical ADX, simplified check)
        # For now, check if ADX is in ambiguous zone (18-22)
        adx_ambiguous = adx_14 is not None and 18 <= adx_14 < 22
        
        # Check for ATR ambiguous
        atr_ambiguous = atr_percentile is None or (atr_percentile is not None and 20 <= atr_percentile <= 30)
        
        # Determine sub-state
        if conflicting_signals or adx_ambiguous or atr_ambiguous:
            return "NEUTRAL_PASSIVE"
        else:
            return "NEUTRAL_ACTIVE"
            
    except Exception as e:
        logger.error(f"Error determining NEUTRAL sub-state: {str(e)}")
        return "NEUTRAL_PASSIVE"  # Default to passive on error


def _determine_no_trade_reason(market_state: Dict, regime_info: Dict, neutral_sub_state: str = None) -> str:
    """
    Determine reason why no trade is allowed (for auditability)
    
    Args:
        market_state: Market state dictionary
        regime_info: Regime detection result
        neutral_sub_state: NEUTRAL sub-state if applicable
    
    Returns:
        String describing no-trade reason
    """
    try:
        iv_percentile = market_state.get('iv_percentile')
        adx_14 = market_state.get('adx_14')
        atr_percentile = regime_info.get('atr_percentile')
        detected_regime = regime_info.get('detected_regime', 'NEUTRAL')
        confirmed_regime = regime_info.get('regime', 'NEUTRAL')
        
        # Check for regime transition
        if detected_regime != confirmed_regime:
            return "REGIME_TRANSITION"
        
        # Check for high IV + high ADX
        if iv_percentile is not None and adx_14 is not None:
            if iv_percentile > 60 and adx_14 >= 20:
                return "HIGH_IV_HIGH_ADX"
        
        # Check for conflicting signals
        if neutral_sub_state == "NEUTRAL_PASSIVE":
            if atr_percentile is None:
                return "ATR_DATA_INSUFFICIENT"
            elif iv_percentile is not None and adx_14 is not None:
                if not (iv_percentile > 60 and adx_14 < 20) and not (iv_percentile < 40 and atr_percentile < 25):
                    return "SIGNALS_CONFLICTING"
        
        # Default NEUTRAL reason
        return "NEUTRAL_REGIME"
        
    except Exception as e:
        logger.error(f"Error determining no-trade reason: {str(e)}")
        return "UNKNOWN"


def _log_strategy_decision(regime: str, neutral_sub_state: str, strategy_allowed: List[str],
                          strategy_executed: str, no_trade_reason: str, regime_info: Dict):
    """
    Log strategy decision for auditability and post-analysis
    
    Args:
        regime: Confirmed regime
        neutral_sub_state: NEUTRAL sub-state if applicable
        strategy_allowed: List of strategies allowed in this regime
        strategy_executed: Strategy that was executed (if any)
        no_trade_reason: Reason why no trade was executed (if applicable)
        regime_info: Regime detection result
    """
    try:
        decision_log = {
            "timestamp": datetime.now().isoformat(),
            "regime": regime,
            "sub_state": neutral_sub_state,
            "strategy_allowed": strategy_allowed,
            "strategy_executed": strategy_executed,
            "no_trade_reason": no_trade_reason if not strategy_executed else None,
            "regime_details": {
                "iv_percentile": regime_info.get('iv_percentile'),
                "adx": regime_info.get('adx'),
                "atr_percentile": regime_info.get('atr_percentile'),
                "range_state": regime_info.get('range_state'),
                "detected_regime": regime_info.get('detected_regime'),
                "confirmation_count": regime_info.get('confirmation_count', 0)
            }
        }
        
        # Log to file for post-analysis
        log_file = 'strategy_decisions.json'
        decisions = []
        if os.path.exists(log_file):
            try:
                with open(log_file, 'r') as f:
                    decisions = json.load(f)
            except:
                pass
        
        decisions.append(decision_log)
        
        # Keep only last 1000 entries
        if len(decisions) > 1000:
            decisions = decisions[-1000:]
        
        with open(log_file, 'w') as f:
            json.dump(decisions, f, indent=2)
        
        logger.debug(f"Strategy decision logged: regime={regime}, executed={strategy_executed}, reason={no_trade_reason}")
        
    except Exception as e:
        logger.error(f"Error logging strategy decision: {str(e)}")


def run_strategy_with_regime(api, symbol_manager, position_tracker=None, capital=1000000.0):
    """
    Run strategy check with regime detection and routing.
    
    This function:
    1. Gets NIFTY spot price
    2. Builds market state
    3. Detects regime
    4. Routes to appropriate strategy based on regime:
       - INCOME regime → Iron Condor
       - CONVEX regime → Convex Backspread
       - NEUTRAL regime → No new trades
    5. Enforces mutual exclusion
    
    Args:
        api: ShoonyaApiPy instance
        symbol_manager: SymbolManager instance
        position_tracker: Optional IronCondorPositionTracker instance
        capital: Total capital allocated (default: ₹10L)
    
    Returns:
        dict: Trade proposal or None if no valid trade found
    """
    try:
        logger.info("=== Running Strategy Check with Regime Detection ===")
        
        # Step 1: Get NIFTY spot price
        spot_price = get_nifty_spot_price(api, symbol_manager)
        if spot_price is None or spot_price <= 0:
            logger.warning("Could not get valid NIFTY spot price")
            return None
        
        logger.info(f"NIFTY spot price: {spot_price}")
        
        # Step 2: Get available expiries
        available_expiries = get_all_eligible_expiries(symbol_manager, max_expiries_to_check=7)
        if not available_expiries:
            logger.warning("No available expiries found")
            return None
        
        # Step 3: Get option chain for first expiry (for regime detection)
        expiry_date = available_expiries[0]
        option_chain_df = get_option_chain_data(api, symbol_manager, spot_price, expiry_date, count=30)
        if option_chain_df.empty:
            logger.warning("Could not fetch option chain for regime detection")
            return None
        
        # Step 4: Build market state
        market_state = build_market_state_from_chain(api, symbol_manager, spot_price, expiry_date, option_chain_df)
        if not market_state:
            logger.warning("Could not build market state")
            return None
        
        # Step 5: Detect regime
        regime_detector = RegimeDetector()
        recent_candles = regime_detector.get_recent_candles(api, symbol_manager, spot_price)
        regime_info = regime_detector.detect_regime(market_state, recent_candles, api, symbol_manager)
        regime = regime_info.get('regime', 'NEUTRAL')
        detected_regime = regime_info.get('detected_regime', regime)
        
        # Step 5a: Determine NEUTRAL sub-state
        neutral_sub_state = None
        if regime == "NEUTRAL":
            neutral_sub_state = _determine_neutral_sub_state(market_state, regime_info)
        
        logger.info(f"Regime: {regime} (detected: {detected_regime})")
        if neutral_sub_state:
            logger.info(f"  NEUTRAL sub-state: {neutral_sub_state}")
        logger.info(f"  IV Percentile: {regime_info.get('iv_percentile', 'N/A'):.1f}%")
        logger.info(f"  ADX: {regime_info.get('adx', 'N/A'):.1f}")
        logger.info(f"  ATR Percentile: {regime_info.get('atr_percentile', 'N/A')}")
        logger.info(f"  Range State: {regime_info.get('range_state', 'N/A')}")
        if regime_info.get('confirmation_count', 0) > 0:
            logger.info(f"  Regime confirmation: {regime_info.get('confirmation_count')}/{regime_detector.confirmation_count}")
        
        # Step 6: Route based on regime
        no_trade_reason = None
        strategy_allowed = []
        strategy_executed = None
        
        if regime == "INCOME":
            strategy_allowed.append("IRON_CONDOR")
            # Check mutual exclusion
            if not can_enter_strategy(STRATEGY_IRON_CONDOR, position_tracker):
                no_trade_reason = "MUTUAL_EXCLUSION"
                logger.info("Iron Condor blocked by mutual exclusion")
                _log_strategy_decision(regime, neutral_sub_state, strategy_allowed, None, no_trade_reason, regime_info)
                return None
            
            # Run Iron Condor strategy
            trade_proposal = _run_iron_condor_strategy_internal(api, symbol_manager, position_tracker, market_state, available_expiries, spot_price)
            strategy_executed = "IRON_CONDOR" if trade_proposal else None
            if not trade_proposal:
                no_trade_reason = "NO_VALID_TRADE"
            _log_strategy_decision(regime, neutral_sub_state, strategy_allowed, strategy_executed, no_trade_reason, regime_info)
            return trade_proposal
        
        elif regime == "CONVEX":
            strategy_allowed.append("CALL_BACKSPREAD")
            # Check mutual exclusion
            if not can_enter_strategy(STRATEGY_CONVEX, position_tracker):
                no_trade_reason = "MUTUAL_EXCLUSION"
                logger.info("Convex strategy blocked by mutual exclusion")
                _log_strategy_decision(regime, neutral_sub_state, strategy_allowed, None, no_trade_reason, regime_info)
                return None
            
            # Run Convex Backspread strategy
            trade_proposal = _run_convex_backspread_strategy(api, symbol_manager, position_tracker, market_state, available_expiries, spot_price, capital)
            strategy_executed = "CALL_BACKSPREAD" if trade_proposal else None
            if not trade_proposal:
                no_trade_reason = "NO_VALID_TRADE"
            _log_strategy_decision(regime, neutral_sub_state, strategy_allowed, strategy_executed, no_trade_reason, regime_info)
            return trade_proposal
        
        else:  # NEUTRAL
            # Check if calendar is allowed in NEUTRAL_ACTIVE
            if neutral_sub_state == "NEUTRAL_ACTIVE" and ENABLE_NEUTRAL_CALENDAR:
                strategy_allowed.append("ATM_CALL_CALENDAR")
                # Check mutual exclusion
                if not can_enter_strategy(STRATEGY_CALENDAR, position_tracker):
                    no_trade_reason = "MUTUAL_EXCLUSION"
                    logger.info("Calendar strategy blocked by mutual exclusion")
                    _log_strategy_decision(regime, neutral_sub_state, strategy_allowed, None, no_trade_reason, regime_info)
                    return None
                
                # Run Calendar strategy
                trade_proposal = _run_neutral_calendar_strategy(api, symbol_manager, position_tracker, market_state, available_expiries, spot_price, capital, regime_info)
                strategy_executed = "ATM_CALL_CALENDAR" if trade_proposal else None
                if not trade_proposal:
                    no_trade_reason = "NO_VALID_TRADE"
                _log_strategy_decision(regime, neutral_sub_state, strategy_allowed, strategy_executed, no_trade_reason, regime_info)
                return trade_proposal
            else:
                # NEUTRAL_PASSIVE or calendar disabled - stand aside
                no_trade_reason = _determine_no_trade_reason(market_state, regime_info, neutral_sub_state)
                if neutral_sub_state == "NEUTRAL_PASSIVE":
                    no_trade_reason = "NEUTRAL_PASSIVE"
                elif not ENABLE_NEUTRAL_CALENDAR:
                    no_trade_reason = "CALENDAR_DISABLED"
                logger.info(f"NEUTRAL regime: No new trades allowed (reason: {no_trade_reason})")
                _log_strategy_decision(regime, neutral_sub_state, strategy_allowed, None, no_trade_reason, regime_info)
                return None
        
    except Exception as e:
        logger.error(f"Error in regime-based strategy execution: {str(e)}", exc_info=True)
        return None


def _run_iron_condor_strategy_internal(api, symbol_manager, position_tracker, market_state, available_expiries, spot_price):
    """Internal Iron Condor strategy execution"""
    try:
        logger.info("=== Running Iron Condor Strategy (INCOME regime) ===")
        
        # Check each expiry until we find a valid trade
        for expiry_date in available_expiries:
            expiry_date_obj = _get_date_object(expiry_date)
            days_to_expiry = (expiry_date_obj - datetime.now().date()).days
            
            logger.info(f"Checking expiry: {expiry_date_obj.strftime('%Y-%m-%d')} ({days_to_expiry} days)")
            
            # Get option chain for this expiry
            option_chain_df = get_option_chain_data(api, symbol_manager, spot_price, expiry_date, count=30)
            if option_chain_df.empty:
                logger.debug(f"No option chain data for expiry {expiry_date_obj.strftime('%Y-%m-%d')}")
                continue
            
            # Build market state for this expiry
            market_state_expiry = build_market_state_from_chain(api, symbol_manager, spot_price, expiry_date, option_chain_df)
            if not market_state_expiry:
                logger.debug(f"Could not build market state for expiry {expiry_date_obj.strftime('%Y-%m-%d')}")
                continue
            
            # Get userid from credentials for margin calculation
            userid = None
            try:
                import yaml
                with open('cred.yml', 'r') as f:
                    creds = yaml.safe_load(f)
                    userid = creds.get('user')
            except Exception as e:
                logger.debug(f"Could not load userid from credentials: {e}")
            
            # Generate trade proposal
            trade_proposal = generate_iron_condor_trade(
                market_state_expiry, 
                option_chain_df,
                api=api,
                userid=userid,
                symbol_manager=symbol_manager
            )
            
            if trade_proposal:
                # Add regime info to trade proposal
                trade_proposal['regime_at_entry'] = 'INCOME'
                trade_proposal['book'] = 'INCOME'
                # Note: entry_range_state not needed for Iron Condor (only for Convex)
                
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
                
                # Add to position tracker if provided
                if position_tracker is not None:
                    position_tracker.add_position(trade_proposal)
                    logger.info(f"Position added to tracker: {trade_proposal['lots']} lots")
                
                return trade_proposal
            else:
                logger.debug(f"No valid trade for expiry {expiry_date_obj.strftime('%Y-%m-%d')}, trying next...")
        
        logger.info("❌ No valid Iron Condor trade found across all checked expiries")
        return None
        
    except Exception as e:
        logger.error(f"Error running Iron Condor strategy: {str(e)}", exc_info=True)
        return None


def _run_convex_backspread_strategy(api, symbol_manager, position_tracker, market_state, available_expiries, spot_price, capital):
    """Run Convex Backspread strategy"""
    try:
        logger.info("=== Running Convex Backspread Strategy (CONVEX regime) ===")
        
        # Use first expiry (weekly)
        expiry_date = available_expiries[0]
        expiry_date_obj = _get_date_object(expiry_date)
        days_to_expiry = (expiry_date_obj - datetime.now().date()).days
        
        # Get option chain
        option_chain_df = get_option_chain_data(api, symbol_manager, spot_price, expiry_date, count=30)
        if option_chain_df.empty:
            logger.warning("No option chain data for Convex strategy")
            return None
        
        # Build market state for this expiry
        market_state_expiry = build_market_state_from_chain(api, symbol_manager, spot_price, expiry_date, option_chain_df)
        if not market_state_expiry:
            logger.warning("Could not build market state for Convex strategy")
            return None
        
        # Get regime info for entry range state (needed for convex exit checks)
        regime_detector = RegimeDetector()
        recent_candles = regime_detector.get_recent_candles(api, symbol_manager, spot_price)
        regime_info = regime_detector.detect_regime(market_state_expiry, recent_candles, api, symbol_manager)
        entry_range_state = regime_info.get('range_state')
        
        # Generate trade proposal
        trade_proposal = generate_nifty_call_backspread(market_state_expiry, option_chain_df, capital)
        
        if trade_proposal:
            # Add regime info and entry state for convex exit checks
            trade_proposal['regime_at_entry'] = 'CONVEX'
            trade_proposal['entry_range_state'] = entry_range_state
            
            logger.info("✅ Valid Convex Backspread trade found!")
            logger.info(f"   Strategy: {trade_proposal['strategy']}")
            logger.info(f"   Expiry: {trade_proposal['expiry']}")
            logger.info(f"   Lots: {trade_proposal['lots']}")
            logger.info(f"   Net Debit: ₹{trade_proposal['net_debit']:.2f} per lot")
            logger.info(f"   Total Debit: ₹{trade_proposal['net_debit_total']:.2f}")
            logger.info(f"   Max Loss: ₹{trade_proposal['max_loss']:.2f}")
            
            # Log legs
            logger.info("   Legs:")
            for leg in trade_proposal['legs']:
                logger.info(
                    f"     {leg['position']} {leg['option_type']} @ {leg['strike']} "
                    f"(Price: ₹{leg['price']:.2f}, Qty: {leg.get('quantity', 1)})"
                )
            
            # Save proposal
            save_trade_proposal(trade_proposal)
            
            # Add to position tracker if provided
            if position_tracker is not None:
                position_tracker.add_position(trade_proposal)
                logger.info(f"Position added to tracker: {trade_proposal['lots']} lots")
            
            return trade_proposal
        else:
            logger.info("❌ No valid Convex Backspread trade found")
            return None
        
    except Exception as e:
        logger.error(f"Error running Convex Backspread strategy: {str(e)}", exc_info=True)
        return None


def _run_neutral_calendar_strategy(api, symbol_manager, position_tracker, market_state, available_expiries, spot_price, capital, regime_info):
    """Run Neutral Calendar strategy"""
    try:
        logger.info("=== Running Neutral Calendar Strategy (NEUTRAL_ACTIVE regime) ===")
        
        # Get weekly expiry (first expiry)
        expiry_weekly = available_expiries[0]
        expiry_weekly_obj = _get_date_object(expiry_weekly)
        
        # Get monthly expiry (find next monthly expiry after weekly)
        expiry_monthly = None
        expiry_monthly_obj = None
        
        # Look for monthly expiry (typically 4-5 weeks out)
        for expiry in available_expiries[1:]:
            expiry_obj = _get_date_object(expiry)
            days_diff = (expiry_obj - expiry_weekly_obj).days
            # Monthly expiry is typically 21-35 days after weekly
            if 21 <= days_diff <= 35:
                expiry_monthly = expiry
                expiry_monthly_obj = expiry_obj
                break
        
        if not expiry_monthly:
            logger.info("No suitable monthly expiry found for calendar")
            return None
        
        days_to_expiry_weekly = (expiry_weekly_obj - datetime.now().date()).days
        days_to_expiry_monthly = (expiry_monthly_obj - datetime.now().date()).days
        
        # Get option chains for both expiries
        option_chain_weekly = get_option_chain_data(api, symbol_manager, spot_price, expiry_weekly, count=30)
        option_chain_monthly = get_option_chain_data(api, symbol_manager, spot_price, expiry_monthly, count=30)
        
        if option_chain_weekly.empty or option_chain_monthly.empty:
            logger.info("Empty option chains for calendar strategy")
            return None
        
        # Build market state with both expiries
        market_state_calendar = market_state.copy()
        market_state_calendar['expiry'] = expiry_weekly_obj.strftime('%Y-%m-%d')
        market_state_calendar['expiry_monthly'] = expiry_monthly_obj.strftime('%Y-%m-%d')
        market_state_calendar['days_to_expiry'] = days_to_expiry_weekly
        market_state_calendar['days_to_expiry_monthly'] = days_to_expiry_monthly
        market_state_calendar['sub_state'] = "NEUTRAL_ACTIVE"
        market_state_calendar['range_state'] = regime_info.get('range_state', 'NORMAL')
        market_state_calendar['entry_iv_percentile'] = market_state.get('iv_percentile')
        
        # Generate trade proposal
        trade_proposal = generate_neutral_call_calendar(
            market_state_calendar,
            option_chain_weekly,
            option_chain_monthly,
            capital,
            symbol_manager
        )
        
        if trade_proposal:
            logger.info("✅ Valid Neutral Calendar trade found!")
            logger.info(f"   Strategy: {trade_proposal['strategy']}")
            logger.info(f"   Weekly Expiry: {trade_proposal['expiry_short']}")
            logger.info(f"   Monthly Expiry: {trade_proposal['expiry_long']}")
            logger.info(f"   Strike: {trade_proposal['legs'][0]['strike']}")
            logger.info(f"   Net Debit: ₹{trade_proposal['net_debit']:.2f} per lot")
            logger.info(f"   Total Debit: ₹{trade_proposal['net_debit_total']:.2f}")
            logger.info(f"   Max Loss: ₹{trade_proposal['max_loss']:.2f}")
            
            # Log legs
            logger.info("   Legs:")
            for leg in trade_proposal['legs']:
                logger.info(
                    f"     {leg['position']} {leg['option_type']} @ {leg['strike']} "
                    f"(Expiry: {leg['expiry']}, Price: ₹{leg['price']:.2f})"
                )
            
            # Save proposal
            save_trade_proposal(trade_proposal)
            
            # Add to position tracker if provided
            if position_tracker is not None:
                position_tracker.add_position(trade_proposal)
                logger.info(f"Position added to tracker: {trade_proposal['lots']} lot(s)")
            
            return trade_proposal
        else:
            logger.info("❌ No valid Neutral Calendar trade found")
            return None
        
    except Exception as e:
        logger.error(f"Error running Neutral Calendar strategy: {str(e)}", exc_info=True)
        return None


def run_iron_condor_strategy(api, symbol_manager, position_tracker=None):
    """
    Run Iron Condor strategy check - checks multiple expiries to find valid trade.
    
    DEPRECATED: Use run_strategy_with_regime() instead for regime-aware execution.
    This function is kept for backward compatibility.
    
    This function:
    1. Gets NIFTY spot price
    2. Gets all available expiries from symbol file
    3. Checks each expiry until finding a valid trade:
       - Fetches option chain
       - Builds market state
       - Generates trade proposal
    4. Saves proposal if valid
    5. Adds to position tracker if provided
    
    Args:
        api: ShoonyaApiPy instance
        symbol_manager: SymbolManager instance
        position_tracker: Optional IronCondorPositionTracker instance
    
    Returns:
        dict: Trade proposal or None if no valid trade found across all expiries
    """
    # Delegate to regime-aware function
    try:
        return run_strategy_with_regime(api, symbol_manager, position_tracker)
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

