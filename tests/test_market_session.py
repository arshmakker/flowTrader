"""LIVE-18 — market-session / anomaly handling.

Pins the ``is_tradable_now`` authority that RegimeFilter.get_regime_gate
consults before permitting new entries. Every refusal path needs a
recognisable ``reason`` tag so IC_REJECT records stay searchable in logs.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from datetime import datetime

try:
    from zoneinfo import ZoneInfo

    IST = ZoneInfo("Asia/Kolkata")
except ImportError:  # pragma: no cover — Python<3.9 fallback, not used in CI
    IST = None


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
        settings,
        "MUHURAT_SESSIONS",
        [{"date": "2026-11-08", "open": "18:15", "close": "19:15"}],
        raising=False,
    )
    ok, reason = is_tradable_now(now=_ist(2026, 11, 8, 18, 45))
    assert ok is True
    assert reason == "muhurat"


def test_muhurat_outside_window_refused(monkeypatch):
    monkeypatch.setattr(
        settings,
        "MUHURAT_SESSIONS",
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
        settings,
        "MUHURAT_SESSIONS",
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
        settings,
        "MUHURAT_SESSIONS",
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
        settings,
        "MUHURAT_SESSIONS",
        [{"date": "2026-04-22", "open": "not-a-time", "close": "11:30"}],
        raising=False,
    )
    # 2026-04-22 is a regular Wednesday — falls through to the session check.
    ok, reason = is_tradable_now(now=_ist(2026, 4, 22, 11, 0))
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
    minutes; the history must sit INSIDE that window, not before it.
    History uses wall-clock (time.time()) so it survives process restarts."""
    import time

    rf = RegimeFilter(api=_StubApi())
    now_wall = time.time()
    rf._vix_cache = (vix_value, time.monotonic())
    # Spread 20 entries evenly across the last N minutes so every one of
    # them survives the stability filter.
    needed_sec = settings.IC_VIX_STABLE_MINS * 60
    step = needed_sec / 25  # leave a little headroom
    rf._vix_history = [(now_wall - (step * i), vix_value) for i in range(20)]
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


# ── VIX history save/restore — crash-restart carries stability window ─────
# Regression for 2026-04-29: on restart the gate blocked with
# "VIX not stable for 45 mins" because _vix_history used time.monotonic()
# (process-relative) and was wiped on restart. Fix: wall-clock timestamps
# + save_state()/restore_state() wired into position_persistence.

import time as _time_mod


def test_vix_history_save_restore_carries_over():
    """save_state → restore_state roundtrip: entries within the 45-min window
    are preserved and is_vix_stable() immediately returns True on the
    restored instance."""
    rf_orig = _ready_filter(vix_value=18.0)
    assert rf_orig.is_vix_stable() is True, "pre-condition: original filter is stable"

    state = rf_orig.save_state()

    rf_new = RegimeFilter(api=_StubApi())
    assert rf_new.is_vix_stable() is False, "pre-condition: fresh filter has no history"

    rf_new.restore_state(state)
    assert rf_new.is_vix_stable() is True, (
        "restored filter must see 45-min window as stable — "
        "VIX history not carrying over causes gate to block for 45 min after every restart"
    )


def test_vix_history_restore_drops_stale_entries():
    """Entries older than IC_VIX_STABLE_MINS + 5 min are pruned on restore."""
    from trading_system.config import settings

    rf = RegimeFilter(api=_StubApi())
    now = _time_mod.time()
    window = settings.IC_VIX_STABLE_MINS * 60
    # Two entries inside window, one well outside.
    rf._vix_history = [
        (now - 60, 18.0),
        (now - 120, 18.0),
        (now - (window + 400), 18.0),  # too old — must be pruned
    ]
    state = rf.save_state()

    rf2 = RegimeFilter(api=_StubApi())
    rf2.restore_state(state)
    assert len(rf2._vix_history) == 2, f"stale entry must be pruned on restore; got {len(rf2._vix_history)} entries"


def test_vix_stable_8min_window_excludes_pre_classify_volatile_data(monkeypatch):
    """LIVE-28: 8-min stability window must exclude pre-classify volatile VIX.
    The 15-min window looked back into the opening-hour noise (VIX swings >1.5pt)
    and kept blocking entries until ~11:15. The 8-min window only checks
    post-classify calm data, unblocking morning entries by ~10:38."""
    import time

    rf = RegimeFilter(api=_StubApi())
    now = time.time()

    # Samples from 15–9 minutes ago: volatile (opening-hour VIX swings)
    volatile = [(now - 900 + i * 60, 12.0 + (i % 2) * 2.0) for i in range(6)]
    # VIX alternates 12.0 and 14.0 → range = 2.0 > IC_VIX_STABLE_BAND (1.5)

    # Samples from last 8 minutes: stable (post-classify calm)
    calm = [(now - 480 + i * 60, 13.0) for i in range(9)]

    rf._vix_history = volatile + calm
    rf._vix_cache = (13.0, time.monotonic())

    # 8-min window only sees the calm samples → stable
    monkeypatch.setattr(settings, "IC_VIX_STABLE_MINS", 8)
    assert rf.is_vix_stable() is True, "8-min window should exclude pre-classify volatile data and report stable"

    # 15-min window sweeps in the volatile samples → unstable (prior blocking behavior)
    monkeypatch.setattr(settings, "IC_VIX_STABLE_MINS", 15)
    assert rf.is_vix_stable() is False, (
        "15-min window included volatile pre-classify data — this was blocking morning entries"
    )


def test_vix_history_restore_reset_daily_clears_history():
    """restore_state(state, reset_daily=True) drops all history — next day restart
    must start fresh, not carry yesterday's VIX readings."""
    rf_orig = _ready_filter(vix_value=18.0)
    state = rf_orig.save_state()

    rf_new = RegimeFilter(api=_StubApi())
    rf_new.restore_state(state, reset_daily=True)
    assert rf_new._vix_history == [], "next-day restore must clear VIX history"
    assert rf_new.is_vix_stable() is False
