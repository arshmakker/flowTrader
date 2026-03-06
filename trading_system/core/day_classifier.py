"""
Day classifier (agent.md §6).

Runs once at CLASSIFY_TIME (10:30 AM). Classifies the day as RANGING,
TRENDING_UP, or TRENDING_DOWN based on price move from open and distance
from VWAP. Classification is locked for the day — cannot be overridden.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional

from trading_system.config import settings


@dataclass
class DayClassification:
    day_type: str  # 'RANGING' | 'TRENDING_UP' | 'TRENDING_DOWN'
    confidence: str  # 'HIGH' | 'MEDIUM' | 'LOW'
    open_price: float
    current_price: float
    move_pct: float  # percent, e.g. 1.5
    vwap_distance_pct: float  # percent
    classified_at: str  # HH:MM:SS


class DayClassifier:
    """
    Classify the trading day once at 10:30 AM; result is locked until reset().

    Requires:
        market_data — object with .get_open_price(symbol) -> float
                       and .get_ltp(symbol_key) -> float
        signal_engine — object with .compute_vwap_value() -> float
    """

    def __init__(self, market_data: Any, signal_engine: Any):
        self.md = market_data
        self.se = signal_engine
        self._result: Optional[DayClassification] = None

    def classify(self) -> DayClassification:
        if self._result is not None:
            return self._result  # locked once set

        open_px = self.md.get_open_price(settings.NIFTY_SYMBOL)
        current = self.md.get_ltp("NSE|Nifty 50")
        move_pct = (current - open_px) / open_px
        vwap = self.se.compute_vwap_value()
        vwap_dist = abs(current - vwap) / vwap if vwap else 0.0
        abs_move = abs(move_pct)

        if abs_move >= settings.TREND_MOVE_THRESHOLD and vwap_dist >= settings.VWAP_TREND_DISTANCE:
            day_type = "TRENDING_UP" if move_pct > 0 else "TRENDING_DOWN"
            confidence = "HIGH" if abs_move > 0.02 else "MEDIUM"
        elif abs_move >= settings.TREND_MOVE_THRESHOLD:
            day_type, confidence = "RANGING", "MEDIUM"  # big move but near VWAP
        else:
            day_type, confidence = "RANGING", "HIGH"

        self._result = DayClassification(
            day_type=day_type,
            confidence=confidence,
            open_price=open_px,
            current_price=current,
            move_pct=round(move_pct * 100, 2),
            vwap_distance_pct=round(vwap_dist * 100, 2),
            classified_at=datetime.now().strftime("%H:%M:%S"),
        )
        return self._result

    def reset(self) -> None:
        """Call at start of each trading day."""
        self._result = None
