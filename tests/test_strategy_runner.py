"""Tests for strategy_runner public functions."""
import sys
import os
from datetime import datetime, date, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from strategy_runner import (
    get_weekly_expiry,
    is_market_hours,
    is_market_closed_ist,
    save_daily_metrics,
)


# ── get_weekly_expiry ────────────────────────────────────────────────

def test_weekly_expiry_monday_returns_thursday():
    monday = datetime(2026, 3, 9, 10, 0)  # Monday
    exp = get_weekly_expiry(monday)
    assert exp.weekday() == 3  # Thursday
    assert exp == date(2026, 3, 12)


def test_weekly_expiry_thursday_before_close():
    thursday_morning = datetime(2026, 3, 12, 10, 0)
    exp = get_weekly_expiry(thursday_morning)
    assert exp == date(2026, 3, 12)


def test_weekly_expiry_thursday_after_close():
    thursday_evening = datetime(2026, 3, 12, 16, 0)
    exp = get_weekly_expiry(thursday_evening)
    assert exp == date(2026, 3, 19)  # next Thursday


def test_weekly_expiry_friday_returns_next_thursday():
    friday = datetime(2026, 3, 13, 10, 0)
    exp = get_weekly_expiry(friday)
    assert exp.weekday() == 3
    assert exp == date(2026, 3, 19)


def test_weekly_expiry_wednesday_returns_thursday():
    wednesday = datetime(2026, 3, 11, 10, 0)
    exp = get_weekly_expiry(wednesday)
    assert exp == date(2026, 3, 12)


def test_weekly_expiry_saturday():
    saturday = datetime(2026, 3, 14, 10, 0)
    exp = get_weekly_expiry(saturday)
    assert exp.weekday() == 3
    assert exp == date(2026, 3, 19)


# ── is_market_hours / is_market_closed_ist ───────────────────────────

def test_market_hours_returns_bool():
    result = is_market_hours()
    assert isinstance(result, bool)


def test_market_closed_returns_bool():
    result = is_market_closed_ist()
    assert isinstance(result, bool)


# ── save_daily_metrics ───────────────────────────────────────────────

def test_save_daily_metrics_creates_file():
    import json
    import shutil

    metrics = {"nifty_close": 24500, "vix": 14.5}
    save_daily_metrics(metrics, date_str="20260101")
    data_dir = "market_data_20260101"
    path = os.path.join(data_dir, "daily_metrics.json")
    assert os.path.exists(path)

    with open(path) as f:
        data = json.load(f)
    assert data["nifty_close"] == 24500
    assert data["date"] == "20260101"

    shutil.rmtree(data_dir, ignore_errors=True)


def test_save_daily_metrics_merges():
    import json
    import shutil

    save_daily_metrics({"a": 1}, date_str="20260102")
    save_daily_metrics({"b": 2}, date_str="20260102")
    path = os.path.join("market_data_20260102", "daily_metrics.json")
    with open(path) as f:
        data = json.load(f)
    assert data["a"] == 1
    assert data["b"] == 2

    shutil.rmtree("market_data_20260102", ignore_errors=True)
