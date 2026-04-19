"""
S/R Manager — implements the 20-day High/Low proxy rule (agents.md).

Short strikes must maintain a >= 50-point buffer from the 20-day high and 20-day low.
"""

import os
import logging
import pandas as pd
from datetime import datetime, timedelta
from typing import Tuple, Optional, List
from pathlib import Path

from trading_system.config import settings

logger = logging.getLogger(__name__)

class SRManager:
    def __init__(self, base_dir: str = "."):
        self.base_dir = Path(base_dir)
        # BUG-17: cache by (instrument, fingerprint-of-market_data-dirs). Invalidates
        # when a new market_data_* directory appears or the newest one is mtime-bumped.
        self._cache: dict = {}  # instrument -> (sr_high, sr_low, fingerprint)

    def _dirs_fingerprint(self) -> tuple:
        """Cheap signature of the market_data_* directory set. Used to invalidate the cache."""
        dirs = sorted(self.base_dir.glob('market_data_*'))
        return tuple((d.name, int(d.stat().st_mtime)) for d in dirs if d.is_dir())

    @staticmethod
    def _is_trading_day_dir(dir_name: str) -> bool:
        """
        market_data_YYYYMMDD directories are treated as trading days only if:
        - weekday (Mon-Fri)
        - not in settings.TRADING_HOLIDAYS_IST (ISO YYYY-MM-DD)
        """
        try:
            if not dir_name.startswith('market_data_'):
                return True
            date_str = dir_name.split('_', 2)[-1]
            dt = datetime.strptime(date_str, '%Y%m%d').date()
            if dt.weekday() >= 5:
                return False
            holidays = getattr(settings, 'TRADING_HOLIDAYS_IST', set()) or set()
            return dt.isoformat() not in set(holidays)
        except Exception:
            # If parsing fails, keep legacy behaviour (do not drop data).
            return True

    def get_20day_high_low(self, index_name: str) -> Tuple[float, float]:
        """
        Returns (20_day_high, 20_day_low) for the given index.
        Uses stored futures/spot data as proxy if available.
        Result is cached and invalidated when the market_data_* directory set changes.
        """
        fingerprint = self._dirs_fingerprint()
        cached = self._cache.get(index_name)
        if cached is not None and cached[2] == fingerprint:
            return cached[0], cached[1]

        # 1. Identify relevant market data directories
        data_dirs = sorted([d for d in self.base_dir.glob('market_data_*') if d.is_dir()], reverse=True)
        
        if not data_dirs:
            logger.warning(f"No market data directories found for S/R calculation of {index_name}")
            return 0.0, 0.0

        all_daily_highs = []
        all_daily_lows = []
        days_found = 0

        # 2. Iterate through directories to find 20 trading days of data
        for data_dir in data_dirs:
            if days_found >= 20:
                break

            # Skip weekends/holidays if a directory was created anyway
            if not self._is_trading_day_dir(data_dir.name):
                continue
            
            # Use futures as proxy for High/Low if spot OHLCV not available
            # Or use raw data if collected
            raw_futures_dir = data_dir / 'raw_data' / 'futures'
            if not raw_futures_dir.exists():
                continue
            
            # Find files starting with index_name
            # e.g., NIFTY24MAR26F_20260318.csv
            pattern = f"{index_name}*_{data_dir.name.split('_')[2]}.csv"
            files = list(raw_futures_dir.glob(pattern))
            
            if not files:
                continue
            
            try:
                # Read the file and get max/min of ltp
                df = pd.read_csv(files[0])
                if not df.empty and 'ltp' in df.columns:
                    all_daily_highs.append(df['ltp'].max())
                    all_daily_lows.append(df['ltp'].min())
                    days_found += 1
            except Exception as e:
                logger.debug(f"Error reading {files[0]} for S/R: {e}")
                continue

        if not all_daily_highs:
            logger.warning(f"Could not calculate 20-day S/R for {index_name} - no data files found")
            return 0.0, 0.0

        sr_high = max(all_daily_highs)
        sr_low = min(all_daily_lows)

        logger.info(f"{index_name}: 20-day S/R calculated over {days_found} days: High={sr_high:.2f}, Low={sr_low:.2f}")
        self._cache[index_name] = (sr_high, sr_low, fingerprint)
        return sr_high, sr_low

    def apply_buffer(self, strike: float, sr_high: float, sr_low: float, opt_type: str, step: int = 50) -> float:
        """
        Adjusts strike to respect the IC_SR_BUFFER (50 points).
        - Short Call: must be at least sr_high + 50.
        - Short Put: must be at least sr_low - 50.
        Rounding is based on the 'step' of the instrument.
        """
        if sr_high == 0.0 or sr_low == 0.0:
            return strike

        buffer = settings.IC_SR_BUFFER
        
        if opt_type == 'CE':
            # CE strike must be ABOVE sr_high + buffer
            min_allowed = sr_high + buffer
            if strike < min_allowed:
                adjusted = (int(min_allowed / step) + 1) * step # round up to next step
                logger.info(f"IC S/R Buffer: Adjusting CE strike {strike} -> {adjusted} (SR High: {sr_high}, step: {step})")
                return float(adjusted)
        elif opt_type == 'PE':
            # PE strike must be BELOW sr_low - buffer
            max_allowed = sr_low - buffer
            if strike > max_allowed:
                adjusted = (int(max_allowed / step)) * step # round down to prev step
                logger.info(f"IC S/R Buffer: Adjusting PE strike {strike} -> {adjusted} (SR Low: {sr_low}, step: {step})")
                return float(adjusted)
        
        return strike
