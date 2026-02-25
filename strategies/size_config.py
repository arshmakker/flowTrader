"""
Centralized sizing configuration for strategies.

Provides MIN_LOTS and MAX_LOTS and helpers to validate/clamp lot counts.
"""

from typing import Optional

# Hard policy limits (can be tuned)
# Minimum lots to trade for multi-leg option strategies
MIN_LOTS = 10
# Maximum lots per trade (safety cap)
MAX_LOTS = 10

def clamp_lots(lots: Optional[int]) -> int:
    """
    Clamp a proposed lot count to [MIN_LOTS, MAX_LOTS].
    If lots is None or <= 0, returns 0 (indicates invalid / should be rejected).
    """
    try:
        if lots is None:
            return 0
        lots = int(lots)
    except Exception:
        return 0

    if lots <= 0:
        return 0
    if lots < MIN_LOTS:
        return MIN_LOTS
    if lots > MAX_LOTS:
        return MAX_LOTS
    return lots

