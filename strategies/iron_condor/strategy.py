"""
Iron Condor strategy orchestrator
"""

import pandas as pd
import logging
from typing import Dict, Optional
from datetime import datetime

from .eligibility import is_market_eligible
from .strike_selector import select_strikes
from .payoff_validator import validate_payoff, StrategyRejectedError
from .position_sizer import calculate_lots
from .exit_rules import (
    PROFIT_TARGET_PCT,
    STOP_LOSS_MULTIPLIER,
    MANDATORY_EXIT_DTE,
    MANDATORY_EXIT_TIME
)

logger = logging.getLogger(__name__)


def generate_iron_condor_trade(
    market_state: dict,
    option_chain: pd.DataFrame
) -> Optional[Dict]:
    """
    Generate Iron Condor trade proposal.
    
    Flow:
    1. Check eligibility
    2. Select strikes
    3. Validate payoff
    4. Size position
    5. Return trade proposal object
    
    Args:
        market_state: Dictionary containing:
            - iv_percentile: float
            - days_to_expiry: int
            - adx_14: float
            - has_major_event: bool
            - instrument: str
            - instrument_type: str
            - spot_price: float (current spot price)
            - expiry: str (YYYY-MM-DD format)
        option_chain: DataFrame with option chain data
    
    Returns:
        Trade proposal dictionary or None if rejected:
        {
            "strategy": "IRON_CONDOR_WEEKLY",
            "expiry": "YYYY-MM-DD",
            "legs": [
                {
                    "position": "SHORT",
                    "option_type": "CE",
                    "strike": float,
                    "price": float,
                    ...
                },
                ...
            ],
            "lots": int,
            "max_profit": float,
            "max_loss": float,
            "net_credit": float,
            "reward_to_risk": float,
            "exit_rules": {
                "profit_target_pct": (0.50, 0.60),
                "stop_loss_multiplier": 1.2,
                "mandatory_exit_dte": 1,
                "mandatory_exit_time": "14:30"
            }
        }
    """
    try:
        logger.info("Starting Iron Condor trade generation")
        
        # Step 1: Check eligibility
        if not is_market_eligible(market_state):
            logger.info("Market not eligible for Iron Condor strategy")
            return None
        
        # Extract spot price and expiry
        spot_price = market_state.get('spot_price')
        expiry = market_state.get('expiry')
        
        if spot_price is None or spot_price <= 0:
            logger.error("Invalid or missing spot_price in market_state")
            return None
        
        if expiry is None:
            logger.error("Missing expiry in market_state")
            return None
        
        # Step 2: Select strikes
        try:
            legs = select_strikes(option_chain, spot_price)
        except Exception as e:
            logger.warning(f"Strike selection failed: {str(e)}")
            return None
        
        # Step 3: Validate payoff
        try:
            # Get days to expiry and IV for PoP calculation
            days_to_expiry = market_state.get('days_to_expiry')
            
            # Try to get IV from market_state or calculate from option chain
            iv = None
            if 'current_iv' in market_state:
                iv = market_state['current_iv']
                # Convert from percentage to decimal if needed
                if iv and iv > 1:
                    iv = iv / 100.0
            
            payoff = validate_payoff(
                legs=legs,
                spot_price=spot_price,
                days_to_expiry=days_to_expiry,
                iv=iv,
                option_chain=option_chain
            )
        except StrategyRejectedError as e:
            logger.info(f"Payoff validation failed: {str(e)}")
            return None
        
        # Step 4: Size position
        max_loss_per_lot = payoff['max_loss']
        lots = calculate_lots(max_loss_per_lot)
        
        if lots == 0:
            logger.info("Position sizing resulted in 0 lots, rejecting trade")
            return None
        
        # Step 5: Build trade proposal
        trade_proposal = {
            "strategy": "IRON_CONDOR_WEEKLY",
            "expiry": expiry,
            "legs": [
                {
                    "position": "SHORT",
                    "option_type": legs['short_call']['option_type'],
                    "strike": legs['short_call']['strike'],
                    "price": legs['short_call'].get('mid_price', legs['short_call'].get('ltp', 0)),
                    "delta": legs['short_call'].get('delta'),
                    "ltp": legs['short_call']['ltp'],
                    "bid": legs['short_call']['bid'],
                    "ask": legs['short_call']['ask'],
                    "oi": legs['short_call']['oi'],
                    "volume": legs['short_call']['volume']
                },
                {
                    "position": "SHORT",
                    "option_type": legs['short_put']['option_type'],
                    "strike": legs['short_put']['strike'],
                    "price": legs['short_put'].get('mid_price', legs['short_put'].get('ltp', 0)),
                    "delta": legs['short_put'].get('delta'),
                    "ltp": legs['short_put']['ltp'],
                    "bid": legs['short_put']['bid'],
                    "ask": legs['short_put']['ask'],
                    "oi": legs['short_put']['oi'],
                    "volume": legs['short_put']['volume']
                },
                {
                    "position": "LONG",
                    "option_type": legs['long_call']['option_type'],
                    "strike": legs['long_call']['strike'],
                    "price": legs['long_call'].get('mid_price', legs['long_call'].get('ltp', 0)),
                    "delta": legs['long_call'].get('delta'),
                    "ltp": legs['long_call']['ltp'],
                    "bid": legs['long_call']['bid'],
                    "ask": legs['long_call']['ask'],
                    "oi": legs['long_call']['oi'],
                    "volume": legs['long_call']['volume']
                },
                {
                    "position": "LONG",
                    "option_type": legs['long_put']['option_type'],
                    "strike": legs['long_put']['strike'],
                    "price": legs['long_put'].get('mid_price', legs['long_put'].get('ltp', 0)),
                    "delta": legs['long_put'].get('delta'),
                    "ltp": legs['long_put']['ltp'],
                    "bid": legs['long_put']['bid'],
                    "ask": legs['long_put']['ask'],
                    "oi": legs['long_put']['oi'],
                    "volume": legs['long_put']['volume']
                }
            ],
            "lots": lots,
            "max_profit": payoff['net_credit'] * lots,
            "max_loss": payoff['max_loss'] * lots,
            "net_credit": payoff['net_credit'],
            "net_credit_total": payoff['net_credit'] * lots,
            "max_loss_per_lot": payoff['max_loss'],
            "reward_to_risk": payoff['reward_to_risk'],
            "probability_of_profit": payoff.get('probability_of_profit'),
            "spot_price": spot_price,
            "generated_at": datetime.now().isoformat(),
            "exit_rules": {
                "profit_target_pct": PROFIT_TARGET_PCT,
                "stop_loss_multiplier": STOP_LOSS_MULTIPLIER,
                "mandatory_exit_dte": MANDATORY_EXIT_DTE,
                "mandatory_exit_time": MANDATORY_EXIT_TIME
            }
        }
        
        pop_str = f", PoP={payoff.get('probability_of_profit', 0):.1f}%" if payoff.get('probability_of_profit') is not None else ""
        logger.info(
            f"Iron Condor trade proposal generated: {lots} lots, "
            f"credit={payoff['net_credit']:.2f}, max_loss={payoff['max_loss']:.2f}{pop_str}"
        )
        
        return trade_proposal
        
    except Exception as e:
        logger.error(f"Error generating Iron Condor trade: {str(e)}", exc_info=True)
        return None



