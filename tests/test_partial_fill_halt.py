"""LIVE-05 — partial-fill handling on entry.

A partial-filled leg during IC entry must be reversed by the actual fill
quantity (not the originally requested qty — over-reversing leaves
NET-WRONG-SIDE exposure on a short strike).

Halt policy differs by entry path:

  - Legacy ``enter`` (IC_ENTRY_MODE != "hedge_first", inactive in production):
    a single partial-fill escalates to halt. Pinned by the legacy tests.

  - Hedge-first ``enter_hedge_first`` (default, active in production):
    a single Phase-5b partial-fill skips the cycle and increments
    ``_consecutive_partial_fails``; only ``settings.IC_PARTIAL_FAIL_CAP``
    consecutive partial-fills escalate to halt. Bid drift between
    QuoteBook fetch and order submission is normal microstructure noise,
    not stressed liquidity — incidents 2026-04-27 and 2026-04-28 both
    showed the same mechanism, with halt-on-first costing ~₹1,200/event
    plus all forward expected value.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from datetime import datetime, timedelta
from itertools import cycle
from unittest.mock import MagicMock, patch

from trading_system.config import settings
from trading_system.core.iron_condor import IronCondorStrategy

# ── Test helpers ───────────────────────────────────────────────────────


def _make_order(status, fill_qty, requested_qty, side, symbol="X", fill_price=50.0):
    return {
        "order_id": "OID",
        "symbol": symbol,
        "side": side,
        "quantity": requested_qty,
        "fill_qty": fill_qty,
        "fill_price": fill_price,
        "status": status,
        "stt": 0.0,
        "brokerage": 0.0,
        "timestamp": "2026-04-24T11:00:00",
        "paper": False,
    }


def _complete(qty=650, side="SELL", symbol="X", fill_price=50.0):
    return _make_order("COMPLETE", qty, qty, side, symbol, fill_price)


def _canceled_partial(fill_qty, qty=650, side="SELL", symbol="X", fill_price=50.0):
    return _make_order("CANCELED", fill_qty, qty, side, symbol, fill_price)


def _build_ic(om):
    import re

    md = MagicMock()
    md.get_ltp.side_effect = cycle([50.0, 45.0, 20.0, 18.0])
    md.get_lot_size.return_value = 65

    def _book_for(sym: str):
        m = re.search(r"([CP])(\d+)$", sym)
        b = MagicMock()
        b.is_tradable = True
        if not m:
            b.bid, b.ask = 10.0, 11.0
            return b
        letter, strike = m.group(1), int(m.group(2))
        is_short = (letter == "C" and strike < 24200) or (letter == "P" and strike > 23800)
        if is_short:
            b.bid, b.ask = 23.0, 23.25
        else:
            b.bid, b.ask = 2.75, 3.0
        return b

    md.get_quote_book.side_effect = _book_for

    ic = IronCondorStrategy(order_manager=om, market_data=md, instrument="NIFTY")
    return ic, md


def _sr_stub():
    m = MagicMock()
    m.apply_buffer.side_effect = lambda strike, h, l, t, step=50: strike
    return m


# ── Legacy enter() — partial fill must escalate halt even on clean reversal ──


def test_legacy_partial_fill_escalates_halt_on_clean_reversal(tmp_path, monkeypatch):
    """Previously: partial fill on leg 2, leg 1 + leg 2 reverse cleanly →
    _last_rollback_stuck_legs stays empty → next cycle retries. LIVE-05 fix:
    always emit a halt sentinel so main.py's drain escalates."""
    monkeypatch.setattr(settings, "IC_ENTRY_MODE", "sequential", raising=False)

    om = MagicMock()
    om.build_option_symbol.side_effect = lambda inst, exp, s, t: f"NFO|{inst}{exp}{t[0]}{int(s)}"
    om.tracker = None
    om.get_available_margin.return_value = float("inf")
    om.place_order.side_effect = [
        _complete(qty=650, side="SELL"),  # leg 1 SC full
        _canceled_partial(fill_qty=300, qty=650, side="BUY"),  # leg 2 LC partial
        # Reversals succeed cleanly:
        _make_order("COMPLETE", 300, 300, side="SELL"),  # reverse leg 2 partial
        _complete(qty=650, side="BUY"),  # reverse leg 1 full
    ]

    ic, _ = _build_ic(om)
    stuck_path = tmp_path / "stuck.json"
    with patch("trading_system.live.live_order_manager._STUCK_LEGS_PATH", str(stuck_path)):
        result = ic.enter(24000, 12.0, 24500, 23500, _sr_stub(), "17-APR-2026", 10)

    assert result is False
    # Critical: halt sentinel present even though reversals succeeded.
    assert len(ic._last_rollback_stuck_legs) >= 1
    event = ic._last_rollback_stuck_legs[0]
    assert event["reason"] == "partial_fill_during_entry"
    assert event["reversal_incomplete_count"] == 0
    # Sentinel captures what partial-filled:
    fills = {p["symbol"]: p["fill_qty"] for p in event["partial_legs"]}
    assert any(v == 300 for v in fills.values()), "sentinel must name the partial-filled leg"


