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
