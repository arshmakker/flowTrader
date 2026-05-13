from unittest.mock import MagicMock, patch

import pytest

from trading_system.config import settings
from trading_system.core.iron_condor import IC_Position, IronCondorStrategy
from trading_system.existing.market_data import QuoteBook


@pytest.fixture(autouse=True)
def _force_sequential_entry(monkeypatch):
    """These tests cover the legacy sequential entry path. Pin IC_ENTRY_MODE
    so they keep exercising it regardless of the branch-level default."""
    monkeypatch.setattr(settings, "IC_ENTRY_MODE", "sequential")


@pytest.fixture
def mock_om():
    om = MagicMock()
    om.build_option_symbol.side_effect = lambda inst, exp, strike, type: f"NFO|{inst}{exp}{type[0]}{int(strike)}"
    # BUG-11: place_order must return a dict with status="COMPLETE" so the
    # atomic four-leg gate (iron_condor.py:220) accepts the leg. Previously the
    # fixture returned a raw MagicMock whose .get("status") was another MagicMock,
    # causing every entry to abort on leg 1.
    om.place_order.return_value = {
        "status": "COMPLETE",
        "fill_price": 18.0,
        "order_id": "PAPER_MOCK",
    }
    # No tracker for these unit tests — BUG-03/BUG-04 unwind paths skip when None.
    om.tracker = None
    om.get_available_margin.return_value = float("inf")
    return om


@pytest.fixture
def mock_md():
    md = MagicMock()
    # Default LTP
    md.get_ltp.return_value = 10.0
    md.get_lot_size.return_value = settings.NIFTY_LOT_SIZE
    return md


def test_ic_strategy_entry_success(mock_om, mock_md):
    s = IronCondorStrategy(mock_om, mock_md, "NIFTY")

    # spot=22000, vix=15 (NORMAL tier: OTM=200, width=100, step=50)
    # SC=22200, SP=21800, LC=22300, LP=21700
    def ltp_side_effect(sym):
        if "C22200" in sym or "P21800" in sym:
            return 15.0
        if "C22300" in sym or "P21700" in sym:
            return 5.0
        return 10.0

    mock_md.get_ltp.side_effect = ltp_side_effect

    success = s.enter(22000, 15, "19-MAR-2026", settings.IC_LOT_SIZE)
    assert success is True
    assert s.is_active() is True
    assert s._position.max_profit == 20.0 * settings.IC_LOT_SIZE * settings.NIFTY_LOT_SIZE


def test_ic_strategy_harvest(mock_om, mock_md):
    s = IronCondorStrategy(mock_om, mock_md, "NIFTY")
    s._position = IC_Position(
        instrument="NIFTY",
        sc_sym="SC",
        sp_sym="SP",
        lc_sym="LC",
        lp_sym="LP",
        sc_strike=22150,
        sp_strike=21850,
        lc_strike=22200,
        lp_strike=21800,
        max_profit=1000,
        entry_credit=20,
        lots=2,
        entry_time="10:00:00",
    )

    # Harvest trigger = 15% of max_profit (1000) = 150 (NIFTY threshold).
    # LTP premium = 8.5+8.5-0.5-0.5 = 16.0. PnL = (20-16.0)*2*65 = 4.0*130 = 520 >= 150.
    # LC/LP must be > 0 so ltp_missing is empty — dual-source guard requires both LTP sources.
    # Spot ("NSE|Nifty 50") must be between strikes (22150/21850) to avoid false breach.
    def ltp_side_effect(sym):
        if sym in ("SC", "SP"):
            return 8.5
        if sym in ("LC", "LP"):
            return 0.5
        return 22000  # spot — between sc_strike=22150 and sp_strike=21850

    # bid/ask: exit_premium=(8.5+8.5)-(0.4+0.4)=16.2, fill_gross=(20-16.2)*130=494>fees.
    def qb_side_effect(sym):
        if sym in ("SC", "SP"):
            return QuoteBook(symbol=sym, bid=8.4, ask=8.5, bid_qty=100, ask_qty=100)
        return QuoteBook(symbol=sym, bid=0.4, ask=0.6, bid_qty=100, ask_qty=100)

    mock_md.get_ltp.side_effect = ltp_side_effect
    mock_md.get_quote_book.side_effect = qb_side_effect

    result = s.monitor()
    assert result is not None
    assert result["exit_reason"] == "PROFIT_HARVEST"
    assert s.is_active() is False


def test_ic_strategy_force_exit_pnl(mock_om, mock_md):
    s = IronCondorStrategy(mock_om, mock_md, "NIFTY")
    s._position = IC_Position(
        instrument="NIFTY",
        sc_sym="SC",
        sp_sym="SP",
        lc_sym="LC",
        lp_sym="LP",
        sc_strike=22150,
        sp_strike=21850,
        lc_strike=22200,
        lp_strike=21800,
        max_profit=1000,
        entry_credit=20.0,
        lots=2,
        entry_time="10:00:00",
    )

    # Current premium: 25.0. PnL = (20.0 - 25.0) * 2 * 65 = -5.0 * 130 = -650.0
    def ltp_side_effect(sym):
        if sym in ("SC", "SP"):
            return 15.0
        if sym in ("LC", "LP"):
            return 2.5
        return 0.0

    mock_md.get_ltp.side_effect = ltp_side_effect

    result = s.force_exit()
    assert result is not None
    assert result["exit_reason"] == "FORCE_EXIT"
    assert result["gross_pnl"] == -650.0
    assert result["net_pnl"] == -650.0
    assert s.is_active() is False


