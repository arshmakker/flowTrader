"""
Expiry Manager — returns the nearest active weekly expiry for NIFTY.
"""

import logging
from datetime import date, datetime
from typing import Optional

import pandas as pd

logger = logging.getLogger(__name__)


class ExpiryManager:
    def __init__(self, symbol_manager):
        self.sm = symbol_manager

    def get_expiry(self, index_name: str, current_date: Optional[date] = None) -> Optional[date]:
        """Return the nearest active expiry date for index_name, or None."""
        if current_date is None:
            current_date = datetime.now().date()

        if self.sm.nse_fo is None:
            logger.error("NFO symbols not loaded in SymbolManager")
            return None

        options_df = self.sm.nse_fo[
            (self.sm.nse_fo["instrument"] == "OPTIDX") & (self.sm.nse_fo["symbol"] == index_name)
        ].copy()

        if options_df.empty:
            logger.error("No options found for %s", index_name)
            return None

        options_df["expiry_dt"] = pd.to_datetime(options_df["expiry"], format="%d-%b-%Y").dt.date
        active_expiries = sorted(e for e in options_df["expiry_dt"].unique() if e >= current_date)

        if not active_expiries:
            logger.error("No active expiries found for %s", index_name)
            return None

        return active_expiries[0]
