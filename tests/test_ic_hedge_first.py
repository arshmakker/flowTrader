"""LIVE-25: hedge-first IC entry sequencing — state machine tests.

Each test drives a specific branch of the 5-phase state machine:
    Phase 1: wings submitted as MKT
    Phase 2: both wings must fill; if one fails, close the other, halt
    Phase 3: compute short limits from wing fills; if credit infeasible, refuse
    Phase 4: shorts as LMT
    Phase 5a: both shorts fill → post-fill credit re-check
    Phase 5b: any short doesn't fill → unwind all

Mock conventions:
    - place_order returns whatever the test sets via side_effect
    - get_quote_book returns a QuoteBook fixture so Phase 3's math is deterministic
"""

import os
import sys
from unittest.mock import MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from trading_system.config import settings
from trading_system.core.iron_condor import IronCondorStrategy
from trading_system.existing.market_data import QuoteBook


def _book(bid, ask, bid_qty=10_000, ask_qty=10_000, symbol=""):
    return QuoteBook(symbol=symbol, bid=bid, ask=ask, bid_qty=bid_qty, ask_qty=ask_qty)


@pytest.fixture
def mock_om():
    om = MagicMock()
    om.build_option_symbol.side_effect = lambda inst, exp, strike, type: f"NFO|{inst}{exp}{type[0]}{int(strike)}"
    om.tracker = None
    om.get_available_margin.return_value = float("inf")
    return om


@pytest.fixture
def mock_md():
    md = MagicMock()
    md.get_lot_size.return_value = settings.NIFTY_LOT_SIZE
    return md


def _configure_books_for_clean_ic(mock_md, sc_bid=18.0, sp_bid=18.0, lc_ask=5.0, lp_ask=5.0):
    """All four legs have tradable books that yield a healthy (sc+sp)-(lc+lp) credit."""

    def _gqb(sym):
        if "C22150" in sym:
            return _book(bid=sc_bid, ask=sc_bid + 0.25, symbol=sym)
        if "P21850" in sym:
            return _book(bid=sp_bid, ask=sp_bid + 0.25, symbol=sym)
        if "C22200" in sym:
            return _book(bid=lc_ask - 0.25, ask=lc_ask, symbol=sym)
        if "P21800" in sym:
            return _book(bid=lp_ask - 0.25, ask=lp_ask, symbol=sym)
        return None

    mock_md.get_quote_book.side_effect = _gqb


def _fill(price, qty):
    return {"status": "COMPLETE", "fill_qty": qty, "fill_price": price, "order_id": "X"}


def _canceled(reason="limit_not_reached"):
    return {"status": "CANCELED", "fill_qty": 0, "fill_price": 0.0, "reason": reason, "order_id": "X"}


def _rejected(reason="REJECT"):
    return {"status": "REJECTED", "fill_qty": 0, "fill_price": 0.0, "reason": reason, "order_id": "X"}


def _sr_mgr():
    m = MagicMock()
    m.apply_buffer.side_effect = lambda strike, h, l, type, step=50: strike
    return m


# ── Happy path ────────────────────────────────────────────────────────


def test_happy_path_all_four_legs_fill(mock_om, mock_md):
    """Clean books, wings fill at MKT, shorts fill at LMT → entry complete."""
    _configure_books_for_clean_ic(mock_md, sc_bid=18.0, sp_bid=18.0, lc_ask=5.0, lp_ask=5.0)
    qty = settings.IC_LOT_SIZE * settings.NIFTY_LOT_SIZE  # 650

    def place_side_effect(symbol, side, q, price_type="MKT", price=0.0):
        if "C22200" in symbol:
            return _fill(5.0, q)  # LC MKT fill at ask
        if "P21800" in symbol:
            return _fill(5.0, q)  # LP MKT fill at ask
        if "C22150" in symbol:
            return _fill(18.0, q)  # SC LMT fill at bid
        if "P21850" in symbol:
            return _fill(18.0, q)  # SP LMT fill at bid
        raise AssertionError(f"unexpected order: {symbol}")

    mock_om.place_order.side_effect = place_side_effect

    s = IronCondorStrategy(mock_om, mock_md, "NIFTY")
    ok = s.enter_hedge_first(22000, 12, 22500, 21500, _sr_mgr(), "19-MAR-2026", settings.IC_LOT_SIZE)

    assert ok is True
    assert s.is_active() is True
    # Exactly 4 orders placed: LC, LP, SC, SP — no unwinds
    assert mock_om.place_order.call_count == 4
    # Net credit: (18+18) - (5+5) = 26 per unit
    assert s._position.entry_credit == 26.0
    assert s._position.max_profit == 26.0 * qty


def test_happy_path_wings_placed_before_shorts(mock_om, mock_md):
    """Ordering invariant: LC and LP go out before SC and SP — the whole point
    of hedge-first. Without this, the risk profile is identical to sequential."""
    _configure_books_for_clean_ic(mock_md)
    calls_order = []

    def place_side_effect(symbol, side, q, price_type="MKT", price=0.0):
        calls_order.append(symbol)
        return _fill(18.0 if side == "SELL" else 5.0, q)

    mock_om.place_order.side_effect = place_side_effect

    s = IronCondorStrategy(mock_om, mock_md, "NIFTY")
    s.enter_hedge_first(22000, 12, 22500, 21500, _sr_mgr(), "19-MAR-2026", settings.IC_LOT_SIZE)

    # First two calls must be the long wings (C22200, P21800).
    assert "C22200" in calls_order[0]  # LC
    assert "P21800" in calls_order[1]  # LP
    # Next two must be the shorts.
    assert "C22150" in calls_order[2]  # SC
    assert "P21850" in calls_order[3]  # SP


# ── Phase 2: one wing fails ───────────────────────────────────────────


