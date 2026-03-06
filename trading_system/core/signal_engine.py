"""
Signal engine (agent.md §7).

Four independent signals — VWAP, RSI, PCR, Max Pain.
Returns a SignalResult with consensus vote and confidence (0-4).

Refresh cadence (managed by caller / orchestrator):
  VWAP + RSI  : every 60 seconds
  PCR         : every 5 minutes
  Max Pain    : once at market open
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import pandas as pd

from trading_system.config import settings


@dataclass
class SignalResult:
    vwap_bias: str = "RANGE"       # BULL | BEAR | RANGE
    rsi_signal: str = "NEUTRAL"    # BULL | BEAR | NEUTRAL | OB | OS
    pcr_signal: str = "NEUTRAL"    # BULL | BEAR | NEUTRAL
    max_pain: float = 0.0
    max_pain_signal: str = "NEUTRAL"  # BULL | BEAR | NEUTRAL
    consensus: str = "RANGE"       # BULL | BEAR | RANGE (majority vote)
    confidence: int = 0            # 0–4 signals in agreement


class SignalEngine:
    """
    Stateless computations — callers pass fresh data each time.

    Data expectations
    -----------------
    ohlcv_df : DataFrame with columns [open, high, low, close, volume]
               index = datetime, 15-min bars, reset daily at 09:15.
    close_series : pd.Series of 15-min close prices (at least RSI_PERIOD + 1 bars).
    chain_data : DataFrame with columns [strike, option_type('CE'/'PE'), oi]
                 (full option chain for current expiry).
    spot : float — current NIFTY spot price.
    """

    def __init__(self) -> None:
        self._prev_rsi: Optional[float] = None

    # ── VWAP ────────────────────────────────────────────────────────────

    def compute_vwap_value(self, ohlcv_df: Optional[pd.DataFrame] = None) -> float:
        """Raw VWAP float (used by DayClassifier)."""
        if ohlcv_df is None or ohlcv_df.empty:
            return 0.0
        typical = (ohlcv_df["high"] + ohlcv_df["low"] + ohlcv_df["close"]) / 3.0
        vol = ohlcv_df["volume"].astype(float)
        cum_tp_vol = (typical * vol).sum()
        cum_vol = vol.sum()
        if cum_vol == 0:
            return 0.0
        return cum_tp_vol / cum_vol

    def compute_vwap(self, ohlcv_df: pd.DataFrame, spot: float) -> str:
        """VWAP bias: BULL / BEAR / RANGE."""
        vwap = self.compute_vwap_value(ohlcv_df)
        if vwap <= 0:
            return "RANGE"
        upper = vwap * (1 + settings.VWAP_BAND_PCT)
        lower = vwap * (1 - settings.VWAP_BAND_PCT)
        if spot > upper:
            return "BULL"
        if spot < lower:
            return "BEAR"
        return "RANGE"

    # ── RSI ─────────────────────────────────────────────────────────────

    @staticmethod
    def _wilder_rsi(series: pd.Series, period: int) -> pd.Series:
        """Wilder's smoothed RSI."""
        delta = series.diff()
        gain = delta.clip(lower=0)
        loss = (-delta).clip(lower=0)
        avg_gain = gain.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
        avg_loss = loss.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
        rs = avg_gain / avg_loss.replace(0, 1e-10)
        return 100 - (100 / (1 + rs))

    def compute_rsi(self, close_series: pd.Series) -> str:
        """
        RSI signal with crossover detection.
        Returns: BULL | BEAR | NEUTRAL | OB | OS
        """
        if close_series is None or len(close_series) < settings.RSI_PERIOD + 2:
            return "NEUTRAL"

        rsi_vals = self._wilder_rsi(close_series, settings.RSI_PERIOD)
        current_rsi = rsi_vals.iloc[-1]
        prev_rsi = self._prev_rsi if self._prev_rsi is not None else rsi_vals.iloc[-2]
        self._prev_rsi = current_rsi

        if current_rsi >= settings.RSI_OVERBOUGHT:
            return "OB"
        if current_rsi <= settings.RSI_OVERSOLD:
            return "OS"
        if prev_rsi < 50 and current_rsi >= settings.RSI_BULL_THRESH:
            return "BULL"
        if prev_rsi > 50 and current_rsi <= settings.RSI_BEAR_THRESH:
            return "BEAR"
        return "NEUTRAL"

    # ── PCR ─────────────────────────────────────────────────────────────

    @staticmethod
    def compute_pcr(chain_data: pd.DataFrame) -> str:
        """Put-Call Ratio signal: BULL / BEAR / NEUTRAL."""
        if chain_data is None or chain_data.empty:
            return "NEUTRAL"
        puts = chain_data.loc[chain_data["option_type"] == "PE", "oi"]
        calls = chain_data.loc[chain_data["option_type"] == "CE", "oi"]
        total_put_oi = puts.sum()
        total_call_oi = calls.sum()
        if total_call_oi == 0:
            return "NEUTRAL"
        pcr = total_put_oi / total_call_oi
        if pcr > settings.PCR_BULL:
            return "BULL"
        if pcr < settings.PCR_BEAR:
            return "BEAR"
        return "NEUTRAL"

    # ── Max Pain ────────────────────────────────────────────────────────

    @staticmethod
    def compute_max_pain(chain_data: pd.DataFrame) -> float:
        """
        Max-pain strike: the strike where total option-holder losses are minimised.

        For each candidate settlement strike S, total pain =
          sum over all strikes K of:
              max(S - K, 0) * call_OI[K]   (call holders lose if S > K)
            + max(K - S, 0) * put_OI[K]    (put  holders lose if K > S)
        Max pain = S with the minimum total pain.
        """
        if chain_data is None or chain_data.empty:
            return 0.0
        calls = chain_data[chain_data["option_type"] == "CE"][["strike", "oi"]].copy()
        puts = chain_data[chain_data["option_type"] == "PE"][["strike", "oi"]].copy()
        if calls.empty and puts.empty:
            return 0.0

        all_strikes = sorted(
            set(calls["strike"].tolist() + puts["strike"].tolist())
        )
        call_oi = dict(zip(calls["strike"], calls["oi"]))
        put_oi = dict(zip(puts["strike"], puts["oi"]))

        min_pain = float("inf")
        mp_strike = all_strikes[len(all_strikes) // 2]

        for s in all_strikes:
            pain = 0.0
            for k in all_strikes:
                pain += max(s - k, 0) * call_oi.get(k, 0)
                pain += max(k - s, 0) * put_oi.get(k, 0)
            if pain < min_pain:
                min_pain = pain
                mp_strike = s

        return float(mp_strike)

    @staticmethod
    def max_pain_signal(spot: float, max_pain_strike: float) -> str:
        """BULL if spot below max-pain, BEAR if above, else NEUTRAL."""
        if max_pain_strike <= 0:
            return "NEUTRAL"
        dist = spot - max_pain_strike
        if dist < -settings.MAX_PAIN_DISTANCE:
            return "BULL"   # price below max pain → expected pull up
        if dist > settings.MAX_PAIN_DISTANCE:
            return "BEAR"   # price above max pain → expected pull down
        return "NEUTRAL"

    # ── Aggregator ──────────────────────────────────────────────────────

    def get_signals(
        self,
        ohlcv_df: pd.DataFrame,
        close_series: pd.Series,
        chain_data: pd.DataFrame,
        spot: float,
        cached_max_pain: Optional[float] = None,
    ) -> SignalResult:
        """
        Run all four signals. Returns SignalResult with consensus + confidence.
        Pass `cached_max_pain` if already computed (once at open).
        """
        vwap_bias = self.compute_vwap(ohlcv_df, spot)
        rsi_sig = self.compute_rsi(close_series)
        pcr_sig = self.compute_pcr(chain_data)

        mp = cached_max_pain if cached_max_pain is not None else self.compute_max_pain(chain_data)
        mp_sig = self.max_pain_signal(spot, mp)

        direction_map = {
            "BULL": "BULL",
            "BEAR": "BEAR",
            "RANGE": "RANGE",
            "NEUTRAL": "RANGE",
            "OB": "BEAR",   # overbought leans bearish
            "OS": "BULL",   # oversold leans bullish
        }
        votes = [
            direction_map.get(vwap_bias, "RANGE"),
            direction_map.get(rsi_sig, "RANGE"),
            direction_map.get(pcr_sig, "RANGE"),
            direction_map.get(mp_sig, "RANGE"),
        ]
        bull = votes.count("BULL")
        bear = votes.count("BEAR")
        rng = votes.count("RANGE")

        if bull > bear and bull > rng:
            consensus, confidence = "BULL", bull
        elif bear > bull and bear > rng:
            consensus, confidence = "BEAR", bear
        else:
            consensus, confidence = "RANGE", rng

        return SignalResult(
            vwap_bias=vwap_bias,
            rsi_signal=rsi_sig,
            pcr_signal=pcr_sig,
            max_pain=mp,
            max_pain_signal=mp_sig,
            consensus=consensus,
            confidence=confidence,
        )

    def reset(self) -> None:
        """Call at start of each trading day."""
        self._prev_rsi = None
