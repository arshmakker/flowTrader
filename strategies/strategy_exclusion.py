"""
Strategy Mutual Exclusion Module

Convex and Iron Condor can run and hold positions at the same time.
(Calendar and Trend strategies removed - convex-only mode)
"""

import logging
import json
import os
from typing import Optional, List, Dict
from strategies.iron_condor.position_tracker import IronCondorPositionTracker

logger = logging.getLogger(__name__)

# Strategy types (removed TREND and CALENDAR - convex-only mode)
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
        
        # Check strategy types (Iron Condor and Convex only)
        iron_condor_active = False
        convex_active = False
        
        for position in active_positions:
            strategy = position.get('strategy', '').upper()
            book = position.get('book', '').upper()
            
            if strategy == 'IRON_CONDOR_WEEKLY' or 'IRON_CONDOR' in strategy:
                iron_condor_active = True
            elif strategy == 'CALL_BACKSPREAD' or book == 'CONVEX':
                convex_active = True
        
        # Convex and Iron Condor can coexist
        if iron_condor_active and convex_active:
            return STRATEGY_CONVEX
        if iron_condor_active:
            return STRATEGY_IRON_CONDOR
        if convex_active:
            return STRATEGY_CONVEX
        else:
            return STRATEGY_NONE
            
    except Exception as e:
        logger.error(f"Error getting active strategy type: {str(e)}")
        return STRATEGY_NONE


def can_enter_strategy(strategy_type: str, position_tracker: Optional[IronCondorPositionTracker] = None) -> bool:
    """
    Check if a strategy can enter new trades.
    Convex and Iron Condor can both enter when the other has positions (coexist).
    """
    active_strategy = get_active_strategy_type(position_tracker)
    
    if active_strategy == STRATEGY_NONE:
        return True
    
    # Convex and Iron Condor can run at the same time
    if strategy_type == STRATEGY_IRON_CONDOR:
        if active_strategy in (STRATEGY_NONE, STRATEGY_CONVEX):
            return True
        logger.info(f"Iron Condor blocked: {active_strategy} strategy has active positions")
        return False
    
    if strategy_type == STRATEGY_CONVEX:
        if active_strategy in (STRATEGY_NONE, STRATEGY_IRON_CONDOR, STRATEGY_CONVEX):
            return True
        logger.info(f"Convex strategy blocked: {active_strategy} strategy has active positions")
        return False
    
    return False
