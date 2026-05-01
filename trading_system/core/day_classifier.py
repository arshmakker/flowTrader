"""
Day classifier (agent.md §6).

Runs once at CLASSIFY_TIME (10:30 AM). Classifies the day as RANGING,
TRENDING_UP, or TRENDING_DOWN based on price move from open and distance
from VWAP. Classification is locked for the day — cannot be overridden.
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any, Dict, Optional

from trading_system.config import settings

logger = logging.getLogger(__name__)


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
    BUG-08: one classifier per instrument. Pass symbol + spot_key to decouple
    from NIFTY-only hardcoding.

    Requires:
        market_data — object with .get_open_price(symbol) -> float
                       and .get_ltp(symbol_key) -> float
        signal_engine — object with .compute_vwap_value(ohlcv_df) -> float
        symbol — trade symbol used for open-price lookup (default: NIFTY)
        spot_key — exchange|name key used for current LTP lookup
    """

    def __init__(
        self,
        market_data: Any,
        signal_engine: Any,
        symbol: Optional[str] = None,
        spot_key: Optional[str] = None,
    ):
        self.md = market_data
        self.se = signal_engine
        self.symbol = symbol if symbol is not None else settings.NIFTY_SYMBOL
        self.spot_key = spot_key if spot_key is not None else settings.NIFTY_SPOT_KEY
        self._result: Optional[DayClassification] = None

    def classify(self) -> DayClassification:
        if self._result is not None:
            return self._result  # locked once set

        open_px = self.md.get_open_price(self.symbol)
        current = self.md.get_ltp(self.spot_key)
        if open_px <= 0 or current <= 0:
            self._result = DayClassification(
                day_type="RANGING",
                confidence="LOW",
                open_price=open_px,
                current_price=current,
                move_pct=0.0,
                vwap_distance_pct=0.0,
                classified_at=datetime.now().strftime("%H:%M:%S"),
            )
            return self._result
        move_pct = (current - open_px) / open_px
        vwap = self.se.compute_vwap_value(self.md.get_ohlcv_df())
        vwap_dist = abs(current - vwap) / vwap if vwap else 0.0
        abs_move = abs(move_pct)

        if abs_move >= settings.TREND_MOVE_THRESHOLD and vwap_dist >= settings.VWAP_TREND_DISTANCE:
            day_type = "TRENDING_UP" if move_pct > 0 else "TRENDING_DOWN"
            confidence = "HIGH" if abs_move > settings.TREND_HIGH_CONFIDENCE else "MEDIUM"
        elif abs_move >= settings.TREND_MOVE_THRESHOLD:
            day_type, confidence = "RANGING", "MEDIUM"  # big move but near VWAP
        else:
            day_type, confidence = "RANGING", "HIGH"

        if hasattr(self.md, "is_open_price_reliable") and not self.md.is_open_price_reliable(self.symbol):
            confidence = "LOW"
            logger.warning(
                "Open price for %s was an LTP fallback — downgrading day classification confidence to LOW",
                self.symbol,
            )

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

    def save_state(self) -> Optional[Dict]:
        if self._result:
            return asdict(self._result)
        return None

    def restore_state(self, state: Optional[Dict]) -> None:
        if state:
            self._result = DayClassification(**state)
            logger.info(f"Restored Day Classification: {self._result.day_type}")
