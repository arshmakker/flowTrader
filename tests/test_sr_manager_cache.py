"""tests/test_sr_manager_cache.py — BUG-17 regression: SRManager caches scan results."""

import csv
import os
import pathlib
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from trading_system.core.sr_manager import SRManager


def _make_market_data_dir(base, date_str, ltp_values):
    """Create market_data_YYYYMMDD/raw_data/futures/INDEX_YYYYMMDD.csv with given LTPs."""
    dir_path = pathlib.Path(base) / f"market_data_{date_str}" / "raw_data" / "futures"
    dir_path.mkdir(parents=True, exist_ok=True)
    csv_path = dir_path / f"NIFTY_{date_str}.csv"
    with open(csv_path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["ltp", "ts"])
        for v in ltp_values:
            w.writerow([v, "2026-01-01T10:00:00"])
    return csv_path


def test_sr_cached_on_second_call(tmp_path):
    """BUG-17: second call with unchanged dirs must not re-read the CSV."""
    _make_market_data_dir(tmp_path, "20260105", [24000, 24100, 24200])  # Mon
    _make_market_data_dir(tmp_path, "20260106", [24050, 24150])  # Tue

    sr = SRManager(base_dir=str(tmp_path))
    high1, low1 = sr.get_20day_high_low("NIFTY")
    assert high1 == 24200
    assert low1 == 24000

    # Second call: should hit cache. Sabotage disk so any re-scan would fail —
    # the only way this passes is if the cache is used.
    for p in (tmp_path).glob("**/*.csv"):
        p.unlink()
    high2, low2 = sr.get_20day_high_low("NIFTY")
    assert (high2, low2) == (high1, low1), "cache miss — disk was re-read"


def test_sr_cache_invalidates_when_new_dir_appears(tmp_path):
    """BUG-17: adding a new market_data dir must invalidate the cache."""
    _make_market_data_dir(tmp_path, "20260105", [24000, 24100])
    sr = SRManager(base_dir=str(tmp_path))
    high1, low1 = sr.get_20day_high_low("NIFTY")
    assert (high1, low1) == (24100, 24000)

    # Add a new day with higher highs.
    _make_market_data_dir(tmp_path, "20260106", [25000, 24500])
    high2, low2 = sr.get_20day_high_low("NIFTY")
    assert high2 == 25000
    assert low2 == 24000