def test_phase2_one_wing_fails_closes_other_wing_at_market(mock_om, mock_md):
    """LP wing fails → close LC at market and halt. No shorts submitted.
    Bounded loss = LC premium paid."""
    _configure_books_for_clean_ic(mock_md)

    def place_side_effect(symbol, side, q, price_type="MKT", price=0.0):
        if "C22200" in symbol and side == "BUY":
            return _fill(5.0, q)  # LC fills
        if "P21800" in symbol and side == "BUY":
            return _rejected()  # LP rejected
        if "C22200" in symbol and side == "SELL":
            return _fill(5.0, q)  # LC unwind close
        raise AssertionError(f"unexpected order: {side} {symbol}")

    mock_om.place_order.side_effect = place_side_effect

    s = IronCondorStrategy(mock_om, mock_md, "NIFTY")
    ok = s.enter_hedge_first(22000, 12, 22500, 21500, _sr_mgr(), "19-MAR-2026", settings.IC_LOT_SIZE)

    assert ok is False
    assert s.is_active() is False
    # LC (buy) + LP (rejected) + LC close (sell) = 3 calls. No shorts.
    assert mock_om.place_order.call_count == 3
    # No short submission at all — this is the critical invariant.
    for call in mock_om.place_order.call_args_list:
        sym = call.args[0]
        assert "C22150" not in sym and "P21850" not in sym


def test_phase2_both_wings_fail_nothing_to_unwind(mock_om, mock_md):
    """If both wings reject there is no exposure to unwind. Halt cleanly."""
    _configure_books_for_clean_ic(mock_md)
    mock_om.place_order.return_value = _rejected()

    s = IronCondorStrategy(mock_om, mock_md, "NIFTY")
    ok = s.enter_hedge_first(22000, 12, 22500, 21500, _sr_mgr(), "19-MAR-2026", settings.IC_LOT_SIZE)

    assert ok is False
    # Both wing submissions but zero unwinds.
    assert mock_om.place_order.call_count == 2


# ── Phase 3: credit infeasible after wings ────────────────────────────


def test_phase3_credit_infeasible_refuses_and_unwinds_wings(mock_om, mock_md):
    """Pre-entry credit check passes (quote book healthy) but wings fill at a
    price materially worse than quoted ask — simulates price drift between
    quote fetch and MKT fill in live. Phase 3 recomputes projected credit from
    actual wing fills + current bid; projection < IC_MIN_CREDIT → refuse and
    unwind wings. Shorts never submitted.

    In deterministic paper this branch is unreachable (paper MKT fills exactly
    at ask+slip, so Phase 3's math matches pre-entry's). The test deliberately
    simulates live divergence by having the mock om.place_order return a
    fill_price worse than the quote book implies."""
    _configure_books_for_clean_ic(mock_md, sc_bid=18.0, sp_bid=18.0, lc_ask=5.0, lp_ask=5.0)
    # Pre-entry mid credit ≈ (18+18) - (5+5) = 26 → passes IC_MIN_CREDIT=18.

    def place_side_effect(symbol, side, q, price_type="MKT", price=0.0):
        # Wings fill at 15 instead of 5 — simulated live slippage.
        if side == "BUY" and price_type == "MKT":
            return _fill(15.0, q)
        # Wing unwind sells.
        if side == "SELL" and price_type == "MKT":
            return _fill(14.5, q)
        raise AssertionError(f"unexpected: {side} {price_type} {symbol}")

    mock_om.place_order.side_effect = place_side_effect

    s = IronCondorStrategy(mock_om, mock_md, "NIFTY")
    ok = s.enter_hedge_first(22000, 12, 22500, 21500, _sr_mgr(), "19-MAR-2026", settings.IC_LOT_SIZE)

    # Phase 3 projection: sc_bid(18) + sp_bid(18) - lc_fill(15) - lp_fill(15) = 6 < 18.
    assert ok is False
    # Exactly 4 orders: 2 wing buys + 2 wing unwinds. No shorts.
    assert mock_om.place_order.call_count == 4
    for call in mock_om.place_order.call_args_list:
        sym = call.args[0]
        assert "C22150" not in sym and "P21850" not in sym, "no short leg must be submitted"


# ── Phase 2 partial wing fill → halt-and-alert (BUG 2 regression) ─────


def test_phase2_partial_wing_fill_halts_without_submitting_shorts(mock_om, mock_md):
    """LIVE-05 semantics: a wing that returns with fill_qty < qty means we
    have partial exposure. Close whatever filled on BOTH wings (the full leg
    too) and halt. No shorts submitted. Regression for a bug where the
    predicate treated partial as 'not filled' and walked past the partial
    wing without closing it."""
    _configure_books_for_clean_ic(mock_md)
    settings.IC_LOT_SIZE * settings.NIFTY_LOT_SIZE  # 650

    def place_side_effect(symbol, side, q, price_type="MKT", price=0.0):
        # LC partial: COMPLETE but only 300 of 650 filled (live broker reports this).
        if "C22200" in symbol and side == "BUY":
            return {"status": "COMPLETE", "fill_qty": 300, "fill_price": 5.0, "order_id": "X"}
        # LP full fill.
        if "P21800" in symbol and side == "BUY":
            return _fill(5.0, q)
        # Wing unwinds — accept whatever qty we ask for.
        if side == "SELL" and price_type == "MKT":
            return _fill(5.0, q)
        raise AssertionError(f"unexpected: {side} {price_type} {symbol}")

    mock_om.place_order.side_effect = place_side_effect

    s = IronCondorStrategy(mock_om, mock_md, "NIFTY")
    ok = s.enter_hedge_first(22000, 12, 22500, 21500, _sr_mgr(), "19-MAR-2026", settings.IC_LOT_SIZE)

    assert ok is False
    # LC buy (partial 300) + LP buy (full) + LC sell (300) + LP sell (650) = 4 orders.
    assert mock_om.place_order.call_count == 4
    # No shorts submitted — this is the invariant LIVE-05 pins.
    for call in mock_om.place_order.call_args_list:
        sym = call.args[0]
        assert "C22150" not in sym and "P21850" not in sym
    # Partial LC was unwound at its actual fill_qty (300), not the full 650.
    lc_sell_calls = [c for c in mock_om.place_order.call_args_list if "C22200" in c.args[0] and c.args[1] == "SELL"]
    assert len(lc_sell_calls) == 1
    assert lc_sell_calls[0].args[2] == 300, "partial wing must be closed at actual fill_qty"


