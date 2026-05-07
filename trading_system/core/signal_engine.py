"""
Signal engine — session VWAP for the day classifier.

Under Axiom 1 (iron-condor only) and the IC-only scope, the broader signal
toolkit (RSI, PCR, Max Pain, consensus voting) was never wired into live
decision-making and has been removed. Only the session-VWAP computation
consumed by DayClassifier remains.
"""

from typing import Optional

import pandas as pd


class SignalEngine:
    """Stateless VWAP computation. Callers pass the intraday OHLCV each call."""

    def compute_vwap_value(self, ohlcv_df: Optional[pd.DataFrame] = None) -> float:
        """Session VWAP from 15-minute OHLCV bars. Returns 0.0 on empty/None input."""
        if ohlcv_df is None or ohlcv_df.empty:
            return 0.0
        typical = (ohlcv_df["high"] + ohlcv_df["low"] + ohlcv_df["close"]) / 3.0
        vol = ohlcv_df["volume"].astype(float)
        cum_tp_vol = (typical * vol).sum()
        cum_vol = vol.sum()
        if cum_vol == 0:
            return 0.0
        return cum_tp_vol / cum_vol
