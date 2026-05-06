import io
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from trading_system.config import settings
from trading_system.dashboard.terminal_dashboard import TerminalDashboard


def _make_dashboard(monkeypatch, *, summary_json=None, signals_lines=None, rich_import_raises=False):
    if rich_import_raises:
        import builtins

        real_import = builtins.__import__

        def mock_import(name, *args, **kwargs):
            if name == "rich":
                raise ImportError("No rich")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", mock_import)

    dash = TerminalDashboard(pnl_engine=None, trade_logger=None)

    def mock_open(path, *args, **kwargs):
        abspath = os.path.abspath(path)
        summary_path = os.path.abspath(os.path.join(settings.DATA_DIR, "pnl_snapshot.json"))
        signals_path = os.path.abspath(os.path.join(settings.DATA_DIR, "paper_signals.log"))
        if abspath == summary_path and summary_json is not None:
            return io.StringIO(json.dumps(summary_json))
        if abspath == signals_path and signals_lines is not None:
            if not signals_lines:
                return io.StringIO("")
            return io.StringIO("\n".join(signals_lines) + "\n")
        raise FileNotFoundError(path)

    monkeypatch.setattr("builtins.open", mock_open)

    def mock_exists(path):
        abspath = os.path.abspath(path)
        summary_path = os.path.abspath(os.path.join(settings.DATA_DIR, "pnl_snapshot.json"))
        signals_path = os.path.abspath(os.path.join(settings.DATA_DIR, "paper_signals.log"))
        if abspath == summary_path and summary_json is not None:
            return True
        if abspath == signals_path and signals_lines is not None:
            return True
        return False

    monkeypatch.setattr("os.path.exists", mock_exists)

    return dash


def test_load_summary_returns_dict(monkeypatch):
    dash = _make_dashboard(monkeypatch, summary_json={"total_pnl": 100, "win_rate_pct": 60.0})
    result = dash._load_summary()
    assert result["total_pnl"] == 100


def test_load_summary_file_missing(monkeypatch):
    dash = _make_dashboard(monkeypatch, summary_json=None)
    monkeypatch.setattr("os.path.exists", lambda p: False)
    assert dash._load_summary() == {}


def test_load_summary_invalid_json(monkeypatch):
    dash = TerminalDashboard(pnl_engine=None, trade_logger=None)

    def mock_open_bad(path, *args, **kwargs):
        return io.StringIO("{not valid json")

    monkeypatch.setattr("builtins.open", mock_open_bad)
    monkeypatch.setattr("os.path.exists", lambda p: True)

    assert dash._load_summary() == {}


def test_load_signals_returns_last_n(monkeypatch):
    lines = [f"signal-{i}" for i in range(10)]
    dash = _make_dashboard(monkeypatch, signals_lines=lines)
    result = dash._load_signals(n=3)
    assert len(result) == 3
    assert result == ["signal-7", "signal-8", "signal-9"]


def test_load_signals_file_missing(monkeypatch):
    dash = _make_dashboard(monkeypatch, signals_lines=None)
    monkeypatch.setattr("os.path.exists", lambda p: False)
    assert dash._load_signals() == []


def test_load_signals_empty_file(monkeypatch):
    dash = _make_dashboard(monkeypatch, signals_lines=[])
    result = dash._load_signals()
    assert result == []


def test_run_rich_missing(monkeypatch):
    dash = _make_dashboard(monkeypatch, rich_import_raises=True)
    dash.run()
