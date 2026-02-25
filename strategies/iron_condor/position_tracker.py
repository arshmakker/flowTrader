"""
Position Tracker for Iron Condor trades
Tracks open positions and monitors profit targets
"""

import json
import os
import logging
from datetime import datetime
from typing import Dict, List, Optional, Any

logger = logging.getLogger(__name__)

# Require N consecutive regime != CONVEX before exiting Convex position on regime change (reduces whipsaw)
CONVEX_REGIME_CHANGE_CONFIRMATION_CHECKS = 3

# Convex trailing stop loss (MTM-based)
CONVEX_TSL_ACTIVATION_MTM_PCT = 0.20   # Activate TSL when mtm >= +20% of entry premium
CONVEX_TSL_ACTIVATION_TIME_PCT = 0.25  # Or when time elapsed >= 25% of expiry
CONVEX_TSL_TRAIL_PCT = 0.35            # Base trailing drawdown 35% from peak
CONVEX_TSL_TRAIL_TIGHT_PCT = 0.25      # Tighten to 25% when time > 40% or ATR% < 30
CONVEX_TSL_TIGHT_TIME_PCT = 0.40
CONVEX_TSL_ATR_TIGHT_THRESHOLD = 30
CONVEX_MAX_LOSS_MTM_PCT = 0.30         # Absolute exit if mtm <= -30% of entry premium


