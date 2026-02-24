"""
Position sizing logic for Iron Condor strategy
"""

import math
import logging
from .config import MAX_PER_TRADE_RISK
from strategies.size_config import MIN_LOTS, MAX_LOTS, clamp_lots

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
    try:
        if max_loss_per_lot <= 0:
            logger.warning(f"Invalid max_loss_per_lot: {max_loss_per_lot}")
            return 0
        
        # Calculate lots
        lots = math.floor(MAX_PER_TRADE_RISK / max_loss_per_lot)
        
        # Apply central clamp/limits
        clamped = clamp_lots(lots)
        if clamped == 0:
            # clamped==0 implies lots <= 0 originally; return 0 to indicate invalid sizing
            logger.info(f"Position sizing resulted in 0 lots for max_loss_per_lot={max_loss_per_lot:.2f}")
            return 0
        if clamped != lots:
            logger.info(f"Adjusting lots from {lots} to {clamped} (MIN/MAX enforced)")
            lots = clamped
        
        logger.info(f"Calculated {lots} lots for max_loss_per_lot={max_loss_per_lot:.2f}")
        return lots
        
    except Exception as e:
        logger.error(f"Error in position sizing: {str(e)}")
        return 0



