"""
Payoff validation for Iron Condor strategy
"""

import logging
from typing import Dict, Optional
from .config import (
    NET_CREDIT_MIN,
    NET_CREDIT_MAX,
    MAX_LOSS_PER_LOT_MAX,
    MIN_REWARD_TO_RISK
)
from technical_indicators import calculate_probability_of_profit, calculate_atm_iv

logger = logging.getLogger(__name__)


class StrategyRejectedError(Exception):
    """Raised when strategy fails validation"""
    pass


def validate_payoff(legs: Dict, spot_price: Optional[float] = None, 
                    days_to_expiry: Optional[int] = None, 
                    iv: Optional[float] = None,
                    option_chain: Optional[object] = None) -> Dict:
    """
    Validate Iron Condor payoff meets risk/reward criteria.
    
    Rules:
    1. Net credit per lot ∈ [NET_CREDIT_MIN, NET_CREDIT_MAX]
    2. Max loss per lot ≤ MAX_LOSS_PER_LOT_MAX
    3. Reward-to-risk ≥ MIN_REWARD_TO_RISK
    
    Args:
        legs: Dictionary with keys:
            - short_call: dict with mid_price, strike
            - short_put: dict with mid_price, strike
            - long_call: dict with mid_price, strike
            - long_put: dict with mid_price, strike
        spot_price: Current spot price (for PoP calculation)
        days_to_expiry: Days to expiration (for PoP calculation)
        iv: Implied volatility (annual, as decimal). If None, will try to calculate from option_chain
        option_chain: DataFrame with option chain data (for IV calculation if iv not provided)
    
    Returns:
        Dictionary with:
            - net_credit: float (per lot)
            - max_loss: float (per lot)
            - reward_to_risk: float
            - probability_of_profit: float (percentage, 0-100)
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
        
        # Calculate Probability of Profit (PoP)
        probability_of_profit = None
        if spot_price and days_to_expiry and days_to_expiry > 0:
            # Get IV if not provided
            calculated_iv = iv
            if calculated_iv is None and option_chain is not None:
                try:
                    import pandas as pd
                    if isinstance(option_chain, pd.DataFrame) and not option_chain.empty:
                        calculated_iv = calculate_atm_iv(option_chain, spot_price, days_to_expiry)
                        if calculated_iv:
                            # Convert from percentage to decimal if needed
                            if calculated_iv > 1:
                                calculated_iv = calculated_iv / 100.0
                except Exception as e:
                    logger.debug(f"Could not calculate IV for PoP: {str(e)}")
            
            if calculated_iv and calculated_iv > 0:
                try:
                    probability_of_profit = calculate_probability_of_profit(
                        spot_price=spot_price,
                        short_call_strike=short_call_strike,
                        short_put_strike=short_put_strike,
                        iv=calculated_iv,
                        days_to_expiry=days_to_expiry
                    )
                    logger.debug(f"Calculated PoP: {probability_of_profit:.1f}%")
                except Exception as e:
                    logger.debug(f"Error calculating PoP: {str(e)}")
        
        # Validation checks
        validation_result = {
            "net_credit": net_credit,
            "max_loss": max_loss,
            "reward_to_risk": reward_to_risk,
            "probability_of_profit": probability_of_profit,
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
        pop_str = f", PoP={probability_of_profit:.1f}%" if probability_of_profit is not None else ""
        logger.info(
            f"Payoff validation passed: credit={net_credit:.2f}, "
            f"max_loss={max_loss:.2f}, R:R={reward_to_risk:.2f}{pop_str}"
        )
        
        return validation_result
        
    except StrategyRejectedError:
        raise
    except Exception as e:
        logger.error(f"Error in payoff validation: {str(e)}")
        raise StrategyRejectedError(f"Payoff validation error: {str(e)}")



