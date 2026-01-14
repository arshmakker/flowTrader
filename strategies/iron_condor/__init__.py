"""
Iron Condor Strategy Module

A self-contained, rule-based Iron Condor strategy for NIFTY weekly options.
All logic is deterministic, stateless, and side-effect free.
"""

from .strategy import generate_iron_condor_trade
from .eligibility import is_market_eligible
from .strike_selector import select_strikes
from .payoff_validator import validate_payoff, StrategyRejectedError
from .position_sizer import calculate_lots
from .exit_rules import (
    PROFIT_TARGET_PCT,
    PROFIT_TARGET_MARGIN_PCT,
    STOP_LOSS_MULTIPLIER,
    MANDATORY_EXIT_DTE,
    MANDATORY_EXIT_TIME
)
from .position_tracker import IronCondorPositionTracker

__all__ = [
    'generate_iron_condor_trade',
    'is_market_eligible',
    'select_strikes',
    'validate_payoff',
    'StrategyRejectedError',
    'calculate_lots',
    'PROFIT_TARGET_PCT',
    'PROFIT_TARGET_MARGIN_PCT',
    'STOP_LOSS_MULTIPLIER',
    'MANDATORY_EXIT_DTE',
    'MANDATORY_EXIT_TIME',
    'IronCondorPositionTracker',
]



