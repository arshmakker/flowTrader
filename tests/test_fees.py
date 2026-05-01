"""LIVE-12 - tax and fee computation for Indian F&O options.

Pins the six-component cost stack that PaperOrderManager and PaperPositionTracker
both call through. The numbers here are deliberately tied to the rates baked
into ``settings.FEES_NIFTY_OPT`` - if those rates are revised (SEBI, NSE,
Budget STT hike), these tests should fail loudly so a rate-change PR gets a
paired test-pin update rather than silently shifting paper-PnL calibration.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from trading_system.config import settings
from trading_system.core.fees import compute_taxes_and_fees

# A canonical option symbol in Shoonya's tradingsymbol shape.
_CE = "NFO|NIFTY17APR26C22000"
_PE = "NFO|NIFTY17APR26P22000"

# -- Side-asymmetry is load-bearing ---------------------------------------


def test_sell_leg_has_stt_no_stamp():
    f = compute_taxes_and_fees(_CE, "SELL", price=50.0, qty=650)
    assert f["stt"] > 0
    assert f["stamp"] == 0.0


def test_buy_leg_has_stamp_no_stt():
    f = compute_taxes_and_fees(_CE, "BUY", price=50.0, qty=650)
    assert f["stamp"] > 0
    assert f["stt"] == 0.0


def test_short_side_aliases_accepted():
    """Shoonya/legacy code sometimes passes 'S' / 'B' instead of SELL/BUY."""
    long_sell = compute_taxes_and_fees(_CE, "SELL", price=50.0, qty=650)
    short_sell = compute_taxes_and_fees(_CE, "S", price=50.0, qty=650)
    assert long_sell == short_sell

    long_buy = compute_taxes_and_fees(_CE, "BUY", price=50.0, qty=650)
    short_buy = compute_taxes_and_fees(_CE, "B", price=50.0, qty=650)
    assert long_buy == short_buy


# -- Each component pinned independently ---------------------------------


def test_brokerage_is_flat_per_order():
    """Shoonya is flat Rs 5, NOT tiered. Premium size must not scale brokerage."""
    small = compute_taxes_and_fees(_CE, "SELL", price=1.0, qty=65)
    large = compute_taxes_and_fees(_CE, "SELL", price=500.0, qty=650)
    assert small["brokerage"] == settings.FEES_NIFTY_OPT["brokerage_per_order"]
    assert large["brokerage"] == settings.FEES_NIFTY_OPT["brokerage_per_order"]


def test_stt_sell_rate_matches_budget_2026_hike():
    """0.15% on SELL premium, post-Budget-2026 (eff. 2026-04-01)."""
    f = compute_taxes_and_fees(_CE, "SELL", price=50.0, qty=650)
    expected = 50.0 * 650 * 0.0015  # Rs 48.75
    assert f["stt"] == pytest.approx(expected, abs=0.01)


def test_exchange_transaction_charge():
    f = compute_taxes_and_fees(_CE, "SELL", price=50.0, qty=650)
    expected = 50.0 * 650 * 0.0000255  # Rs 0.83
    assert f["exch_txn"] == pytest.approx(expected, abs=0.01)


def test_sebi_turnover_fee():
    """Rs 10/crore = 1e-6 fraction of turnover. Tiny but non-zero."""
    f = compute_taxes_and_fees(_CE, "SELL", price=50.0, qty=650)
    expected = 50.0 * 650 * 0.000001  # Rs 0.03
    assert f["sebi"] == pytest.approx(expected, abs=0.01)


def test_stamp_buy_rate():
    """Stamp is 0.003% on BUY premium only."""
    f = compute_taxes_and_fees(_CE, "BUY", price=50.0, qty=650)
    expected = 50.0 * 650 * 0.00003  # Rs 0.98
    assert f["stamp"] == pytest.approx(expected, abs=0.01)


# -- GST base is load-bearing: MUST exclude STT and stamp ------------------


def test_gst_excludes_stt_and_stamp():
    """Common implementation bug: GSTing the whole bill. The rule is
    GST = 18% x (brokerage + exch_txn + sebi) only - STT and stamp are
    government taxes, not services, and GST does not compound onto them."""
    f = compute_taxes_and_fees(_CE, "SELL", price=50.0, qty=650)
    expected_base = f["brokerage"] + f["exch_txn"] + f["sebi"]
    assert f["gst"] == pytest.approx(expected_base * 0.18, abs=0.01)

    # And: if we take the same leg as a BUY (stamp replaces STT), GST still
    # excludes stamp - the base is the same.
    f_buy = compute_taxes_and_fees(_CE, "BUY", price=50.0, qty=650)
    assert f_buy["gst"] == pytest.approx((f_buy["brokerage"] + f_buy["exch_txn"] + f_buy["sebi"]) * 0.18, abs=0.01)


# -- Total is the authoritative sum -------------------------------------------


def test_total_equals_sum_of_components():
    """Callers should use fees['total'] rather than re-summing - pin the
    invariant that total IS the sum (modulo paise rounding)."""
    f = compute_taxes_and_fees(_CE, "SELL", price=50.0, qty=650)
    component_sum = f["brokerage"] + f["stt"] + f["exch_txn"] + f["sebi"] + f["stamp"] + f["gst"]
    assert f["total"] == pytest.approx(component_sum, abs=0.01)


# -- Worked examples against the numbers documented in the LIVE-12 brief ----


def test_worked_example_10lot_sell_at_50():
    """10 lots NIFTY (qty=650) SELL CE @ Rs 50 - roughly Rs 55.66 all-in per leg.

    Component-by-component against the documented LIVE-12 design numbers:
      brokerage   =   5.00
      stt         = 32500 x 0.0015    = 48.75
      exch_txn    = 32500 x 0.0000255 = 0.83
      sebi        = 32500 x 0.000001  =  0.03
      stamp       = 0 (SELL)
      gst         = (5 + 0.83 + 0.03) x 0.18 = 1.06
      total       ~ 55.67
    """
    f = compute_taxes_and_fees(_CE, "SELL", price=50.0, qty=650)
    assert f["brokerage"] == pytest.approx(5.00, abs=0.01)
    assert f["stt"] == pytest.approx(48.75, abs=0.01)
    assert f["exch_txn"] == pytest.approx(0.83, abs=0.01)
    assert f["sebi"] == pytest.approx(0.03, abs=0.01)
    assert f["stamp"] == 0.0
    assert f["gst"] == pytest.approx(1.06, abs=0.01)
    assert f["total"] == pytest.approx(55.67, abs=0.02)


def test_worked_example_10lot_buy_at_5_is_cheap():
    """10 lots NIFTY BUY CE @ Rs 5 (short exit at harvest). Turnover Rs 3,250 -
    Rs 25-ish all-in. Sanity-check that BUY-side cost stack is materially
    smaller than SELL-side of same turnover (no STT, tiny stamp)."""
    f = compute_taxes_and_fees(_CE, "BUY", price=5.0, qty=650)
    assert f["brokerage"] == 5.0
    assert f["stt"] == 0.0
    assert f["exch_txn"] == pytest.approx(0.08, abs=0.01)
    assert f["sebi"] == pytest.approx(0.0, abs=0.01)
    assert f["stamp"] == pytest.approx(0.10, abs=0.01)
    assert f["gst"] == pytest.approx((5 + 0.08 + 0.0) * 0.18, abs=0.02)
    # All-in ~Rs 7.36 - meaningfully smaller than SELL of same turnover.
    assert 5.0 < f["total"] < 10.0


# -- Non-fill / edge-case inputs don't explode -------------------------------


def test_zero_qty_returns_zero_fees():
    """Zero-qty leg can't have any cost. Kept defensive so the rejected-order
    code path in PaperOrderManager doesn't need its own zeroing logic."""
    f = compute_taxes_and_fees(_CE, "SELL", price=50.0, qty=0)
    assert f["total"] == 0.0
    assert all(f[k] == 0.0 for k in ("brokerage", "stt", "exch_txn", "sebi", "stamp", "gst"))


def test_zero_price_returns_zero_fees():
    f = compute_taxes_and_fees(_CE, "SELL", price=0.0, qty=650)
    assert f["total"] == 0.0


def test_put_symbol_recognised_as_option():
    """PE tradingsymbols ending in 'P{strike}' must go through the options
    branch same as CEs - the detection used to miss this."""
    f = compute_taxes_and_fees(_PE, "SELL", price=50.0, qty=650)
    assert f["stt"] > 0
    assert f["stamp"] == 0.0


# -- Rate-revision guard - if FEES_NIFTY_OPT is edited, these must track ------


def test_rates_block_is_current_as_of_2026_04_24():
    """If you bump any of these rates, update the expectation here AND the
    worked-example tests above. Leaving this pinned guards against an
    accidental half-migration where only some rates are updated."""
    r = settings.FEES_NIFTY_OPT
    assert r["brokerage_per_order"] == 5.0
    assert r["stt_sell_pct"] == 0.0015
    assert r["stt_exercise_pct"] == 0.0015
    assert r["exch_txn_pct"] == 0.0000255
    assert r["sebi_pct"] == 0.000001
    assert r["stamp_buy_pct"] == 0.00003
    assert r["gst_pct"] == 0.18
