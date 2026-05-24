"""
Risk Manager — daily loss cap and rollback-failure halt for PCR Credit Spread.
"""

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from trading_system.config import settings
from trading_system.ops.alerts import Alert, AlertChannel, NullAlertChannel

logger = logging.getLogger(__name__)


class RiskManager:
    def __init__(self, alerts: Optional[AlertChannel] = None):
        self.halted = False
        self.stop_hit_at = None
        self._rollback_failures: List[Dict] = []
        self._daily_cap_halted = False
        self._paper_cap_notified = False
        self._alerts: AlertChannel = alerts if alerts is not None else NullAlertChannel()

    def check_daily_loss_cap(self, pnl_engine: Any) -> bool:
        """Returns True and halts if daily realised P&L breaches DAILY_MAX_LOSS.

        Paper mode: fires a one-time log + alert but does NOT halt — informational only.
        """
        if self.halted:
            return True
        if self._paper_cap_notified:
            return False

        daily = pnl_engine.daily_realised_pnl
        cap = settings.DAILY_MAX_LOSS  # negative value e.g. -50_000
        if daily < cap:
            if settings.PAPER_TRADE_MODE:
                logger.critical(
                    "DAILY LOSS CAP HIT (paper mode — no halt): daily_pnl=%.2f < %.0f. Trading continues.",
                    daily,
                    cap,
                )
                self._alerts.send(
                    Alert(
                        event="daily_loss_cap",
                        severity="critical",
                        title="PCR Trader: daily loss cap hit (paper)",
                        body=f"Daily P&L ₹{daily:,.0f} breached cap ₹{cap:,.0f}. Paper mode — trading continues.",
                    )
                )
                self._paper_cap_notified = True
                return False

            logger.critical(
                "DAILY LOSS CAP HIT: daily_pnl=%.2f < %.0f. Halting.",
                daily,
                cap,
            )
            self.halted = True
            self._daily_cap_halted = True
            self.stop_hit_at = datetime.now()
            self._alerts.send(
                Alert(
                    event="daily_loss_cap",
                    severity="critical",
                    title="PCR Trader: daily loss cap hit",
                    body=f"Daily P&L ₹{daily:,.0f} breached cap ₹{cap:,.0f}. Halting.",
                )
            )
            return True
        return False

    def escalate_rollback_failure(self, instrument: str, stuck_legs: List[Dict]) -> None:
        """Axiom 3+4: rollback failure halts new entries."""
        self.halted = True
        if self.stop_hit_at is None:
            self.stop_hit_at = datetime.now()
        record = {"at": datetime.now().isoformat(), "instrument": instrument, "stuck_legs": stuck_legs}
        self._rollback_failures.append(record)
        logger.critical("ROLLBACK FAILURE — halting. instrument=%s stuck_legs=%s", instrument, stuck_legs)
        self._alerts.send(
            Alert(
                event="rollback_failure",
                severity="critical",
                title="PCR Trader: rollback failure",
                body=f"Rollback failed for {instrument}; {len(stuck_legs)} stuck leg(s). Trading halted.",
            )
        )

    def reset_daily(self) -> None:
        self.halted = False
        self.stop_hit_at = None
        self._daily_cap_halted = False
        self._paper_cap_notified = False

    def save_state(self) -> Dict:
        return {
            "halted": self.halted,
            "stop_hit_at": self.stop_hit_at.isoformat() if self.stop_hit_at else None,
            "rollback_failures": self._rollback_failures,
            "daily_cap_halted": self._daily_cap_halted,
        }

    def restore_state(self, state: Dict, *, reset_daily: bool = False) -> None:
        if reset_daily:
            self.reset_daily()
            return
        self.halted = state.get("halted", False)
        self._rollback_failures = state.get("rollback_failures", [])
        self._daily_cap_halted = state.get("daily_cap_halted", False)
        stop_hit_str = state.get("stop_hit_at")
        if stop_hit_str:
            self.stop_hit_at = datetime.fromisoformat(stop_hit_str)
        if self.halted:
            logger.warning("Restored risk state: Trading HALTED.")
