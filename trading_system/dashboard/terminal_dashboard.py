"""
Terminal dashboard (agent.md §17).

Rich-based 4-panel terminal UI. Runs in a daemon thread.
Reads paper_summary.json every 5 seconds — never computes P&L itself.
"""

import json
import logging
import os
import time
from typing import Any

from trading_system.config import settings

logger = logging.getLogger(__name__)


class TerminalDashboard:
    """
    Requires `rich` (pip install rich).
    Falls back gracefully if rich is not installed.
    """

    def __init__(self, pnl_engine: Any, trade_logger: Any) -> None:
        self.pnl = pnl_engine
        self.tl = trade_logger
        # Live P&L: read the continuously refreshed snapshot (realised + unrealised).
        self._summary_path = os.path.join(settings.DATA_DIR, "pnl_snapshot.json")
        self._signals_path = os.path.join(settings.DATA_DIR, "paper_signals.log")

    def _load_summary(self) -> dict:
        try:
            if os.path.exists(self._summary_path):
                with open(self._summary_path) as f:
                    return json.load(f)
        except Exception:
            pass
        return {}

    def _load_signals(self, n: int = 5) -> list[str]:
        try:
            if os.path.exists(self._signals_path):
                with open(self._signals_path) as f:
                    lines = f.readlines()
                return [l.rstrip() for l in lines[-n:]]
        except Exception:
            pass
        return []

    def run(self) -> None:
        """Blocking loop — run in a daemon thread."""
        try:
            from rich.console import Console
            from rich.layout import Layout
            from rich.live import Live
            from rich.panel import Panel
            from rich.table import Table
            from rich.text import Text
        except ImportError:
            logger.warning("rich not installed; terminal dashboard disabled")
            return

        console = Console()

        def make_layout() -> Layout:
            layout = Layout()
            layout.split_column(
                Layout(name="header", size=3),
                Layout(name="body"),
                Layout(name="signals", size=8),
            )
            layout["body"].split_row(
                Layout(name="today"),
                Layout(name="strategies"),
            )
            return layout

        def render() -> Layout:
            s = self._load_summary()
            signals = self._load_signals()
            layout = make_layout()

            header_text = Text(
                f"  PAPER TRADING  |  "
                f"Total: ₹{s.get('total_pnl', 0):,.0f}  |  "
                f"Win Rate: {s.get('win_rate_pct', 0):.1f}%  |  "
                f"Trades: {s.get('total_trades', 0)}",
                style="bold white on blue",
            )
            layout["header"].update(Panel(header_text, style="blue"))

            today_text = (
                f"Realised:   ₹{s.get('realised_pnl', 0):>10,.0f}\n"
                f"Unrealised: ₹{s.get('unrealised_pnl', 0):>10,.0f}\n"
                f"Total:      ₹{s.get('total_pnl', 0):>10,.0f}"
            )
            layout["today"].update(Panel(today_text, title="Today"))

            strat_table = Table(show_header=True)
            strat_table.add_column("Strategy", width=10)
            strat_table.add_column("Trades", justify="right")
            strat_table.add_column("P&L", justify="right")
            strat_table.add_column("Win%", justify="right")
            strat_stats = s.get("strategy_stats", {}) or {}
            preferred = ("A", "B", "C", "D", "E")
            ordered_keys = [k for k in preferred if k in strat_stats]
            if not ordered_keys:
                ordered_keys = sorted(strat_stats.keys())
            for k in ordered_keys:
                ss = strat_stats.get(k, {}) or {}
                pnl_val = ss.get("total_pnl", 0)
                style = "green" if pnl_val >= 0 else "red"
                strat_table.add_row(
                    k,
                    str(ss.get("trades", 0)),
                    f"₹{pnl_val:,.0f}",
                    f"{ss.get('win_rate', 0):.0f}%",
                    style=style,
                )
            layout["strategies"].update(Panel(strat_table, title="Strategies"))

            sig_text = "\n".join(signals) if signals else "(no signals yet)"
            layout["signals"].update(Panel(sig_text, title="Last Signals"))

            return layout

        try:
            with Live(render(), console=console, refresh_per_second=0.2) as live:
                while True:
                    time.sleep(5)
                    live.update(render())
        except KeyboardInterrupt:
            pass