def test_from_dict_infers_expiry_from_symbol_when_absent():
    """Bug fix: positions saved before expiry_date field existed must have it
    backfilled from the sc_sym on restore, so _find_expiring_today can close them."""
    d = {
        "instrument": "NIFTY",
        "sc_sym": "NFO|NIFTY21APR26C24350",
        "sp_sym": "NFO|NIFTY21APR26P22400",
        "lc_sym": "NFO|NIFTY21APR26C24450",
        "lp_sym": "NFO|NIFTY21APR26P22300",
        "sc_strike": 24350,
        "sp_strike": 22400,
        "lc_strike": 24450,
        "lp_strike": 22300,
        "max_profit": 18752.5,
        "entry_credit": 28.85,
        "lots": 10,
        "entry_time": "13:42:14",
        "peak_pnl": 0.0,
        # expiry_date intentionally absent (old state format)
    }
    pos = IC_Position.from_dict(d)
    assert pos.expiry_date == "2026-04-21"


def test_from_dict_preserves_existing_expiry_date():
    """from_dict must not overwrite a valid expiry_date already in the saved state."""
    d = {
        "instrument": "BANKNIFTY",
        "sc_sym": "NFO|BANKNIFTY28APR26C57400",
        "sp_sym": "NFO|BANKNIFTY28APR26P51000",
        "lc_sym": "NFO|BANKNIFTY28APR26C57500",
        "lp_sym": "NFO|BANKNIFTY28APR26P50900",
        "sc_strike": 57400,
        "sp_strike": 51000,
        "lc_strike": 57500,
        "lp_strike": 50900,
        "max_profit": 5970,
        "entry_credit": 19.9,
        "lots": 10,
        "entry_time": "10:36:21",
        "peak_pnl": 0.0,
        "expiry_date": "2026-04-28",
    }
    pos = IC_Position.from_dict(d)
    assert pos.expiry_date == "2026-04-28"


def test_freeze_qty_breach_refuses_entry_and_places_no_orders(mock_om, mock_md):
    """LIVE-13: per-leg qty exceeding NSE freeze-qty must refuse upfront,
    before any order is submitted. Prevents a leg-3-rejection cascade into
    LIVE-03's rollback path."""
    s = IronCondorStrategy(mock_om, mock_md, "NIFTY")

    # Good LTPs — credit rule passes so we reach the freeze check.
    def ltp_side_effect(sym):
        if "C22150" in sym or "P21850" in sym:
            return 18.0
        if "C22200" in sym or "P21800" in sym:
            return 5.0
        return 10.0

    mock_md.get_ltp.side_effect = ltp_side_effect

    # NIFTY lot_size=65, FREEZE_QTY_NIFTY=1800 → breach at ≥28 lots (28*65=1820).
    # Pick 30 lots → qty=1950 > 1800.
    breaching_lots = 30
    assert breaching_lots * settings.NIFTY_LOT_SIZE > settings.FREEZE_QTY_NIFTY

    success = s.enter(22000, 12, "19-MAR-2026", breaching_lots)

    assert success is False
    assert s.is_active() is False
    # No orders should have been placed — the guard runs before the legs loop.
    assert mock_om.place_order.call_count == 0


def test_just_below_freeze_qty_allows_entry(mock_om, mock_md):
    """Qty under the freeze cap passes the guard; 27 × 65 = 1755 < 1800."""
    s = IronCondorStrategy(mock_om, mock_md, "NIFTY")

    # VIX=15 NORMAL tier: SC=22200, SP=21800, LC=22300, LP=21700
    def ltp_side_effect(sym):
        if "C22200" in sym or "P21800" in sym:
            return 15.0
        if "C22300" in sym or "P21700" in sym:
            return 5.0
        return 10.0

    mock_md.get_ltp.side_effect = ltp_side_effect

    s.enter(22000, 15, "19-MAR-2026", 27)

    assert mock_om.place_order.call_count == 4  # all four legs went out


def test_freeze_qty_breach_for_banknifty_uses_banknifty_cap(mock_om, mock_md):
    """BANKNIFTY has its own cap (900). Guard must read the right instrument's
    setting — not silently fall through to NIFTY's value."""
    s = IronCondorStrategy(mock_om, mock_md, "BANKNIFTY")
    mock_md.get_lot_size.return_value = settings.BANKNIFTY_LOT_SIZE  # 30

    # Tighten to a symbol-agnostic LTP table — BANKNIFTY strikes differ.
    mock_md.get_ltp.side_effect = lambda sym: (
        18.0 if sym.endswith(("C52000", "P48000")) else 5.0 if sym.endswith(("C52100", "P47900")) else 10.0
    )

    # 31 lots × 30 = 930 > 900 FREEZE_QTY_BANKNIFTY, but under 1800 (NIFTY cap).
    # Using NIFTY's cap here would let the order through, which is the bug we
    # are pinning against.
    breaching_lots = 31
    assert breaching_lots * settings.BANKNIFTY_LOT_SIZE > settings.FREEZE_QTY_BANKNIFTY
    assert breaching_lots * settings.BANKNIFTY_LOT_SIZE < settings.FREEZE_QTY_NIFTY

    success = s.enter(50000, 12, "19-MAR-2026", breaching_lots)

    assert success is False
    assert mock_om.place_order.call_count == 0


