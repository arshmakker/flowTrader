"""
Regime Filter — monitors India VIX and stability (agents.md).
"""

import time
import logging
from typing import Any, Dict, Tuple, Optional, List

from trading_system.config import settings

logger = logging.getLogger(__name__)

class RegimeFilter:
    def __init__(self, api: Any):
        self.api = api
        self._vix_cache: Optional[Tuple[float, float]] = None
        # Wall-clock timestamps (time.time()) so history survives process restarts.
        self._vix_history: List[Tuple[float, float]] = []

    def get_vix(self) -> float:
        """Fetches India VIX with 60s caching."""
        if self._vix_cache is not None:
            v, ts = self._vix_cache
            if (time.monotonic() - ts) <= settings.VIX_CACHE_SEC and v > 0:
                return v

        try:
            q = self.api.get_quotes(settings.NIFTY_SPOT_EXCHANGE, settings.INDIA_VIX_TOKEN)
            v = float((q or {}).get("lp") or 0.0)
        except Exception as e:
            logger.error(f"Error fetching VIX: {e}")
            v = 0.0

        if v > 0:
            self._vix_cache = (v, time.monotonic())
            now_wall = time.time()
            self._vix_history.append((now_wall, v))
            cutoff = now_wall - (settings.IC_VIX_STABLE_MINS * 60 + 300)
            self._vix_history = [(t, val) for t, val in self._vix_history if t >= cutoff]

        return v

    def is_vix_stable(self) -> bool:
        """Checks if VIX has been stable within a band for the last X minutes."""
        needed_sec = settings.IC_VIX_STABLE_MINS * 60
        now = time.time()
        recent_history = [val for t, val in self._vix_history if (now - t) <= needed_sec]

        if len(recent_history) < 5:
            return False

        v_min, v_max = min(recent_history), max(recent_history)
        is_stable = (v_max - v_min) <= settings.IC_VIX_STABLE_BAND
        if not is_stable:
            logger.debug(f"VIX unstable: range {v_max - v_min:.2f} > band {settings.IC_VIX_STABLE_BAND}")
        return is_stable

    def save_state(self) -> Dict:
        return {"vix_history": [[t, v] for t, v in self._vix_history]}

    def restore_state(self, state: Dict, *, reset_daily: bool = False) -> None:
        if reset_daily:
            self._vix_history = []
            return
        now = time.time()
        cutoff = now - (settings.IC_VIX_STABLE_MINS * 60 + 300)
        self._vix_history = [
            (float(t), float(v))
            for t, v in state.get("vix_history", [])
            if float(t) >= cutoff
        ]
        logger.info(
            "Restored VIX history: %d samples (oldest %.0fs ago)",
            len(self._vix_history),
            (now - self._vix_history[0][0]) if self._vix_history else 0,
        )

    def get_regime_gate(self, day_type: str) -> bool:
        """
        Entry Gate: New positions permitted ONLY when:
        - market is in a tradable session (LIVE-18: not pre-open, weekend, holiday, ...)
        - day_type == 'RANGING'
        - VIX < 30.0
        - VIX stable for 45 mins
        """
        # LIVE-18: refuse entries during pre-open, after-close, weekends,
        # holidays, or muhurat-date-outside-window. Put this first — no
        # point consulting VIX or day type if we can't place an order.
        from strategy_runner import is_tradable_now
        tradable, reason = is_tradable_now()
        if not tradable:
            logger.info(f"Entry Gate BLOCKED: market not tradable (reason={reason})")
            return False

        vix = self.get_vix()

        if day_type != 'RANGING':
            logger.info(f"Entry Gate BLOCKED: Day type is {day_type} (not RANGING)")
            return False

        if vix >= settings.IC_VIX_MAX:
            logger.info(f"Entry Gate BLOCKED: VIX {vix:.2f} >= Limit {settings.IC_VIX_MAX}")
            return False

        if not self.is_vix_stable():
            logger.info(f"Entry Gate BLOCKED: VIX not stable for {settings.IC_VIX_STABLE_MINS} mins")
            return False

        return True
