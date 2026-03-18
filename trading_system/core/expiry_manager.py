"""
Expiry Manager — implements the '3 DTE Rolling Rule' (agents.md).

If current weekly expiry has < 3 DTE, roll all new entries to the next week's expiry contract.
"""

import logging
from datetime import datetime, date
from typing import Optional, List
import pandas as pd

from trading_system.config import settings

logger = logging.getLogger(__name__)

class ExpiryManager:
    def __init__(self, symbol_manager):
        self.sm = symbol_manager

    def get_expiry(self, index_name: str, current_date: Optional[date] = None) -> Optional[str]:
        """
        Returns the optimal expiry (str) for a new Iron Condor position.
        Format: DD-MMM-YYYY (e.g., '19-MAR-2026')
        """
        if current_date is None:
            current_date = datetime.now().date()

        # 1. Get all active expiries for the index
        if self.sm.nse_fo is None:
            logger.error("NFO symbols not loaded in SymbolManager")
            return None

        options_df = self.sm.nse_fo[
            (self.sm.nse_fo['instrument'] == 'OPTIDX') &
            (self.sm.nse_fo['symbol'] == index_name)
        ].copy()

        if options_df.empty:
            logger.error(f"No options found for {index_name}")
            return None

        # 2. Parse and filter expiries
        options_df['expiry_dt'] = pd.to_datetime(options_df['expiry'], format='%d-%b-%Y').dt.date
        unique_expiries = sorted(options_df['expiry_dt'].unique())
        active_expiries = [e for e in unique_expiries if e >= current_date]

        if not active_expiries:
            logger.error(f"No active expiries found for {index_name}")
            return None

        # 3. Apply the 3 DTE Rolling Rule
        nearest_expiry = active_expiries[0]
        dte = (nearest_expiry - current_date).days

        if dte < settings.IC_DTE_THRESHOLD:
            if len(active_expiries) > 1:
                selected_expiry = active_expiries[1]
                logger.info(f"{index_name}: DTE={dte} (<{settings.IC_DTE_THRESHOLD}), rolling to next week: {selected_expiry}")
            else:
                selected_expiry = nearest_expiry
                logger.warning(f"{index_name}: DTE={dte} (<{settings.IC_DTE_THRESHOLD}) but no next week expiry found! Using {selected_expiry}")
        else:
            selected_expiry = nearest_expiry
            logger.info(f"{index_name}: DTE={dte} (>= {settings.IC_DTE_THRESHOLD}), using current week: {selected_expiry}")

        # Convert back to DD-MMM-YYYY
        return selected_expiry.strftime('%d-%b-%Y').upper()