def test_exit_result_contains_entry_date(mock_om, mock_md):
    """Bug fix: for overnight carries the result dict must carry entry_date so the
    CSV logs the entry day, not the exit day."""
    s = IronCondorStrategy(mock_om, mock_md, "NIFTY")
    s._position = IC_Position(
        instrument="NIFTY",
        sc_sym="SC",
        sp_sym="SP",
        lc_sym="LC",
        lp_sym="LP",
        sc_strike=22150,
        sp_strike=21850,
        lc_strike=22200,
        lp_strike=21800,
        max_profit=1000,
        entry_credit=20.0,
        lots=2,
        entry_time="15:02:49",
        entry_date="2026-04-20",
    )
    mock_md.get_ltp.return_value = 10.0
    result = s.force_exit()
    assert result is not None
    assert result["entry_date"] == "2026-04-20"


def test_banknifty_credit_floor_allows_entry_above_floor(mock_om, mock_md):
    """LIVE-27: BANKNIFTY at ₹32 credit must enter above the ₹30 floor.
    Strikes: spot=50000, VIX=12 (low tier OTM=150, step=100) → floor widens to 700pt:
    SC=50700, SP=49300, LC=50800, LP=49200."""
    mock_md.get_lot_size.return_value = settings.BANKNIFTY_LOT_SIZE
    s = IronCondorStrategy(mock_om, mock_md, "BANKNIFTY")

    # (17+17) - (1+1) = 32 > 30 floor → must enter
    mock_md.get_ltp.side_effect = lambda sym: (
        17.0 if sym.endswith(("C50700", "P49300")) else 1.0 if sym.endswith(("C50800", "P49200")) else 10.0
    )
    success = s.enter(50000, 12, "19-MAR-2026", 10)
    assert success is True
    assert mock_om.place_order.call_count == 4


def test_banknifty_credit_floor_refuses_below_floor(mock_om, mock_md):
    """LIVE-27: BANKNIFTY at ₹28 credit must be refused below the ₹30 floor."""
    mock_md.get_lot_size.return_value = settings.BANKNIFTY_LOT_SIZE
    s = IronCondorStrategy(mock_om, mock_md, "BANKNIFTY")

    # (15+15) - (1+1) = 28 < 30 floor → must refuse; floor-widened strikes at 700pt OTM
    mock_md.get_ltp.side_effect = lambda sym: (
        15.0 if sym.endswith(("C50700", "P49300")) else 1.0 if sym.endswith(("C50800", "P49200")) else 10.0
    )
    success = s.enter(50000, 12, "19-MAR-2026", 10)
    assert success is False
    assert mock_om.place_order.call_count == 0


def test_banknifty_harvest_below_threshold_does_not_trigger(mock_om, mock_md):
    """BANKNIFTY at 8.7% MTM ratio must NOT harvest (threshold is 13%).
    Regression: flat 1% threshold fires fee-negative harvests on BANKNIFTY
    because the 8-leg round-trip fee stack breaks even at ~11.8% of max_profit."""
    mock_md.get_lot_size.return_value = settings.BANKNIFTY_LOT_SIZE
    s = IronCondorStrategy(mock_om, mock_md, "BANKNIFTY")
    # max_profit=10000, so 13% trigger = 1300; 8.7% MTM = 870 — below threshold.
    s._position = IC_Position(
        instrument="BANKNIFTY",
        sc_sym="SC",
        sp_sym="SP",
        lc_sym="LC",
        lp_sym="LP",
        sc_strike=52000,
        sp_strike=51000,
        lc_strike=52500,
        lp_strike=50500,
        max_profit=10000,
        entry_credit=33.0,
        lots=10,
        entry_time="10:30:00",
    )

    # current_premium mid = (SC+SP)-(LC+LP) = (17+17)-(1.95+1.95) = 30.1
    # pnl_unit = entry_credit - current_premium = 33.0 - 30.1 = 2.9
    # total_pnl = 2.9 * 10 * 30 = 870 = 8.7% of max_profit=10000 → below 13% gate
    # LTP mirrors book mids so pnl_ltp is also ~870 — both sources below trigger.
    def qb_side_effect(sym):
        if sym in ("SC", "SP"):
            return QuoteBook(symbol=sym, bid=16.5, ask=17.5, bid_qty=100, ask_qty=100)
        return QuoteBook(symbol=sym, bid=1.45, ask=2.45, bid_qty=100, ask_qty=100)

    def ltp_side(sym):
        if sym in ("SC", "SP"):
            return 17.0  # ltp_premium=17+17-1.95-1.95=30.1; pnl_ltp=870 < 1300
        if sym in ("LC", "LP"):
            return 1.95
        return 51500.0  # spot between strikes (51000–52000) — no breach

    mock_md.get_quote_book.side_effect = qb_side_effect
    mock_md.get_ltp.side_effect = ltp_side
    result = s.monitor()
    assert result is None, "BANKNIFTY must not harvest at 8.7% of max_profit (below 13% threshold)"


