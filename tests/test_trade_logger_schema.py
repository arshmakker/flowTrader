"""Regression tests for TradeLogger schema-drift handling (Bug G).

If an existing paper_trades.csv has a header that differs from the current
TRADE_COLUMNS (schema drift from an older logger version), the logger must
archive the stale file to paper_trades_legacy.csv and start fresh rather than
appending misaligned rows to the old header.
"""

import csv
import os
import shutil
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from trading_system.core.trade_logger import TRADE_COLUMNS, TradeLogger


def _tmpdir(name):
    d = f"/tmp/test_tl_schema_{name}"
    if os.path.isdir(d):
        shutil.rmtree(d)
    os.makedirs(d)
    return d


def test_fresh_directory_writes_header():
    d = _tmpdir("fresh")
    TradeLogger(data_dir=d)
    path = os.path.join(d, "paper_trades.csv")
    with open(path) as f:
        header = next(csv.reader(f))
    assert header == TRADE_COLUMNS
    assert not os.path.exists(os.path.join(d, "paper_trades_legacy.csv"))


def test_matching_header_is_left_alone():
    d = _tmpdir("match")
    path = os.path.join(d, "paper_trades.csv")
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(TRADE_COLUMNS)
        w.writerow([f"row_{i}" for i in range(len(TRADE_COLUMNS))])
    TradeLogger(data_dir=d)
    assert not os.path.exists(os.path.join(d, "paper_trades_legacy.csv"))
    with open(path) as f:
        rows = list(csv.reader(f))
    assert rows[0] == TRADE_COLUMNS
    assert len(rows) == 2


def test_drifted_header_is_archived_and_replaced():
    d = _tmpdir("drift")
    path = os.path.join(d, "paper_trades.csv")
    legacy_path = os.path.join(d, "paper_trades_legacy.csv")
    old_header = [
        "trade_id",
        "date",
        "time_entry",
        "time_exit",
        "strategy",
        "instrument",
        "direction",
        "strike_1",
        "strike_2",
        "strike_3",
        "strike_4",
        "entry_price",
        "exit_price",
        "gross_pnl",
        "costs",
        "net_pnl",
        "exit_reason",
        "duration_mins",
        "lots",
        "vix_entry",
        "regime_entry",
        "day_type",
        "vwap_bias",
        "rsi_signal",
        "pcr_signal",
        "max_pain",
        "signal_confidence",
        "daily_target",
        "target_hit_today",
        "paper",
    ]
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(old_header)
        w.writerow(["x"] * len(old_header))

    TradeLogger(data_dir=d)

    # Active file has fresh header, legacy file has the old one.
    with open(path) as f:
        assert next(csv.reader(f)) == TRADE_COLUMNS
    assert os.path.exists(legacy_path)
    with open(legacy_path) as f:
        assert next(csv.reader(f)) == old_header


def test_second_drift_gets_timestamped_legacy_path():
    d = _tmpdir("twice")
    path = os.path.join(d, "paper_trades.csv")
    legacy_path = os.path.join(d, "paper_trades_legacy.csv")

    # Seed first drifted file.
    with open(path, "w", newline="") as f:
        csv.writer(f).writerow(["different"])
    TradeLogger(data_dir=d)
    assert os.path.exists(legacy_path)

    # Seed second drifted file and re-init — should not overwrite the first legacy.
    with open(path, "w", newline="") as f:
        csv.writer(f).writerow(["also_different"])
    TradeLogger(data_dir=d)

    # Original legacy still present; a timestamped variant also exists.
    assert os.path.exists(legacy_path)
    timestamped = [f for f in os.listdir(d) if f.startswith("paper_trades_legacy_") and f.endswith(".csv")]
    assert len(timestamped) == 1


def test_log_trade_after_drift_writes_under_new_header():
    d = _tmpdir("write_after")
    path = os.path.join(d, "paper_trades.csv")
    with open(path, "w", newline="") as f:
        csv.writer(f).writerow(["stale_col"])
    tl = TradeLogger(data_dir=d)
    tl.log_trade({"instrument": "NIFTY", "net_pnl": 1234})
    with open(path) as f:
        rows = list(csv.reader(f))
    assert rows[0] == TRADE_COLUMNS
    assert len(rows) == 2
    # trade_id must be populated in the new row.
    assert rows[1][0]
