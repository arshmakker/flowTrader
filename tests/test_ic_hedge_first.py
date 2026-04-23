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
    om.build_option_symbol.side_effect = lambda inst, exp, strike, type: (
        f"NFO|{inst}{exp}{type[0]}{int(strike)}"
    )
    om.tracker = None
    return om


@pytest.fixture
def mock_md():
    md = MagicMock()
    md.get_lot_size.return_value = settings.NIFTY_LOT_SIZE
    return md


def _configure_books_for_clean_ic(mock_md, sc_bid=18.0, sp_bid=18.0, lc_ask=5.0, lp_ask=5.0):
    """All four legs have tradable books that yield a healthy (sc+sp)-(lc+lp) credit."""
    books = {}
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
        if "C22200" in symbol: return _fill(5.0, q)     # LC MKT fill at ask
        if "P21800" in symbol: return _fill(5.0, q)     # LP MKT fill at ask
        if "C22150" in symbol: return _fill(18.0, q)    # SC LMT fill at bid
        if "P21850" in symbol: return _fill(18.0, q)    # SP LMT fill at bid
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
        if "C22200" in symbol and side == "BUY": return _fill(5.0, q)    # LC fills
        if "P21800" in symbol and side == "BUY": return _rejected()       # LP rejected
        if "C22200" in symbol and side == "SELL": return _fill(5.0, q)   # LC unwind close
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
    qty_full = settings.IC_LOT_SIZE * settings.NIFTY_LOT_SIZE  # 650

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
    lc_sell_calls = [c for c in mock_om.place_order.call_args_list
                     if "C22200" in c.args[0] and c.args[1] == "SELL"]
    assert len(lc_sell_calls) == 1
    assert lc_sell_calls[0].args[2] == 300, "partial wing must be closed at actual fill_qty"


# ── Phase 5b: short timeout unwinds everything ────────────────────────

def test_phase5b_both_shorts_cancel_unwinds_all(mock_om, mock_md):
    """Both shorts CANCELED (limit not reached / timed out) → close both wings
    at market. No filled shorts to close. Bounded loss = wings' premium."""
    _configure_books_for_clean_ic(mock_md)

    def place_side_effect(symbol, side, q, price_type="MKT", price=0.0):
        if side == "BUY" and price_type == "MKT": return _fill(5.0, q)    # Wings fill
        if side == "SELL" and price_type == "LMT": return _canceled()     # Shorts cancel
        if side == "SELL" and price_type == "MKT": return _fill(5.0, q)   # Wing unwinds
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
            if side == "BUY": return _fill(5.0, q)       # wing entry
            if side == "SELL": return _fill(5.0, q)      # wing unwind
        if "C22150" in symbol:
            short_calls["C22150"] += 1
            if short_calls["C22150"] == 1: return _fill(18.0, q)   # first call: LMT fills
            return _fill(18.0, q)                                   # second call: MKT buyback
        if "P21850" in symbol:
            short_calls["P21850"] += 1
            if short_calls["P21850"] == 1: return _canceled()       # first call: LMT canceled
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
        if "P21800" in sym: return None  # LP quote unavailable
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
        if "C22150" in sym: return _book(bid=0.0, ask=1.0, symbol=sym)  # untradable short
        if "P21850" in sym: return _book(bid=18.0, ask=18.25, symbol=sym)
        if "C22200" in sym: return _book(bid=4.75, ask=5.0, symbol=sym)
        if "P21800" in sym: return _book(bid=4.75, ask=5.0, symbol=sym)
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
        if side == "BUY" and price_type == "MKT": return _fill(8.0, q)
        if side == "SELL" and price_type == "MKT": return _fill(8.0, q)
        raise AssertionError(f"unexpected: {side} {price_type} {symbol}")
    mock_om.place_order.side_effect = place_side_effect

    s = IronCondorStrategy(mock_om, mock_md, "NIFTY")
    ok = s.enter_hedge_first(22000, 12, 22500, 21500, _sr_mgr(), "19-MAR-2026", settings.IC_LOT_SIZE)

    # Actual credit = 8+8 - 8-8 = 0 < IC_MIN_CREDIT=18 → Phase 5a unwinds.
    assert ok is False
    assert s.is_active() is False
    # LC buy + LP buy + SC LMT + SP LMT + SC buyback + SP buyback + LC sell + LP sell = 8.
    assert mock_om.place_order.call_count == 8
