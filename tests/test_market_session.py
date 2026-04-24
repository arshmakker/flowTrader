"""LIVE-18 — market-session / anomaly handling.

Pins the ``is_tradable_now`` authority that RegimeFilter.get_regime_gate
consults before permitting new entries. Every refusal path needs a
recognisable ``reason`` tag so IC_REJECT records stay searchable in logs.
"""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from datetime import datetime

try:
    from zoneinfo import ZoneInfo
    IST = ZoneInfo("Asia/Kolkata")
except ImportError:  # pragma: no cover — Python<3.9 fallback, not used in CI
    IST = None

import pytest

from strategy_runner import is_tradable_now
from trading_system.config import settings
from trading_system.core.regime_filter import RegimeFilter


def _ist(y, m, d, hh=0, mm=0):
    naive = datetime(y, m, d, hh, mm)
    return naive.replace(tzinfo=IST) if IST else naive


# ── Happy path ─────────────────────────────────────────────────────────

def test_regular_session_is_tradable():
    """Wednesday 2026-04-22 at 11:00 — mid-session on a known trading day
    (same weekday we saw 12 entries in the live log)."""
    ok, reason = is_tradable_now(now=_ist(2026, 4, 22, 11, 0))
    assert ok is True
    assert reason == "regular"


def test_session_open_boundary_is_tradable():
    """09:15:00 exactly is tradable — pre-open ends at 09:15."""
    ok, reason = is_tradable_now(now=_ist(2026, 4, 22, 9, 15))
    assert ok is True
    assert reason == "regular"


# ── Intra-day refusals ─────────────────────────────────────────────────

def test_pre_open_refused():
    ok, reason = is_tradable_now(now=_ist(2026, 4, 22, 9, 5))
    assert ok is False
    assert reason == "pre_open"


def test_before_open_refused():
    ok, reason = is_tradable_now(now=_ist(2026, 4, 22, 8, 30))
    assert ok is False
    assert reason == "before_open"


def test_after_close_refused_at_exactly_1530():
    ok, reason = is_tradable_now(now=_ist(2026, 4, 22, 15, 30))
    assert ok is False
    assert reason == "after_close"


def test_after_close_refused_later():
    ok, reason = is_tradable_now(now=_ist(2026, 4, 22, 16, 0))
    assert ok is False
    assert reason == "after_close"


# ── Calendar refusals ──────────────────────────────────────────────────

def test_saturday_refused():
    """2026-04-25 is a Saturday — weekend regardless of time of day."""
    ok, reason = is_tradable_now(now=_ist(2026, 4, 25, 11, 0))
    assert ok is False
    assert reason == "weekend"


def test_sunday_refused():
    ok, reason = is_tradable_now(now=_ist(2026, 4, 26, 11, 0))
    assert ok is False
    assert reason == "weekend"


def test_configured_holiday_refused():
    """2026-04-03 (Good Friday) is in settings.TRADING_HOLIDAYS_IST."""
    assert "2026-04-03" in settings.TRADING_HOLIDAYS_IST
    ok, reason = is_tradable_now(now=_ist(2026, 4, 3, 11, 0))
    assert ok is False
    assert reason == "holiday"


# ── Muhurat session ────────────────────────────────────────────────────

def test_muhurat_inside_window_is_tradable(monkeypatch):
    """Muhurat session is the only tradable slice on its date, even if that
    date happens to also be flagged as a holiday elsewhere."""
    monkeypatch.setattr(
        settings, "MUHURAT_SESSIONS",
        [{"date": "2026-11-08", "open": "18:15", "close": "19:15"}],
        raising=False,
    )
    ok, reason = is_tradable_now(now=_ist(2026, 11, 8, 18, 45))
    assert ok is True
    assert reason == "muhurat"


def test_muhurat_outside_window_refused(monkeypatch):
    monkeypatch.setattr(
        settings, "MUHURAT_SESSIONS",
        [{"date": "2026-11-08", "open": "18:15", "close": "19:15"}],
        raising=False,
    )
    # Same date, mid-afternoon — muhurat hasn't opened yet.
    ok, reason = is_tradable_now(now=_ist(2026, 11, 8, 14, 0))
    assert ok is False
    assert reason == "muhurat_closed"


def test_muhurat_end_boundary_refused(monkeypatch):
    """Half-open window: close time itself is NOT tradable."""
    monkeypatch.setattr(
        settings, "MUHURAT_SESSIONS",
        [{"date": "2026-11-08", "open": "18:15", "close": "19:15"}],
        raising=False,
    )
    ok, reason = is_tradable_now(now=_ist(2026, 11, 8, 19, 15))
    assert ok is False
    assert reason == "muhurat_closed"


