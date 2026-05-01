from __future__ import annotations

import argparse
import shutil
import sys
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

# Ensure repo root is importable when run as a script from tools/
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


@dataclass(frozen=True)
class Candidate:
    path: Path
    d: date
    reason: str  # "weekend" | "holiday"


def _load_holidays_iso() -> set[str]:
    """Holidays as ISO date strings ('YYYY-MM-DD') from settings."""
    try:
        from trading_system.config import settings  # type: ignore

        raw = getattr(settings, "TRADING_HOLIDAYS_IST", None)
        if not raw:
            return set()
        return set(raw)
    except Exception:
        return set()


def _parse_market_data_dir(d: Path) -> date | None:
    """Parse market_data_YYYYMMDD directory name into a date."""
    name = d.name
    if not name.startswith("market_data_"):
        return None
    suffix = name.split("_", 2)[-1]
    try:
        return datetime.strptime(suffix, "%Y%m%d").date()
    except ValueError:
        return None


def find_non_trading_day_dirs(base: Path) -> list[Candidate]:
    holidays = _load_holidays_iso()
    out: list[Candidate] = []

    for p in sorted(base.iterdir()):
        if not p.is_dir():
            continue
        d = _parse_market_data_dir(p)
        if d is None:
            continue
        if d.weekday() >= 5:
            out.append(Candidate(path=p, d=d, reason="weekend"))
            continue
        if d.isoformat() in holidays:
            out.append(Candidate(path=p, d=d, reason="holiday"))
            continue

    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="Delete market_data_YYYYMMDD folders that are weekends/holidays.")
    ap.add_argument("--base", default=".", help="Base directory to scan (default: repo root).")
    ap.add_argument("--dry-run", action="store_true", help="Print what would be deleted, but do not delete anything.")
    args = ap.parse_args()

    base = Path(args.base).resolve()
    candidates = find_non_trading_day_dirs(base)

    if not candidates:
        print("No weekend/holiday market_data_YYYYMMDD directories found.")
        return 0

    for c in candidates:
        print(f"{c.path.name}	{c.d.isoformat()}	{c.reason}")

    if args.dry_run:
        print(f"Dry run: would delete {len(candidates)} directories.")
        return 0

    deleted = 0
    for c in candidates:
        shutil.rmtree(c.path, ignore_errors=False)
        deleted += 1

    print(f"Deleted {deleted} directories.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
