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
        
        for position in active_positions:
            strategy = position.get('strategy', '').upper()
            book = position.get('book', '').upper()
            
            if strategy == 'IRON_CONDOR_WEEKLY' or 'IRON_CONDOR' in strategy:
                iron_condor_active = True
            elif strategy == 'CALL_BACKSPREAD' or book == 'CONVEX':
                convex_active = True
        
        # Mutual exclusion check
        if iron_condor_active and convex_active:
            logger.error(
                "CRITICAL: Both Iron Condor and Convex strategies have active positions! "
                "This violates mutual exclusion. Manual intervention required."
            )
            # Return the first one found (but log error)
            return STRATEGY_IRON_CONDOR
        
        if iron_condor_active:
            return STRATEGY_IRON_CONDOR
        elif convex_active:
            return STRATEGY_CONVEX
        else:
            return STRATEGY_NONE
            
    except Exception as e:
        logger.error(f"Error getting active strategy type: {str(e)}")
        return STRATEGY_NONE


def can_enter_strategy(strategy_type: str, position_tracker: Optional[IronCondorPositionTracker] = None) -> bool:
    """
    Check if a strategy can enter new trades based on mutual exclusion rules.
    
    Args:
        strategy_type: "IRON_CONDOR" or "CONVEX"
        position_tracker: Optional IronCondorPositionTracker instance
    
    Returns:
        True if strategy can enter, False if blocked
    """
    active_strategy = get_active_strategy_type(position_tracker)
    
    if active_strategy == STRATEGY_NONE:
        # No active positions, allow entry
        return True
    
    if strategy_type == STRATEGY_IRON_CONDOR:
        # Iron Condor can only enter if no Convex positions
        if active_strategy == STRATEGY_CONVEX:
            logger.info("Iron Condor blocked: Convex strategy has active positions")
            return False
        return True
    
    elif strategy_type == STRATEGY_CONVEX:
        # Convex can only enter if no Iron Condor positions
        if active_strategy == STRATEGY_IRON_CONDOR:
            logger.info("Convex strategy blocked: Iron Condor has active positions")
            return False
        return True
    
    return False