def test_legacy_partial_fill_reversal_incomplete_still_escalates(tmp_path, monkeypatch):
    """If the reversal itself partial-fills, the stuck-leg record is added
    AFTER the event sentinel — operator needs both the trigger event and
    the unfinished-reversal detail."""
    monkeypatch.setattr(settings, "IC_ENTRY_MODE", "sequential", raising=False)

    om = MagicMock()
    om.build_option_symbol.side_effect = lambda inst, exp, s, t: f"NFO|{inst}{exp}{t[0]}{int(s)}"
    om.tracker = None
    om.get_available_margin.return_value = float("inf")
    om.place_order.side_effect = [
        _complete(qty=650, side="SELL"),
        _canceled_partial(fill_qty=300, qty=650, side="BUY"),
        # Partial reversal — only 200 of 300 reversed:
        _make_order("CANCELED", 200, 300, side="SELL"),
        _complete(qty=650, side="BUY"),
    ]

    ic, _ = _build_ic(om)
    stuck_path = tmp_path / "stuck.json"
    with patch("trading_system.live.live_order_manager._STUCK_LEGS_PATH", str(stuck_path)):
        ic.enter(24000, 12.0, 24500, 23500, _sr_stub(), "17-APR-2026", 10)

    # Sentinel + one unreversed leg
    assert len(ic._last_rollback_stuck_legs) == 2
    assert ic._last_rollback_stuck_legs[0]["reason"] == "partial_fill_during_entry"
    assert ic._last_rollback_stuck_legs[0]["reversal_incomplete_count"] == 1
    assert ic._last_rollback_stuck_legs[1]["reason"] == "partial_fill_reversal_incomplete"
    assert ic._last_rollback_stuck_legs[1]["reversed_qty"] == 200


# ── Hedge-first Phase 5b — partial short must unwind by fill_qty + halt ──


def test_hedgefirst_phase5b_partial_short_unwound_by_fill_qty(tmp_path, monkeypatch):
    """The bug LIVE-05 catches: partial-filled SC reversed with REQUESTED qty
    instead of FILL qty = net LONG exposure on the short strike. Fix must
    pass the actual fill_qty to the reversing BUY."""
    monkeypatch.setattr(settings, "IC_ENTRY_MODE", "hedge_first", raising=False)

    om = MagicMock()
    om.build_option_symbol.side_effect = lambda inst, exp, s, t: f"NFO|{inst}{exp}{t[0]}{int(s)}"
    om.tracker = None
    om.get_available_margin.return_value = float("inf")
    # Wings fill at 5 (book ask=20 but test controls fill price), shorts would
    # fill at 18 → credit 18+18-5-5 = 26 > IC_MIN_CREDIT=18.
    om.place_order.side_effect = [
        _complete(qty=650, side="BUY", fill_price=5.0),  # wing LC
        _complete(qty=650, side="BUY", fill_price=5.0),  # wing LP
        _canceled_partial(fill_qty=300, qty=650, side="SELL", fill_price=18.0),  # SC partial
        _make_order("CANCELED", 0, 650, side="SELL"),  # SP clean fail
        # Phase 5b unwinds:
        _complete(qty=300, side="BUY"),  # SC reverse — MUST be 300
        _complete(qty=650, side="SELL"),  # LC wing unwind
        _complete(qty=650, side="SELL"),  # LP wing unwind
    ]

    ic, _ = _build_ic(om)
    stuck_path = tmp_path / "stuck.json"
    with patch("trading_system.live.live_order_manager._STUCK_LEGS_PATH", str(stuck_path)):
        result = ic.enter(24000, 12.0, 24500, 23500, _sr_stub(), "17-APR-2026", 10)

    assert result is False
    # The 5th place_order call is the SC reverse. Its side is BUY (reversing
    # the short SELL) and qty MUST be 300 — the fill_qty, not the requested 650.
    # Over-reversing leaves net LONG exposure on the short strike — the exact
    # bug LIVE-05 prevents.
    sc_reverse_call = om.place_order.call_args_list[4]
    reverse_side = sc_reverse_call.args[1]
    reversed_qty = sc_reverse_call.args[2]
    assert reverse_side == "BUY"
    assert reversed_qty == 300, f"SC partial must be reversed by fill_qty=300, got {reversed_qty}"


