"""
Trade logger (agents.md).

Writes output files:
  data/paper_trades.csv  — one row per completed trade
"""

import csv
import logging
import os
from datetime import datetime
from typing import Any, Dict

from trading_system.config import settings

logger = logging.getLogger(__name__)

TRADE_COLUMNS = [
    "trade_id",
    "date",
    "entry_date",
    "time_entry",
    "time_exit",
    "instrument",
    "sc_strike",
    "sp_strike",
    "lc_strike",
    "lp_strike",
    "entry_credit",
    "exit_price",
    "gross_pnl",
    "net_pnl",
    "exit_reason",
    "lots",
    "peak_pnl",
    "vix_entry",
    "day_type",
    "paper",
]


class TradeLogger:
    def __init__(self, data_dir: str = settings.DATA_DIR) -> None:
        self.data_dir = data_dir
        os.makedirs(self.data_dir, exist_ok=True)
        self._trades_path = os.path.join(self.data_dir, "paper_trades.csv")
        self._ensure_csv_header()
        self._trade_counter = self._read_last_counter()

    def _ensure_csv_header(self) -> None:
        if not os.path.exists(self._trades_path):
            with open(self._trades_path, "w", newline="") as f:
                csv.writer(f).writerow(TRADE_COLUMNS)
            return
        try:
            with open(self._trades_path, "r", newline="") as f:
                existing_header = next(csv.reader(f), [])
        except Exception:
            logger.exception("Failed to read existing trades CSV header")
            return
        if existing_header == TRADE_COLUMNS:
            return
        archived = self._archive_path(self._trades_path)
        try:
            os.replace(self._trades_path, archived)
        except Exception:
            logger.exception("Failed to archive trades CSV with stale header; keeping as-is")
            return
        logger.warning(
            "paper_trades.csv header mismatch (had %d cols, expected %d) — archived to %s",
            len(existing_header),
            len(TRADE_COLUMNS),
            archived,
        )
        with open(self._trades_path, "w", newline="") as f:
            csv.writer(f).writerow(TRADE_COLUMNS)

    @staticmethod
    def _archive_path(trades_path: str) -> str:
        base, ext = os.path.splitext(trades_path)
        legacy = f"{base}_legacy{ext}"
        if not os.path.exists(legacy):
            return legacy
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        return f"{base}_legacy_{ts}{ext}"

    def _read_last_counter(self) -> int:
        if not os.path.exists(self._trades_path):
            return 0
        try:
            with open(self._trades_path, "r", newline="") as f:
                reader = csv.reader(f)
                last_id = ""
                for row in reader:
                    if row and row[0] != "trade_id":
                        last_id = row[0]
                if last_id and "_" in last_id:
                    return int(last_id.rsplit("_", 1)[1])
        except Exception:
            pass
        return 0

    def _next_trade_id(self) -> str:
        self._trade_counter += 1
        return f"{datetime.now().strftime('%Y%m%d')}_{self._trade_counter:04d}"

    def log_trade(self, trade: Dict[str, Any]) -> str:
        trade_id = self._next_trade_id()
        trade["trade_id"] = trade_id
        trade.setdefault("date", datetime.now().strftime("%Y-%m-%d"))
        trade.setdefault("paper", True)

        row = [trade.get(col, "") for col in TRADE_COLUMNS]
        try:
            with open(self._trades_path, "a", newline="") as f:
                csv.writer(f).writerow(row)
        except Exception:
            logger.exception("Failed to write trade to CSV")
        return trade_id