def _to_json_serializable(obj: Any) -> Any:
    """Convert numpy/pandas scalar types to native Python for JSON serialization."""
    try:
        import numpy as np
        if isinstance(obj, (np.integer,)):
            return int(obj)
        if isinstance(obj, (np.floating,)):
            return float(obj)
        if isinstance(obj, (np.bool_,)):
            return bool(obj)
        if isinstance(obj, np.ndarray):
            return obj.tolist()
    except ImportError:
        pass
    if isinstance(obj, dict):
        return {k: _to_json_serializable(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_to_json_serializable(v) for v in obj]
    return obj


class IronCondorPositionTracker:
    """Track and monitor open Iron Condor positions"""
    
    def __init__(self, trade_proposals_dir='trade_proposals', 
                 active_positions_file='active_positions.json'):
        self.trade_proposals_dir = trade_proposals_dir
        self.active_positions_file = active_positions_file
        self.active_positions = self._load_active_positions()
    
    def _load_active_positions(self) -> List[Dict]:
        """Load active positions from file"""
        try:
            if os.path.exists(self.active_positions_file):
                with open(self.active_positions_file, 'r') as f:
                    return json.load(f)
        except Exception as e:
            logger.error(f"Error loading active positions: {e}")
        return []
    
    def _save_active_positions(self):
        """Save active positions to file"""
        try:
            serializable = _to_json_serializable(self.active_positions)
            with open(self.active_positions_file, 'w') as f:
                json.dump(serializable, f, indent=2)
        except Exception as e:
            logger.error(f"Error saving active positions: {e}")
    
    def add_position(self, trade_proposal: Dict):
        """Add a new position to track"""
        import uuid
        
        strategy = trade_proposal.get('strategy', 'UNKNOWN').upper()
        is_futures_strategy = 'FUTURE' in strategy or trade_proposal.get('instrument', '').upper() == 'NIFTY_FUTURE'
        
        # Use proposal_id if available, otherwise generate a unique ID
        # proposal_id groups all legs of a single proposal together
        proposal_id = trade_proposal.get('proposal_id', None)
        if not proposal_id:
            proposal_id = f"{datetime.now().isoformat()}_{uuid.uuid4().hex[:8]}"
        
        # Generate unique trade_id for this specific position (handles multi-leg strategies)
        trade_id = f"{proposal_id}_{uuid.uuid4().hex[:8]}"
        
        # Build position based on strategy type
        position = {
            'trade_id': trade_id,
            'proposal_id': proposal_id,  # Group all legs of a proposal together
            'entry_time': datetime.now().isoformat(),
            'lots': trade_proposal.get('lots', 0),
            'lot_size': trade_proposal.get('lot_size', 50),
            'entry_spot': trade_proposal.get('spot_price'),
            'strategy': trade_proposal.get('strategy', 'UNKNOWN'),
            'book': trade_proposal.get('book', 'UNKNOWN'),
            'regime_at_entry': trade_proposal.get('regime_at_entry', 'UNKNOWN'),
            'status': 'OPEN'
        }
        
        if is_futures_strategy:
            # Futures-specific fields
            # Parse expiry if it's a string (ISO format) or use directly if it's already a date
            expiry = trade_proposal.get('expiry', None)
            if expiry and isinstance(expiry, str):
                try:
                    expiry = datetime.fromisoformat(expiry).date()
                except:
                    pass
            
            position.update({
                'symbol': trade_proposal.get('instrument', 'NIFTY_FUTURE'),  # Add symbol field from instrument
                'expiry': expiry.isoformat() if expiry and hasattr(expiry, 'isoformat') else (expiry if expiry else None),  # Store expiry date
                'days_to_expiry': trade_proposal.get('days_to_expiry', None),  # Days to expiry at entry
                'legs': [],  # Futures don't have legs
                'entry_credit': 0,  # Futures don't have credit/debit
                'margin_used': trade_proposal.get('margin_used'),
                'profit_target_margin': trade_proposal.get('profit_target_margin'),
                'max_loss': trade_proposal.get('risk_amount', 0),  # Use risk_amount as max_loss for futures
                'entry_price': trade_proposal.get('entry_price'),
                'stop_loss_price': trade_proposal.get('stop_loss_price'),
                'current_stop_price': trade_proposal.get('stop_loss_price'),  # Initialize with initial stop, will be updated for trailing
                'direction': trade_proposal.get('direction'),
                'quantity': trade_proposal.get('quantity'),
                'days_to_expiry': trade_proposal.get('days_to_expiry'),
                'days_to_expiry_short': None,
                'entry_range_state': None,
                'entry_iv_percentile': None,
                'entry_prices': {}
            })
        else:
            # Options-specific fields
            position.update({
                'expiry': trade_proposal.get('expiry'),
                'legs': trade_proposal.get('legs', []),
                'entry_credit': trade_proposal.get('net_credit_total', trade_proposal.get('net_debit_total', 0)),
                'margin_used': trade_proposal.get('margin_used'),
                'profit_target_margin': trade_proposal.get('profit_target_margin'),
                'profit_target_inr': trade_proposal.get('profit_target_inr'),  # Convex: 1% of capital
                'max_loss': trade_proposal.get('max_loss', 0),
                'days_to_expiry': trade_proposal.get('days_to_expiry'),
                'days_to_expiry_short': trade_proposal.get('days_to_expiry_short'),
                'entry_range_state': None,  # Will be set from regime_info if available
                'entry_iv_percentile': trade_proposal.get('entry_iv_percentile'),
                'entry_prices': {leg['option_type'] + str(int(leg['strike'])): leg['price'] for leg in trade_proposal.get('legs', [])},
                'profit_locked_inr': 0,  # Trailing lock: first 300, then trail 200 below current PnL
            })
            # Convex-only trailing stop state (MTM-based)
            if position.get('book') == 'CONVEX' or 'BACKSPREAD' in strategy:
                position['convex_tsl_active'] = False
                position['convex_peak_mtm'] = 0.0  # Entry MTM at open
        
        self.active_positions.append(position)
        self._save_active_positions()
        logger.info(f"Added position to tracker: {position['trade_id']} ({strategy})")
    
    def get_active_positions(self) -> List[Dict]:
        """Get all active positions"""
        return [p for p in self.active_positions if p.get('status') == 'OPEN']
    
    def calculate_current_pnl(self, position: Dict, current_prices: Dict) -> float:
        """
        Calculate current P&L for a position
        
        Args:
            position: Position dictionary
            current_prices: Dict of {option_key: current_price}
                          e.g., {'CE26200': 50.5, 'PE25800': 45.2, ...}
        
        Returns:
            Current P&L in ₹
        """
        entry_credit = position['entry_credit']
        lots = position['lots']
        lot_size = position.get('lot_size', 50)
        
        current_value = 0.0
        
        for leg in position['legs']:
            strike = int(leg['strike'])
            option_type = leg['option_type']
            entry_price = leg['price']
            quantity = leg.get('quantity', 1)  # Support different quantities (e.g., 2 for convex backspread)
            
            # Get current price
            option_key = f"{option_type}{strike}"
            current_price = current_prices.get(option_key, entry_price)
            
            # Calculate value change
            if leg['position'] == 'SHORT':
                # Short: profit when price decreases
                # Value = (entry_price - current_price) × lots × lot_size × quantity
                value = (entry_price - current_price) * lots * lot_size * quantity
            else:  # LONG
                # Long: profit when price increases
                # Value = (current_price - entry_price) × lots × lot_size × quantity
                value = (current_price - entry_price) * lots * lot_size * quantity
            
            current_value += value
        
        # P&L calculation depends on strategy type
        strategy = position.get('strategy', '').upper()
        if 'BACKSPREAD' in strategy or position.get('book') == 'CONVEX':
            # For convex backspread: current_value already is total P&L (entry credit + mark-to-market).
            # entry_credit is stored as net_debit (negative when we received credit); do not add it again.
            pnl = current_value
        else:
            # For Iron Condor: entry_credit is positive (we received it)
            # P&L = entry_credit - current_value
            pnl = entry_credit - current_value
        
        return pnl

    def update_trailing_lock_and_check(self, position: Dict, current_pnl: float) -> bool:
        """
        Update trailing PnL lock for Iron Condor: first lock at ₹300, then trail ₹200 below current PnL.
        Updates position['profit_locked_inr'] in place and persists. Call after calculating current_pnl.

        Returns:
            True if trailing stop hit (current_pnl < profit_locked_inr), else False.
        """
        from strategies.iron_condor.exit_rules import MIN_PNL_LOCK_INR, PNL_TRAIL_DISTANCE_INR
        current_lock = position.get('profit_locked_inr', 0)
        if current_pnl >= MIN_PNL_LOCK_INR:
            if current_lock == 0:
                new_lock = MIN_PNL_LOCK_INR
            else:
                new_lock = max(current_lock, current_pnl - PNL_TRAIL_DISTANCE_INR)
            position['profit_locked_inr'] = new_lock
            current_lock = new_lock
        if current_lock > 0 and current_pnl < current_lock:
            return True
        if current_pnl >= MIN_PNL_LOCK_INR:
            self._save_active_positions()
        return False

    def check_profit_target(self, position: Dict, current_pnl: float) -> bool:
        """Profit-target exit disabled: we use only TSL (trailing stop) for Convex and Iron Condor."""
        return False
    
    def check_convex_exit_conditions(self, position: Dict, current_regime: str, 
                                     current_spot: float, entry_spot: float,
                                     days_to_expiry: int, entry_days_to_expiry: int,
                                     current_atr_percentile: float = None,
                                     entry_range_state: str = None,
                                     current_range_state: str = None,
                                     current_mtm: float = None):
        """
        Check mandatory exit conditions for Convex Backspread strategy
        
        MANDATORY exits:
        - Exit when regime flips from TRENDING (or CONVEX) to SIDEWAYS, after confirmation (REGIME_CHANGED)
        - Exit if no ATR expansion within 40% of expiry time
        - Exit if time elapsed > 40% of expiry duration
        - Exit if price re-enters compression range after entry
        - (MTM-based) Exit if current_mtm <= -30% of entry premium (CONVEX_MAX_LOSS)
        - (MTM-based) Trailing stop: activate at +20% mtm or 25% time; exit on drawdown from peak (CONVEX_TSL_HIT)
        
        Args:
            position: Position dictionary
            current_regime: Current detected regime
            current_spot: Current spot price
            entry_spot: Entry spot price
            days_to_expiry: Current days to expiry
            entry_days_to_expiry: Days to expiry at entry
            current_atr_percentile: Current ATR percentile (optional)
            entry_range_state: Range state at entry (optional)
            current_range_state: Current range state (optional)
            current_mtm: Current mark-to-market PnL (required for TSL / max-loss checks)
        
        Returns:
            Tuple of (should_exit: bool, exit_reason: str)
        """
        try:
            strategy = position.get('strategy', '').upper()
            if 'BACKSPREAD' not in strategy and position.get('book') != 'CONVEX':
                # Not a convex position, use standard exit logic
                return False, None
            
            # Exit condition 1: Regime changed from regime at entry (with confirmation to reduce whipsaw)
            # Skip regime-change exit once TSL is active: let TSL or max loss handle exit (reduces regime-change losses)
            # Two-fork: TRENDING = convex-friendly, SIDEWAYS = not; treat TRENDING and CONVEX as equivalent for entry regime
            if not position.get('convex_tsl_active', False):
                regime_at_entry = position.get('regime_at_entry') or 'CONVEX'
                entry_is_trending = regime_at_entry in ('TRENDING', 'CONVEX')
                current_is_trending = current_regime in ('TRENDING', 'CONVEX')
                if entry_is_trending and not current_is_trending:
                    count = position.get('convex_regime_change_count', 0) + 1
                    position['convex_regime_change_count'] = count
                    self._save_active_positions()
                    if count >= CONVEX_REGIME_CHANGE_CONFIRMATION_CHECKS:
                        return True, "REGIME_CHANGED"
                    return False, None
                else:
                    # Still in trending (or entry was sideways); reset regime-change count
                    if position.get('convex_regime_change_count', 0) > 0:
                        position['convex_regime_change_count'] = 0
                        self._save_active_positions()

            # Exit condition 2: Time elapsed > 40% of expiry duration
            if entry_days_to_expiry > 0:
                time_elapsed_pct = (entry_days_to_expiry - days_to_expiry) / entry_days_to_expiry
                if time_elapsed_pct > 0.40:
                    return True, "TIME_ELAPSED_40PCT"
            
            # Exit condition 3: No ATR expansion within 40% of expiry time
            # Check if we're past 40% of time and ATR hasn't expanded
            if entry_days_to_expiry > 0:
                time_elapsed_pct = (entry_days_to_expiry - days_to_expiry) / entry_days_to_expiry
                if time_elapsed_pct >= 0.40:
                    # Check if ATR has expanded (percentile should be higher)
                    if current_atr_percentile is not None:
                        # If ATR percentile is still low (< 30), no expansion occurred
                        if current_atr_percentile < 30:
                            return True, "NO_ATR_EXPANSION"
            
            # Exit condition 4: Price re-entered compression range
            # If range was COMPRESSED at entry and is still COMPRESSED, check if price moved back
            if entry_range_state == "COMPRESSED" and current_range_state == "COMPRESSED":
                # Calculate price movement from entry
                price_change_pct = abs(current_spot - entry_spot) / entry_spot
                # If price moved significantly but range is still compressed, might indicate re-compression
                # This is a conservative check - if we're still in compression after time elapsed, exit
                if entry_days_to_expiry > 0:
                    time_elapsed_pct = (entry_days_to_expiry - days_to_expiry) / entry_days_to_expiry
                    if time_elapsed_pct > 0.30 and price_change_pct < 0.005:  # Less than 0.5% movement
                        return True, "RE_COMPRESSION"
            
            # Time elapsed for TSL / absolute protection (reuse in this block)
            time_elapsed_pct = (entry_days_to_expiry - days_to_expiry) / entry_days_to_expiry if entry_days_to_expiry and entry_days_to_expiry > 0 else 0.0
            
            # Absolute protection (fail-safe): exit if mtm <= -30% of entry premium (regardless of TSL state)
            if current_mtm is not None:
                entry_premium = abs(position.get('entry_credit') or 0)
                if entry_premium > 0 and current_mtm <= -CONVEX_MAX_LOSS_MTM_PCT * entry_premium:
                    return True, "CONVEX_MAX_LOSS"
                
                # Initialize peak MTM if not set (entry MTM = 0 at open)
                if 'convex_peak_mtm' not in position:
                    position['convex_peak_mtm'] = float(current_mtm)
                    self._save_active_positions()
                
                # Activation gate: activate TSL if mtm >= +20% of entry premium OR time >= 25%
                if not position.get('convex_tsl_active', False):
                    mtm_pct = (current_mtm / entry_premium) if entry_premium > 0 else 0.0
                    if (entry_premium > 0 and mtm_pct >= CONVEX_TSL_ACTIVATION_MTM_PCT) or time_elapsed_pct >= CONVEX_TSL_ACTIVATION_TIME_PCT:
                        position['convex_tsl_active'] = True
                        self._save_active_positions()
                
                # Trailing stop: once active, track peak and exit on drawdown
                if position.get('convex_tsl_active', False):
                    old_peak = position.get('convex_peak_mtm', current_mtm)
                    peak = max(old_peak, current_mtm)
                    position['convex_peak_mtm'] = float(peak)
                    if peak != old_peak:
                        self._save_active_positions()
                    trailing_pct = CONVEX_TSL_TRAIL_PCT
                    if time_elapsed_pct > CONVEX_TSL_TIGHT_TIME_PCT or (current_atr_percentile is not None and current_atr_percentile < CONVEX_TSL_ATR_TIGHT_THRESHOLD):
                        trailing_pct = CONVEX_TSL_TRAIL_TIGHT_PCT
                    if current_mtm <= peak * (1.0 - trailing_pct):
                        return True, "CONVEX_TSL_HIT"
            
            return False, None
            
        except Exception as e:
            logger.error(f"Error checking convex exit conditions: {str(e)}")
            return False, None
    
    def check_calendar_exit_conditions(self, position: Dict, current_regime: str,
                                      current_spot: float, entry_spot: float,
                                      days_to_expiry_short: int, entry_days_to_expiry_short: int,
                                      current_prices: Dict, entry_prices: Dict,
                                      current_iv_percentile: float = None,
                                      entry_iv_percentile: float = None) -> tuple:
        """
        Check mandatory exit conditions for Calendar strategy
        
        Exit immediately if ANY trigger fires:
        1) regime != "NEUTRAL"
        2) short option has decayed ≥ 65%
        3) days_to_short_expiry ≤ 1
        4) abs(spot_move) > 0.75 × expected_move
        5) IV spike ≥ +10 points without price follow-through
        
        Args:
            position: Position dictionary
            current_regime: Current detected regime
            current_spot: Current spot price
            entry_spot: Entry spot price
            days_to_expiry_short: Current days to short expiry
            entry_days_to_expiry_short: Days to short expiry at entry
            current_prices: Current option prices dict
            entry_prices: Entry option prices dict
            current_iv_percentile: Current IV percentile (optional)
            entry_iv_percentile: Entry IV percentile (optional)
        
        Returns:
            Tuple of (should_exit: bool, exit_reason: str)
        """
        try:
            strategy = position.get('strategy', '').upper()
            if 'CALENDAR' not in strategy and position.get('book') != 'NEUTRAL':
                # Not a calendar position
                return False, None
            
            from strategies.neutral.config import (
                SHORT_DECAY_THRESHOLD,
                MIN_SHORT_DTE,
                SPOT_MOVE_THRESHOLD,
                IV_SPIKE_THRESHOLD
            )
            
            # Exit condition 1: Regime changed from NEUTRAL
            if current_regime != "NEUTRAL":
                return True, "REGIME_CHANGED"
            
            # Exit condition 2: Days to short expiry ≤ 1
            if days_to_expiry_short <= MIN_SHORT_DTE:
                return True, "SHORT_DTE_THRESHOLD"
            
            # Exit condition 3: Short option decayed ≥ 65%
            # Find short leg (weekly expiry)
            short_leg = None
            long_leg = None
            for leg in position['legs']:
                if leg['position'] == 'SHORT':
                    short_leg = leg
                elif leg['position'] == 'LONG':
                    long_leg = leg
            
            if short_leg:
                entry_short_price = short_leg['price']
                strike = int(short_leg['strike'])
                option_type = short_leg['option_type']
                option_key = f"{option_type}{strike}"
                current_short_price = current_prices.get(option_key, entry_short_price)
                
                # Calculate decay percentage
                if entry_short_price > 0:
                    decay_pct = (entry_short_price - current_short_price) / entry_short_price
                    if decay_pct >= SHORT_DECAY_THRESHOLD:
                        return True, "SHORT_DECAY_65PCT"
            
            # Exit condition 4: Spot move > 0.75 × expected_move
            # Expected move = spot × IV × sqrt(days/365)
            if entry_iv_percentile is not None and entry_days_to_expiry_short > 0:
                entry_iv = entry_iv_percentile / 100.0  # Convert to decimal
                expected_move = entry_spot * entry_iv * (entry_days_to_expiry_short / 365.0) ** 0.5
                spot_move = abs(current_spot - entry_spot)
                
                if spot_move > (SPOT_MOVE_THRESHOLD * expected_move):
                    return True, "SPOT_MOVE_EXCEEDED"
            
            # Exit condition 5: IV spike ≥ +10 points without price follow-through
            if current_iv_percentile is not None and entry_iv_percentile is not None:
                iv_change = current_iv_percentile - entry_iv_percentile
                if iv_change >= IV_SPIKE_THRESHOLD:
                    # Check if price followed through
                    spot_move_pct = abs(current_spot - entry_spot) / entry_spot
                    # If IV spiked but price didn't move much, exit
                    if spot_move_pct < 0.01:  # Less than 1% price move
                        return True, "IV_SPIKE_NO_FOLLOWTHROUGH"
            
            return False, None
            
        except Exception as e:
            logger.error(f"Error checking calendar exit conditions: {str(e)}")
            return False, None
    
    def close_position(self, position: Dict, exit_reason: str, final_pnl: float):
        """Close a position and log performance by regime"""
        position['status'] = 'CLOSED'
        position['exit_time'] = datetime.now().isoformat()
        position['exit_reason'] = exit_reason
        position['final_pnl'] = final_pnl
        self._save_active_positions()
        
        # Log performance by regime
        self._log_performance_by_regime(position, final_pnl)
        
        logger.info(
            f"Closed position {position['trade_id']}: {exit_reason}, "
            f"P&L=₹{final_pnl:.2f}"
        )
    
    def _log_performance_by_regime(self, position: Dict, final_pnl: float):
        """Log trade performance by regime to performance_by_regime.json"""
        try:
            performance_file = 'performance_by_regime.json'
            
            # Load existing performance data
            performance_data = []
            if os.path.exists(performance_file):
                with open(performance_file, 'r') as f:
                    performance_data = json.load(f)
            
            # Create performance entry
            performance_entry = {
                "strategy": position.get('strategy', 'UNKNOWN'),
                "book": position.get('book', 'UNKNOWN'),
                "regime_at_entry": position.get('regime_at_entry', 'UNKNOWN'),
                "entry_time": position.get('entry_time', ''),
                "exit_time": position.get('exit_time', datetime.now().isoformat()),
                "pnl": final_pnl,
                "max_loss": position.get('max_loss', 0),
                "lots": position.get('lots', 0),
                "trade_id": position.get('trade_id', '')
            }
            
            performance_data.append(performance_entry)
            
            # Save to file
            with open(performance_file, 'w') as f:
                json.dump(performance_data, f, indent=2)
            
            logger.debug(f"Logged performance by regime: {performance_entry}")
            
        except Exception as e:
            logger.error(f"Error logging performance by regime: {e}")
