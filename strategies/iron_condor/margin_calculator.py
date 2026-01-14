"""
Margin calculation for Iron Condor strategy using SPAN calculator
"""

import logging
from typing import Dict, Optional, List
from datetime import datetime
from NorenRestApiPy.NorenApi import position

logger = logging.getLogger(__name__)


def calculate_iron_condor_margin(
    api,
    legs: List[Dict],
    lots: int,
    expiry_date: str,
    symbol_name: str = "NIFTY",
    userid: Optional[str] = None,
    lot_size: Optional[int] = None,
    symbol_manager=None
) -> Optional[float]:
    """
    Calculate total margin required for Iron Condor position using SPAN calculator.
    
    For Iron Condor:
    - Short positions (SHORT): Require SPAN + Exposure margin
    - Long positions (LONG): Require premium paid (capital used, not margin)
    
    Total margin = SPAN margin for shorts + Premium paid for longs
    
    Args:
        api: Shoonya API instance
        legs: List of 4 leg dictionaries with position, option_type, strike, price
        lots: Number of lots
        expiry_date: Expiry date in format "YYYY-MM-DD" (e.g., "2026-01-27")
        symbol_name: Underlying symbol (default: "NIFTY")
        userid: User ID for SPAN calculator (if None, will try to extract)
        lot_size: Lot size from NFO.csv (if None, will try to get from symbol_manager)
        symbol_manager: SymbolManager instance to look up lot size if not provided
    
    Returns:
        Total margin required in ₹, or None if calculation fails
    """
    try:
        # Get lot size - priority: parameter > symbol_manager lookup > fallback
        if lot_size is None:
            if symbol_manager is not None:
                lot_size = _get_lot_size_from_symbol_manager(symbol_manager, symbol_name)
            else:
                logger.warning("Lot size not provided and symbol_manager not available, using fallback")
                lot_size = 50  # Fallback (should not happen in production)
        
        if lot_size is None or lot_size <= 0:
            logger.error(f"Invalid lot size: {lot_size}")
            return _calculate_fallback_margin(legs, lots, 50)  # Use 50 as last resort
        
        logger.info(f"Using lot size: {lot_size} (from NFO.csv)")
        
        # Format expiry: YYYY-MM-DD → DD-MMM-YYYY
        expiry_formatted = _format_expiry_date(expiry_date)
        
        # Build position list for SPAN calculator
        position_list = []
        for leg in legs:
            pos = position()
            pos.prd = 'H'  # Hedge product type
            pos.exch = 'NFO'
            pos.instname = 'OPTIDX'  # Option Index
            pos.symname = symbol_name
            pos.exd = expiry_formatted
            pos.optt = leg['option_type']  # 'CE' or 'PE'
            pos.strprc = str(int(leg['strike']))
            
            qty = lots * lot_size  # Use lot_size from NFO.csv
            
            if leg['position'] == 'SHORT':
                # Short: we sell options
                pos.buyqty = '0'
                pos.sellqty = str(qty)
                pos.netqty = str(-qty)  # Negative for short
            else:
                # Long: we buy options
                pos.buyqty = str(qty)
                pos.sellqty = '0'
                pos.netqty = str(qty)  # Positive for long
            
            position_list.append(pos)
        
        # Get account ID
        actid = userid or _get_account_id(api)
        if not actid:
            logger.warning("Could not determine account ID, using fallback margin")
            return _calculate_fallback_margin(legs, lots, lot_size)
        
        # Call SPAN calculator
        span_result = api.span_calculator(actid, position_list)
        
        # Parse response
        if not span_result:
            logger.warning("SPAN calculator returned None")
            return _calculate_fallback_margin(legs, lots, lot_size)
        
        if span_result.get('stat') != 'Ok':
            error_msg = span_result.get('emsg', 'Unknown error')
            logger.warning(f"SPAN calculator error: {error_msg}")
            return _calculate_fallback_margin(legs, lots, lot_size)
        
        # Extract margin from response
        span_margin = _extract_margin_from_response(span_result)
        if span_margin is None:
            logger.warning("Could not extract margin from SPAN response")
            return _calculate_fallback_margin(legs, lots, lot_size)
        
        # Add premium paid for long positions
        premium_paid = sum(
            leg['price'] * lots * lot_size  # Use lot_size from NFO.csv
            for leg in legs 
            if leg['position'] == 'LONG'
        )
        
        total_margin = span_margin + premium_paid
        
        logger.info(
            f"Margin calculation: SPAN={span_margin:.2f}, "
            f"Premium={premium_paid:.2f}, Total={total_margin:.2f} "
            f"(Lot size: {lot_size})"
        )
        
        return total_margin
        
    except Exception as e:
        logger.error(f"Error in margin calculation: {e}", exc_info=True)
        fallback_lot_size = lot_size if lot_size else 50
        return _calculate_fallback_margin(legs, lots, fallback_lot_size)


