from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import sys

# Ensure repo root is importable when run as a script from tools/
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from strategy_runner import is_trading_day_ist, is_market_hours, is_market_closed_ist  # noqa: E402
from trading_system.config import settings  # noqa: E402
from tools.purge_non_trading_day_data import find_non_trading_day_dirs  # noqa: E402


@dataclass(frozen=True)
class Report:
    when: datetime
    trading_day: bool
    market_hours: bool
    market_closed: bool


def _parse_when(s: str) -> datetime:
    """
    Parse a timestamp in one of:
    - YYYY-MM-DD
    - YYYY-MM-DD HH:MM
    - YYYY-MM-DD HH:MM:SS
    """
    s = s.strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(s, fmt)
            if fmt == "%Y-%m-%d":
                return dt.replace(hour=12, minute=0, second=0, microsecond=0)
            return dt
        except ValueError:
            continue
    raise ValueError(f"Unrecognized --when format: {s!r}")


def build_report(when: datetime) -> Report:
    return Report(
        when=when,
        trading_day=is_trading_day_ist(when),
        market_hours=is_market_hours(when),
        market_closed=is_market_closed_ist(when),
    )


def print_report(r: Report) -> None:
    holidays = set(getattr(settings, "TRADING_HOLIDAYS_IST", set()) or set())
    d = r.when.date().isoformat()
    is_holiday = d in holidays
    print("=== Dry run: holiday-sensitive parameters ===")
    print(f"when                : {r.when.isoformat(sep=' ')}")
    print(f"date iso            : {d}")
    print(f"is_weekend          : {r.when.date().weekday() >= 5}")
    print(f"is_config_holiday   : {is_holiday}")
    print(f"is_trading_day_ist  : {r.trading_day}")
    print(f"is_market_hours     : {r.market_hours}")
    print(f"is_market_closed_ist: {r.market_closed}")
    print()
    print("Expected runtime effect")
    if not r.trading_day:
        print("- main.py: will exit BEFORE login/symbol load, so no market_data_YYYYMMDD folders are created.")
        print("- DataCollector/SymbolManager: will not run, so no quote collection or intraday metrics.")
        print("- Credit/VIX/VWAP/day classification: not evaluated.")
    else:
        print("- main.py: proceeds to login + normal orchestration.")
        print("- is_market_hours: controls whether collector.start_collection() is allowed.")
    print()


def print_purge_preview(repo_root: Path) -> None:
    print("=== Purge preview (repo root) ===")
    candidates = find_non_trading_day_dirs(repo_root)
    if not candidates:
        print("No weekend/holiday market_data_YYYYMMDD directories found.")
        return
    for c in candidates:
        print(f"{c.path.name}	{c.d.isoformat()}	{c.reason}")
    print(f"Total: {len(candidates)} directories would be deleted.")
    print()


def main() -> int:
    ap = argparse.ArgumentParser(description="Offline dry-run of holiday-sensitive system parameters.")
    ap.add_argument(
        "--when",
        required=True,
        help="Timestamp to evaluate (YYYY-MM-DD[ HH:MM[:SS]]).",
    )
    ap.add_argument(
        "--purge-preview",
        action="store_true",
        help="Also list weekend/holiday market_data_YYYYMMDD dirs that would be purged under this repo root.",
    )
    args = ap.parse_args()

    when = _parse_when(args.when)
    r = build_report(when)
    print_report(r)
    if args.purge_preview:
        print_purge_preview(_REPO_ROOT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
