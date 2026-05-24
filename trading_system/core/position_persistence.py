"""
Position persistence — survives restarts.

Saves strategy positions, tracker state, P&L engine, and risk manager to a JSON
file after every monitoring cycle. On startup the orchestrator calls load() to
restore any open positions interrupted by a restart.
"""

import json
import logging
import os
from datetime import datetime
from typing import Any, Dict, Optional

from trading_system.config import settings

logger = logging.getLogger(__name__)

STATE_FILE = os.path.join(settings.DATA_DIR, "open_positions.json")
SESSION_ACTIVE = "active"
SESSION_CLOSING = "closing"
SESSION_FLAT = "flat"


def _tracker_position_count(position_tracker: Any) -> int:
    positions = getattr(position_tracker, "_positions", {})
    if isinstance(positions, dict):
        return len(positions)
    return 0


def _quarantine_corrupt_state() -> None:
    if not os.path.exists(STATE_FILE):
        return
    quarantine_path = f"{STATE_FILE}.corrupt.{datetime.now().strftime('%Y%m%d%H%M%S')}"
    try:
        os.replace(STATE_FILE, quarantine_path)
        logger.warning("Quarantined corrupt state file to %s", quarantine_path)
    except OSError:
        logger.exception("Failed to quarantine corrupt state file")


def is_flat(strategies: Dict[str, Any], position_tracker: Any) -> bool:
    """True when no strategy is active and the tracker has no open positions."""
    has_active_strategy = any(getattr(strat, "is_active", lambda: False)() for strat in strategies.values())
    has_tracker_positions = False
    if hasattr(position_tracker, "has_open_positions"):
        try:
            has_tracker_positions = bool(position_tracker.has_open_positions())
        except Exception:
            logger.exception("Failed to inspect tracker positions")
            has_tracker_positions = _tracker_position_count(position_tracker) > 0
    else:
        has_tracker_positions = _tracker_position_count(position_tracker) > 0
    return not has_active_strategy and not has_tracker_positions


def save(
    strategies: Dict[str, Any],
    position_tracker: Any,
    pnl_engine: Any = None,
    risk_manager: Any = None,
    *,
    session_status: str = SESSION_ACTIVE,
    trading_date: Optional[str] = None,
    shutdown_reason: str = "",
    flat_verified_at: Optional[str] = None,
) -> None:
    """Persist strategy positions, tracker state, and P&L engine to disk."""
    os.makedirs(settings.DATA_DIR, exist_ok=True)
    now_iso = datetime.now().isoformat()

    payload: Dict[str, Any] = {
        "saved_at": now_iso,
        "session_status": session_status,
        "trading_date": trading_date or datetime.now().date().isoformat(),
        "last_shutdown_reason": shutdown_reason,
        "last_loop_at": now_iso,
        "flat_verified_at": flat_verified_at,
        "strategies": {},
        "tracker_positions": {},
        "pnl_state": {},
        "risk_state": {},
    }

    for key, strat in strategies.items():
        state = strat.save_state()
        if state is not None:
            payload["strategies"][key] = state

    if hasattr(position_tracker, "_positions"):
        payload["tracker_positions"] = position_tracker._positions

    if pnl_engine is not None and hasattr(pnl_engine, "save_state"):
        payload["pnl_state"] = pnl_engine.save_state()
    if risk_manager is not None and hasattr(risk_manager, "save_state"):
        payload["risk_state"] = risk_manager.save_state()

    tmp = STATE_FILE + ".tmp"
    try:
        with open(tmp, "w") as f:
            json.dump(payload, f, indent=2, default=str)
        os.replace(tmp, STATE_FILE)
    except Exception:
        logger.exception("Failed to persist positions")


