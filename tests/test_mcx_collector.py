"""Offline tests for tools/mcx_collector.

Exercises the schema parser, front-2-expiries resolver, session-window
predicate, and one full polling iteration with a stubbed broker. No network,
no real Shoonya client, no sleeps.
"""

import csv
import sys
from datetime import date, datetime
from pathlib import Path

import pytest
import pytz

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))

import mcx_collector as mc  # noqa: E402

IST = pytz.timezone("Asia/Kolkata")


def _write_master(tmp_path: Path, rows: list[list[str]]) -> Path:
    p = tmp_path / "MCX.csv"
    header = [
        "Exchange",
        "Token",
        "LotSize",
        "GNGD",
        "Symbol",
        "TradingSymbol",
        "Expiry",
        "Instrument",
        "OptionType",
        "StrikePrice",
        "TickSize",
        "",
    ]
    with open(p, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        for r in rows:
            w.writerow(r)
    return p


def test_parse_mcx_master_extracts_expected_fields(tmp_path):
    p = _write_master(
        tmp_path,
        [
            ["MCX", "12345", "100", "1", "GOLD", "GOLD05JUN26", "05-JUN-2026", "FUTCOM", "XX", "0", "1", ""],
            ["MCX", "67890", "30", "1", "SILVER", "SILVER05MAY26", "05-MAY-2026", "FUTCOM", "XX", "0", "1", ""],
        ],
    )
    rows = mc.parse_mcx_master(p)
    assert len(rows) == 2
    assert rows[0]["symbol"] == "GOLD"
    assert rows[0]["trading_symbol"] == "GOLD05JUN26"
    assert rows[0]["lot_size"] == 100
    assert rows[0]["token"] == "12345"
    assert rows[0]["instrument"] == "FUTCOM"


def test_parse_mcx_master_rejects_changed_schema(tmp_path):
    """If Shoonya ever drops/reorders a column, fail loudly — silent
    misalignment was the documented risk."""
    p = tmp_path / "MCX.csv"
    with open(p, "w") as f:
        f.write("Exchange,Token,LotSize,Symbol\n")
        f.write("MCX,1,1,GOLD\n")
    with pytest.raises(ValueError, match="schema changed"):
        mc.parse_mcx_master(p)


def test_select_front_two_picks_earliest_two_per_underlying(tmp_path):
    today = date(2026, 5, 2)
    rows = [
        # GOLD: 3 expiries — should pick first 2
        {
            "symbol": "GOLD",
            "trading_symbol": "GOLD05JUN26",
            "expiry": "05-JUN-2026",
            "instrument": "FUTCOM",
            "exchange": "MCX",
            "token": "1",
            "lot_size": 100,
        },
        {
            "symbol": "GOLD",
            "trading_symbol": "GOLD05AUG26",
            "expiry": "05-AUG-2026",
            "instrument": "FUTCOM",
            "exchange": "MCX",
            "token": "2",
            "lot_size": 100,
        },
        {
            "symbol": "GOLD",
            "trading_symbol": "GOLD05OCT26",
            "expiry": "05-OCT-2026",
            "instrument": "FUTCOM",
            "exchange": "MCX",
            "token": "3",
            "lot_size": 100,
        },
        # SILVER: 1 expiry only
        {
            "symbol": "SILVER",
            "trading_symbol": "SILVER03JUL26",
            "expiry": "03-JUL-2026",
            "instrument": "FUTCOM",
            "exchange": "MCX",
            "token": "4",
            "lot_size": 30,
        },
    ]
    selected = mc.select_front_two(rows, ["GOLD", "SILVER"], today)
    syms = [c["trading_symbol"] for c in selected]
    assert syms == ["GOLD05JUN26", "GOLD05AUG26", "SILVER03JUL26"]


def test_select_front_two_skips_expired_contracts():
    today = date(2026, 5, 2)
    rows = [
        # already expired
        {
            "symbol": "GOLD",
            "trading_symbol": "GOLD05APR26",
            "expiry": "05-APR-2026",
            "instrument": "FUTCOM",
            "exchange": "MCX",
            "token": "1",
            "lot_size": 100,
        },
        # current and forward
        {
            "symbol": "GOLD",
            "trading_symbol": "GOLD05JUN26",
            "expiry": "05-JUN-2026",
            "instrument": "FUTCOM",
            "exchange": "MCX",
            "token": "2",
            "lot_size": 100,
        },
        {
            "symbol": "GOLD",
            "trading_symbol": "GOLD05AUG26",
            "expiry": "05-AUG-2026",
            "instrument": "FUTCOM",
            "exchange": "MCX",
            "token": "3",
            "lot_size": 100,
        },
    ]
    selected = mc.select_front_two(rows, ["GOLD"], today)
    assert [c["trading_symbol"] for c in selected] == ["GOLD05JUN26", "GOLD05AUG26"]


def test_select_front_two_skips_options_and_other_underlyings():
    """OPTFUT rows and non-targeted underlyings (GOLDM, etc) must not leak in."""
    today = date(2026, 5, 2)
    rows = [
        {
            "symbol": "GOLD",
            "trading_symbol": "GOLD05JUN26C50000",
            "expiry": "05-JUN-2026",
            "instrument": "OPTFUT",
            "exchange": "MCX",
            "token": "1",
            "lot_size": 100,
        },
        {
            "symbol": "GOLDM",
            "trading_symbol": "GOLDM05JUN26",
            "expiry": "05-JUN-2026",
            "instrument": "FUTCOM",
            "exchange": "MCX",
            "token": "2",
            "lot_size": 10,
        },
        {
            "symbol": "GOLD",
            "trading_symbol": "GOLD05JUN26",
            "expiry": "05-JUN-2026",
            "instrument": "FUTCOM",
            "exchange": "MCX",
            "token": "3",
            "lot_size": 100,
        },
    ]
    selected = mc.select_front_two(rows, ["GOLD"], today)
    assert [c["trading_symbol"] for c in selected] == ["GOLD05JUN26"]


def test_in_session_respects_weekends_and_hours():
    # Monday 10:00 IST — in session
    assert mc.in_session(IST.localize(datetime(2026, 5, 4, 10, 0)))
    # Monday 23:29 IST — still in
    assert mc.in_session(IST.localize(datetime(2026, 5, 4, 23, 29)))
    # Monday 23:31 IST — out
    assert not mc.in_session(IST.localize(datetime(2026, 5, 4, 23, 31)))
    # Monday 08:59 IST — pre-open
    assert not mc.in_session(IST.localize(datetime(2026, 5, 4, 8, 59)))
    # Saturday 12:00 IST — weekend
    assert not mc.in_session(IST.localize(datetime(2026, 5, 2, 12, 0)))
    # Sunday — weekend
    assert not mc.in_session(IST.localize(datetime(2026, 5, 3, 12, 0)))


def test_append_tick_creates_file_with_header_then_appends(tmp_path):
    contract = {
        "trading_symbol": "GOLD05JUN26",
        "instrument": "FUTCOM",
        "expiry": "05-JUN-2026",
        "lot_size": 100,
    }
    quote = {"lp": 71200.0, "v": 1234, "bp1": 71195.0, "sp1": 71205.0, "oi": 5000, "bq1": 10, "sq1": 12}
    ts = IST.localize(datetime(2026, 5, 4, 10, 0, 0))
    out = tmp_path / "out"
    out.mkdir()
    mc.append_tick(out, contract, quote, ts)
    mc.append_tick(out, contract, quote, ts)
    fpath = out / "MCX_GOLD05JUN26_20260504.csv"
    assert fpath.exists()
    with open(fpath) as f:
        lines = f.readlines()
    assert lines[0].startswith("timestamp,symbol,instrument")
    assert len(lines) == 3  # header + 2 rows


def test_append_tick_handles_missing_quote_fields(tmp_path):
    """Shoonya occasionally returns minimal quote dicts; missing keys must
    coerce to 0 rather than crash the loop."""
    contract = {"trading_symbol": "X", "instrument": "FUTCOM", "expiry": "05-JUN-2026", "lot_size": 1}
    ts = IST.localize(datetime(2026, 5, 4, 10, 0, 0))
    mc.append_tick(tmp_path, contract, {"lp": 100}, ts)
    fpath = tmp_path / "MCX_X_20260504.csv"
    with open(fpath) as f:
        rows = list(csv.DictReader(f))
    assert rows[0]["volume"] == "0"
    assert rows[0]["bid"] == "0.0"


def test_poll_once_writes_one_file_per_contract(tmp_path):
    class StubApi:
        def get_quotes(self, exchange, token, priority, context):
            return {"lp": 71000.0 + int(token), "v": 100, "bp1": 70999, "sp1": 71001, "oi": 1, "bq1": 1, "sq1": 1}

    contracts = [
        {
            "exchange": "MCX",
            "token": "1",
            "trading_symbol": "GOLD05JUN26",
            "instrument": "FUTCOM",
            "expiry": "05-JUN-2026",
            "lot_size": 100,
        },
        {
            "exchange": "MCX",
            "token": "2",
            "trading_symbol": "SILVER03JUL26",
            "instrument": "FUTCOM",
            "expiry": "03-JUL-2026",
            "lot_size": 30,
        },
    ]
    ts = IST.localize(datetime(2026, 5, 4, 10, 0, 0))
    import logging

    n = mc.poll_once(StubApi(), contracts, tmp_path, logging.getLogger("test"), ts)
    assert n == 2
    assert (tmp_path / "MCX_GOLD05JUN26_20260504.csv").exists()
    assert (tmp_path / "MCX_SILVER03JUL26_20260504.csv").exists()


def test_poll_once_skips_contract_on_quote_exception(tmp_path):
    class FlakyApi:
        def __init__(self):
            self.calls = 0

        def get_quotes(self, exchange, token, priority, context):
            self.calls += 1
            if token == "bad":
                raise RuntimeError("simulated network blip")
            return {"lp": 100.0}

    contracts = [
        {
            "exchange": "MCX",
            "token": "good",
            "trading_symbol": "OK",
            "instrument": "FUTCOM",
            "expiry": "05-JUN-2026",
            "lot_size": 1,
        },
        {
            "exchange": "MCX",
            "token": "bad",
            "trading_symbol": "FAIL",
            "instrument": "FUTCOM",
            "expiry": "05-JUN-2026",
            "lot_size": 1,
        },
    ]
    ts = IST.localize(datetime(2026, 5, 4, 10, 0, 0))
    import logging

    n = mc.poll_once(FlakyApi(), contracts, tmp_path, logging.getLogger("test"), ts)
    assert n == 1
    assert (tmp_path / "MCX_OK_20260504.csv").exists()
    assert not (tmp_path / "MCX_FAIL_20260504.csv").exists()


def test_run_exits_when_outside_session(tmp_path):
    """Saturday → run() should return on first iteration without polling."""
    polled = []

    class SpyApi:
        def get_quotes(self, **kw):
            polled.append(kw)
            return {"lp": 1.0}

    contracts = [
        {
            "exchange": "MCX",
            "token": "1",
            "trading_symbol": "X",
            "instrument": "FUTCOM",
            "expiry": "05-JUN-2026",
            "lot_size": 1,
        }
    ]
    sat = IST.localize(datetime(2026, 5, 2, 12, 0))
    import logging

    mc.run(
        SpyApi(),
        contracts,
        tmp_path,
        logging.getLogger("test"),
        now_fn=lambda: sat,
        sleep_fn=lambda _s: None,
        max_iterations=5,
    )
    assert polled == []  # never polled because weekend


def test_default_interval_keeps_sustained_rate_under_low_lane_cap():
    """If POLL_INTERVAL_SEC is lowered without re-deriving the budget, this
    test fails. Low-priority lane is 4 calls/sec; with 10 contracts (liquid-5
    × front-2 expiries) the cadence must keep sustained rate <= 4/s and
    leave headroom for paper-trader overlap (server-side cap is 10/s
    combined; both processes throttle independently)."""
    contracts = 2 * len(mc.LIQUID_5)
    sustained = contracts / mc.POLL_INTERVAL_SEC
    assert sustained <= 4.0, f"low-lane cap breach: {sustained:.2f}/s > 4/s"
    # Headroom for overlap: leave at least 50% of the 10/s global cap for
    # main.py's high-priority strategy calls.
    assert sustained <= 5.0


def test_run_polls_for_max_iterations_then_returns(tmp_path):
    """Bound the loop in tests."""

    class StubApi:
        def get_quotes(self, **kw):
            return {"lp": 100.0}

    contracts = [
        {
            "exchange": "MCX",
            "token": "1",
            "trading_symbol": "X",
            "instrument": "FUTCOM",
            "expiry": "05-JUN-2026",
            "lot_size": 1,
        }
    ]
    mon = IST.localize(datetime(2026, 5, 4, 10, 0))
    import logging

    mc.run(
        StubApi(),
        contracts,
        tmp_path,
        logging.getLogger("test"),
        now_fn=lambda: mon,
        sleep_fn=lambda _s: None,
        max_iterations=3,
    )
    fpath = tmp_path / "market_data_20260504" / "raw_data" / "futures" / "MCX_X_20260504.csv"
    assert fpath.exists()
    with open(fpath) as f:
        lines = f.readlines()
    assert len(lines) == 1 + 3  # header + 3 polls
