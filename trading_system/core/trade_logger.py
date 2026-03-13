"""
Trade logger (agent.md §15).

Writes two output files:
  data/paper_trades.csv  — one row per completed trade (source of truth for go-live evaluator)
  data/paper_signals.log — append-only signal evaluation log

Both files are also read by the web dashboard — the logger is the only writer.
"""

from __future__ import annotations

import csv
import logging
import os
from datetime import datetime
from typing import Any, Dict, Optional

from trading_system.config import settings

logger = logging.getLogger(__name__)

TRADE_COLUMNS = [
    "trade_id", "date", "time_entry", "time_exit", "strategy",
    "instrument", "direction", "strike_1", "strike_2",
    "strike_3", "strike_4",
    "entry_price", "exit_price", "gross_pnl", "costs", "net_pnl",
    "exit_reason",
    "duration_mins", "lots",
    "vix_entry", "regime_entry", "day_type",
    "vwap_bias", "rsi_signal", "pcr_signal", "max_pain",
    "signal_confidence",
    "daily_target", "target_hit_today",
    "paper",
]


class TradeLogger:
    def __init__(self, data_dir: str = settings.DATA_DIR) -> None:
        self.data_dir = data_dir
        os.makedirs(self.data_dir, exist_ok=True)
        self._trades_path = os.path.join(self.data_dir, "paper_trades.csv")
        self._signals_path = os.path.join(self.data_dir, "paper_signals.log")
        self._ensure_csv_header()
        self._trade_counter = self._read_last_counter()

    def _ensure_csv_header(self) -> None:
        if not os.path.exists(self._trades_path):
            with open(self._trades_path, "w", newline="") as f:
                csv.writer(f).writerow(TRADE_COLUMNS)

    def _read_last_counter(self) -> int:
        """Resume counter from existing CSV to avoid duplicate trade IDs across restarts."""
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
            logger.debug("Could not read last trade counter from CSV", exc_info=True)
        return 0

    def _next_trade_id(self) -> str:
        self._trade_counter += 1
        return f"{datetime.now().strftime('%Y%m%d')}_{self._trade_counter:04d}"

    # ── Trade logging ───────────────────────────────────────────────────

    # Strategy results use different keys than CSV columns — normalize here
    _KEY_ALIASES = {
        "exit_reason": ["reason", "exit_reason"],
        "gross_pnl": ["pnl", "gross_pnl"],
        "net_pnl": ["pnl", "net_pnl"],
        "time_entry": ["entry_time", "time_entry"],
        "costs": ["costs", "total_costs"],
    }

    def log_trade(self, trade: Dict[str, Any]) -> str:
        trade_id = self._next_trade_id()
        trade["trade_id"] = trade_id
        trade.setdefault("date", datetime.now().strftime("%Y-%m-%d"))
        trade.setdefault("paper", True)

        row = []
        for col in TRADE_COLUMNS:
            val = trade.get(col, "")
            if val == "" and col in self._KEY_ALIASES:
                for alias in self._KEY_ALIASES[col]:
                    val = trade.get(alias, "")
                    if val != "":
                        break
            row.append(val)
        try:
            with open(self._trades_path, "a", newline="") as f:
                csv.writer(f).writerow(row)
        except Exception:
            logger.exception("Failed to write trade to CSV")
        return trade_id

    # ── Signal logging ──────────────────────────────────────────────────

    def log_signal(
        self,
        message: Optional[str] = None,
        *,
        regime: str = "",
        day_type: str = "",
        day_confidence: str = "",
        routing: Optional[Dict] = None,
        signals: Optional[Any] = None,
        action: str = "",
    ) -> None:
        ts = datetime.now().strftime("%H:%M:%S")
        if message:
            line = f"[{ts}] {message}"
        else:
            route_str = ",".join((routing or {}).get("primary", []))
            sig_str = ""
            if signals:
                sig_str = (
                    f"VWAP={signals.vwap_bias} RSI={signals.rsi_signal} "
                    f"PCR={signals.pcr_signal} CONF={signals.confidence}"
                )
            line = (
                f"[{ts}] VIX={regime} | DAY={day_type} {day_confidence} | "
                f"ROUTE={route_str}\n"
                f"           {sig_str} | ACTION={action}"
            )
        try:
            with open(self._signals_path, "a") as f:
                f.write(line + "\n")
        except Exception:
            logger.exception("Failed to write signal log")