def test_banknifty_harvest_above_threshold_triggers(mock_om, mock_md):
    """BANKNIFTY at 13%+ MTM ratio must harvest when bid/ask confirms a fee-viable exit."""
    mock_md.get_lot_size.return_value = settings.BANKNIFTY_LOT_SIZE
    s = IronCondorStrategy(mock_om, mock_md, "BANKNIFTY")
    # max_profit=10000, so 13% trigger = 1300; 15% MTM = 1500 — above threshold.
    s._position = IC_Position(
        instrument="BANKNIFTY",
        sc_sym="SC",
        sp_sym="SP",
        lc_sym="LC",
        lp_sym="LP",
        sc_strike=52000,
        sp_strike=51000,
        lc_strike=52500,
        lp_strike=50500,
        max_profit=10000,
        entry_credit=33.0,
        lots=10,
        entry_time="10:30:00",
    )

    # current_premium = (SC+SP)-(LC+LP) = (15.5+15.5)-(1.5+1.5) = 28.0
    # pnl_unit = 33.0 - 28.0 = 5.0; total_pnl = 5.0 * 10 * 30 = 1500 = 15% of 10000
    def ltp_side_effect(sym):
        if sym in ("SC", "SP"):
            return 15.5
        if sym in ("LC", "LP"):
            return 1.5
        return 0.0

    # bid/ask: ask_sc=ask_sp=16.0, bid_lc=bid_lp=1.0
    # fill_gross = (33.0 - (16+16-1-1)) * 300 = (33.0 - 30.0) * 300 = 900 → fee-positive
    def qb_side_effect(sym):
        if sym in ("SC", "SP"):
            return QuoteBook(symbol=sym, bid=15.0, ask=16.0, bid_qty=100, ask_qty=100)
        return QuoteBook(symbol=sym, bid=1.0, ask=2.0, bid_qty=100, ask_qty=100)

    mock_md.get_ltp.side_effect = ltp_side_effect
    mock_md.get_quote_book.side_effect = qb_side_effect
    result = s.monitor()
    assert (
        result is not None and result.get("exit_reason") == "PROFIT_HARVEST"
    ), "BANKNIFTY should harvest at 15% of max_profit (above 13% threshold) with viable fill"


def _make_bnf_position():
    """Return an IC_Position fixture for harvest fill-viability tests."""
    return IC_Position(
        instrument="BANKNIFTY",
        sc_sym="SC",
        sp_sym="SP",
        lc_sym="LC",
        lp_sym="LP",
        sc_strike=57000,
        sp_strike=55000,
        lc_strike=57100,
        lp_strike=54900,
        max_profit=17415,
        entry_credit=58.05,
        lots=10,
        entry_time="10:06:00",
    )


def test_harvest_deferred_when_bidasked_exit_would_be_fee_negative(mock_om, mock_md):
    """When mid-based premium shows profit above trigger but the fill-side (pay ask on
    shorts, receive bid on longs) would be fee-negative due to a wide spread, _harvest_fill_viable
    must detect this and defer rather than calling exit().

    Wide spread scenario: SC bid=20, ask=32 → mid=26 (shorts must be bought back at ask=32).
    Mid-based pnl=3615 (>13% of 17415) but fill_gross=(58.05-62)*300=-1185 → fee-negative."""
    mock_md.get_lot_size.return_value = settings.BANKNIFTY_LOT_SIZE
    s = IronCondorStrategy(mock_om, mock_md, "BANKNIFTY")
    s._position = _make_bnf_position()

    # Mid: SC/SP=(20+32)/2=26, LC/LP=(1+5)/2=3 → premium=46 → pnl=12.05*300=3615 > trigger ✓
    # Fill: exit_prem=(32+32)-(1+1)=62 > entry=58.05 → fill_gross=-1185 → fee-negative → DEFER ✓
    def qb_side_effect(sym):
        if sym in ("SC", "SP"):
            return QuoteBook(symbol=sym, bid=20.0, ask=32.0, bid_qty=100, ask_qty=100)
        return QuoteBook(symbol=sym, bid=1.0, ask=5.0, bid_qty=100, ask_qty=100)

    mock_md.get_quote_book.side_effect = qb_side_effect
    # Spot between strikes (sp_strike=55000 < 56000 < sc_strike=57000) so breach logic doesn't fire
    mock_md.get_ltp.return_value = 56000.0
    result = s.monitor()
    assert result is None, (
        "Harvest must be deferred when fill-side bid/ask exit is fee-negative, "
        "even though mid-based P&L shows the trigger crossed"
    )


def test_harvest_proceeds_when_bidasked_exit_is_fee_positive(mock_om, mock_md):
    """Complement of the deferral test: when both LTP threshold AND bid/ask fill-check
    pass, harvest must fire."""
    mock_md.get_lot_size.return_value = settings.BANKNIFTY_LOT_SIZE
    s = IronCondorStrategy(mock_om, mock_md, "BANKNIFTY")
    s._position = _make_bnf_position()

    # mid: SC/SP=(24+25)/2=24.5, LC/LP=(1.5+2)/2=1.75 → premium=45.5 → pnl=12.55*300=3765 > trigger
    # fill: exit_prem=(25+25)-(1.5+1.5)=47; fill_gross=(58.05-47)*300=3315 → well above fees
    def qb_side_effect(sym):
        if sym in ("SC", "SP"):
            return QuoteBook(symbol=sym, bid=24.0, ask=25.0, bid_qty=100, ask_qty=100)
        return QuoteBook(symbol=sym, bid=1.5, ask=2.0, bid_qty=100, ask_qty=100)

    mock_md.get_quote_book.side_effect = qb_side_effect
    result = s.monitor()
    assert (
        result is not None and result.get("exit_reason") == "PROFIT_HARVEST"
    ), "Harvest must fire when both LTP threshold and bid/ask fill-check are satisfied"


def test_harvest_skipped_when_quote_book_unavailable(mock_om, mock_md, caplog):
    """When get_quote_book returns None (API contamination or glitch), monitor() cannot
    compute a reliable premium and must skip the cycle rather than firing a harvest on
    unknown data. This is the safer behavior: a transient gap (30s) is acceptable;
    firing a harvest with no price data risks an exit at a stale or zero fill price."""
    import logging

    mock_md.get_lot_size.return_value = settings.BANKNIFTY_LOT_SIZE
    s = IronCondorStrategy(mock_om, mock_md, "BANKNIFTY")
    s._position = _make_bnf_position()

    mock_md.get_quote_book.return_value = None  # all legs unavailable across all retries
    with (
        patch("trading_system.core.iron_condor.time.sleep"),
        caplog.at_level(logging.WARNING, logger="trading_system.core.iron_condor"),
    ):
        result = s.monitor()

    assert result is None, "monitor() must skip when quotes are unavailable — no harvest on unknown data"
    assert any(
        "quote unavailable" in r.message.lower() for r in caplog.records
    ), "monitor() must log a WARNING when skipping due to unavailable quotes"


