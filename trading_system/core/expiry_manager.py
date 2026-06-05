"""
Expiry Manager — returns active weekly expiries for NIFTY.
"""

import logging
from datetime import date, datetime
from typing import List, Optional

import pandas as pd

logger = logging.getLogger(__name__)


class ExpiryManager:
    def __init__(self, symbol_manager):
        self.sm = symbol_manager

    def _active_expiries(self, index_name: str, current_date: date) -> List[date]:
        """Return sorted list of all active expiry dates for index_name."""
        if self.sm.nse_fo is None:
            logger.error("NFO symbols not loaded in SymbolManager")
            return []

        options_df = self.sm.nse_fo[
            (self.sm.nse_fo["instrument"] == "OPTIDX") & (self.sm.nse_fo["symbol"] == index_name)
        ].copy()

        if options_df.empty:
            logger.error("No options found for %s", index_name)
            return []

        options_df["expiry_dt"] = pd.to_datetime(options_df["expiry"], format="%d-%b-%Y").dt.date
        return sorted(e for e in options_df["expiry_dt"].unique() if e >= current_date)

    def get_expiry(self, index_name: str, current_date: Optional[date] = None) -> Optional[date]:
        """Return the nearest active expiry date for index_name, or None."""
        if current_date is None:
            current_date = datetime.now().date()
        expiries = self._active_expiries(index_name, current_date)
        if not expiries:
            logger.error("No active expiries found for %s", index_name)
            return None
        return expiries[0]

    def get_expiries(self, index_name: str, count: int = 3, current_date: Optional[date] = None) -> List[date]:
        """Return up to `count` upcoming expiry dates, nearest first."""
        if current_date is None:
            current_date = datetime.now().date()
        expiries = self._active_expiries(index_name, current_date)
        if not expiries:
            logger.error("No active expiries found for %s", index_name)
        return expiries[:count]
