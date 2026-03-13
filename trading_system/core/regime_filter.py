from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

from trading_system.config import settings


@dataclass(frozen=True)
class Routing:
    primary: list[str]
    secondary: list[str]
    forbidden: list[str]
    target: float
    mult: float


class RegimeFilter:
    """
    VIX regime gate + routing (agent.md §5).

    Expectations:
    - `api` is Shoonya/Noren-like and supports `get_quotes(exchange, token)` OR keyword args.
    - India VIX token used by this repo historically: NSE token 26017.
    - Cached for 60 seconds to reduce API calls.
    """

    def __init__(self, api: Any):
        self.api = api
        self._vix_cache: Optional[Tuple[float, float]] = None

    def get_vix(self) -> float:
        if self._vix_cache is not None:
            v, ts = self._vix_cache
            if (time.monotonic() - ts) <= settings.VIX_CACHE_SEC and v > 0:
                return v

        q = None
        try:
            q = self.api.get_quotes(settings.NIFTY_SPOT_EXCHANGE, settings.INDIA_VIX_TOKEN)
        except TypeError:
            q = self.api.get_quotes(exchange=settings.NIFTY_SPOT_EXCHANGE, token=settings.INDIA_VIX_TOKEN)

        v = 0.0
        try:
            v = float((q or {}).get("lp") or 0.0)
        except Exception:
            v = 0.0

        # Cache even if zero so we don't spam; callers handle bad values.
        self._vix_cache = (v, time.monotonic())
        return v

    def get_regime(self) -> str:
        v = self.get_vix()
        if v < settings.VIX_CALM:
            return "CALM"
        if v < settings.VIX_NORMAL_HIGH:
            return "NORMAL"
        if v < settings.VIX_DANGER:
            return "ELEVATED"
        return "DANGER"

    def size_multiplier(self) -> float:
        return {
            "CALM": 1.0,
            "NORMAL": 0.5,
            "ELEVATED": 0.3,
            "DANGER": 0.25,
        }.get(self.get_regime(), 0.25)

    def daily_loss_limit(self) -> float:
        return {
            "CALM": float(settings.LOSS_LIMIT_CALM),
            "NORMAL": float(settings.LOSS_LIMIT_NORMAL),
            "ELEVATED": float(settings.LOSS_LIMIT_ELEVATED),
            "DANGER": float(settings.LOSS_LIMIT_HIGH_VIX),
        }.get(self.get_regime(), float(settings.LOSS_LIMIT_HIGH_VIX))

    def get_routing(self, day_type: str) -> Dict[str, Any]:
        """
        Returns routing dict per agent.md.
        Never halts trading — always routes to some strategy.
        """
        regime = self.get_regime()
        mult = self.size_multiplier()

        routes: Dict[tuple[str, str], Dict[str, Any]] = {
            ("CALM", "RANGING"): dict(primary=["A"], secondary=["B", "C"], forbidden=["D", "E"], target=settings.TARGET_CALM, mult=mult),
            ("CALM", "TRENDING_UP"): dict(primary=["B"], secondary=["C"], forbidden=["D", "E"], target=settings.TARGET_CALM, mult=mult),
            ("CALM", "TRENDING_DOWN"): dict(primary=["B"], secondary=["C"], forbidden=["D", "E"], target=settings.TARGET_CALM, mult=mult),
            ("NORMAL", "RANGING"): dict(primary=["A"], secondary=["B"], forbidden=["C", "D", "E"], target=settings.TARGET_NORMAL, mult=mult),
            ("NORMAL", "TRENDING_UP"): dict(primary=["B"], secondary=[], forbidden=["A", "D", "E"], target=settings.TARGET_NORMAL, mult=mult),
            ("NORMAL", "TRENDING_DOWN"): dict(primary=["B"], secondary=[], forbidden=["A", "D", "E"], target=settings.TARGET_NORMAL, mult=mult),
            ("ELEVATED", "RANGING"): dict(primary=["D"], secondary=[], forbidden=["A", "B", "C", "E"], target=settings.TARGET_ELEVATED, mult=mult),
            ("ELEVATED", "TRENDING_UP"): dict(primary=["E"], secondary=["D"], forbidden=["A", "B", "C"], target=settings.TARGET_ELEVATED, mult=mult),
            ("ELEVATED", "TRENDING_DOWN"): dict(primary=["E"], secondary=["D"], forbidden=["A", "B", "C"], target=settings.TARGET_ELEVATED, mult=mult),
            ("DANGER", "RANGING"): dict(primary=["D"], secondary=[], forbidden=["A", "B", "C", "E"], target=settings.TARGET_HIGH_VIX, mult=mult),
            ("DANGER", "TRENDING_UP"): dict(primary=["E"], secondary=["D"], forbidden=["A", "B", "C"], target=settings.TARGET_HIGH_VIX, mult=mult),
            ("DANGER", "TRENDING_DOWN"): dict(primary=["E"], secondary=["D"], forbidden=["A", "B", "C"], target=settings.TARGET_HIGH_VIX, mult=mult),
        }

        return routes.get(
            (regime, day_type),
            dict(primary=["D"], secondary=[], forbidden=["A", "B", "C", "E"], target=settings.TARGET_HIGH_VIX, mult=0.25),
        )