def test_monitor_logs_warning_when_quote_unavailable(mock_om, mock_md, caplog):
    """Regression: monitor() was silent when API contamination staled all LTPs past
    TTL (2026-05-08 — 56-minute blind spot). After fix, monitor() uses get_quote_book
    mid for premium computation; if any leg's quote is None/not tradable it must log a
    WARNING naming the affected leg(s) so operators can diagnose contamination vs hang."""
    import logging

    mock_md.get_lot_size.return_value = settings.BANKNIFTY_LOT_SIZE
    s = IronCondorStrategy(mock_om, mock_md, "BANKNIFTY")
    s._position = _make_bnf_position()

    good_book = QuoteBook(symbol="X", bid=10.0, ask=10.2, bid_qty=100, ask_qty=100)

    def qb_sc_unavailable(sym):
        if sym == "SC":
            return None  # contaminated / API gap
        return good_book

    mock_md.get_quote_book.side_effect = qb_sc_unavailable

    with (
        patch("trading_system.core.iron_condor.time.sleep"),
        caplog.at_level(logging.WARNING, logger="trading_system.core.iron_condor"),
    ):
        result = s.monitor()

    assert result is None, "monitor() must return None when any quote is unavailable"
    assert any(
        "sc" in r.message.lower() and "quote unavailable" in r.message.lower() for r in caplog.records
    ), "monitor() must log a WARNING naming the unavailable leg(s)"


def test_monitor_retries_short_leg_quote_before_skipping(mock_om, mock_md):
    """Regression: monitor() skipped immediately on first empty short-leg book, causing
    57 blind cycles in one session. After fix, it retries twice before giving up — a
    transient Shoonya API glitch clears on retry and the cycle proceeds normally."""
    good_book = QuoteBook(symbol="X", bid=10.0, ask=10.2, bid_qty=100, ask_qty=100)
    call_counts = {"sc": 0}

    def qb_flaky(sym):
        if "SC" in sym or sym == "SC":
            call_counts["sc"] += 1
            if call_counts["sc"] == 1:
                return None  # first call: API glitch
            return good_book  # retry succeeds
        return good_book

    mock_md.get_lot_size.return_value = settings.BANKNIFTY_LOT_SIZE
    mock_md.get_ltp.return_value = 10.0
    mock_md.get_quote_book.side_effect = qb_flaky
    s = IronCondorStrategy(mock_om, mock_md, "BANKNIFTY")
    s._position = _make_bnf_position()

    with patch("trading_system.core.iron_condor.time.sleep") as mock_sleep:
        result = s.monitor()

    mock_sleep.assert_called()
    assert call_counts["sc"] >= 2, "monitor() must retry the short-leg quote at least once"
    assert result is None or isinstance(result, dict), "monitor() must not crash after retry"


def test_monitor_no_phantom_pnl_from_stale_ltp_illusion(mock_om, mock_md):
    """Regression: stale-LTP illusion (2026-05-08) — monitor() computed phantom
    P&L > harvest trigger using last_valid prices from different market moments,
    creating a false peak_pnl and repeatedly deferring harvests. After fix,
    monitor() uses get_quote_book mid; when fresh bid/ask show the position is
    at a loss the harvest trigger must not fire."""
    mock_md.get_lot_size.return_value = settings.BANKNIFTY_LOT_SIZE
    s = IronCondorStrategy(mock_om, mock_md, "BANKNIFTY")
    pos = _make_bnf_position()
    s._position = pos

    # Fresh bid/ask show shorts expanded: exit_prem=70 > entry=57.10 → net loss.
    # (SC ask=38, SP ask=33, LC bid=0.5, LP bid=0.5 → exit_prem=70)
    # Mid-based premium: (37+38)/2+(32+33)/2-(0.4+0.5)/2-(0.4+0.5)/2=70.55-0.45=70.1
    def loss_books(sym):
        if sym == "SC":
            return QuoteBook(symbol=sym, bid=37.0, ask=38.0, bid_qty=100, ask_qty=100)
        if sym == "SP":
            return QuoteBook(symbol=sym, bid=32.0, ask=33.0, bid_qty=100, ask_qty=100)
        # LC and LP (longs, sold cheaply)
        return QuoteBook(symbol=sym, bid=0.4, ask=0.5, bid_qty=100, ask_qty=100)

    mock_md.get_quote_book.side_effect = loss_books
    initial_peak = pos.peak_pnl

    result = s.monitor()

    assert result is None, "monitor() must not trigger harvest when position is at a loss"
    assert pos.peak_pnl == initial_peak, "peak_pnl must not be updated when position is at a loss"