def test_hedgefirst_phase5b_partial_short_skips_cycle(tmp_path, monkeypatch):
    """A single Phase-5b partial-fill skips the cycle and bumps the
    consecutive-fail counter; it does NOT halt. Bid drift between QuoteBook
    fetch and order submission is normal noise — only sustained streaks
    (cap consecutive) signal real liquidity stress and halt."""
    monkeypatch.setattr(settings, "IC_ENTRY_MODE", "hedge_first", raising=False)

    om = MagicMock()
    om.build_option_symbol.side_effect = lambda inst, exp, s, t: f"NFO|{inst}{exp}{t[0]}{int(s)}"
    om.tracker = None
    om.get_available_margin.return_value = float("inf")
    om.place_order.side_effect = [
        _complete(qty=650, side="BUY", fill_price=5.0),  # LC wing
        _complete(qty=650, side="BUY", fill_price=5.0),  # LP wing
        _canceled_partial(fill_qty=300, qty=650, side="SELL", fill_price=18.0),  # SC partial
        _complete(qty=650, side="SELL", fill_price=18.0),  # SP fully fills — but SC didn't, so 5b still triggers
        _complete(qty=300, side="BUY"),  # SC reverse partial
        _complete(qty=650, side="BUY"),  # SP reverse full
        _complete(qty=650, side="SELL"),  # LC wing unwind
        _complete(qty=650, side="SELL"),  # LP wing unwind
    ]

    ic, _ = _build_ic(om)
    stuck_path = tmp_path / "stuck.json"
    with patch("trading_system.live.live_order_manager._STUCK_LEGS_PATH", str(stuck_path)):
        ic.enter(24000, 12.0, 24500, 23500, _sr_stub(), "17-APR-2026", 10)

    assert (
        ic._last_rollback_stuck_legs == []
    ), f"single partial-fill must NOT halt — got stuck_legs={ic._last_rollback_stuck_legs}"
    assert ic._consecutive_partial_fails == 1


def test_hedgefirst_phase5b_partial_fill_cap_reached_suspends_not_halts(tmp_path, monkeypatch):
    """The Nth consecutive Phase-5b partial-fill suspends same-day re-entries
    but does NOT escalate to a session halt (2026-04-29 regression: cap used to
    populate stuck_legs → _drain_rollback_failures → full halt, killing NIFTY
    for the rest of the day). Only 3× stop / DAILY_MAX_LOSS halt the session."""
    monkeypatch.setattr(settings, "IC_ENTRY_MODE", "hedge_first", raising=False)

    om = MagicMock()
    om.build_option_symbol.side_effect = lambda inst, exp, s, t: f"NFO|{inst}{exp}{t[0]}{int(s)}"
    om.tracker = None
    om.get_available_margin.return_value = float("inf")
    om.place_order.side_effect = [
        _complete(qty=650, side="BUY", fill_price=5.0),
        _complete(qty=650, side="BUY", fill_price=5.0),
        _canceled_partial(fill_qty=300, qty=650, side="SELL", fill_price=18.0),
        _complete(qty=650, side="SELL", fill_price=18.0),
        _complete(qty=300, side="BUY"),
        _complete(qty=650, side="BUY"),
        _complete(qty=650, side="SELL"),
        _complete(qty=650, side="SELL"),
    ]

    ic, _ = _build_ic(om)
    # Pre-load the counter to one below the cap — this entry will tip it over.
    ic._consecutive_partial_fails = settings.IC_PARTIAL_FAIL_CAP - 1
    stuck_path = tmp_path / "stuck.json"
    with patch("trading_system.live.live_order_manager._STUCK_LEGS_PATH", str(stuck_path)):
        ic.enter(24000, 12.0, 24500, 23500, _sr_stub(), "17-APR-2026", 10)

    assert ic._phase5b_suspended is True, "cap reached → adjustments suspended"
    assert (
        ic._last_rollback_stuck_legs == []
    ), "cap reached must NOT populate stuck_legs — that triggers a session halt reserved for 3× stop / daily-loss only"


