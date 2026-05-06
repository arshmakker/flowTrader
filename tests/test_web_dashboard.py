import io
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from trading_system.config import settings


def _make_dashboard(
    monkeypatch, *, summary_json=None, trades_csv=None, signals_text=None, golive_result=None, flask_import_raises=False
):
    captured = {}

    if flask_import_raises:
        import builtins

        real_import = builtins.__import__

        def mock_import(name, *args, **kwargs):
            if name == "flask":
                raise ImportError("No flask")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", mock_import)
        from trading_system.dashboard.web_dashboard import WebDashboard

        return None, captured

    from trading_system.dashboard.web_dashboard import WebDashboard

    def mock_open(path, *args, **kwargs):
        abspath = os.path.abspath(path)
        summary_path = os.path.abspath(os.path.join(settings.DATA_DIR, "pnl_snapshot.json"))
        trades_path = os.path.abspath(os.path.join(settings.DATA_DIR, "paper_trades.csv"))
        signals_path = os.path.abspath(os.path.join(settings.DATA_DIR, "paper_signals.log"))

        if abspath == summary_path and summary_json is not None:
            return io.StringIO(json.dumps(summary_json))
        if abspath == trades_path and trades_csv is not None:
            import csv

            buf = io.StringIO()
            writer = csv.DictWriter(buf, fieldnames=trades_csv[0].keys())
            writer.writeheader()
            for row in trades_csv:
                writer.writerow(row)
            buf.seek(0)
            return buf
        if abspath == signals_path and signals_text is not None:
            return io.StringIO(signals_text)
        raise FileNotFoundError(path)

    monkeypatch.setattr("builtins.open", mock_open)

    def mock_exists(path):
        abspath = os.path.abspath(path)
        summary_path = os.path.abspath(os.path.join(settings.DATA_DIR, "pnl_snapshot.json"))
        trades_path = os.path.abspath(os.path.join(settings.DATA_DIR, "paper_trades.csv"))
        signals_path = os.path.abspath(os.path.join(settings.DATA_DIR, "paper_signals.log"))
        if abspath in (summary_path, trades_path, signals_path):
            return True
        return False

    monkeypatch.setattr("os.path.exists", mock_exists)

    class MockEvaluator:
        def evaluate(self, summary, df):
            captured["evaluate_called"] = True
            return golive_result or {"verdict": "NOT_READY", "score": 0, "total": 10, "checks": {}}

    # Mock pd.read_csv to return empty DataFrame
    import pandas as pd

    monkeypatch.setattr("pandas.read_csv", lambda p: pd.DataFrame())

    dash = WebDashboard(pnl_engine=None, trade_logger=None, evaluator=MockEvaluator())
    app = dash._create_app()
    if app is None:
        return None, captured
    app.config["TESTING"] = True
    return app.test_client(), captured


def test_flask_missing(monkeypatch):
    client, _ = _make_dashboard(monkeypatch, flask_import_raises=True)
    assert client is None


def test_index_returns_html(monkeypatch):
    client, _ = _make_dashboard(monkeypatch)
    resp = client.get("/")
    assert resp.status_code == 200
    assert b"RegimeTrader" in resp.data


def test_summary_returns_json(monkeypatch):
    summary = {"total_pnl": 1234, "win_rate_pct": 55.0, "total_trades": 10}
    client, _ = _make_dashboard(monkeypatch, summary_json=summary)
    resp = client.get("/api/summary")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["total_pnl"] == 1234


def test_summary_missing_file(monkeypatch):
    client, _ = _make_dashboard(monkeypatch, summary_json=None)
    monkeypatch.setattr("os.path.exists", lambda p: False)
    resp = client.get("/api/summary")
    assert resp.status_code == 200
    assert resp.get_json() == {}


def test_trades_returns_list(monkeypatch):
    rows = [
        {"time_exit": "09:30", "strategy": "A", "direction": "LONG", "net_pnl": "10.5", "exit_reason": "TP"},
        {"time_exit": "09:45", "strategy": "B", "direction": "SHORT", "net_pnl": "20.0", "exit_reason": "SL"},
    ]
    client, _ = _make_dashboard(monkeypatch, trades_csv=rows)
    resp = client.get("/api/trades")
    assert resp.status_code == 200
    data = resp.get_json()
    assert isinstance(data, list)
    assert len(data) == 2


def test_trades_missing_file(monkeypatch):
    client, _ = _make_dashboard(monkeypatch, trades_csv=None)
    monkeypatch.setattr("os.path.exists", lambda p: False)
    resp = client.get("/api/trades")
    assert resp.status_code == 200
    assert resp.get_json() == []


def test_signals_returns_text(monkeypatch):
    text = "signal1\nsignal2\nsignal3\n"
    client, _ = _make_dashboard(monkeypatch, signals_text=text)
    resp = client.get("/api/signals")
    assert resp.status_code == 200
    assert resp.content_type.startswith("text/plain")
    assert b"signal2" in resp.data


def test_signals_missing_file(monkeypatch):
    client, _ = _make_dashboard(monkeypatch, signals_text=None)
    monkeypatch.setattr("os.path.exists", lambda p: False)
    resp = client.get("/api/signals")
    assert resp.status_code == 200
    assert resp.data == b""


def test_golive_returns_json(monkeypatch):
    result = {"verdict": "READY", "score": 10, "total": 10, "checks": {"live_ack": True}}
    client, captured = _make_dashboard(monkeypatch, golive_result=result)
    resp = client.get("/api/golive")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["verdict"] == "READY"
    assert captured["evaluate_called"] is True


def test_golive_exception(monkeypatch):
    from trading_system.dashboard.web_dashboard import WebDashboard

    class BrokenEvaluator:
        def evaluate(self, summary, df):
            raise RuntimeError("boom")

    dash = WebDashboard(pnl_engine=None, trade_logger=None, evaluator=BrokenEvaluator())
    app = dash._create_app()
    app.config["TESTING"] = True
    client = app.test_client()

    resp = client.get("/api/golive")
    assert resp.status_code == 200
    data = resp.get_json()
    assert "error" in data
