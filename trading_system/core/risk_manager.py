"""
Risk Manager — implements the 3x combined stop-loss and recovery protocol (agents.md).

- Hard Stop-Loss: 3x combined max profit of all spreads.
- Recovery Protocol: Single-sided recovery if stop-loss hit before 1:00 PM and VIX stable/falling.
"""

import logging
from datetime import datetime, time
from typing import Dict, List, Any

from trading_system.config import settings

logger = logging.getLogger(__name__)

class RiskManager:
    def __init__(self):
        self.daily_pnl = 0.0
        self.halted = False
        self.recovery_mode = False
        self.recovery_side = None # 'BULL_PUT' | 'BEAR_CALL'
        self.stop_hit_at = None

    def update_pnl(self, pnl: float):
        self.daily_pnl += pnl

    def check_combined_stop_loss(self, active_strategies: List[Any]) -> bool:
        """
        Checks if the combined unrealized P&L of all active instruments 
        hits the 3x combined max profit threshold.
        """
        if self.halted:
            return True

        total_unrealized = 0.0
        total_max_profit = 0.0
        
        for s in active_strategies:
            if s.is_active():
                pos = s._position
                # Calculate current unrealized P&L for this strategy
                prices = {
                    'sc': s.md.get_ltp(pos.sc_sym),
                    'sp': s.md.get_ltp(pos.sp_sym),
                    'lc': s.md.get_ltp(pos.lc_sym),
                    'lp': s.md.get_ltp(pos.lp_sym)
                }
                if any(p <= 0 for p in prices.values()):
                    continue

                current_prem = (prices['sc'] + prices['sp']) - (prices['lc'] + prices['lp'])
                lot_size = s.md.get_lot_size(pos.sc_sym)
                total_unrealized += (pos.entry_credit - current_prem) * pos.lots * lot_size
                total_max_profit += pos.max_profit

        if total_max_profit > 0:
            stop_limit = -total_max_profit * settings.IC_STOP_LOSS_MULT
            if total_unrealized <= stop_limit:
                logger.critical(f"HARD STOP HIT: Combined PnL {total_unrealized:.2f} <= Limit {stop_limit:.2f}")
                self.halted = True
                self.stop_hit_at = datetime.now()
                return True
        
        return False

    def can_enter_recovery(self, vix_stable: bool, vix_falling: bool) -> bool:
        """
        Recovery Exception: A single-sided credit spread re-entry is permitted 
        after a stop ONLY if it occurs before 1:00 PM and VIX is stable/falling.
        """
        if not self.halted or self.recovery_mode:
            return False
        
        if self.stop_hit_at is None:
            return False

        # 1. Check Time (Before 1:00 PM)
        deadline = datetime.strptime(settings.RECOVERY_DEADLINE, "%H:%M").time()
        if self.stop_hit_at.time() >= deadline:
            logger.info(f"Recovery denied: Stop hit at {self.stop_hit_at.time()} (>= {deadline})")
            return False

        # 2. Check VIX Stability/Trend
        if not (vix_stable or vix_falling):
            logger.info("Recovery denied: VIX is not stable or falling")
            return False

        return True

    def reset_daily(self):
        self.daily_pnl = 0.0
        self.halted = False
        self.recovery_mode = False
        self.recovery_side = None
        self.stop_hit_at = None