def test_monitor_no_phantom_harvest_when_ltp_shows_loss(mock_om, mock_md):
    """Regression: 2026-05-11 phantom PROFIT_HARVEST — quote-book mids at market
    open showed phantom profit (stale/wide resting orders on bp1/sp1) while LTP
    (last-trade) showed the position at a loss. monitor() fired harvest; fills
    at LTP-based prices produced net PnL=-5,594 instead of profit.

    Fix: dual-source check — both mid-based AND LTP-based PnL must clear the
    harvest trigger before exit() is called. This test verifies that when mids
    show profit >= trigger but LTP shows a loss, monitor() defers the harvest."""
    mock_md.get_lot_size.return_value = settings.BANKNIFTY_LOT_SIZE
    s = IronCondorStrategy(mock_om, mock_md, "BANKNIFTY")
    # Mirrors today's position: entry_credit=67.60, 10 lots, lot_size=30
    # max_profit = 67.60 * 10 * 30 = 20,280; trigger = 20,280 * 0.13 = 2,636.40
    s._position = IC_Position(
        instrument="BANKNIFTY",
        sc_sym="SC",
        sp_sym="SP",
        lc_sym="LC",
        lp_sym="LP",
        sc_strike=55900,
        sp_strike=54200,
        lc_strike=56000,
        lp_strike=54100,
        max_profit=20280,
        entry_credit=67.60,
        lots=10,
        entry_time="09:30:34",
    )

    # Phantom mids: stale resting orders make shorts look cheap (SC mid=25, SP mid=20)
    # and longs look near-zero (LC/LP mid=1.25).
    # mid_premium = 25+20-1.25-1.25 = 42.5
    # pnl_unit_mid = 67.60 - 42.5 = 25.1; total_pnl_mid = 25.1*300 = 7,530 > 2,636 ✓
    def phantom_qb(sym):
        if sym == "SC":
            return QuoteBook(symbol=sym, bid=20.0, ask=30.0, bid_qty=100, ask_qty=100)
        if sym == "SP":
            return QuoteBook(symbol=sym, bid=15.0, ask=25.0, bid_qty=100, ask_qty=100)
        return QuoteBook(symbol=sym, bid=0.5, ask=2.0, bid_qty=100, ask_qty=100)

    # Actual LTP (last-trade): shorts still expensive, position at a loss
    # ltp_premium = 535+480-495-445 = 75; pnl_ltp = (67.60-75)*300 = -2,220 < trigger
    def real_ltp(sym):
        if sym == "SC":
            return 535.0
        if sym == "SP":
            return 480.0
        if sym == "LC":
            return 495.0
        if sym == "LP":
            return 445.0
        return 55000.0  # spot between strikes — no breach

    mock_md.get_quote_book.side_effect = phantom_qb
    mock_md.get_ltp.side_effect = real_ltp

    result = s.monitor()
    pos = s._position  # still active (harvest deferred, not exited)

    assert result is None, (
        "Harvest must be deferred when mid-based PnL shows phantom profit "
        "but LTP-based PnL shows the position is at a loss"
    )
    assert pos.peak_pnl == 0, (
        "peak_pnl must not be updated when mid-based PnL is phantom "
        "(above trigger but LTP disagrees) — confirmed by 2026-05-11 12:36 session"
    )


def test_monitor_harvest_fires_ltp_primary_mid_confirms_direction(mock_om, mock_md):
    """Regression: 2026-05-13 — LTP-based PnL crossed the harvest trigger (₹2,880 vs
    ₹2,486 trigger) while mid-based lagged below it (₹2,137). The old code only checked
    mid-based as primary; LTP never got to trigger the harvest.

    Fix: LTP-primary path — when LTP >= trigger and mid > 0 (direction confirmed),
    harvest fires. Mid > 0 prevents harvesting when mid shows an outright loss (which
    would indicate a potentially corrupted LTP reading)."""
    mock_md.get_lot_size.return_value = settings.BANKNIFTY_LOT_SIZE
    s = IronCondorStrategy(mock_om, mock_md, "BANKNIFTY")
    s._position = _make_bnf_position()
    # max_profit=17415; trigger=17415*0.13=2263.95

    # Short legs have live, tradable books — mids show small profit (< trigger)
    # Wings have empty books → _wing_price() falls back to get_ltp()
    # SC mid=29, SP mid=27, wing LTP fallback=0.5 each
    # mid_premium = 29+27-0.5-0.5 = 55; pnl_mid = (58.05-55)*300 = 915 > 0 < 2264 ✓
    def qb_wings_empty(sym):
        if sym == "SC":
            return QuoteBook(symbol=sym, bid=28.0, ask=30.0, bid_qty=100, ask_qty=100)
        if sym == "SP":
            return QuoteBook(symbol=sym, bid=26.0, ask=28.0, bid_qty=100, ask_qty=100)
        return None  # wings → LTP fallback; also makes _harvest_fill_viable fail-open

    # LTP: shorts cheap, wings cheap → large profit signal above trigger
    # ltp_premium = 25+22-0.5-0.5 = 46; pnl_ltp = (58.05-46)*300 = 3615 >= 2264 ✓
    def real_ltp(sym):
        if sym == "SC":
            return 25.0
        if sym == "SP":
            return 22.0
        if sym in ("LC", "LP"):
            return 0.5
        return 56000.0  # spot between strikes — no breach

    mock_md.get_quote_book.side_effect = qb_wings_empty
    mock_md.get_ltp.side_effect = real_ltp

    result = s.monitor()

    assert result is not None and result.get("exit_reason") == "PROFIT_HARVEST", (
        "harvest must fire when LTP-based PnL crosses the trigger and mid-based "
        "confirms profit direction (mid > 0), even if mid is below the trigger"
    )


