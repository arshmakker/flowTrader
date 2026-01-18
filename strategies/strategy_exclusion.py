"""
Strategy Mutual Exclusion Module

Enforces hard mutual exclusion between Iron Condor and Convex strategies.
Ensures only one strategy type can have active positions at a time.
"""

import logging
import json
import os
from typing import Optional, List, Dict
from strategies.iron_condor.position_tracker import IronCondorPositionTracker

logger = logging.getLogger(__name__)

# Strategy types
STRATEGY_IRON_CONDOR = "IRON_CONDOR"
STRATEGY_CONVEX = "CONVEX"
STRATEGY_CALENDAR = "CALENDAR"
STRATEGY_TREND = "TREND"
STRATEGY_NONE = "NONE"


def get_active_strategy_type(position_tracker: Optional[IronCondorPositionTracker] = None,
                            active_positions_file: str = 'active_positions.json') -> str:
    """
    Get the type of strategy that currently has active positions.
    
    This is the single source of truth for strategy exclusion.
    
    Args:
        position_tracker: Optional IronCondorPositionTracker instance
        active_positions_file: Path to active positions file
    
    Returns:
        "IRON_CONDOR" if Iron Condor positions are active
        "CONVEX" if Convex positions are active
        "NONE" if no positions are active
    """
    try:
        # Get active positions
        active_positions = []
        
        if position_tracker:
            active_positions = position_tracker.get_active_positions()
        else:
            # Load directly from file
            if os.path.exists(active_positions_file):
                with open(active_positions_file, 'r') as f:
                    all_positions = json.load(f)
                    active_positions = [p for p in all_positions if p.get('status') == 'OPEN']
        
        if not active_positions:
            return STRATEGY_NONE
        
        # Check strategy types
        iron_condor_active = False
        convex_active = False
        calendar_active = False
        trend_active = False
        
        for position in active_positions:
            strategy = position.get('strategy', '').upper()
            book = position.get('book', '').upper()
            
            if strategy == 'IRON_CONDOR_WEEKLY' or 'IRON_CONDOR' in strategy:
                iron_condor_active = True
            elif strategy == 'CALL_BACKSPREAD' or book == 'CONVEX':
                convex_active = True
            elif strategy == 'ATM_CALL_CALENDAR' or book == 'NEUTRAL':
                calendar_active = True
            elif strategy == 'TREND_FOLLOW_FUTURE' or book == 'TREND':
                trend_active = True
        
        # Mutual exclusion check - only one strategy type can be active
        active_count = sum([iron_condor_active, convex_active, calendar_active, trend_active])
        if active_count > 1:
            logger.error(
                f"CRITICAL: Multiple strategies have active positions! "
                f"Iron Condor: {iron_condor_active}, Convex: {convex_active}, Calendar: {calendar_active}, Trend: {trend_active} "
                "This violates mutual exclusion. Manual intervention required."
            )
            # Return priority order: Iron Condor > Convex > Trend > Calendar
            if iron_condor_active:
                return STRATEGY_IRON_CONDOR
            elif convex_active:
                return STRATEGY_CONVEX
            elif trend_active:
                return STRATEGY_TREND
            else:
                return STRATEGY_CALENDAR
        
        if iron_condor_active:
            return STRATEGY_IRON_CONDOR
        elif convex_active:
            return STRATEGY_CONVEX
        elif trend_active:
            return STRATEGY_TREND
        elif calendar_active:
            return STRATEGY_CALENDAR
        else:
            return STRATEGY_NONE
            
    except Exception as e:
        logger.error(f"Error getting active strategy type: {str(e)}")
        return STRATEGY_NONE


def can_enter_strategy(strategy_type: str, position_tracker: Optional[IronCondorPositionTracker] = None) -> bool:
    """
    Check if a strategy can enter new trades based on mutual exclusion rules.
    
    Args:
        strategy_type: "IRON_CONDOR", "CONVEX", or "CALENDAR"
        position_tracker: Optional IronCondorPositionTracker instance
    
    Returns:
        True if strategy can enter, False if blocked
    """
    active_strategy = get_active_strategy_type(position_tracker)
    
    if active_strategy == STRATEGY_NONE:
        # No active positions, allow entry
        return True
    
    # Calendar can only enter if no other strategies active
    if strategy_type == STRATEGY_CALENDAR:
        if active_strategy != STRATEGY_NONE:
            logger.info(f"Calendar blocked: {active_strategy} strategy has active positions")
            return False
        return True
    
    if strategy_type == STRATEGY_IRON_CONDOR:
        # Iron Condor can only enter if no other strategies active
        if active_strategy != STRATEGY_NONE:
            logger.info(f"Iron Condor blocked: {active_strategy} strategy has active positions")
            return False
        return True
    
    elif strategy_type == STRATEGY_CONVEX:
        # Convex can only enter if no other strategies active
        if active_strategy != STRATEGY_NONE:
            logger.info(f"Convex strategy blocked: {active_strategy} strategy has active positions")
            return False
        return True
    
    elif strategy_type == STRATEGY_TREND:
        # Trend can only enter if no other strategies active
        if active_strategy != STRATEGY_NONE:
            logger.info(f"Trend strategy blocked: {active_strategy} strategy has active positions")
            return False
        return True
    
    return False