def test_muhurat_overrides_weekend(monkeypatch):
    """Muhurat sessions commonly fall on a Sunday (Diwali). The muhurat
    window must still be tradable even though the date is a weekend."""
    # 2026-11-08 is a Sunday.
    assert datetime(2026, 11, 8).weekday() == 6
    monkeypatch.setattr(
        settings, "MUHURAT_SESSIONS",
        [{"date": "2026-11-08", "open": "18:15", "close": "19:15"}],
        raising=False,
    )
    ok, reason = is_tradable_now(now=_ist(2026, 11, 8, 18, 45))
    assert ok is True
    assert reason == "muhurat"


def test_malformed_muhurat_entry_is_skipped(monkeypatch):
    """Operator typo in MUHURAT_SESSIONS must not crash the loop — it should
    fall through to the ordinary weekend/holiday/session checks."""
    monkeypatch.setattr(
        settings, "MUHURAT_SESSIONS",
        [{"date": "2026-04-22", "open": "not-a-time", "close": "11:30"}],
        raising=False,
    )
    # 2026-04-22 is a regular Wednesday — falls through to the session check.
    ok, reason = is_tradable_now(now=_ist(2026, 4, 22, 11, 0))
    assert ok is True
    assert reason == "regular"


# ── Broker halt hook ───────────────────────────────────────────────────

def test_broker_halt_flag_refuses_even_mid_session():
    """Circuit halt during regular hours: plumbing hook for a future live
    broker feed. A True halt flag dominates every other state."""
    ok, reason = is_tradable_now(
        now=_ist(2026, 4, 22, 11, 0),
        broker_halt_flag=True,
    )
    assert ok is False
    assert reason == "broker_halt"


def test_broker_halt_none_is_ignored():
    """None = unknown = pass through to clock/calendar checks. Only a
    positive True flag refuses."""
    ok, reason = is_tradable_now(
        now=_ist(2026, 4, 22, 11, 0),
        broker_halt_flag=None,
    )
    assert ok is True
    assert reason == "regular"


# ── RegimeFilter integration — tradability is the FIRST gate ───────────

class _StubApi:
    """Minimal api stub that get_regime_gate only touches via get_vix, which
    we bypass by seeding the filter's VIX cache + history directly."""
    def get_quotes(self, *_args, **_kwargs):
        return {"lp": 0.0}


def _ready_filter(vix_value=18.0):
    """Build a RegimeFilter with enough VIX history to pass is_vix_stable.
    is_vix_stable filters to entries within the last IC_VIX_STABLE_MINS
    minutes; the history must sit INSIDE that window, not before it."""
    import time
    rf = RegimeFilter(api=_StubApi())
    now = time.monotonic()
    rf._vix_cache = (vix_value, now)
    # Spread 20 entries evenly across the last N minutes so every one of
    # them survives the stability filter.
    needed_sec = settings.IC_VIX_STABLE_MINS * 60
    step = needed_sec / 25  # leave a little headroom
    rf._vix_history = [(now - (step * i), vix_value) for i in range(20)]
    return rf


def test_regime_gate_refuses_when_not_tradable(monkeypatch):
    """Even with a perfectly RANGING day + stable low VIX, a non-tradable
    clock state (pre-open) must refuse entry."""
    rf = _ready_filter(vix_value=18.0)

    def _pre_open(*args, **kwargs):
        return (False, "pre_open")

    # get_regime_gate does `from strategy_runner import is_tradable_now`
    # lazily; patch the source module so the late import picks up the stub.
    monkeypatch.setattr("strategy_runner.is_tradable_now", _pre_open)
    assert rf.get_regime_gate("RANGING") is False


def test_regime_gate_passes_when_tradable(monkeypatch):
    """Sanity check: the new tradability gate does not break the normal
    pass path when every other check is green."""
    rf = _ready_filter(vix_value=18.0)
    # Force is_tradable_now to succeed so the test is independent of
    # whatever real IST-time the suite happens to run at.
    monkeypatch.setattr(
        "strategy_runner.is_tradable_now",
        lambda *a, **kw: (True, "regular"),
    )
    assert rf.get_regime_gate("RANGING") is True


def test_regime_gate_forwards_broker_halt_flag(monkeypatch):
    """The broker_halt_flag kwarg must reach is_tradable_now so a future
    live caller can refuse entries on a broker circuit-halt signal without
    needing to rewrite the gate."""
    rf = _ready_filter(vix_value=18.0)
    captured = {}

    def _spy(*args, **kwargs):
        captured["broker_halt_flag"] = kwargs.get("broker_halt_flag")
        return (False, "broker_halt")

    monkeypatch.setattr("strategy_runner.is_tradable_now", _spy)
    assert rf.get_regime_gate("RANGING", broker_halt_flag=True) is False
    assert captured["broker_halt_flag"] is True