def test_monitor_harvest_deferred_ltp_primary_mid_shows_loss(mock_om, mock_md):
    """Direction mismatch guard: LTP >= trigger but mid-based shows outright loss (mid <= 0).
    The two sources contradict each other — harvest must be deferred to avoid acting on
    a potentially stale or corrupted LTP reading."""
    mock_md.get_lot_size.return_value = settings.BANKNIFTY_LOT_SIZE
    s = IronCondorStrategy(mock_om, mock_md, "BANKNIFTY")
    s._position = _make_bnf_position()

    # Short leg books show expensive shorts (position at a large mid-based loss)
    # SC mid=70, SP mid=65, wings LTP fallback=0.5 → mid_premium=134.0
    # pnl_mid = (58.05-134)*300 = -22,785 < 0 ✓
    def qb_expensive_shorts(sym):
        if sym == "SC":
            return QuoteBook(symbol=sym, bid=68.0, ask=72.0, bid_qty=100, ask_qty=100)
        if sym == "SP":
            return QuoteBook(symbol=sym, bid=63.0, ask=67.0, bid_qty=100, ask_qty=100)
        return None

    # LTP shows shorts cheap — large LTP-based profit above trigger
    # ltp_premium = 25+22-0.5-0.5 = 46; pnl_ltp = (58.05-46)*300 = 3615 >= 2264 ✓
    def real_ltp(sym):
        if sym == "SC":
            return 25.0
        if sym == "SP":
            return 22.0
        if sym in ("LC", "LP"):
            return 0.5
        return 56000.0

    mock_md.get_quote_book.side_effect = qb_expensive_shorts
    mock_md.get_ltp.side_effect = real_ltp

    result = s.monitor()

    assert result is None, (
        "harvest must be deferred when LTP is above trigger but mid-based shows "
        "an outright loss — sources contradict, possible stale LTP"
    )


def test_monitor_harvest_fires_when_only_wings_have_no_book(mock_om, mock_md):
    """Regression: monitor() skipped every cycle when lc/lp had empty books even
    though sc/sp were live and the position was above the harvest trigger.
    After fix, missing wing books fall back to get_ltp() and harvest can fire.
    (Root cause: 2026-05-11 — lp/lc routinely bid=0,ask=0 throughout session.)"""
    mock_md.get_lot_size.return_value = settings.BANKNIFTY_LOT_SIZE
    s = IronCondorStrategy(mock_om, mock_md, "BANKNIFTY")
    s._position = _make_bnf_position()

    def qb_wings_empty(sym):
        if sym in ("SC", "SP"):
            return QuoteBook(symbol=sym, bid=24.0, ask=25.0, bid_qty=100, ask_qty=100)
        return QuoteBook(symbol=sym, bid=0.0, ask=0.0, bid_qty=0, ask_qty=0)

    def ltp_side(sym):
        if sym in ("SC", "SP"):
            return 24.5
        if sym in ("LC", "LP"):
            return 1.5
        return 56000.0

    mock_md.get_quote_book.side_effect = qb_wings_empty
    mock_md.get_ltp.side_effect = ltp_side
    result = s.monitor()
    assert result is not None and result.get("exit_reason") == "PROFIT_HARVEST"


def test_monitor_harvest_deferred_when_wings_missing_and_ltp_zero(mock_om, mock_md):
    """When wing books are empty AND get_ltp() returns 0 for wings, the ltp_missing
    branch in the dual-source guard defers harvest conservatively. monitor() must
    not crash and must not fire harvest on incomplete data."""
    mock_md.get_lot_size.return_value = settings.BANKNIFTY_LOT_SIZE
    s = IronCondorStrategy(mock_om, mock_md, "BANKNIFTY")
    s._position = _make_bnf_position()

    def qb_wings_empty(sym):
        if sym in ("SC", "SP"):
            return QuoteBook(symbol=sym, bid=24.0, ask=25.0, bid_qty=100, ask_qty=100)
        return QuoteBook(symbol=sym, bid=0.0, ask=0.0, bid_qty=0, ask_qty=0)

    def ltp_zero_wings(sym):
        if sym in ("SC", "SP"):
            return 24.5
        if sym == "NSE|Nifty Bank":
            return 56000.0  # spot between strikes — no breach
        return 0.0  # wings have no LTP — ltp_missing guard should defer

    mock_md.get_quote_book.side_effect = qb_wings_empty
    mock_md.get_ltp.side_effect = ltp_zero_wings
    result = s.monitor()
    assert result is None  # deferred via ltp_missing, not crashed


def test_peak_pnl_updated_only_after_ltp_confirmation(mock_om, mock_md):
    """When both mid-based and LTP-based PnL clear the harvest trigger, peak_pnl
    must be recorded before the fill-viability check. Complement of the phantom
    test: confirmed profit must update the peak."""
    mock_md.get_lot_size.return_value = settings.BANKNIFTY_LOT_SIZE
    s = IronCondorStrategy(mock_om, mock_md, "BANKNIFTY")
    pos = _make_bnf_position()
    s._position = pos

    # mid: SC/SP=(24+25)/2=24.5, LC/LP=(1.5+2)/2=1.75 → premium=45.5
    # pnl_unit=58.05-45.5=12.55; total_pnl=12.55*300=3765 > trigger(2732) ✓
    def qb_side(sym):
        if sym in ("SC", "SP"):
            return QuoteBook(symbol=sym, bid=24.0, ask=25.0, bid_qty=100, ask_qty=100)
        return QuoteBook(symbol=sym, bid=1.5, ask=2.0, bid_qty=100, ask_qty=100)

    # LTP confirms: ltp_premium=24.5+24.5-1.5-1.5=46; pnl_ltp=(58.05-46)*300=3615 > trigger ✓
    def ltp_side(sym):
        if sym in ("SC", "SP"):
            return 24.5
        if sym in ("LC", "LP"):
            return 1.5
        return 56000.0

    mock_md.get_quote_book.side_effect = qb_side
    mock_md.get_ltp.side_effect = ltp_side
    s.monitor()
    assert pos.peak_pnl > 0, "peak_pnl must be updated when both sources confirm profit above trigger"


