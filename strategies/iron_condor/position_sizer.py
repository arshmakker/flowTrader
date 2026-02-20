"""
Position sizing logic for Iron Condor strategy
"""

import math
import logging
from .config import MAX_PER_TRADE_RISK

logger = logging.getLogger(__name__)


def calculate_lots(max_loss_per_lot: float) -> int:
    """
    Calculate number of lots based on maximum per-trade risk.
    
    Rules:
    - Max per-trade risk = ₹30,000
    - Minimum lots = 20
    - Capital allocated = ₹10L (not used in calculation, just for reference)
    - Margin buffer = 30% (not used in calculation, just for reference)
    - Lots = floor(30000 / max_loss_per_lot), minimum 20
    
    Args:
        max_loss_per_lot: Maximum loss per lot in ₹
    
    Returns:
        Number of lots (integer, minimum 20)
    """
    MIN_LOTS = 20
    
    try:
        if max_loss_per_lot <= 0:
            logger.warning(f"Invalid max_loss_per_lot: {max_loss_per_lot}")
            return 0
        
        # Calculate lots
        lots = math.floor(MAX_PER_TRADE_RISK / max_loss_per_lot)
        
        # Ensure minimum of 20 lots if calculation allows
        if lots < MIN_LOTS:
            logger.info(f"Adjusting lots from {lots} to minimum {MIN_LOTS}")
            lots = MIN_LOTS
        
        logger.info(f"Calculated {lots} lots for max_loss_per_lot={max_loss_per_lot:.2f}")
        return lots
        
    except Exception as e:
        logger.error(f"Error in position sizing: {str(e)}")
        return 0