def test_hedgefirst_phase5b_no_partial_does_not_escalate_halt(tmp_path, monkeypatch):
    """Sanity: if shorts FULLY fail to fill with zero fill_qty on both,
    that's a clean non-fill (liquidity too thin to even partial-fill) —
    wings unwind cleanly, but no halt escalation is needed. The strategy
    would simply try again next cycle."""
    monkeypatch.setattr(settings, "IC_ENTRY_MODE", "hedge_first", raising=False)

    om = MagicMock()
    om.build_option_symbol.side_effect = lambda inst, exp, s, t: f"NFO|{inst}{exp}{t[0]}{int(s)}"
    om.tracker = None
    om.get_available_margin.return_value = float("inf")
    om.place_order.side_effect = [
        _complete(qty=650, side="BUY", symbol="LC"),
        _complete(qty=650, side="BUY", symbol="LP"),
        _make_order("CANCELED", 0, 650, side="SELL", symbol="SC"),  # clean non-fill
        _make_order("CANCELED", 0, 650, side="SELL", symbol="SP"),  # clean non-fill
        _complete(qty=650, side="SELL", symbol="LC"),
        _complete(qty=650, side="SELL", symbol="LP"),
    ]

    ic, _ = _build_ic(om)
    stuck_path = tmp_path / "stuck.json"
    with patch("trading_system.live.live_order_manager._STUCK_LEGS_PATH", str(stuck_path)):
        ic.enter(24000, 12.0, 24500, 23500, _sr_stub(), "17-APR-2026", 10)

    # Non-partial shorts timeout isn't a partial-fill event — no halt sentinel.
    assert ic._last_rollback_stuck_legs == []


def test_phase5b_cooloff_not_expired_blocks_reentry(monkeypatch):
    """While the cool-off window has not expired, a re-entry is still blocked.
    No orders must be placed — the guard returns False before touching the book."""
    monkeypatch.setattr(settings, "IC_ENTRY_MODE", "hedge_first", raising=False)
    monkeypatch.setattr(settings, "IC_PHASE5B_COOLOFF_MINS", 30, raising=False)

    om = MagicMock()
    om.build_option_symbol.side_effect = lambda inst, exp, s, t: f"NFO|{inst}{exp}{t[0]}{int(s)}"
    om.tracker = None
    om.get_available_margin.return_value = float("inf")

    ic, _ = _build_ic(om)
    ic._phase5b_suspended = True
    ic._phase5b_suspended_at = datetime.now() - timedelta(minutes=5)
    ic._last_exit_reason = "PROFIT_HARVEST"
    ic._last_exit_date = datetime.now().date().isoformat()

    result = ic.enter(24000, 12.0, 24500, 23500, _sr_stub(), "17-APR-2026", 10)

    assert result is False
    om.place_order.assert_not_called()
    assert ic._phase5b_suspended is True


def test_phase5b_cooloff_expired_resets_and_probes(monkeypatch):
    """Once the cool-off expires, the guard clears the suspension and counter
    so the re-entry attempt proceeds. A clean 4-leg fill succeeds and confirms
    the reset (both flag and counter back to initial state)."""
    monkeypatch.setattr(settings, "IC_ENTRY_MODE", "hedge_first", raising=False)
    monkeypatch.setattr(settings, "IC_PHASE5B_COOLOFF_MINS", 30, raising=False)

    om = MagicMock()
    om.build_option_symbol.side_effect = lambda inst, exp, s, t: f"NFO|{inst}{exp}{t[0]}{int(s)}"
    om.tracker = None
    om.get_available_margin.return_value = float("inf")
    om.place_order.side_effect = [
        _complete(qty=650, side="BUY", fill_price=3.0),  # LC wing
        _complete(qty=650, side="BUY", fill_price=3.0),  # LP wing
        _complete(qty=650, side="SELL", fill_price=23.0),  # SC short
        _complete(qty=650, side="SELL", fill_price=23.0),  # SP short
    ]

    ic, _ = _build_ic(om)
    ic._phase5b_suspended = True
    ic._phase5b_suspended_at = datetime.now() - timedelta(minutes=31)
    ic._consecutive_partial_fails = settings.IC_PARTIAL_FAIL_CAP
    ic._last_exit_reason = "PROFIT_HARVEST"
    ic._last_exit_date = datetime.now().date().isoformat()

    result = ic.enter(24000, 12.0, 24500, 23500, _sr_stub(), "17-APR-2026", 10)

    assert result is True, "probe after cool-off expiry must succeed on a clean fill"
    assert ic._phase5b_suspended is False
    assert ic._consecutive_partial_fails == 0
