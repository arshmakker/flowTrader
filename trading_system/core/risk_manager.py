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
        self.halted = False
        self.stop_hit_at = None
        self._stop_breach_streak = 0
        self._rollback_failures: List[Dict] = []

    def check_combined_stop_loss(self, active_strategies: List[Any]) -> bool:
        """
        Checks if the combined unrealized P&L of all active instruments 
        hits the 3x combined max profit threshold.
        """
        if self.halted:
            return True

        total_unrealized = 0.0
        total_max_profit = 0.0
        active_count = 0
        valid_count = 0
        
        for s in active_strategies:
            if s.is_active():
                active_count += 1
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
                valid_count += 1

                current_prem = (prices['sc'] + prices['sp']) - (prices['lc'] + prices['lp'])
                lot_size = s.md.get_lot_size(pos.sc_sym)
                total_unrealized += (pos.entry_credit - current_prem) * pos.lots * lot_size
                total_max_profit += pos.max_profit

        # If any active strategy has invalid/missing quotes, skip hard-stop decision for this tick.
        if active_count > 0 and valid_count < active_count:
            if self._stop_breach_streak:
                logger.warning("Hard stop streak reset due to invalid quote snapshot.")
            self._stop_breach_streak = 0
            return False

        if total_max_profit > 0:
            stop_limit = -total_max_profit * settings.IC_STOP_LOSS_MULT
            if total_unrealized <= stop_limit:
                self._stop_breach_streak += 1
                required = max(1, int(getattr(settings, "IC_HARD_STOP_CONFIRM_TICKS", 1)))
                logger.warning(
                    "Hard-stop breach %d/%d: Combined PnL %.2f <= Limit %.2f",
                    self._stop_breach_streak, required, total_unrealized, stop_limit
                )
                if self._stop_breach_streak >= required:
                    logger.critical(f"HARD STOP HIT: Combined PnL {total_unrealized:.2f} <= Limit {stop_limit:.2f}")
                    self.halted = True
                    self.stop_hit_at = datetime.now()
                    self._stop_breach_streak = 0
                    return True
            else:
                self._stop_breach_streak = 0
        
        return False

    def escalate_rollback_failure(self, instrument: str, stuck_legs: List[Dict]) -> None:
        """BUG-05 / Axiom 3+4: rollback failure is a safety event. Halt new entries
        and record the stuck legs so the operator can reconcile against the broker.
        """
        self.halted = True
        if self.stop_hit_at is None:
            self.stop_hit_at = datetime.now()
        record = {
            "at": datetime.now().isoformat(),
            "instrument": instrument,
            "stuck_legs": stuck_legs,
        }
        self._rollback_failures.append(record)
        logger.critical(
            "ROLLBACK FAILURE — halting trading. instrument=%s stuck_legs=%s",
            instrument, stuck_legs,
        )

    def reset_daily(self):
        self.halted = False
        self.stop_hit_at = None
        self._stop_breach_streak = 0

    def save_state(self) -> Dict:
        return {
            "halted": self.halted,
            "stop_hit_at": self.stop_hit_at.isoformat() if self.stop_hit_at else None,
            "stop_breach_streak": self._stop_breach_streak,
            "rollback_failures": self._rollback_failures,
        }

    def restore_state(self, state: Dict, *, reset_daily: bool = False) -> None:
        if reset_daily:
            self.reset_daily()
            return
        self.halted = state.get("halted", False)
        self._stop_breach_streak = state.get("stop_breach_streak", 0)
        self._rollback_failures = state.get("rollback_failures", [])
        stop_hit_str = state.get("stop_hit_at")
        if stop_hit_str:
            self.stop_hit_at = datetime.fromisoformat(stop_hit_str)
        if self.halted:
            logger.warning("Restored risk state: Trading HALTED.")
