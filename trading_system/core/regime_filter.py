"""
Regime Filter — monitors India VIX and stability (agents.md).
"""

import time
import logging
from typing import Any, Tuple, Optional, List

from trading_system.config import settings

logger = logging.getLogger(__name__)

class RegimeFilter:
    def __init__(self, api: Any, quote_stream: Any = None):
        self.api = api
        self.quote_stream = quote_stream
        self._vix_cache: Optional[Tuple[float, float]] = None
        self._vix_history: List[Tuple[float, float]] = [] # (timestamp, vix_value)

    def get_vix(self) -> float:
        """Fetches India VIX with 60s caching."""
        if self._vix_cache is not None:
            v, ts = self._vix_cache
            if (time.monotonic() - ts) <= settings.VIX_CACHE_SEC and v > 0:
                return v

        try:
            q = None
            if self.quote_stream is not None:
                q = self.quote_stream.get_quote(
                    exchange=settings.NIFTY_SPOT_EXCHANGE,
                    token=settings.INDIA_VIX_TOKEN,
                    max_age_sec=settings.WS_VIX_MAX_AGE_SEC,
                )
                if q is None and settings.WS_STRICT_MODE:
                    return 0.0
            if q is None:
                q = self.api.get_quotes(settings.NIFTY_SPOT_EXCHANGE, settings.INDIA_VIX_TOKEN)
            v = float((q or {}).get("lp") or 0.0)
        except Exception as e:
            logger.error(f"Error fetching VIX: {e}")
            v = 0.0

        if v > 0:
            self._vix_cache = (v, time.monotonic())
            self._vix_history.append((time.monotonic(), v))
            # Keep only history needed for stability check
            cutoff = time.monotonic() - (settings.IC_VIX_STABLE_MINS * 60 + 300)
            self._vix_history = [(t, val) for t, val in self._vix_history if t >= cutoff]
            
        return v

    def is_vix_stable(self) -> bool:
        """Checks if VIX has been stable within a band for the last X minutes."""
        needed_sec = settings.IC_VIX_STABLE_MINS * 60
        now = time.monotonic()
        recent_history = [val for t, val in self._vix_history if (now - t) <= needed_sec]
        
        if len(recent_history) < 5: # Need some data points
            return False
            
        v_min, v_max = min(recent_history), max(recent_history)
        is_stable = (v_max - v_min) <= settings.IC_VIX_STABLE_BAND
        if not is_stable:
            logger.debug(f"VIX unstable: range {v_max - v_min:.2f} > band {settings.IC_VIX_STABLE_BAND}")
        return is_stable

    def is_vix_falling(self) -> bool:
        """Checks if VIX is currently in a downward trend."""
        if len(self._vix_history) < 10:
            return False
        
        recent = [val for t, val in self._vix_history[-10:]]
        # Simple check: last value < average of previous 9
        return recent[-1] < (sum(recent[:-1]) / len(recent[:-1]))

    def get_regime_gate(self, day_type: str) -> bool:
        """
        Entry Gate: New positions permitted ONLY when:
        - day_type == 'RANGING'
        - VIX < 30.0
        - VIX stable for 45 mins
        """
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