def _get_lot_size_from_symbol_manager(symbol_manager, symbol_name):
    """Get lot size from symbol manager for a given symbol"""
    try:
        if symbol_manager is None or symbol_manager.nse_fo is None:
            return None
        
        # Find any option for this symbol to get lot size
        # All options for the same underlying have the same lot size
        symbol_options = symbol_manager.nse_fo[
            (symbol_manager.nse_fo['symbol'] == symbol_name) &
            (symbol_manager.nse_fo['instrument'] == 'OPTIDX')
        ]
        
        if not symbol_options.empty:
            lot_size = int(symbol_options.iloc[0]['lotsize'])
            logger.debug(f"Found lot size for {symbol_name}: {lot_size}")
            return lot_size
        
        logger.warning(f"Could not find lot size for {symbol_name} in symbol manager")
        return None
        
    except Exception as e:
        logger.error(f"Error getting lot size from symbol manager: {e}")
        return None


def _format_expiry_date(expiry_date: str) -> str:
    """
    Convert expiry date from YYYY-MM-DD to DD-MMM-YYYY format
    
    Args:
        expiry_date: Date in format "YYYY-MM-DD"
    
    Returns:
        Date in format "DD-MMM-YYYY" (e.g., "27-JAN-2026")
    """
    try:
        # Parse input date
        dt = datetime.strptime(expiry_date, "%Y-%m-%d")
        # Format to DD-MMM-YYYY
        return dt.strftime("%d-%b-%Y").upper()
    except Exception as e:
        logger.error(f"Error formatting expiry date {expiry_date}: {e}")
        return expiry_date


def _get_account_id(api) -> str:
    """Try to extract account ID from API"""
    if hasattr(api, 'userid'):
        return api.userid
    if hasattr(api, 'get_user_id'):
        return api.get_user_id()
    return None


def _extract_margin_from_response(span_result: dict) -> Optional[float]:
    """Extract margin value from SPAN calculator response"""
    # Try different possible field names
    possible_fields = ['margin', 'marginused', 'marginRequired', 'totalmargin', 'spanmargin']
    
    for field in possible_fields:
        if field in span_result:
            try:
                return float(span_result[field])
            except (ValueError, TypeError):
                continue
    
    # If no standard field found, log the response for debugging
    logger.warning(f"SPAN response structure: {list(span_result.keys())}")
    return None


def _calculate_fallback_margin(legs: List[Dict], lots: int, lot_size: int) -> float:
    """
    Manual margin calculation fallback when SPAN API is unavailable.
    
    This is a simplified calculation. For accurate margin, use SPAN calculator.
    
    For Iron Condor:
    - Short options: Approximate margin ~15-20% of notional value
    - Long options: Premium paid (not margin)
    
    Args:
        legs: List of leg dictionaries
        lots: Number of lots
        lot_size: Lot size from NFO.csv
    
    Returns:
        Estimated margin in ₹
    """
    # Rough estimate: margin for short options only
    short_margin = 0.0
    premium_paid = 0.0
    
    for leg in legs:
        if leg['position'] == 'SHORT':
            # Rough estimate: 15% of strike value as margin
            notional_value = leg['strike'] * lots * lot_size
            estimated_margin = notional_value * 0.15  # 15% of notional
            short_margin += estimated_margin
        else:  # LONG
            premium_paid += leg['price'] * lots * lot_size
    
    total = short_margin + premium_paid
    logger.warning(
        f"Using manual margin calculation (fallback): {total:.2f}. "
        f"For accurate margin, ensure SPAN calculator API is working. "
        f"(lot_size: {lot_size})"
    )
    
    return total