# ── Phase 5b: short timeout unwinds everything ────────────────────────


def test_phase5b_both_shorts_cancel_unwinds_all(mock_om, mock_md):
    """Both shorts CANCELED (limit not reached / timed out) → close both wings
    at market. No filled shorts to close. Bounded loss = wings' premium."""
    _configure_books_for_clean_ic(mock_md)

    def place_side_effect(symbol, side, q, price_type="MKT", price=0.0):
        if side == "BUY" and price_type == "MKT":
            return _fill(5.0, q)  # Wings fill
        if side == "SELL" and price_type == "LMT":
            return _canceled()  # Shorts cancel
        if side == "SELL" and price_type == "MKT":
            return _fill(5.0, q)  # Wing unwinds
        raise AssertionError(f"unexpected: {side} {price_type} {symbol}")

    mock_om.place_order.side_effect = place_side_effect

    s = IronCondorStrategy(mock_om, mock_md, "NIFTY")
    ok = s.enter_hedge_first(22000, 12, 22500, 21500, _sr_mgr(), "19-MAR-2026", settings.IC_LOT_SIZE)

    assert ok is False
    # 2 wing buys + 2 short LMT (canceled) + 2 wing sells (unwind) = 6
    assert mock_om.place_order.call_count == 6


def test_phase5b_one_short_fills_one_cancels_unwinds_everything(mock_om, mock_md):
    """One short filled, one didn't → close the filled short at market, then
    close both wings. All 4 legs unwound; 0 legs carried."""
    _configure_books_for_clean_ic(mock_md)

    short_calls = {"C22150": 0, "P21850": 0}

    def place_side_effect(symbol, side, q, price_type="MKT", price=0.0):
        if "C22200" in symbol or "P21800" in symbol:
            if side == "BUY":
                return _fill(5.0, q)  # wing entry
            if side == "SELL":
                return _fill(5.0, q)  # wing unwind
        if "C22150" in symbol:
            short_calls["C22150"] += 1
            if short_calls["C22150"] == 1:
                return _fill(18.0, q)  # first call: LMT fills
            return _fill(18.0, q)  # second call: MKT buyback
        if "P21850" in symbol:
            short_calls["P21850"] += 1
            if short_calls["P21850"] == 1:
                return _canceled()  # first call: LMT canceled
            raise AssertionError("P21850 should not be called again")
        raise AssertionError(f"unexpected: {side} {price_type} {symbol}")

    mock_om.place_order.side_effect = place_side_effect

    s = IronCondorStrategy(mock_om, mock_md, "NIFTY")
    ok = s.enter_hedge_first(22000, 12, 22500, 21500, _sr_mgr(), "19-MAR-2026", settings.IC_LOT_SIZE)

    assert ok is False
    # LC+LP entry + SC+SP (one fills, one cancels) + SC buyback + LC+LP unwind = 7
    assert mock_om.place_order.call_count == 7
    # SC got 2 calls total (LMT fill, then MKT buyback); SP got 1 (LMT cancel only).
    assert short_calls["C22150"] == 2
    assert short_calls["P21850"] == 1


# ── Untradable book on any leg → pre-entry refusal ─────────────────────


def test_untradable_book_on_any_leg_refuses_before_submitting(mock_om, mock_md):
    """get_quote_book returns None for one leg → no orders placed."""

    def _gqb(sym):
        if "P21800" in sym:
            return None  # LP quote unavailable
        return _book(18.0, 18.25, symbol=sym)

    mock_md.get_quote_book.side_effect = _gqb

    s = IronCondorStrategy(mock_om, mock_md, "NIFTY")
    ok = s.enter_hedge_first(22000, 12, 22500, 21500, _sr_mgr(), "19-MAR-2026", settings.IC_LOT_SIZE)

    assert ok is False
    assert mock_om.place_order.call_count == 0


def test_zero_bid_on_short_leg_refuses_pre_entry(mock_om, mock_md):
    """LIVE-06 motivating case: SC_bid = 0 (deep OTM with no buyers) means the
    short can't be sold at any reasonable price. Refuse before submitting."""

    def _gqb(sym):
        if "C22150" in sym:
            return _book(bid=0.0, ask=1.0, symbol=sym)  # untradable short
        if "P21850" in sym:
            return _book(bid=18.0, ask=18.25, symbol=sym)
        if "C22200" in sym:
            return _book(bid=4.75, ask=5.0, symbol=sym)
        if "P21800" in sym:
            return _book(bid=4.75, ask=5.0, symbol=sym)
        return None

    mock_md.get_quote_book.side_effect = _gqb

    s = IronCondorStrategy(mock_om, mock_md, "NIFTY")
    ok = s.enter_hedge_first(22000, 12, 22500, 21500, _sr_mgr(), "19-MAR-2026", settings.IC_LOT_SIZE)

    assert ok is False
    assert mock_om.place_order.call_count == 0


# ── LIVE-13 preserved under hedge-first ────────────────────────────────


def test_freeze_qty_breach_refuses_under_hedge_first_too(mock_om, mock_md):
    """The LIVE-13 guard must fire in the hedge-first path as well."""
    _configure_books_for_clean_ic(mock_md)
    breaching_lots = 30  # 30 * 65 = 1950 > FREEZE_QTY_NIFTY=1800

    s = IronCondorStrategy(mock_om, mock_md, "NIFTY")
    ok = s.enter_hedge_first(22000, 12, 22500, 21500, _sr_mgr(), "19-MAR-2026", breaching_lots)

    assert ok is False
    assert mock_om.place_order.call_count == 0


