"""
Risk Manager — implements the 3x combined stop-loss and recovery protocol (agents.md).

- Hard Stop-Loss: 3x combined max profit of all spreads.
- Recovery Protocol: Single-sided recovery if stop-loss hit before 1:00 PM and VIX stable/falling.
"""

import logging
from datetime import datetime, time
from typing import Any, Dict, List, Optional

from trading_system.config import settings
from trading_system.ops.alerts import Alert, AlertChannel, NullAlertChannel

logger = logging.getLogger(__name__)

class RiskManager:
    def __init__(self, alerts: Optional[AlertChannel] = None):
        self.halted = False
        self.stop_hit_at = None
        self._stop_breach_streak = 0
        self._rollback_failures: List[Dict] = []
        # LIVE-23: alerts channel. Default to NullAlertChannel so existing
        # RiskManager() call sites keep working unchanged.
        self._alerts: AlertChannel = alerts if alerts is not None else NullAlertChannel()

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
                prev_streak = self._stop_breach_streak
                self._stop_breach_streak += 1
                required = max(1, int(getattr(settings, "IC_HARD_STOP_CONFIRM_TICKS", 1)))
                # Log only on entering a breach (0 → 1) and on confirmation —
                # per-tick logging during a sustained breach window fills the
                # log with non-state-transition noise at 5–60s cadence.
                if prev_streak == 0:
                    logger.warning(
                        "Hard-stop breach started (1/%d): Combined PnL %.2f <= Limit %.2f",
                        required, total_unrealized, stop_limit
                    )
                if self._stop_breach_streak >= required:
                    logger.critical(f"HARD STOP HIT: Combined PnL {total_unrealized:.2f} <= Limit {stop_limit:.2f}")
                    self.halted = True
                    self.stop_hit_at = datetime.now()
                    self._stop_breach_streak = 0
                    self._alerts.send(Alert(
                        event="combined_stop",
                        severity="critical",
                        title="RegimeTrader: hard stop hit",
                        body=(
                            f"Combined unrealised PnL ₹{total_unrealized:,.0f} <= "
                            f"limit ₹{stop_limit:,.0f}. Trading halted."
                        ),
                    ))
                    return True
            else:
                self._stop_breach_streak = 0
        
        return False

    def check_daily_loss_cap(self, pnl_engine: Any) -> bool:
        """LIVE-22: returns True and halts if daily P&L breaches the loss cap.

        Effective cap is settings.DAILY_MAX_LOSS_SHAKEDOWN when SHAKEDOWN_MODE
        is True (proving-period tighter ceiling), else settings.DAILY_MAX_LOSS.
        """
        if self.halted:
            return True
        cap = (
            settings.DAILY_MAX_LOSS_SHAKEDOWN
            if settings.SHAKEDOWN_MODE
            else settings.DAILY_MAX_LOSS
        )
        daily = pnl_engine.daily_realised_pnl + pnl_engine.unrealised_pnl
        if daily < -cap:
            logger.critical(
                "DAILY LOSS CAP HIT: daily_pnl=%.2f < -%.0f (shakedown=%s). Halting entries and flattening.",
                daily, cap, settings.SHAKEDOWN_MODE,
            )
            self.halted = True
            self.stop_hit_at = datetime.now()
            self._alerts.send(Alert(
                event="daily_loss_cap",
                severity="critical",
                title="RegimeTrader: daily loss cap hit",
                body=(
                    f"Daily P&L ₹{daily:,.0f} breached cap ₹{-cap:,.0f}. "
                    "Halting entries and flattening."
                ),
            ))
            return True
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
        self._alerts.send(Alert(
            event="rollback_failure",
            severity="critical",
            title="RegimeTrader: rollback failure",
            body=(
                f"Rollback failed for {instrument}; {len(stuck_legs)} stuck leg(s). "
                "Trading halted. Reconcile against broker before clearing."
            ),
        ))

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
