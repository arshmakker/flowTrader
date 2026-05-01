import os
import tempfile

from data_collector import DataCollector
from symbol_manager import SymbolManager
from trading_system.core import position_persistence


class DummyApi:
    def get_quotes(self, exchange=None, token=None):
        return {"lp": "100", "v": "1", "bp1": "99", "sp1": "101", "oi": "0", "bq1": "1", "sq1": "1"}


class DummySymbolManager:
    def get_data_collection_symbols(self):
        return [{"symbol": "Nifty 50", "exchange": "NSE", "token": "26000", "instrument": "EQ"}]


def test_data_collector_duplicate_start_is_ignored():
    collector = DataCollector(DummyApi(), DummySymbolManager())
    collector.start_collection()
    first_thread = collector.collection_thread
    collector.start_collection()
    assert collector.collection_thread is first_thread
    collector.stop_collection()


def test_data_collector_refresh_for_current_day_updates_paths():
    collector = DataCollector(DummyApi(), DummySymbolManager())
    old_dir = collector.data_directory
    collector.data_directory = "market_data_19990101"
    collector.raw_data_directory = os.path.join(collector.data_directory, "raw_data")
    collector.refresh_for_current_day()
    assert collector.data_directory != "market_data_19990101"
    assert collector.raw_data_directory.startswith(collector.data_directory)
    assert collector.data_directory == old_dir


def test_symbol_manager_refresh_for_current_day_updates_paths():
    sm = SymbolManager(DummyApi())
    current_dir = sm.data_directory
    sm.data_directory = "market_data_19990101"
    sm.master_directory = os.path.join(sm.data_directory, "master_files")
    sm.refresh_for_current_day()
    assert sm.data_directory == current_dir
    assert sm.master_directory.startswith(sm.data_directory)


def test_flat_session_restores_pnl_on_same_day_restart():
    """Regression: flat session restart must restore daily PnL counters.

    Bug: load() returned early for SESSION_FLAT without restoring pnl_state,
    so a mid-day flat restart zeroed out trades that had already closed.
    (2026-04-30: ₹870 BANKNIFTY harvest lost after 14:15 restart found status=flat)
    """
    import json
    import tempfile
    from datetime import date

    from trading_system.core.position_persistence import SESSION_FLAT

    class _PnLStub:
        def __init__(self):
            self.restored = None
            self.reset_daily_arg = None

        def restore_state(self, state, *, reset_daily):
            self.restored = state
            self.reset_daily_arg = reset_daily

        def save_state(self):
            return {}

    today = date.today().isoformat()
    original = position_persistence.STATE_FILE
    try:
        with tempfile.TemporaryDirectory() as tmp:
            position_persistence.STATE_FILE = os.path.join(tmp, "open_positions.json")
            payload = {
                "saved_at": f"{today}T14:15:00",
                "session_status": SESSION_FLAT,
                "trading_date": today,
                "last_shutdown_reason": "shutdown",
                "strategies": {},
                "tracker_positions": {},
                "pnl_state": {"realised_pnl": 870.0, "total_trades": 1},
                "risk_state": {},
                "target_state": {},
                "regime_state": {},
            }
            with open(position_persistence.STATE_FILE, "w") as f:
                json.dump(payload, f)

            pnl_stub = _PnLStub()
            meta = position_persistence.load({}, object(), pnl_engine=pnl_stub)
            assert meta["restored_strategies"] == 0
            assert pnl_stub.restored is not None, "PnL must be restored on same-day flat restart"
            assert pnl_stub.restored["realised_pnl"] == 870.0
            assert pnl_stub.reset_daily_arg is False
    finally:
        position_persistence.STATE_FILE = original


def test_flat_session_does_not_restore_pnl_on_stale_day():
    """Stale-day flat session: PnL must NOT be restored (daily counters belong to a prior day)."""
    import json
    import tempfile

    from trading_system.core.position_persistence import SESSION_FLAT

    class _PnLStub:
        def __init__(self):
            self.restored = None

        def restore_state(self, state, *, reset_daily):
            self.restored = state

        def save_state(self):
            return {}

    original = position_persistence.STATE_FILE
    try:
        with tempfile.TemporaryDirectory() as tmp:
            position_persistence.STATE_FILE = os.path.join(tmp, "open_positions.json")
            payload = {
                "saved_at": "2020-01-01T15:10:00",
                "session_status": SESSION_FLAT,
                "trading_date": "2020-01-01",
                "last_shutdown_reason": "shutdown",
                "strategies": {},
                "tracker_positions": {},
                "pnl_state": {"realised_pnl": 870.0, "total_trades": 1},
                "risk_state": {},
                "target_state": {},
                "regime_state": {},
            }
            with open(position_persistence.STATE_FILE, "w") as f:
                json.dump(payload, f)

            pnl_stub = _PnLStub()
            position_persistence.load({}, object(), pnl_engine=pnl_stub)
            assert pnl_stub.restored is None, "Stale-day flat session must not restore yesterday's PnL"
    finally:
        position_persistence.STATE_FILE = original


def test_corrupt_state_file_is_quarantined():
    original = position_persistence.STATE_FILE
    try:
        with tempfile.TemporaryDirectory() as tmp:
            position_persistence.STATE_FILE = os.path.join(tmp, "open_positions.json")
            with open(position_persistence.STATE_FILE, "w") as f:
                f.write("{ bad json")
            meta = position_persistence.load({}, object())
            assert meta["restored_strategies"] == 0
            assert not os.path.exists(position_persistence.STATE_FILE)
            quarantined = [name for name in os.listdir(tmp) if ".corrupt." in name]
            assert quarantined
    finally:
        position_persistence.STATE_FILE = original