# ── Phase 5a: post-fill credit under floor ─────────────────────────────


def test_ic_entry_mode_sequential_does_not_route_to_hedge_first(mock_om, mock_md, monkeypatch):
    """With IC_ENTRY_MODE='sequential' (default), enter() must NOT call
    enter_hedge_first — regression guarding against an accidental default flip."""
    monkeypatch.setattr(settings, "IC_ENTRY_MODE", "sequential")
    s = IronCondorStrategy(mock_om, mock_md, "NIFTY")

    called = {"hedge_first": False}
    orig = s.enter_hedge_first

    def _spy(*a, **k):
        called["hedge_first"] = True
        return orig(*a, **k)

    monkeypatch.setattr(s, "enter_hedge_first", _spy)

    # Legacy path uses get_ltp, not get_quote_book — wire that up.
    _configure_books_for_clean_ic(mock_md)  # harmless in legacy path
    mock_md.get_ltp.side_effect = lambda sym: 5.0 if "2220" in sym or "2180" in sym else 18.0
    mock_om.place_order.return_value = _fill(18.0, 650)

    s.enter(22000, 12, 22500, 21500, _sr_mgr(), "19-MAR-2026", settings.IC_LOT_SIZE)

    assert called["hedge_first"] is False


def test_ic_entry_mode_hedge_first_routes_through_enter_hedge_first(mock_om, mock_md, monkeypatch):
    """Flipping IC_ENTRY_MODE='hedge_first' dispatches enter() to the new path."""
    monkeypatch.setattr(settings, "IC_ENTRY_MODE", "hedge_first")
    _configure_books_for_clean_ic(mock_md)

    def place_side_effect(symbol, side, q, price_type="MKT", price=0.0):
        if "C22200" in symbol or "P21800" in symbol:
            return _fill(5.0, q)
        return _fill(18.0, q)

    mock_om.place_order.side_effect = place_side_effect

    s = IronCondorStrategy(mock_om, mock_md, "NIFTY")
    called = {"hedge_first": False}
    orig = s.enter_hedge_first

    def _spy(*a, **k):
        called["hedge_first"] = True
        return orig(*a, **k)

    monkeypatch.setattr(s, "enter_hedge_first", _spy)

    ok = s.enter(22000, 12, 22500, 21500, _sr_mgr(), "19-MAR-2026", settings.IC_LOT_SIZE)

    assert called["hedge_first"] is True
    assert ok is True


def test_phase5a_post_fill_credit_under_floor_unwinds_all_four(mock_om, mock_md):
    """Live-divergence scenario: pre-entry book healthy, Phase 3 projection
    healthy (uses sc_bid at quote time), BUT the short LMTs fill materially
    below the bid that was quoted (market moved down between Phase 3 and
    Phase 4). Phase 5a catches it and unwinds all 4 legs rather than
    carrying a structurally-worse IC.

    In deterministic paper this branch is also unreachable (paper SELL LMT
    fills at max(limit, bid) = bid exactly, so sc_fill == sc_limit). The
    mock simulates a live short fill worse than the bid quoted at Phase 3."""
    _configure_books_for_clean_ic(mock_md, sc_bid=18.0, sp_bid=18.0, lc_ask=5.0, lp_ask=5.0)
    # Pre-entry mid credit (26), Phase 3 projection (sc_bid+sp_bid - lc_fill - lp_fill =
    # 18+18 - 8-8 = 20 ≥ 18) both pass. But short actual fill is 8 each → credit = 0.

    def place_side_effect(symbol, side, q, price_type="MKT", price=0.0):
        # Wings fill at 8 — above ask-quoted (5) but not bad enough for Phase 3 to refuse.
        if "C22200" in symbol and side == "BUY" and price_type == "MKT":
            return _fill(8.0, q)
        if "P21800" in symbol and side == "BUY" and price_type == "MKT":
            return _fill(8.0, q)
        # Shorts fill — but at 8 each, not the 18 Phase 3 projected. Market moved down.
        if "C22150" in symbol and side == "SELL" and price_type == "LMT":
            return _fill(8.0, q)
        if "P21850" in symbol and side == "SELL" and price_type == "LMT":
            return _fill(8.0, q)
        # Unwind: shorts bought back at MKT, wings sold at MKT.
        if side == "BUY" and price_type == "MKT":
            return _fill(8.0, q)
        if side == "SELL" and price_type == "MKT":
            return _fill(8.0, q)
        raise AssertionError(f"unexpected: {side} {price_type} {symbol}")

    mock_om.place_order.side_effect = place_side_effect

    s = IronCondorStrategy(mock_om, mock_md, "NIFTY")
    ok = s.enter_hedge_first(22000, 12, 22500, 21500, _sr_mgr(), "19-MAR-2026", settings.IC_LOT_SIZE)

    # Actual credit = 8+8 - 8-8 = 0 < IC_MIN_CREDIT=18 → Phase 5a unwinds.
    assert ok is False
    assert s.is_active() is False
    # LC buy + LP buy + SC LMT + SP LMT + SC buyback + SP buyback + LC sell + LP sell = 8.
    assert mock_om.place_order.call_count == 8


# ── QuoteBook prices forwarded as `price=` fallback (incident 2026-04-27 11:20) ──
# When a fresh strike's broker quote is bogus (e.g. C57700 LTP=56130 with no
# last-valid cache, no valid bid-ask mid), market_data.get_ltp returns 0. Wing
# BUYs and unwind orders are MKT and used to be called WITHOUT a `price` kwarg —
# so paper_order_manager rejected them as `missing_ltp`, halting entry. Fix:
# every place_order in the entry path now forwards a QuoteBook-derived price as
# the fallback. Tests below pin the wiring at each phase boundary.