def load(
    strategies: Dict[str, Any],
    position_tracker: Any,
    pnl_engine: Any = None,
    risk_manager: Any = None,
) -> Dict[str, Any]:
    """
    Restore positions + P&L from disk.  Returns the number of strategies restored.

    Safe to call when there is no state file — returns 0.
    """
    empty_meta = {
        "restored_strategies": 0,
        "tracker_positions": 0,
        "session_status": None,
        "trading_date": None,
        "saved_at": None,
        "last_shutdown_reason": None,
    }
    if not os.path.exists(STATE_FILE):
        return empty_meta

    try:
        with open(STATE_FILE, "r") as f:
            payload = json.load(f)
    except (json.JSONDecodeError, OSError):
        logger.exception("Corrupt state file — starting fresh")
        _quarantine_corrupt_state()
        return empty_meta

    saved_at = payload.get("saved_at", "unknown")
    session_status = payload.get("session_status", SESSION_ACTIVE)
    trading_date = payload.get("trading_date")
    last_shutdown_reason = payload.get("last_shutdown_reason", "")
    today = datetime.now().date().isoformat()
    is_stale_trading_day = bool(trading_date and trading_date != today)
    logger.info(
        "Found persisted state from %s (status=%s trading_date=%s shutdown_reason=%s)",
        saved_at,
        session_status,
        trading_date or "unknown",
        last_shutdown_reason or "unknown",
    )
    if session_status == SESSION_FLAT:
        logger.info("Persisted session is flat; skipping position restore")
        # Daily counters (PnL, risk, target) must survive same-day flat restarts.
        # A flat session means no open positions — not that today's trades didn't happen.
        if not is_stale_trading_day:
            pnl_data = payload.get("pnl_state", {})
            if pnl_data and pnl_engine is not None and hasattr(pnl_engine, "restore_state"):
                pnl_engine.restore_state(pnl_data, reset_daily=False)
            risk_data = payload.get("risk_state", {})
            if risk_data and risk_manager is not None and hasattr(risk_manager, "restore_state"):
                risk_manager.restore_state(risk_data, reset_daily=False)
            for key, state in payload.get("strategies", {}).items():
                strat = strategies.get(key)
                if strat is None:
                    continue
                try:
                    strat.restore_state(state)
                except Exception:
                    logger.exception("Failed to restore strategy counters for %s (flat restart)", key)
        return {
            "restored_strategies": 0,
            "tracker_positions": 0,
            "session_status": session_status,
            "trading_date": trading_date,
            "saved_at": saved_at,
            "last_shutdown_reason": last_shutdown_reason,
        }

    restored = 0
    for key, state in payload.get("strategies", {}).items():
        strat = strategies.get(key)
        if strat is None:
            logger.warning("State for unknown strategy %s — skipping", key)
            continue
        try:
            strat.restore_state(state)
            restored += 1
        except Exception:
            logger.exception("Failed to restore strategy %s", key)

    tracker_data = payload.get("tracker_positions", {})
    if tracker_data and hasattr(position_tracker, "_positions"):
        position_tracker._positions = tracker_data
        logger.info("Restored %d tracker positions", len(tracker_data))

    if is_stale_trading_day:
        logger.warning(
            "Persisted runtime state is from prior trading day %s; restoring positions with daily counters reset",
            trading_date,
        )

    pnl_data = payload.get("pnl_state", {})
    if pnl_data and pnl_engine is not None and hasattr(pnl_engine, "restore_state"):
        pnl_engine.restore_state(pnl_data, reset_daily=is_stale_trading_day)

    risk_data = payload.get("risk_state", {})
    if risk_data and risk_manager is not None and hasattr(risk_manager, "restore_state"):
        risk_manager.restore_state(risk_data, reset_daily=is_stale_trading_day)

    return {
        "restored_strategies": restored,
        "tracker_positions": len(tracker_data) if isinstance(tracker_data, dict) else 0,
        "session_status": session_status,
        "trading_date": trading_date,
        "saved_at": saved_at,
        "is_stale_trading_day": is_stale_trading_day,
        "last_shutdown_reason": last_shutdown_reason,
    }


def clear() -> None:
    """Remove persisted state file (e.g. after a clean session close)."""
    try:
        if os.path.exists(STATE_FILE):
            os.remove(STATE_FILE)
            logger.info("Cleared persisted state file")
    except OSError:
        logger.exception("Failed to clear state file")
