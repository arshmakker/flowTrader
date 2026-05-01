"""Regression test: every PaperOrderManager.place_order call persists one row
to data/paper_orders.csv with the expected columns, including REJECTED orders.
Covers the follow-on to the 2026-04-20 audit gap where only aggregate trade
rows were logged, making per-leg entry/exit fills unrecoverable after the
process exited.
"""

import csv
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from trading_system.paper.paper_order_manager import (
    _ORDERS_CSV_COLUMNS,
    PaperOrderManager,
)


class _MD:
    def __init__(self, prices):
        self._prices = prices

    def get_ltp(self, sym):
        return self._prices.get(sym, 0.0)


def _read_rows(path):
    with open(path, "r", newline="") as f:
        return list(csv.reader(f))


def test_complete_order_is_persisted(tmp_path):
    csv_path = str(tmp_path / "paper_orders.csv")
    md = _MD({"NFO|NIFTY19MAR26C22150": 18.0})
    om = PaperOrderManager(md, position_tracker=None, orders_csv_path=csv_path)

    order = om.place_order("NFO|NIFTY19MAR26C22150", "SELL", 65)

    assert order["status"] == "COMPLETE"
    rows = _read_rows(csv_path)
    assert rows[0] == _ORDERS_CSV_COLUMNS
    assert len(rows) == 2, "expected header + one data row"
    data = dict(zip(_ORDERS_CSV_COLUMNS, rows[1]))
    assert data["order_id"] == order["order_id"]
    assert data["symbol"] == "NFO|NIFTY19MAR26C22150"
    assert data["side"] == "SELL"
    assert data["quantity"] == "65"
    assert float(data["fill_price"]) == order["fill_price"]
    assert data["status"] == "COMPLETE"
    assert data["paper"] == "True"


def test_rejected_order_missing_ltp_is_persisted(tmp_path):
    csv_path = str(tmp_path / "paper_orders.csv")
    md = _MD({})  # no LTP available
    om = PaperOrderManager(md, position_tracker=None, orders_csv_path=csv_path)

    order = om.place_order("NFO|NIFTY19MAR26C22150", "BUY", 65)

    assert order["status"] == "REJECTED"
    assert order["reason"] == "missing_ltp"
    rows = _read_rows(csv_path)
    assert len(rows) == 2
    data = dict(zip(_ORDERS_CSV_COLUMNS, rows[1]))
    assert data["status"] == "REJECTED"
    assert data["reason"] == "missing_ltp"
    assert float(data["fill_price"]) == 0.0


def test_rejected_order_suspicious_ltp_is_persisted(tmp_path):
    csv_path = str(tmp_path / "paper_orders.csv")
    # LTP above PAPER_OPTION_LTP_MAX (5000) triggers suspicious_option_ltp reject
    md = _MD({"NFO|NIFTY19MAR26C22150": 9999.0})
    om = PaperOrderManager(md, position_tracker=None, orders_csv_path=csv_path)

    order = om.place_order("NFO|NIFTY19MAR26C22150", "SELL", 65)

    assert order["status"] == "REJECTED"
    assert order["reason"] == "suspicious_option_ltp"
    rows = _read_rows(csv_path)
    assert len(rows) == 2
    data = dict(zip(_ORDERS_CSV_COLUMNS, rows[1]))
    assert data["status"] == "REJECTED"
    assert data["reason"] == "suspicious_option_ltp"


def test_multiple_orders_append_without_duplicating_header(tmp_path):
    csv_path = str(tmp_path / "paper_orders.csv")
    md = _MD(
        {
            "NFO|NIFTY19MAR26C22150": 18.0,
            "NFO|NIFTY19MAR26P21850": 17.0,
            "NFO|NIFTY19MAR26C22200": 5.0,
            "NFO|NIFTY19MAR26P21800": 5.5,
        }
    )
    om = PaperOrderManager(md, position_tracker=None, orders_csv_path=csv_path)

    om.place_order("NFO|NIFTY19MAR26C22150", "SELL", 65)
    om.place_order("NFO|NIFTY19MAR26P21850", "SELL", 65)
    om.place_order("NFO|NIFTY19MAR26C22200", "BUY", 65)
    om.place_order("NFO|NIFTY19MAR26P21800", "BUY", 65)

    rows = _read_rows(csv_path)
    assert rows[0] == _ORDERS_CSV_COLUMNS
    assert len(rows) == 5, "header + 4 legs"
    # Header appears exactly once
    assert sum(1 for r in rows if r == _ORDERS_CSV_COLUMNS) == 1


def test_header_is_created_even_if_no_orders_placed(tmp_path):
    csv_path = str(tmp_path / "paper_orders.csv")
    md = _MD({})
    PaperOrderManager(md, position_tracker=None, orders_csv_path=csv_path)
    assert os.path.exists(csv_path)
    rows = _read_rows(csv_path)
    assert rows == [_ORDERS_CSV_COLUMNS]


def test_second_manager_reuses_existing_csv_without_overwriting(tmp_path):
    csv_path = str(tmp_path / "paper_orders.csv")
    md = _MD({"NFO|NIFTY19MAR26C22150": 18.0})

    om1 = PaperOrderManager(md, position_tracker=None, orders_csv_path=csv_path)
    om1.place_order("NFO|NIFTY19MAR26C22150", "SELL", 65)
    # A second manager against the same path should not wipe the existing file.
    om2 = PaperOrderManager(md, position_tracker=None, orders_csv_path=csv_path)
    om2.place_order("NFO|NIFTY19MAR26C22150", "SELL", 65)

    rows = _read_rows(csv_path)
    assert rows[0] == _ORDERS_CSV_COLUMNS
    assert len(rows) == 3, "header + 2 fills, first manager's row preserved"
