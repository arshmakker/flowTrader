import csv
import json
import os

import pytest

from trading_system.ops.reconcile import load_reconciliation_reports


def _write_engine_csv(path, rows):
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(
            f, fieldnames=["timestamp", "symbol", "side", "quantity", "fill_price", "status", "stt", "brokerage"]
        )
        writer.writeheader()
        for r in rows:
            writer.writerow(r)


def _write_broker_csv(path, rows):
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "timestamp",
                "symbol",
                "side",
                "quantity",
                "fill_price",
                "stt",
                "brokerage",
                "exch_txn",
                "sebi",
                "stamp",
                "gst",
            ],
        )
        writer.writeheader()
        for r in rows:
            writer.writerow(r)


def _write_expected_json(path, data):
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


def test_happy_path_matching(tmp_path):
    engine_rows = [
        {
            "timestamp": "2026-05-05T10:00:00",
            "symbol": "NFO|NIFTY25APR24C24000",
            "side": "SELL",
            "quantity": "650",
            "fill_price": "25.50",
            "status": "COMPLETE",
            "stt": "9.75",
            "brokerage": "5.00",
        },
        {
            "timestamp": "2026-05-05T10:00:02",
            "symbol": "NFO|NIFTY25APR24P23800",
            "side": "SELL",
            "quantity": "650",
            "fill_price": "18.25",
            "status": "COMPLETE",
            "stt": "6.50",
            "brokerage": "5.00",
        },
    ]
    broker_rows = [
        {
            "timestamp": "2026-05-05T10:00:01",
            "symbol": "NFO|NIFTY25APR24C24000",
            "side": "SELL",
            "quantity": "650",
            "fill_price": "25.75",
            "stt": "9.75",
            "brokerage": "5.00",
            "exch_txn": "0.25",
            "sebi": "0.01",
            "stamp": "0.05",
            "gst": "1.87",
        },
        {
            "timestamp": "2026-05-05T10:00:03",
            "symbol": "NFO|NIFTY25APR24P23800",
            "side": "SELL",
            "quantity": "650",
            "fill_price": "18.50",
            "stt": "6.50",
            "brokerage": "5.00",
            "exch_txn": "0.20",
            "sebi": "0.01",
            "stamp": "0.03",
            "gst": "1.50",
        },
    ]
    engine_csv = tmp_path / "paper_trades.csv"
    broker_csv = tmp_path / "broker_trades.csv"
    output_dir = tmp_path / "recon"
    output_path = output_dir / "reconciliation_20260505.json"

    _write_engine_csv(engine_csv, engine_rows)
    _write_broker_csv(broker_csv, broker_rows)
    os.makedirs(output_dir, exist_ok=True)

    from tools.reconcile_session import main

    rc = main(
        [
            "--date",
            "2026-05-05",
            "--broker-csv",
            str(broker_csv),
            "--engine",
            str(engine_csv),
            "--output-dir",
            str(output_dir),
        ]
    )
    assert rc == 0
    assert output_path.exists()

    with open(output_path) as f:
        report = json.load(f)

    assert report["engine_leg_count"] == 2
    assert report["broker_leg_count"] == 2
    assert len(report["matched"]) == 2
    assert len(report["unmatched_engine"]) == 0
    assert len(report["unmatched_broker"]) == 0
    assert len(report["flagged"]) == 0


def test_unmatched_engine_legs(tmp_path):
    engine_rows = [
        {
            "timestamp": "2026-05-05T10:00:00",
            "symbol": "NFO|NIFTY25APR24C24000",
            "side": "SELL",
            "quantity": "650",
            "fill_price": "25.50",
            "status": "COMPLETE",
            "stt": "9.75",
            "brokerage": "5.00",
        },
    ]
    broker_rows = []

    engine_csv = tmp_path / "paper_trades.csv"
    broker_csv = tmp_path / "broker_trades.csv"
    output_dir = tmp_path / "recon"

    _write_engine_csv(engine_csv, engine_rows)
    _write_broker_csv(broker_csv, broker_rows)
    os.makedirs(output_dir, exist_ok=True)

    from tools.reconcile_session import main

    rc = main(
        [
            "--date",
            "2026-05-05",
            "--broker-csv",
            str(broker_csv),
            "--engine",
            str(engine_csv),
            "--output-dir",
            str(output_dir),
        ]
    )
    assert rc == 1

    output_path = output_dir / "reconciliation_20260505.json"
    with open(output_path) as f:
        report = json.load(f)

    assert len(report["unmatched_engine"]) == 1
    assert len(report["unmatched_broker"]) == 0