def test_wing_buys_pass_book_ask_as_price_fallback(mock_om, mock_md):
    """Phase 1: LC and LP wing BUYs must pass `price=books[*].ask` so that
    a transient bad quote on a fresh strike doesn't reject the MKT order."""
    _configure_books_for_clean_ic(mock_md, sc_bid=18.0, sp_bid=18.0, lc_ask=5.0, lp_ask=5.0)

    def place_side_effect(symbol, side, q, price_type="MKT", price=0.0):
        return _fill(price if price > 0 else 5.0, q)

    mock_om.place_order.side_effect = place_side_effect

    s = IronCondorStrategy(mock_om, mock_md, "NIFTY")
    s.enter_hedge_first(22000, 12, 22500, 21500, _sr_mgr(), "19-MAR-2026", settings.IC_LOT_SIZE)

    wing_buy_calls = [
        c
        for c in mock_om.place_order.call_args_list
        if ("C22200" in c.args[0] or "P21800" in c.args[0]) and c.args[1] == "BUY"
    ]
    assert len(wing_buy_calls) == 2, f"expected 2 wing BUYs, got {len(wing_buy_calls)}"
    for c in wing_buy_calls:
        # Each wing BUY must carry a positive `price=` derived from book.ask.
        assert c.kwargs.get("price", 0) > 0, (
            f"wing BUY {c.args[0]} missing price= fallback — bad quote on this "
            f"strike will halt entry; got kwargs={c.kwargs}"
        )


def test_phase5a_unwind_passes_price_fallback_on_all_four_legs(mock_om, mock_md):
    """Phase 5a: post-fill credit-too-low unwind. All 4 unwind orders must
    carry a `price=` so a stale ltp on the unwind moment doesn't strand legs."""
    _configure_books_for_clean_ic(mock_md, sc_bid=18.0, sp_bid=18.0, lc_ask=5.0, lp_ask=5.0)

    def place_side_effect(symbol, side, q, price_type="MKT", price=0.0):
        # Wings fill normally; shorts fill below Phase 3 projection → Phase 5a.
        if side == "BUY" and "C22200" in symbol:
            return _fill(8.0, q)
        if side == "BUY" and "P21800" in symbol:
            return _fill(8.0, q)
        if side == "SELL" and price_type == "LMT":
            return _fill(8.0, q)
        # Unwind orders — what we're testing. Just return a fill.
        return _fill(price if price > 0 else 8.0, q)

    mock_om.place_order.side_effect = place_side_effect

    s = IronCondorStrategy(mock_om, mock_md, "NIFTY")
    s.enter_hedge_first(22000, 12, 22500, 21500, _sr_mgr(), "19-MAR-2026", settings.IC_LOT_SIZE)

    # 4 entry orders + 4 unwind orders. The last 4 are the unwind.
    unwind_calls = mock_om.place_order.call_args_list[-4:]
    for c in unwind_calls:
        assert c.kwargs.get("price", 0) > 0, (
            f"Phase 5a unwind leg {c.args[0]} {c.args[1]} missing price= fallback; kwargs={c.kwargs}"
        )


# ── Harvest re-entry partial-fill policy (incident 2026-04-27 12:00:32) ──────
# Fresh-entry partial-fill is a liquidity-stress signal → halt (unchanged).
# Harvest/adjustment re-entry partial-fill is normal market noise during the
# 1-2-second close-and-reopen cycle (the bid drifted 1.65 in 1.3s on the
# incident) → skip this cycle, increment counter, halt only if N consecutive.

import datetime as _dt


def _setup_phase5b_partial_fill(mock_om, mock_md):
    """Wings fill; SC short cancels (limit_not_reached); SP short fills.
    This is the exact incident-#4 shape."""
    _configure_books_for_clean_ic(mock_md, sc_bid=18.0, sp_bid=18.0, lc_ask=5.0, lp_ask=5.0)

    def place_side_effect(symbol, side, q, price_type="MKT", price=0.0):
        if side == "BUY" and "C22200" in symbol:
            return _fill(5.0, q)
        if side == "BUY" and "P21800" in symbol:
            return _fill(5.0, q)
        if side == "SELL" and price_type == "LMT" and "C22150" in symbol:
            return _canceled()  # SC: cancel — bid drifted below limit
        if side == "SELL" and price_type == "LMT" and "P21850" in symbol:
            return _fill(18.0, q)  # SP: filled
        return _fill(price if price > 0 else 8.0, q)  # unwind

    mock_om.place_order.side_effect = place_side_effect


def test_fresh_entry_partial_fill_skips_cycle_no_halt(mock_om, mock_md):
    """Fresh entry Phase-5b partial-fill skips the cycle (no stuck legs
    persisted, no halt) and increments the unified consecutive-fail counter.
    Incident 2026-04-28 10:49: a single bid-drift cancel on the day's first
    BANKNIFTY entry halted the session — the same mechanism the harvest
    carve-out (2026-04-27) was added to absorb. Policy unified."""
    _setup_phase5b_partial_fill(mock_om, mock_md)

    s = IronCondorStrategy(mock_om, mock_md, "NIFTY")
    # No prior exit → fresh entry. Under unified policy, this still skips.
    ok = s.enter_hedge_first(22000, 12, 22500, 21500, _sr_mgr(), "19-MAR-2026", settings.IC_LOT_SIZE)

    assert ok is False
    assert s._last_rollback_stuck_legs == [], (
        "fresh-entry partial-fill must NOT halt the session under unified "
        f"policy — got stuck_legs={s._last_rollback_stuck_legs}"
    )
    assert s._consecutive_partial_fails == 1