def test_sr_cap_clamps_wide_range_banknifty_to_liquid_strikes(mock_om, mock_md):
    """LIVE-29: when 20-day range forces SC far from spot, cap to IC_SR_CAP_OTM_FROM_SPOT.
    Cap=1000 clamps SC=55000, SP=53000; entry succeeds at viable credit.
    VIX=18 (NORMAL tier): step=100, OTM=200 → floor widens to 700pt: SC=54700, SP=53300,
    LC=54800, LP=53200."""
    mock_md.get_lot_size.return_value = settings.BANKNIFTY_LOT_SIZE
    s = IronCondorStrategy(mock_om, mock_md, "BANKNIFTY")

    mock_md.get_ltp.side_effect = lambda sym: (
        23.0 if sym.endswith(("C54700", "P53300")) else 5.0 if sym.endswith(("C54800", "P53200")) else 0.1
    )
    success = s.enter(54000, 18.0, "19-MAR-2026", 10)
    assert success is True, "entry must succeed at viable credit"
    assert mock_om.place_order.call_count == 4


def test_sr_cap_disabled_wide_range_banknifty_fails_on_illiquid(monkeypatch, mock_om, mock_md):
    """Regression proof: without the floor, illiquid near-zero credit strikes are refused."""
    mock_md.get_lot_size.return_value = settings.BANKNIFTY_LOT_SIZE
    s = IronCondorStrategy(mock_om, mock_md, "BANKNIFTY")

    mock_md.get_ltp.side_effect = lambda sym: 0.1
    success = s.enter(54000, 18.0, "19-MAR-2026", 10)
    assert success is False, "illiquid near-zero credit must fail credit floor"
    assert mock_om.place_order.call_count == 0


def test_banknifty_otm_floor_widens_strikes(mock_om, mock_md):
    """VIX tier gives initial OTM; floor widens strikes to IC_MIN_OTM_BANKNIFTY.
    Scenario: spot=54000, VIX=18 (NORMAL tier, OTM=200). Floor=700pt widens:
    SC=54700, SP=53300."""
    mock_md.get_lot_size.return_value = settings.BANKNIFTY_LOT_SIZE
    s = IronCondorStrategy(mock_om, mock_md, "BANKNIFTY")

    spot = 54000.0
    sc, sp, lc, lp = s.calculate_strikes(spot=spot, vix=18.0)

    floor = settings.IC_MIN_OTM_BANKNIFTY  # 700
    assert sc >= spot + floor, f"SC {sc} must be >= spot+700={spot+floor}"
    assert sp <= spot - floor, f"SP {sp} must be <= spot-700={spot-floor}"


def test_banknifty_not_blocked_by_nifty_min_vix(mock_om, mock_md):
    """LIVE-30: IC_NIFTY_MIN_VIX must gate only NIFTY; BANKNIFTY enters normally at VIX=12."""
    mock_md.get_lot_size.return_value = settings.BANKNIFTY_LOT_SIZE
    s = IronCondorStrategy(mock_om, mock_md, "BANKNIFTY")

    # BANKNIFTY VIX=12 LOW tier: OTM=150 → floor widens to 700pt: SC=50700, SP=49300, LC=50800, LP=49200
    mock_md.get_ltp.side_effect = lambda sym: (
        23.0 if sym.endswith(("C50700", "P49300")) else 5.0 if sym.endswith(("C50800", "P49200")) else 10.0
    )
    success = s.enter(50000, 12.0, "19-MAR-2026", 10)
    assert success is True
    assert mock_om.place_order.call_count == 4


def test_exit_result_uses_gross_pnl_not_pnl(mock_om, mock_md, tmp_path):
    """Regression: exit result dict must use 'gross_pnl' key so TradeLogger
    writes it to paper_trades.csv.  The bug had 'pnl' which is silently
    ignored by log_trade() because TRADE_COLUMNS expects 'gross_pnl'."""
    import csv

    from trading_system.core.trade_logger import TRADE_COLUMNS, TradeLogger

    assert "gross_pnl" in TRADE_COLUMNS
    assert "pnl" not in TRADE_COLUMNS

    s = IronCondorStrategy(mock_om, mock_md, "NIFTY")
    s._position = IC_Position(
        instrument="NIFTY",
        sc_sym="SC",
        sp_sym="SP",
        lc_sym="LC",
        lp_sym="LP",
        sc_strike=22150,
        sp_strike=21850,
        lc_strike=22200,
        lp_strike=21800,
        max_profit=1000,
        entry_credit=20.0,
        lots=2,
        entry_time="10:00:00",
    )

    mock_md.get_ltp.side_effect = lambda sym: 10.0 if sym in ("SC", "SP") else 2.5 if sym in ("LC", "LP") else 0.0

    result = s.force_exit()
    assert result is not None
    assert "gross_pnl" in result, "exit result must contain 'gross_pnl' for TradeLogger"
    assert "pnl" not in result, "exit result must NOT contain 'pnl' — that key is never written to CSV"
    assert result["gross_pnl"] == 650.0
    assert result["net_pnl"] == 650.0

    logger = TradeLogger(data_dir=str(tmp_path))
    trade_id = logger.log_trade(result)
    assert trade_id != ""

    with open(tmp_path / "paper_trades.csv", newline="") as f:
        reader = csv.DictReader(f)
        row = next(reader)

    assert row["gross_pnl"] == "650.0", f"gross_pnl not written to CSV, got: {row.get('gross_pnl')!r}"
    assert "pnl" not in row, "CSV row must not contain a 'pnl' column"