def test_unmatched_broker_legs(tmp_path):
    engine_rows = []
    broker_rows = [
        {
            "timestamp": "2026-05-05T10:00:01",
            "symbol": "NFO|NIFTY25APR24C24000",
            "side": "SELL",
            "quantity": "650",
            "fill_price": "25.75",
            "stt": "9.75",
            "brokerage": "5.00",
            "exch_txn": "0.25",
            "sebi": "0.01",
            "stamp": "0.05",
            "gst": "1.87",
        },
    ]

    engine_csv = tmp_path / "paper_trades.csv"
    broker_csv = tmp_path / "broker_trades.csv"
    output_dir = tmp_path / "recon"

    _write_engine_csv(engine_csv, engine_rows)
    _write_broker_csv(broker_csv, broker_rows)
    os.makedirs(output_dir, exist_ok=True)

    from tools.reconcile_session import main

    rc = main(
        [
            "--date",
            "2026-05-05",
            "--broker-csv",
            str(broker_csv),
            "--engine",
            str(engine_csv),
            "--output-dir",
            str(output_dir),
        ]
    )
    assert rc == 1

    output_path = output_dir / "reconciliation_20260505.json"
    with open(output_path) as f:
        report = json.load(f)

    assert len(report["unmatched_engine"]) == 0
    assert len(report["unmatched_broker"]) == 1


def test_price_drift_flagging(tmp_path):
    engine_rows = [
        {
            "timestamp": "2026-05-05T10:00:00",
            "symbol": "NFO|NIFTY25APR24C24000",
            "side": "SELL",
            "quantity": "650",
            "fill_price": "25.00",
            "status": "COMPLETE",
            "stt": "9.75",
            "brokerage": "5.00",
        },
    ]
    broker_rows = [
        {
            "timestamp": "2026-05-05T10:00:01",
            "symbol": "NFO|NIFTY25APR24C24000",
            "side": "SELL",
            "quantity": "650",
            "fill_price": "26.00",
            "stt": "9.75",
            "brokerage": "5.00",
            "exch_txn": "0.25",
            "sebi": "0.01",
            "stamp": "0.05",
            "gst": "1.87",
        },
    ]

    engine_csv = tmp_path / "paper_trades.csv"
    broker_csv = tmp_path / "broker_trades.csv"
    output_dir = tmp_path / "recon"

    _write_engine_csv(engine_csv, engine_rows)
    _write_broker_csv(broker_csv, broker_rows)
    os.makedirs(output_dir, exist_ok=True)

    from tools.reconcile_session import main

    rc = main(
        [
            "--date",
            "2026-05-05",
            "--broker-csv",
            str(broker_csv),
            "--engine",
            str(engine_csv),
            "--output-dir",
            str(output_dir),
            "--flag-pct",
            "0.02",
        ]
    )
    assert rc == 1

    output_path = output_dir / "reconciliation_20260505.json"
    with open(output_path) as f:
        report = json.load(f)

    assert len(report["flagged"]) == 1
    assert report["flagged"][0]["price_delta_pct"] == pytest.approx(0.04, abs=0.005)


def test_cli_help():
    from io import StringIO
    from unittest.mock import patch

    with patch("sys.stdout", new_callable=StringIO) as fake_out:
        try:
            from tools.reconcile_session import main

            main(["--help"])
        except SystemExit:
            pass
        output = fake_out.getvalue()
        assert "--date" in output
        assert "--broker-csv" in output


def test_load_reconciliation_reports_empty(tmp_path):
    output_dir = tmp_path / "recon"
    os.makedirs(output_dir, exist_ok=True)
    reports = load_reconciliation_reports(str(output_dir))
    assert reports == []


def test_load_reconciliation_reports_multiple(tmp_path):
    output_dir = tmp_path / "recon"
    os.makedirs(output_dir, exist_ok=True)

    report1 = {
        "date": "2026-05-05",
        "engine_leg_count": 2,
        "broker_leg_count": 2,
        "matched_count": 2,
        "unmatched_engine_count": 0,
        "unmatched_broker_count": 0,
        "flagged_count": 0,
        "matched": [],
        "unmatched_engine": [],
        "unmatched_broker": [],
        "flagged": [],
    }
    report2 = {
        "date": "2026-05-06",
        "engine_leg_count": 4,
        "broker_leg_count": 4,
        "matched_count": 4,
        "unmatched_engine_count": 0,
        "unmatched_broker_count": 0,
        "flagged_count": 0,
        "matched": [],
        "unmatched_engine": [],
        "unmatched_broker": [],
        "flagged": [],
    }

    with open(output_dir / "reconciliation_20260505.json", "w") as f:
        json.dump(report1, f)
    with open(output_dir / "reconciliation_20260506.json", "w") as f:
        json.dump(report2, f)

    reports = load_reconciliation_reports(str(output_dir))
    assert len(reports) == 2
    assert reports[0]["date"] == "2026-05-05"
    assert reports[1]["date"] == "2026-05-06"
