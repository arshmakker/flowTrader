"""
Trend entry state: re-entry cooldown after STOP_LOSS_HIT and max trend trades per day.
"""

import json
import os
import logging
from datetime import datetime, date, timedelta
from typing import Tuple

from .config import REENTRY_COOLDOWN_MINUTES, MAX_TREND_TRADES_PER_DAY

logger = logging.getLogger(__name__)

TREND_STATE_FILE = "trend_state.json"


def _state_path() -> str:
    return os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), TREND_STATE_FILE)


def load_trend_state() -> dict:
    path = _state_path()
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r") as f:
            return json.load(f)
    except Exception as e:
        logger.warning(f"Could not load trend state from {path}: {e}")
        return {}


def save_trend_state(state: dict) -> None:
    path = _state_path()
    try:
        with open(path, "w") as f:
            json.dump(state, f, indent=2)
    except Exception as e:
        logger.warning(f"Could not save trend state to {path}: {e}")


def record_stop_loss_exit(exit_time: datetime) -> None:
    """Record that a trend position was closed with STOP_LOSS_HIT; start cooldown."""
    state = load_trend_state()
    cooldown_until = exit_time + timedelta(minutes=REENTRY_COOLDOWN_MINUTES)
    state["cooldown_until_iso"] = cooldown_until.isoformat()
    save_trend_state(state)
    logger.info(
        f"Trend: STOP_LOSS_HIT recorded; re-entry cooldown until {cooldown_until.isoformat()} "
        f"({REENTRY_COOLDOWN_MINUTES} min)"
    )


def record_trend_entry(entry_date: date) -> None:
    """Record that a new trend position was entered today; increment trades_entered_today."""
    state = load_trend_state()
    date_str = entry_date.strftime("%Y%m%d")
    last_date = state.get("last_trade_date")
    count = state.get("trades_entered_today", 0) if last_date == date_str else 0
    count += 1
    state["trades_entered_today"] = count
    state["last_trade_date"] = date_str
    save_trend_state(state)
    logger.info(f"Trend: entry recorded; trades today = {count} (max {MAX_TREND_TRADES_PER_DAY})")


def can_enter_trend(now: datetime) -> Tuple[bool, str]:
    """
    Check if a new trend entry is allowed (not in cooldown, under max trades per day).
    Returns (allowed, reason_string).
    """
    state = load_trend_state()
    today_str = now.strftime("%Y%m%d")
    last_date = state.get("last_trade_date")
    trades_today = state.get("trades_entered_today", 0) if last_date == today_str else 0

    if trades_today >= MAX_TREND_TRADES_PER_DAY:
        return False, f"MAX_TREND_TRADES_PER_DAY ({trades_today} >= {MAX_TREND_TRADES_PER_DAY})"

    cooldown_iso = state.get("cooldown_until_iso")
    if cooldown_iso:
        try:
            cooldown_until = datetime.fromisoformat(cooldown_iso)
            if now < cooldown_until:
                return False, f"REENTRY_COOLDOWN (until {cooldown_until.isoformat()})"
        except Exception:
            pass

    return True, "OK"