def test_harvest_reentry_partial_fill_skips_cycle_no_halt(mock_om, mock_md):
    """Harvest re-entry Phase-5b partial-fill skips the cycle (no stuck legs
    persisted, no halt) — unchanged behavior under the unified policy."""
    _setup_phase5b_partial_fill(mock_om, mock_md)

    s = IronCondorStrategy(mock_om, mock_md, "NIFTY")
    s._last_exit_reason = "PROFIT_HARVEST"
    s._last_exit_date = _dt.datetime.now().date().isoformat()

    ok = s.enter_hedge_first(22000, 12, 22500, 21500, _sr_mgr(), "19-MAR-2026", settings.IC_LOT_SIZE)

    assert ok is False
    assert s._last_rollback_stuck_legs == [], (
        f"harvest re-entry partial-fill must NOT halt the session — got stuck_legs={s._last_rollback_stuck_legs}"
    )
    assert s._consecutive_partial_fails == 1


def test_consecutive_partial_fail_cap_suspends_not_halts(mock_om, mock_md):
    """Cap reached: the Nth consecutive partial-fill suspends same-day adjustment
    re-entries but does NOT escalate to a session halt. Bounds the
    unwind-slippage tail without killing NIFTY for the day.
    (Regression for 2026-04-29 incident: cap → persist_stuck_legs → full halt.)"""
    _setup_phase5b_partial_fill(mock_om, mock_md)

    s = IronCondorStrategy(mock_om, mock_md, "NIFTY")
    s._consecutive_partial_fails = settings.IC_PARTIAL_FAIL_CAP - 1

    s.enter_hedge_first(22000, 12, 22500, 21500, _sr_mgr(), "19-MAR-2026", settings.IC_LOT_SIZE)

    assert s._phase5b_suspended is True, "cap reached → adjustments suspended"
    assert s._last_rollback_stuck_legs == [], (
        "cap reached must NOT populate stuck_legs — that path triggers a "
        "session halt which is reserved for 3× stop / daily-loss only"
    )
    assert s._consecutive_partial_fails == settings.IC_PARTIAL_FAIL_CAP


def test_phase5b_suspended_blocks_re_entry(mock_om, mock_md):
    """Once suspended, subsequent adjustment/harvest re-entry calls return False
    immediately without placing any orders."""
    _setup_phase5b_partial_fill(mock_om, mock_md)

    s = IronCondorStrategy(mock_om, mock_md, "NIFTY")
    s._phase5b_suspended = True
    s._last_exit_reason = "ADJUSTMENT_REQUIRED"
    s._last_exit_date = _dt.datetime.now().date().isoformat()

    ok = s.enter_hedge_first(22000, 12, 22500, 21500, _sr_mgr(), "19-MAR-2026", settings.IC_LOT_SIZE)

    assert ok is False
    assert mock_om.place_order.call_count == 0, "suspended path must place no orders"


def test_phase5b_suspended_does_not_block_fresh_entry(mock_om, mock_md):
    """Suspension only blocks re-entries; a fresh-entry attempt on a new day
    (no prior exit reason) still goes through normally."""
    _configure_books_for_clean_ic(mock_md, sc_bid=18.0, sp_bid=18.0, lc_ask=5.0, lp_ask=5.0)

    def place_side_effect(symbol, side, q, price_type="MKT", price=0.0):
        if "C22200" in symbol:
            return _fill(5.0, q)
        if "P21800" in symbol:
            return _fill(5.0, q)
        if "C22150" in symbol:
            return _fill(18.0, q)
        if "P21850" in symbol:
            return _fill(18.0, q)
        raise AssertionError(f"unexpected: {symbol}")

    mock_om.place_order.side_effect = place_side_effect

    s = IronCondorStrategy(mock_om, mock_md, "NIFTY")
    s._phase5b_suspended = True
    # No prior exit → _is_re_entry() returns False → guard does not fire.

    ok = s.enter_hedge_first(22000, 12, 22500, 21500, _sr_mgr(), "19-MAR-2026", settings.IC_LOT_SIZE)

    assert ok is True, "fresh entry must not be blocked by Phase-5b suspension"


def test_successful_entry_resets_partial_fail_counter(mock_om, mock_md):
    """A clean entry signals liquidity is fine again — counter resets so a
    later isolated partial-fill doesn't compound an old streak into a halt."""
    _configure_books_for_clean_ic(mock_md, sc_bid=18.0, sp_bid=18.0, lc_ask=5.0, lp_ask=5.0)

    def place_side_effect(symbol, side, q, price_type="MKT", price=0.0):
        if "C22200" in symbol:
            return _fill(5.0, q)
        if "P21800" in symbol:
            return _fill(5.0, q)
        if "C22150" in symbol:
            return _fill(18.0, q)
        if "P21850" in symbol:
            return _fill(18.0, q)
        raise AssertionError(f"unexpected: {symbol}")

    mock_om.place_order.side_effect = place_side_effect

    s = IronCondorStrategy(mock_om, mock_md, "NIFTY")
    # Pre-load a streak and suspension from earlier failures.
    s._consecutive_partial_fails = 2
    s._phase5b_suspended = True

    ok = s.enter_hedge_first(22000, 12, 22500, 21500, _sr_mgr(), "19-MAR-2026", settings.IC_LOT_SIZE)

    assert ok is True
    assert s._consecutive_partial_fails == 0, "counter must reset on success"
    assert s._phase5b_suspended is False, "suspension flag must reset on success"


# ── Phase 3: short-leg bid re-fetch closes the stale-window ───────────────
#
# Bid drift between Phase-1 wing fill and Phase-4 short submission was the
# root cause of the 2026-04-28 10:49 + 11:45 partial-fail incidents. The
# top-of-function `books` fetch is ~200-2000ms stale (live network) by the
# time Phase 3 runs. Re-fetching SC/SP at Phase 3 closes the window down
# to just Phase-3→Phase-4 latency (~50-200ms).


