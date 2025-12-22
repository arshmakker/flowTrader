"""
Payoff validation for Iron Condor strategy
"""

import logging
from typing import Dict
from .config import (
    NET_CREDIT_MIN,
    NET_CREDIT_MAX,
    MAX_LOSS_PER_LOT_MAX,
    MIN_REWARD_TO_RISK
)

logger = logging.getLogger(__name__)


class StrategyRejectedError(Exception):
    """Raised when strategy fails validation"""
    pass


def validate_payoff(legs: Dict) -> Dict:
    """
    Validate Iron Condor payoff meets risk/reward criteria.
    
    Rules:
    1. Net credit per lot ∈ ₹70–₹110
    2. Max loss per lot ≤ ₹1,500
    3. Reward-to-risk ≥ 2.0
    
    Args:
        legs: Dictionary with keys:
            - short_call: dict with mid_price
            - short_put: dict with mid_price
            - long_call: dict with mid_price
            - long_put: dict with mid_price
    
    Returns:
        Dictionary with:
            - net_credit: float (per lot)
            - max_loss: float (per lot)
            - reward_to_risk: float
            - is_valid: bool
    
    Raises:
        StrategyRejectedError if validation fails
    """
    try:
        # Extract prices (use mid_price if available, else ltp)
        short_call_price = legs['short_call'].get('mid_price', legs['short_call'].get('ltp', 0))
        short_put_price = legs['short_put'].get('mid_price', legs['short_put'].get('ltp', 0))
        long_call_price = legs['long_call'].get('mid_price', legs['long_call'].get('ltp', 0))
        long_put_price = legs['long_put'].get('mid_price', legs['long_put'].get('ltp', 0))
        
        # Calculate net credit (premiums received - premiums paid)
        # Short positions: we receive premium (positive)
        # Long positions: we pay premium (negative)
        net_credit = (short_call_price + short_put_price) - (long_call_price + long_put_price)
        
        # Calculate max loss
        # Max loss occurs when price moves beyond either wing
        # For Iron Condor: max loss = wing width - net credit
        short_call_strike = legs['short_call']['strike']
        short_put_strike = legs['short_put']['strike']
        long_call_strike = legs['long_call']['strike']
        long_put_strike = legs['long_put']['strike']
        
        # Wing widths
        call_wing_width = long_call_strike - short_call_strike
        put_wing_width = short_put_strike - long_put_strike
        
        # Max loss is the larger of the two wings minus net credit
        max_loss = max(call_wing_width, put_wing_width) - net_credit
        
        # Calculate reward-to-risk ratio
        if max_loss > 0:
            reward_to_risk = net_credit / max_loss
        else:
            # If max_loss <= 0, this is actually a net debit trade (invalid)
            reward_to_risk = 0
        
        # Validation checks
        validation_result = {
            "net_credit": net_credit,
            "max_loss": max_loss,
            "reward_to_risk": reward_to_risk,
            "is_valid": False
        }
        
        # Rule 1: Net credit check
        if not (NET_CREDIT_MIN <= net_credit <= NET_CREDIT_MAX):
            logger.warning(
                f"Net credit {net_credit:.2f} not in range "
                f"[{NET_CREDIT_MIN}, {NET_CREDIT_MAX}]"
            )
            raise StrategyRejectedError(
                f"Net credit {net_credit:.2f} outside acceptable range "
                f"[{NET_CREDIT_MIN}, {NET_CREDIT_MAX}]"
            )
        
        # Rule 2: Max loss check
        if max_loss > MAX_LOSS_PER_LOT_MAX:
            logger.warning(
                f"Max loss {max_loss:.2f} exceeds limit {MAX_LOSS_PER_LOT_MAX}"
            )
            raise StrategyRejectedError(
                f"Max loss {max_loss:.2f} exceeds limit {MAX_LOSS_PER_LOT_MAX}"
            )
        
        # Rule 3: Reward-to-risk check
        if reward_to_risk < MIN_REWARD_TO_RISK:
            logger.warning(
                f"Reward-to-risk {reward_to_risk:.2f} below minimum {MIN_REWARD_TO_RISK}"
            )
            raise StrategyRejectedError(
                f"Reward-to-risk {reward_to_risk:.2f} below minimum {MIN_REWARD_TO_RISK}"
            )
        
        # Additional check: ensure it's actually a credit trade
        if net_credit <= 0:
            logger.warning(f"Net credit {net_credit:.2f} is not positive (debit trade)")
            raise StrategyRejectedError(
                f"Net credit {net_credit:.2f} must be positive"
            )
        
        validation_result["is_valid"] = True
        logger.info(
            f"Payoff validation passed: credit={net_credit:.2f}, "
            f"max_loss={max_loss:.2f}, R:R={reward_to_risk:.2f}"
        )
        
        return validation_result
        
    except StrategyRejectedError:
        raise
    except Exception as e:
        logger.error(f"Error in payoff validation: {str(e)}")
        raise StrategyRejectedError(f"Payoff validation error: {str(e)}")



