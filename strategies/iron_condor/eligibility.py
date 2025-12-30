"""
Market eligibility checks for Iron Condor strategy
"""

import logging
from .config import (
    IV_PERCENTILE_MIN,
    IV_PERCENTILE_MAX,
    DAYS_TO_EXPIRY_MIN,
    DAYS_TO_EXPIRY_MAX,
    ADX_THRESHOLD,
    TARGET_INSTRUMENT,
    INSTRUMENT_TYPE
)

logger = logging.getLogger(__name__)


def is_market_eligible(market_state: dict) -> bool:
    """
    Check if market conditions are eligible for Iron Condor strategy.
    
    Rules:
    1. IV Percentile between 50 and 100
    2. Days to expiry between 3 and 30
    3. ADX(14) < 22
    4. No RBI or major event in next 48h
    5. Instrument = NIFTY (weekly or monthly)
    
    Args:
        market_state: Dictionary containing:
            - iv_percentile: float (0-100)
            - days_to_expiry: int
            - adx_14: float
            - has_major_event: bool (True if event in next 48h)
            - instrument: str (e.g., "NIFTY")
            - instrument_type: str (e.g., "WEEKLY")
    
    Returns:
        True if ALL conditions pass, False otherwise
    """
    try:
        # Extract required fields
        iv_percentile = market_state.get('iv_percentile')
        days_to_expiry = market_state.get('days_to_expiry')
        adx_14 = market_state.get('adx_14')
        has_major_event = market_state.get('has_major_event', False)
        instrument = market_state.get('instrument', '').upper()
        instrument_type = market_state.get('instrument_type', '').upper()
        
        # Validate all required fields are present
        if iv_percentile is None or days_to_expiry is None or adx_14 is None:
            logger.warning("Missing required market_state fields")
            return False
        
        # Rule 1: IV Percentile check
        if not (IV_PERCENTILE_MIN <= iv_percentile <= IV_PERCENTILE_MAX):
            logger.debug(
                f"IV Percentile {iv_percentile} not in range "
                f"[{IV_PERCENTILE_MIN}, {IV_PERCENTILE_MAX}]"
            )
            return False
        
        # Rule 2: Days to expiry check
        if not (DAYS_TO_EXPIRY_MIN <= days_to_expiry <= DAYS_TO_EXPIRY_MAX):
            logger.debug(
                f"Days to expiry {days_to_expiry} not in range "
                f"[{DAYS_TO_EXPIRY_MIN}, {DAYS_TO_EXPIRY_MAX}]"
            )
            return False
        
        # Rule 3: ADX check
        if adx_14 >= ADX_THRESHOLD:
            logger.debug(f"ADX(14) {adx_14} >= threshold {ADX_THRESHOLD}")
            return False
        
        # Rule 4: Major event check
        if has_major_event:
            logger.debug("Major event detected in next 48h")
            return False
        
        # Rule 5: Instrument check
        if instrument != TARGET_INSTRUMENT:
            logger.debug(f"Instrument {instrument} != {TARGET_INSTRUMENT}")
            return False
        
        # Allow both WEEKLY and MONTHLY expiries for more opportunities
        if instrument_type not in ["WEEKLY", "MONTHLY"]:
            logger.debug(f"Instrument type {instrument_type} not in [WEEKLY, MONTHLY]")
            return False
        
        logger.info("Market eligibility check passed")
        return True
        
    except Exception as e:
        logger.error(f"Error in eligibility check: {str(e)}")
        return False