def test_phase3_sc_limit_uses_refetched_bid_not_initial(mock_om, mock_md):
    """The discriminating test: get_quote_book returns 18.0 first, then 17.50
    on the Phase-3 re-fetch. Without the fix the SC limit is 18.0 (would
    cancel against a 17.50 bid). With the fix the SC limit is 17.50 and
    fills cleanly. If this test passes against unfixed code, the test is
    wrong, not the code."""
    sc_calls = {"n": 0}
    sp_calls = {"n": 0}

    def _gqb(sym):
        if "C22150" in sym:
            sc_calls["n"] += 1
            return _book(bid=17.50 if sc_calls["n"] >= 2 else 18.0, ask=18.50, symbol=sym)
        if "P21850" in sym:
            sp_calls["n"] += 1
            return _book(bid=17.80 if sp_calls["n"] >= 2 else 18.0, ask=18.50, symbol=sym)
        if "C22200" in sym:
            return _book(bid=4.75, ask=5.0, symbol=sym)
        if "P21800" in sym:
            return _book(bid=4.75, ask=5.0, symbol=sym)
        return None

    mock_md.get_quote_book.side_effect = _gqb

    placed = {}

    def place_side_effect(symbol, side, q, price_type="MKT", price=0.0):
        if "C22200" in symbol:
            return _fill(5.0, q)
        if "P21800" in symbol:
            return _fill(5.0, q)
        if "C22150" in symbol:
            placed["sc"] = price
            return _fill(price, q)
        if "P21850" in symbol:
            placed["sp"] = price
            return _fill(price, q)
        raise AssertionError(f"unexpected: {symbol}")

    mock_om.place_order.side_effect = place_side_effect

    s = IronCondorStrategy(mock_om, mock_md, "NIFTY")
    ok = s.enter_hedge_first(22000, 12, 22500, 21500, _sr_mgr(), "19-MAR-2026", settings.IC_LOT_SIZE)

    tick = settings.PRICE_TICK
    tol = settings.IC_SHORT_LIMIT_DRIFT_TOL
    assert ok is True
    assert placed["sc"] == round((17.50 - tol) / tick) * tick, (
        f"SC limit must use re-fetched bid 17.50 minus drift_tol {tol}, got {placed['sc']}"
    )
    assert placed["sp"] == round((17.80 - tol) / tick) * tick
    # Each short symbol queried twice: top-of-function + Phase 3 re-fetch.
    assert sc_calls["n"] == 2 and sp_calls["n"] == 2


def test_phase3_refetch_above_initial_proceeds_with_higher_credit(mock_om, mock_md):
    """Symmetric case: bid drifted UP between fetches. Fresh limit is higher
    than initial → entry proceeds with better credit. Pins that the fix is
    direction-agnostic, not a one-way safety knob."""
    sc_calls = {"n": 0}

    def _gqb(sym):
        if "C22150" in sym:
            sc_calls["n"] += 1
            return _book(bid=18.50 if sc_calls["n"] >= 2 else 18.0, ask=19.0, symbol=sym)
        if "P21850" in sym:
            return _book(bid=18.0, ask=18.25, symbol=sym)
        if "C22200" in sym:
            return _book(bid=4.75, ask=5.0, symbol=sym)
        if "P21800" in sym:
            return _book(bid=4.75, ask=5.0, symbol=sym)
        return None

    mock_md.get_quote_book.side_effect = _gqb

    placed_sc = {"v": None}

    def place_side_effect(symbol, side, q, price_type="MKT", price=0.0):
        if "C22200" in symbol or "P21800" in symbol:
            return _fill(5.0, q)
        if "C22150" in symbol:
            placed_sc["v"] = price
            return _fill(price, q)
        if "P21850" in symbol:
            return _fill(price, q)
        raise AssertionError(f"unexpected: {symbol}")

    mock_om.place_order.side_effect = place_side_effect

    s = IronCondorStrategy(mock_om, mock_md, "NIFTY")
    ok = s.enter_hedge_first(22000, 12, 22500, 21500, _sr_mgr(), "19-MAR-2026", settings.IC_LOT_SIZE)

    tick = settings.PRICE_TICK
    tol = settings.IC_SHORT_LIMIT_DRIFT_TOL
    assert ok is True
    assert placed_sc["v"] == round((18.50 - tol) / tick) * tick, (
        f"SC limit should be re-fetched bid 18.50 minus drift_tol {tol}, got {placed_sc['v']}"
    )


def test_phase3_refetch_untradable_aborts_and_unwinds_wings(mock_om, mock_md):
    """Re-fetch returns untradable (broker quote outage during wing-fill
    window) → abort cleanly. Wings round-tripped, no shorts submitted,
    return False. Better than entering with stale data."""
    sc_calls = {"n": 0}

    def _gqb(sym):
        if "C22150" in sym:
            sc_calls["n"] += 1
            # First call (top-of-function) tradable; second (Phase 3) untradable.
            return None if sc_calls["n"] >= 2 else _book(bid=18.0, ask=18.25, symbol=sym)
        if "P21850" in sym:
            return _book(bid=18.0, ask=18.25, symbol=sym)
        if "C22200" in sym:
            return _book(bid=4.75, ask=5.0, symbol=sym)
        if "P21800" in sym:
            return _book(bid=4.75, ask=5.0, symbol=sym)
        return None

    mock_md.get_quote_book.side_effect = _gqb

    place_calls = []

    def place_side_effect(symbol, side, q, price_type="MKT", price=0.0):
        place_calls.append((symbol, side))
        return _fill(5.0, q)

    mock_om.place_order.side_effect = place_side_effect

    s = IronCondorStrategy(mock_om, mock_md, "NIFTY")
    ok = s.enter_hedge_first(22000, 12, 22500, 21500, _sr_mgr(), "19-MAR-2026", settings.IC_LOT_SIZE)

    assert ok is False
    # 2 BUY wings (Phase 1) + 2 SELL wing unwinds (Phase 3 abort) = 4 calls.
    assert len(place_calls) == 4, f"expected 4 wing-only calls, got {place_calls}"
    # No SC/SP order should ever fire on a Phase-3 abort.
    assert not any("C22150" in s for s, _ in place_calls)
    assert not any("P21850" in s for s, _ in place_calls)
    # Each wing was bought once and sold once.
    assert sum(1 for s, sd in place_calls if "C22200" in s and sd == "BUY") == 1
    assert sum(1 for s, sd in place_calls if "C22200" in s and sd == "SELL") == 1
    assert sum(1 for s, sd in place_calls if "P21800" in s and sd == "BUY") == 1
    assert sum(1 for s, sd in place_calls if "P21800" in s and sd == "SELL") == 1


def test_phase3_refetch_below_min_credit_routes_through_existing_refuse(mock_om, mock_md):
    """If the fresh bid drifts low enough to push projected credit below
    `_min_credit`, the existing Phase-3 refuse-and-unwind path fires —
    this is the conversion the advisor flagged: some 'Phase-5b partial-
    fails' become 'Phase-3 refuses' under the fix. Same wing round-trip
    cost, different reason code."""
    sc_calls = {"n": 0}

    def _gqb(sym):
        if "C22150" in sym:
            sc_calls["n"] += 1
            # Initial bid passes pre-entry credit check; re-fetch drops below
            # min_credit threshold once wings are factored in.
            # Initial 18.0 passes pre-entry credit (26 > 18); re-fetch 5.0
            # makes projected credit = (5+18)-(5+5) = 13, below 18 floor.
            return _book(bid=5.0 if sc_calls["n"] >= 2 else 18.0, ask=18.25, symbol=sym)
        if "P21850" in sym:
            return _book(bid=18.0, ask=18.25, symbol=sym)
        if "C22200" in sym:
            return _book(bid=4.75, ask=5.0, symbol=sym)
        if "P21800" in sym:
            return _book(bid=4.75, ask=5.0, symbol=sym)
        return None

    mock_md.get_quote_book.side_effect = _gqb

    place_calls = []

    def place_side_effect(symbol, side, q, price_type="MKT", price=0.0):
        place_calls.append((symbol, side))
        return _fill(5.0, q)

    mock_om.place_order.side_effect = place_side_effect

    s = IronCondorStrategy(mock_om, mock_md, "NIFTY")
    ok = s.enter_hedge_first(22000, 12, 22500, 21500, _sr_mgr(), "19-MAR-2026", settings.IC_LOT_SIZE)

    assert ok is False
    # Wings round-tripped cleanly; no shorts submitted (refuse fires before Phase 4).
    assert not any("C22150" in s for s, _ in place_calls)
    assert not any("P21850" in s for s, _ in place_calls)
    assert sum(1 for s, sd in place_calls if "C22200" in s and sd == "SELL") == 1
    assert sum(1 for s, sd in place_calls if "P21800" in s and sd == "SELL") == 1


# ── Phase 3: SC/SP limit drift tolerance ─────────────────────────────────────
#
# Root cause of 2026-04-29 SC cancels: 4 consecutive SELL LMT orders submitted
# with limit=bid (OFFSET_TICKS=0). The bid drifted 0.15–0.50 pts between Phase-3
# re-fetch and Phase-4 submission, placing limit > live_bid → CANCELED.
# Fix: subtract IC_SHORT_LIMIT_DRIFT_TOL from the limit so normal drift is absorbed.


def test_sc_limit_drift_tolerance_absorbs_normal_bid_drift(mock_om, mock_md):
    """SC/SP limits are set at bid - IC_SHORT_LIMIT_DRIFT_TOL so Phase-3→4
    latency drift does not cancel the order.

    Without fix (limit=bid=18.0): a 0.30-pt drift → bid=17.70 < limit → CANCELED.
    With fix (limit=bid-0.50=17.50): 17.70 >= 17.50 → fills.
    """
    FRESH_BID = 18.0
    DRIFTED_BID = 17.70  # 0.30-pt drift — within today's observed 0.15-0.50 range

    def _gqb(sym):
        if "C22150" in sym:
            return _book(bid=FRESH_BID, ask=FRESH_BID + 0.50, symbol=sym)
        if "P21850" in sym:
            return _book(bid=FRESH_BID, ask=FRESH_BID + 0.50, symbol=sym)
        if "C22200" in sym:
            return _book(bid=4.75, ask=5.0, symbol=sym)
        if "P21800" in sym:
            return _book(bid=4.75, ask=5.0, symbol=sym)
        return None

    mock_md.get_quote_book.side_effect = _gqb

    submitted = {}

    def place_side_effect(symbol, side, q, price_type="MKT", price=0.0):
        if "C22200" in symbol:
            return _fill(5.0, q)
        if "P21800" in symbol:
            return _fill(5.0, q)
        if "C22150" in symbol:
            submitted["sc"] = price
            return _canceled() if price > DRIFTED_BID else _fill(DRIFTED_BID, q)
        if "P21850" in symbol:
            submitted["sp"] = price
            return _canceled() if price > DRIFTED_BID else _fill(DRIFTED_BID, q)
        return _fill(price if price > 0 else 8.0, q)

    mock_om.place_order.side_effect = place_side_effect

    s = IronCondorStrategy(mock_om, mock_md, "NIFTY")
    ok = s.enter_hedge_first(22000, 12, 22500, 21500, _sr_mgr(), "19-MAR-2026", settings.IC_LOT_SIZE)

    tick = settings.PRICE_TICK
    drift_tol = settings.IC_SHORT_LIMIT_DRIFT_TOL
    expected_limit = round((FRESH_BID - drift_tol) / tick) * tick

    assert ok is True, (
        "entry must fill despite 0.30-pt bid drift — "
        "IC_SHORT_LIMIT_DRIFT_TOL must absorb normal Phase-3→4 latency drift"
    )
    assert submitted.get("sc") == expected_limit, (
        f"SC limit {submitted.get('sc')} must equal bid-drift_tol = {expected_limit}"
    )
    assert submitted.get("sp") == expected_limit, (
        f"SP limit {submitted.get('sp')} must equal bid-drift_tol = {expected_limit}"
    )
