"""
Iron Condor Strategist — implements the core Nifty/BankNifty strategist logic (agents.md).

- VIX-adaptive strikes
- 20-day S/R buffers
- 1% Profit Harvest + Re-entry cycle
- Breach adjustment logic
"""

import logging
import re
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple, List
from datetime import datetime

from trading_system.config import settings
from trading_system.core.margin import estimate_ic_required_margin
from trading_system.live.live_order_manager import OrderPollingAbandoned, persist_stuck_legs

logger = logging.getLogger(__name__)

# SHAKEDOWN-03b (loose semantics): exit reasons that mark the next enter()
# call as a continuation of the current trading session (a "re-entry"), not
# a fresh session start. Re-entries do not consume the per-session entry cap,
# so the harvest-and-re-enter loop can run unlimited within a single day.
# FORCE_EXIT and any future stop-loss reason are intentionally excluded —
# entries after those should be refused if the cap is consumed.
_RE_ENTRY_EXIT_REASONS = ("PROFIT_HARVEST", "ADJUSTMENT_REQUIRED")

_EXPIRY_RE = re.compile(r'(\d{2})([A-Z]{3})(\d{2})', re.IGNORECASE)
_MONTH_MAP = {
    "JAN": "01", "FEB": "02", "MAR": "03", "APR": "04",
    "MAY": "05", "JUN": "06", "JUL": "07", "AUG": "08",
    "SEP": "09", "OCT": "10", "NOV": "11", "DEC": "12",
}

@dataclass
class IC_Position:
    instrument: str # 'NIFTY' | 'BANKNIFTY'
    sc_sym: str
    sp_sym: str
    lc_sym: str
    lp_sym: str
    sc_strike: float
    sp_strike: float
    lc_strike: float
    lp_strike: float
    max_profit: float
    entry_credit: float
    lots: int
    entry_time: str
    peak_pnl: float = 0.0
    # BUG-18 / Axiom 2: ISO date "YYYY-MM-DD" of this IC's expiry. Empty string
    # is tolerated only to support loading state saved before this field existed.
    expiry_date: str = ""
    # ISO date "YYYY-MM-DD" of when the IC was entered (may differ from exit date
    # for positions carried overnight).
    entry_date: str = ""

    def to_dict(self) -> Dict:
        return {k: getattr(self, k) for k in self.__dataclass_fields__}

    @staticmethod
    def _infer_expiry_from_symbol(sym: str) -> str:
        """Parse expiry ISO date from a symbol like 'NFO|NIFTY21APR26C24350'."""
        m = _EXPIRY_RE.search(sym.upper())
        if not m:
            return ""
        day, mon, yr2 = m.groups()
        month_num = _MONTH_MAP.get(mon.upper(), "")
        if not month_num:
            return ""
        return f"20{yr2}-{month_num}-{day}"

    @classmethod
    def from_dict(cls, d: Dict) -> "IC_Position":
        obj = cls(**{k: d[k] for k in cls.__dataclass_fields__ if k in d})
        if not obj.expiry_date:
            obj.expiry_date = cls._infer_expiry_from_symbol(obj.sc_sym)
        return obj

class IronCondorStrategy:
    def __init__(self, order_manager: Any, market_data: Any, instrument: str = 'NIFTY'):
        self.om = order_manager
        self.md = market_data
        self.instrument = instrument
        self._position: Optional[IC_Position] = None
        # BUG-05: legs whose rollback reverse-orders failed; drained by main.py after enter().
        self._last_rollback_stuck_legs: List[Dict] = []
        # SHAKEDOWN-03a: per-(instrument, IST date) successful-entry counter,
        # used only when settings.SHAKEDOWN_MODE=True. Persisted via
        # save_state/restore_state and re-keyed on IST date so a crash-restart
        # mid-session preserves the budget; a next-day restart resets it.
        self._entries_today_count = 0
        self._entries_today_date = ""
        # SHAKEDOWN-03b (loose): exit reason + IST date of the most recent
        # close, used to identify whether the next enter() is a fresh session
        # start (consumes the cap) or a harvest/adjustment re-entry (does not).
        self._last_exit_reason = ""
        self._last_exit_date = ""
        # Phase-5b partial-fill streak counter. A single partial-fill is bid
        # drift, not a halt event — skip the cycle and retry on the next
        # signal. Only a sustained streak (settings.IC_PARTIAL_FAIL_CAP
        # consecutive) suspends same-day adjustment re-entries, bounding
        # unwind-slippage exposure. Counter and suspension flag reset on a
        # successful entry (proves liquidity is fine again).
        self._consecutive_partial_fails = 0
        # Set True once the partial-fail cap is hit; blocks same-day adjustment
        # re-entries without halting the full session (only 3× stop/daily-loss
        # halts the session per the no-halt-below-top-stop rule).
        self._phase5b_suspended = False
        if settings.SHAKEDOWN_MODE:
            logger.info(
                "SHAKEDOWN: %s entry counter init=0 (cap=%d/day fresh entries; "
                "harvest/adjustment re-entries unlimited; persisted across crash-restart).",
                self.instrument, settings.IC_MAX_ENTRIES_PER_SESSION_SHAKEDOWN,
            )

    def _min_credit(self) -> float:
        """Per-instrument minimum net credit per lot, calibrated against the
        LIVE-12 cost stack (see docs/calibration_2026_04_26.md)."""
        return settings.IC_MIN_CREDIT_BY_INSTRUMENT[self.instrument]

    def is_active(self) -> bool:
        return self._position is not None

    def _roll_daily_entry_counter(self) -> str:
        """Reset the entry counter on IST date rollover; return today's date string."""
        today = datetime.now().date().isoformat()
        if today != self._entries_today_date:
            self._entries_today_date = today
            self._entries_today_count = 0
        return today

    def _is_re_entry(self) -> bool:
        """SHAKEDOWN-03b (loose): True if the next enter() call follows a same-day
        harvest or adjustment exit — i.e., a continuation of the current trading
        session that should not consume the daily fresh-entry cap. False after a
        stop-loss / FORCE_EXIT / no-prior-exit, in which case the cap rules normally."""
        if not self._last_exit_reason or not self._last_exit_date:
            return False
        today = datetime.now().date().isoformat()
        if self._last_exit_date != today:
            return False
        return self._last_exit_reason in _RE_ENTRY_EXIT_REASONS

    def _check_session_entry_cap(self) -> bool:
        """SHAKEDOWN: returns True if a new entry is allowed under the per-session cap.

        Active only when settings.SHAKEDOWN_MODE=True; otherwise always True.
        Loose semantics (SHAKEDOWN-03b): the cap counts FRESH entries only.
        Same-day harvest or adjustment re-entries bypass the cap so the
        harvest-and-re-enter loop runs as in steady state. Once the fresh-entry
        cap is hit and no qualifying re-entry signal is set, further entries
        are refused for the rest of the IST day.
        """
        if not settings.SHAKEDOWN_MODE:
            return True
        today = self._roll_daily_entry_counter()
        if self._is_re_entry():
            return True
        cap = settings.IC_MAX_ENTRIES_PER_SESSION_SHAKEDOWN
        if self._entries_today_count >= cap:
            logger.info(
                "IC_REJECT reason=SHAKEDOWN_ENTRY_CAP instrument=%s count=%d cap=%d "
                "date=%s last_exit=%s — proving-period fresh-entry cap reached; "
                "no qualifying harvest/adjustment re-entry signal.",
                self.instrument, self._entries_today_count, cap, today,
                self._last_exit_reason or "(none)",
            )
            return False
        return True

    def _record_session_entry(self) -> None:
        """SHAKEDOWN: increment the fresh-entry counter on a successful entry.
        Loose semantics: harvest/adjustment re-entries do not bump the counter."""
        if not settings.SHAKEDOWN_MODE:
            return
        self._roll_daily_entry_counter()
        if self._is_re_entry():
            return
        self._entries_today_count += 1

    @staticmethod
    def _opposite_side(side: str) -> str:
        return "BUY" if side == "SELL" else "SELL"

    def _rollback_partial_entry(self, placed_orders: List[Dict]) -> None:
        """Flatten any filled entry legs when entry orchestration fails.

        Uses fill_qty from each order so partial fills are reversed correctly.
        Axiom 5: tracker state unwound for rolled-back legs. Axiom 3+4 (BUG-05):
        legs whose reverse also fails are recorded in _last_rollback_stuck_legs.
        """
        stuck: List[Dict] = []
        for order in reversed(placed_orders):
            fq = order.get("fill_qty", 0)
            if fq == 0:
                continue
            symbol = order.get("symbol")
            side = order.get("side")
            if not symbol or side not in ("BUY", "SELL"):
                continue
            rollback_side = self._opposite_side(side)
            rollback = self.om.place_order(symbol, rollback_side, fq, track_position=False)
            if rollback.get("fill_qty", 0) < fq:
                logger.error(
                    "IC %s rollback failed for %s %s qty=%d (status=%s, reason=%s)",
                    self.instrument,
                    rollback_side,
                    symbol,
                    fq,
                    rollback.get("status"),
                    rollback.get("reason", ""),
                )
                stuck.append({
                    "symbol": symbol,
                    "original_side": side,
                    "rollback_side": rollback_side,
                    "intended_qty": fq,
                    "reversed_qty": rollback.get("fill_qty", 0),
                    "reason": rollback.get("reason", ""),
                })
                continue
            tracker = getattr(self.om, "tracker", None)
            if tracker is not None:
                try:
                    tracker.close_position(symbol, rollback.get("fill_price", 0.0))
                except Exception:
                    logger.exception("IC %s tracker unwind failed for rollback of %s", self.instrument, symbol)
        self._last_rollback_stuck_legs = stuck
        if stuck:
            persist_stuck_legs(stuck)

    def _handle_partial_fill_halt(self, placed_orders: List[Dict]) -> None:
        """LIVE-05: reverse partial fills and ALWAYS escalate to halt.

        A partial fill during entry is a liquidity-stress signal — the next
        cycle is likely to hit the same book condition. Per LIVE-05's spec,
        this is treated as a leg-failure event that demands operator
        attention, not a silent retry. Even when every reversal reports
        ``fill_qty == requested``, the halt sentinel is recorded so
        ``main.py``'s drain escalates to ``risk.escalate_rollback_failure``
        and the LIVE-23 critical alert fires.
        """
        # Snapshot of the partial-fill event itself, regardless of how the
        # reversal attempts go.
        partial_legs_info = [
            {
                "symbol": o.get("symbol"),
                "side": o.get("side"),
                "fill_qty": o.get("fill_qty", 0),
                "requested_qty": o.get("quantity", 0),
                "status": o.get("status"),
            }
            for o in placed_orders
            if o.get("fill_qty", 0) > 0
        ]

        stuck: List[Dict] = []
        for order in reversed(placed_orders):
            fq = order.get("fill_qty", 0)
            if fq == 0:
                continue
            symbol = order.get("symbol")
            side = order.get("side")
            if not symbol or side not in ("BUY", "SELL"):
                continue
            reverse_side = self._opposite_side(side)
            reverse = self.om.place_order(symbol, reverse_side, fq, track_position=False)
            reversed_qty = reverse.get("fill_qty", 0)
            if reversed_qty < fq:
                logger.error(
                    "IC %s partial-fill reversal incomplete for %s %s: reversed %d of %d",
                    self.instrument, reverse_side, symbol, reversed_qty, fq,
                )
                stuck.append({
                    "symbol": symbol,
                    "original_side": side,
                    "intended_qty": fq,
                    "reversed_qty": reversed_qty,
                    "reason": "partial_fill_reversal_incomplete",
                })
            else:
                tracker = getattr(self.om, "tracker", None)
                if tracker is not None:
                    try:
                        tracker.close_position(symbol, reverse.get("fill_price", 0.0))
                    except Exception:
                        logger.exception("IC %s tracker unwind failed after partial fill of %s", self.instrument, symbol)

        event_record = {
            "symbol": "(entry-partial-fill-event)",
            "reason": "partial_fill_during_entry",
            "partial_legs": partial_legs_info,
            "reversal_incomplete_count": len(stuck),
        }
        self._last_rollback_stuck_legs = [event_record] + stuck
        persist_stuck_legs(self._last_rollback_stuck_legs)

    def _log_credit_rejection(
        self,
        expiry: str,
        spot: float,
        vix: float,
        strikes: Tuple[float, float, float, float],
        symbols: Tuple[str, str, str, str],
        prices: Dict[str, float],
        net_credit_unit: float,
        min_credit: float,
        lots: int,
        lot_size: int,
    ) -> None:
        """Emit detailed machine-friendly and human-readable credit rejection logs."""
        sc, sp, lc, lp = strikes
        sc_sym, sp_sym, lc_sym, lp_sym = symbols
        width = abs(lc - sc)
        credit_gap = min_credit - net_credit_unit
        logger.info(
            "IC_REJECT reason=CREDIT_BELOW_MIN instrument=%s expiry=%s spot=%.2f vix=%.2f "
            "sc=%.2f sp=%.2f lc=%.2f lp=%.2f width=%.2f "
            "credit=%.2f min_credit=%.2f gap=%.2f lots=%s lot_size=%s qty=%s "
            "sc_ltp=%.2f sp_ltp=%.2f lc_ltp=%.2f lp_ltp=%.2f "
            "sc_sym=%s sp_sym=%s lc_sym=%s lp_sym=%s",
            self.instrument,
            expiry,
            spot,
            vix,
            sc,
            sp,
            lc,
            lp,
            width,
            net_credit_unit,
            min_credit,
            credit_gap,
            lots,
            lot_size,
            lots * lot_size,
            prices["sc"],
            prices["sp"],
            prices["lc"],
            prices["lp"],
            sc_sym,
            sp_sym,
            lc_sym,
            lp_sym,
        )

    # ── Helpers ─────────────────────────────────────────────────────────

    def _pre_entry_margin_ok(
        self,
        wing_width: float,
        lot_size: int,
        lots: int,
        qty: int,
        net_credit_unit: float,
    ) -> bool:
        """LIVE-10: reject entry upfront when broker's available margin
        falls below the IC's max-loss × buffer. Prevents a leg-3/leg-4
        mid-entry rejection that would cascade into LIVE-03's rollback
        path. Paper mode's order manager reports float('inf') so the
        check is a no-op there."""
        required = estimate_ic_required_margin(
            wing_width=wing_width,
            lot_size=lot_size,
            lots=lots,
            net_credit_unit=net_credit_unit,
        )
        try:
            available = self.om.get_available_margin()
        except Exception:
            logger.exception(
                "IC %s margin query raised; refusing entry (safety over continuity)",
                self.instrument,
            )
            return False
        if available < required:
            logger.error(
                "IC_REJECT reason=INSUFFICIENT_MARGIN instrument=%s required=%.2f "
                "available=%.2f wing_width=%.2f credit=%.2f qty=%d lots=%d lot_size=%d",
                self.instrument, required, available,
                wing_width, net_credit_unit, qty, lots, lot_size,
            )
            return False
        return True

    def get_vix_tier_params(self, vix: float) -> Tuple[int, int]:
        """Returns (OTM_distance, spread_width) based on VIX tiers."""
        if vix < settings.VIX_LOW_LIMIT:
            return settings.VIX_LOW_OTM, settings.VIX_LOW_WIDTH
        if vix < settings.VIX_NORMAL_LIMIT:
            return settings.VIX_NORMAL_OTM, settings.VIX_NORMAL_WIDTH
        return settings.VIX_HIGH_OTM, settings.VIX_HIGH_WIDTH

    def calculate_strikes(self, spot: float, vix: float, sr_high: float, sr_low: float, sr_manager: Any) -> Tuple[float, float, float, float]:
        """Calculates 4 strikes (SC, SP, LC, LP) based on VIX and S/R buffer."""
        otm_dist, width = self.get_vix_tier_params(vix)
        step = settings.NIFTY_STRIKE_STEP if self.instrument == 'NIFTY' else settings.BANKNIFTY_STRIKE_STEP
        
        # Initial OTM strikes
        sc = round((spot + otm_dist) / step) * step
        sp = round((spot - otm_dist) / step) * step
        
        # Apply 20-day S/R Buffer (50 points)
        sc = sr_manager.apply_buffer(sc, sr_high, sr_low, 'CE', step=step)
        sp = sr_manager.apply_buffer(sp, sr_high, sr_low, 'PE', step=step)

        # Define Wings
        # Ensure width is at least the strike step and a multiple of it
        actual_width = max(round(width / step) * step, step)
        lc = sc + actual_width
        lp = sp - actual_width
        
        return sc, sp, lc, lp

    # ── Entry ───────────────────────────────────────────────────────────

    def enter(self, spot: float, vix: float, sr_high: float, sr_low: float, sr_manager: Any, expiry: str, lots: int) -> bool:
        # LIVE-25 dispatch: operator flips settings.IC_ENTRY_MODE to 'hedge_first'
        # to route through enter_hedge_first (wings-as-MKT then shorts-as-LMT).
        # Default stays 'sequential' (the legacy path) until the proving period
        # validates the new path on real money.
        if settings.IC_ENTRY_MODE == "hedge_first":
            return self.enter_hedge_first(spot, vix, sr_high, sr_low, sr_manager, expiry, lots)

        # SHAKEDOWN: refuse new entries beyond the per-session cap.
        if not self._check_session_entry_cap():
            return False

        sc, sp, lc, lp = self.calculate_strikes(spot, vix, sr_high, sr_low, sr_manager)

        sc_sym = self.om.build_option_symbol(self.instrument, expiry, sc, "CE")
        sp_sym = self.om.build_option_symbol(self.instrument, expiry, sp, "PE")
        lc_sym = self.om.build_option_symbol(self.instrument, expiry, lc, "CE")
        lp_sym = self.om.build_option_symbol(self.instrument, expiry, lp, "PE")

        prices = {
            'sc': self.md.get_ltp(sc_sym),
            'sp': self.md.get_ltp(sp_sym),
            'lc': self.md.get_ltp(lc_sym),
            'lp': self.md.get_ltp(lp_sym)
        }

        if any(p <= 0 for p in prices.values()):
            logger.warning(f"IC {self.instrument}: Could not fetch LTP for all legs. Skipping entry.")
            return False

        # Calculate Max Profit & Net Credit
        # Max profit of spread = Net Credit collected
        # For IC, max profit = (Collected Premium) * LotSize
        net_credit_unit = (prices['sc'] + prices['sp']) - (prices['lc'] + prices['lp'])
        
        # Credit Rule: net_credit >= per-instrument min credit floor
        width = abs(lc - sc)
        min_credit = self._min_credit()
        if net_credit_unit < min_credit:
            lot_size = self.md.get_lot_size(sc_sym)
            self._log_credit_rejection(
                expiry=expiry,
                spot=spot,
                vix=vix,
                strikes=(sc, sp, lc, lp),
                symbols=(sc_sym, sp_sym, lc_sym, lp_sym),
                prices=prices,
                net_credit_unit=net_credit_unit,
                min_credit=min_credit,
                lots=lots,
                lot_size=lot_size,
            )
            logger.info(f"IC {self.instrument}: FAILED Credit Rule (Credit {net_credit_unit:.2f} < Min {min_credit})")
            return False

        # Get actual lot size from master via market data
        lot_size = self.md.get_lot_size(sc_sym)
        qty = lots * lot_size
        max_profit = net_credit_unit * qty

        # LIVE-13: reject if per-leg qty exceeds NSE freeze limit, which would
        # rejection-cascade leg 3 or 4 mid-entry and land us in LIVE-03's
        # rollback path. Prevention is cheap; detection mid-entry is expensive.
        freeze_qty = (
            settings.FREEZE_QTY_NIFTY if self.instrument == 'NIFTY'
            else settings.FREEZE_QTY_BANKNIFTY
        )
        if qty > freeze_qty:
            logger.error(
                "IC_REJECT reason=FREEZE_QTY_BREACH instrument=%s qty=%d freeze_qty=%d "
                "lots=%d lot_size=%d. Reduce IC_LOT_SIZE or split the entry.",
                self.instrument, qty, freeze_qty, lots, lot_size,
            )
            return False

        # LIVE-10: refuse if broker's available margin is below the IC's
        # max-loss × buffer. Paper mode reports infinite margin.
        if not self._pre_entry_margin_ok(
            wing_width=width,
            lot_size=lot_size,
            lots=lots,
            qty=qty,
            net_credit_unit=net_credit_unit,
        ):
            return False

        # Legs interleaved as short+wing pairs so any mid-entry failure leaves
        # a capped spread (call spread or put spread) rather than a naked strangle.
        orders_to_place = [
            (sc_sym, "SELL"),
            (lc_sym, "BUY"),
            (sp_sym, "SELL"),
            (lp_sym, "BUY"),
        ]
        placed_orders: List[Dict] = []
        for symbol, side in orders_to_place:
            order = self.om.place_order(symbol, side, qty)
            placed_orders.append(order)
            status = order.get("status")
            fill_qty = order.get("fill_qty", 0)

            if status == "COMPLETE":
                continue

            if status == "REJECTED" or (status == "CANCELED" and fill_qty == 0):
                logger.error(
                    "IC %s entry aborted: leg %s %s clean-rejected (status=%s, reason=%s)",
                    self.instrument, side, symbol, status, order.get("reason", ""),
                )
                self._rollback_partial_entry(placed_orders)
                return False

            # CANCELED with a partial fill — reverse what filled, then halt.
            logger.error(
                "IC %s entry aborted: leg %s %s partial fill %d/%d",
                self.instrument, side, symbol, fill_qty, qty,
            )
            self._handle_partial_fill_halt(placed_orders)
            return False

        try:
            expiry_iso = datetime.strptime(str(expiry).strip(), "%d-%b-%Y").date().isoformat()
        except (ValueError, TypeError):
            expiry_iso = ""
            logger.warning("IC %s could not parse expiry '%s' to ISO date", self.instrument, expiry)
        now = datetime.now()
        self._position = IC_Position(
            instrument=self.instrument,
            sc_sym=sc_sym, sp_sym=sp_sym, lc_sym=lc_sym, lp_sym=lp_sym,
            sc_strike=sc, sp_strike=sp, lc_strike=lc, lp_strike=lp,
            max_profit=max_profit, entry_credit=net_credit_unit,
            lots=lots, entry_time=now.strftime("%H:%M:%S"),
            expiry_date=expiry_iso,
            entry_date=now.strftime("%Y-%m-%d"),
        )
        logger.info(f"IC {self.instrument} ENTERED: SC={sc} SP={sp} LC={lc} LP={lp} | Credit={net_credit_unit:.2f} | Lots={lots} (LotSize={lot_size})")
        self._record_session_entry()
        return True

    # ── LIVE-25: Hedge-first entry ──────────────────────────────────────

    def enter_hedge_first(
        self, spot: float, vix: float, sr_high: float, sr_low: float,
        sr_manager: Any, expiry: str, lots: int,
    ) -> bool:
        """
        LIVE-25: hedge-first IC entry. Wings (LC+LP) go as MKT first; shorts
        (SC+SP) as LMT at top-of-book bid. Converts the worst-case entry
        failure from unbounded naked short to bounded premium-at-risk.

        Returns True on successful 4-leg entry; False on any abort (wings
        failed, Phase 3 credit-infeasible, shorts didn't fill, or post-fill
        credit under floor). Unwinds cleanly in every failure path.
        """
        # SHAKEDOWN: refuse new entries beyond the per-session cap.
        if not self._check_session_entry_cap():
            return False

        if self._phase5b_suspended and self._is_re_entry():
            logger.warning(
                "IC %s Phase-5b adjustments suspended after cap; skipping re-entry.",
                self.instrument,
            )
            return False

        sc, sp, lc, lp = self.calculate_strikes(spot, vix, sr_high, sr_low, sr_manager)
        sc_sym = self.om.build_option_symbol(self.instrument, expiry, sc, "CE")
        sp_sym = self.om.build_option_symbol(self.instrument, expiry, sp, "PE")
        lc_sym = self.om.build_option_symbol(self.instrument, expiry, lc, "CE")
        lp_sym = self.om.build_option_symbol(self.instrument, expiry, lp, "PE")

        # LIVE-06 consumer: real bid/ask visibility rather than LTP guesses.
        books = {
            "sc": self.md.get_quote_book(sc_sym),
            "sp": self.md.get_quote_book(sp_sym),
            "lc": self.md.get_quote_book(lc_sym),
            "lp": self.md.get_quote_book(lp_sym),
        }
        for leg, book in books.items():
            if book is None or not book.is_tradable:
                logger.warning(
                    "IC %s hedge-first: leg %s book untradable (book=%s) — refusing entry",
                    self.instrument, leg, book,
                )
                return False

        lot_size = self.md.get_lot_size(sc_sym)
        qty = lots * lot_size

        # LIVE-13: freeze-qty guard mirrors the legacy enter() path.
        freeze_qty = (
            settings.FREEZE_QTY_NIFTY if self.instrument == "NIFTY"
            else settings.FREEZE_QTY_BANKNIFTY
        )
        if qty > freeze_qty:
            logger.error(
                "IC_REJECT reason=FREEZE_QTY_BREACH instrument=%s qty=%d freeze_qty=%d",
                self.instrument, qty, freeze_qty,
            )
            return False

        # LIVE-10: hedge-first also refuses on insufficient margin. Credit
        # used here is the bid/ask-book estimate (same signal the credit
        # floor check uses below) — available at this point, pre-fill.
        wing_width_hf = abs(lc - sc)
        pre_entry_credit_for_margin = (
            books["sc"].bid + books["sp"].bid
            - books["lc"].ask - books["lp"].ask
        )
        if not self._pre_entry_margin_ok(
            wing_width=wing_width_hf,
            lot_size=lot_size,
            lots=lots,
            qty=qty,
            net_credit_unit=pre_entry_credit_for_margin,
        ):
            return False

        # Pre-entry credit sanity — use bid/ask mids instead of LTP (LIVE-06).
        # Shorts at bid (we sell into bid); wings at ask (we buy from ask).
        pre_entry_credit_unit = (
            books["sc"].bid + books["sp"].bid
            - books["lc"].ask - books["lp"].ask
        )
        min_credit = self._min_credit()
        if pre_entry_credit_unit < min_credit:
            logger.info(
                "IC %s hedge-first: pre-entry book credit %.2f < min_credit %.2f "
                "(SC_bid=%.2f SP_bid=%.2f LC_ask=%.2f LP_ask=%.2f)",
                self.instrument, pre_entry_credit_unit, min_credit,
                books["sc"].bid, books["sp"].bid, books["lc"].ask, books["lp"].ask,
            )
            return False

        # ── Phase 1: submit wings as MKT ─────────────────────────────
        # OrderPollingAbandoned (from LiveOrderManager.await_terminal_status
        # giving up after MAX_POLL_ERRORS) means we can't determine fill
        # status. Persist stuck-legs metadata so startup-reconcile (LIVE-07)
        # picks up whatever the broker actually has on next start, then let
        # the exception propagate to main.py's cycle handler for halt+alert.
        # Pass QuoteBook prices as `price=` fallback for every place_order in
        # the entry path. paper_order_manager substitutes ltp ← price when
        # get_ltp returns 0 (junk-quote chain exhausted). Without this, a fresh
        # strike whose broker quote is bogus halts entry — incident 2026-04-27
        # 11:20:19 (BANKNIFTY C57700 LTP=56130, no last-valid cache → MKT BUY
        # rejected → Phase-2 halt). Books were validated tradable at line 580.
        lc_order = None
        lp_order = None
        try:
            lc_order = self.om.place_order(lc_sym, "BUY", qty, price=books["lc"].ask)
            lp_order = self.om.place_order(lp_sym, "BUY", qty, price=books["lp"].ask)
        except OrderPollingAbandoned as exc:
            stuck = []
            if lc_order is None:
                stuck.append({"symbol": lc_sym, "side": "BUY", "qty": qty, "phase": "1_wings", "reason": "polling_abandoned_pre_status"})
            else:
                stuck.append({"symbol": lc_sym, "side": "BUY", "qty": qty, "phase": "1_wings", "reason": "polling_abandoned_after_lc_returned", "lc_status": lc_order.get("status")})
            stuck.append({"symbol": lp_sym, "side": "BUY", "qty": qty, "phase": "1_wings", "reason": "polling_abandoned"})
            persist_stuck_legs(stuck)
            self._last_rollback_stuck_legs = stuck
            logger.critical(
                "IC %s Phase 1 OrderPollingAbandoned: in-flight wings (LC,LP). "
                "Stuck-legs persisted for startup reconcile. exc=%s",
                self.instrument, exc,
            )
            raise

        def _filled(o: Dict) -> bool:
            return o.get("status") == "COMPLETE" and int(o.get("fill_qty", 0)) == qty

        # ── Phase 2: both wings must be fully on before any short leg goes out ──
        # Handles: both fully filled (proceed), one clean-failure (close the other),
        # both clean-failure (nothing to unwind), AND partial fills on either wing
        # (close whatever filled on both legs — no shorts ever go out). Per LIVE-05,
        # partial wing fill halts the entry rather than attempting recovery.
        lc_qty_filled = int(lc_order.get("fill_qty", 0))
        lp_qty_filled = int(lp_order.get("fill_qty", 0))
        lc_fully = _filled(lc_order)
        lp_fully = _filled(lp_order)

        if not (lc_fully and lp_fully):
            if lc_qty_filled > 0:
                self.om.place_order(lc_sym, "SELL", lc_qty_filled, price=books["lc"].bid)
            if lp_qty_filled > 0:
                self.om.place_order(lp_sym, "SELL", lp_qty_filled, price=books["lp"].bid)
            logger.error(
                "IC %s Phase 2: wings not both fully filled "
                "(LC status=%s fill=%d/%d, LP status=%s fill=%d/%d) — "
                "unwound partial exposure, halting; no shorts submitted",
                self.instrument,
                lc_order.get("status"), lc_qty_filled, qty,
                lp_order.get("status"), lp_qty_filled, qty,
            )
            return False

        lc_fill = float(lc_order["fill_price"])
        lp_fill = float(lp_order["fill_price"])

        # ── Phase 3: compute short-leg limit prices from actual wing fills ──
        # OFFSET_TICKS=0 submits exactly at the bid. Paper/live SELL LMT fills
        # only if limit ≤ bid, so limit == bid trades at bid.
        #
        # Re-fetch SC/SP bids before computing limits. The books fetched at
        # top-of-function (line ~580) are now stale by the cumulative latency
        # of Phase 1 (wings as MKT) and Phase 2 (wait for fills) — ~200-2000ms
        # in live. In a trending market, the call/put bid drifts during that
        # window; the resulting SELL LMT sits above the live bid and gets
        # CANCELED on submit (incidents 2026-04-27 12:00, 2026-04-28 10:49 +
        # 11:45 — both showed `limit > LTP` on SC by 2-7 points after wings
        # filled). Re-fetching here shrinks the residual stale-window to just
        # Phase-3 → Phase-4 latency (~50-200ms), proportionally dropping the
        # partial-fail rate. Re-fetch untradable → abort and unwind wings;
        # entering with stale data is worse than skipping a cycle.
        fresh_sc = self.md.get_quote_book(sc_sym)
        fresh_sp = self.md.get_quote_book(sp_sym)
        if (fresh_sc is None or not fresh_sc.is_tradable
                or fresh_sp is None or not fresh_sp.is_tradable):
            logger.error(
                "IC %s Phase 3: short-leg re-quote untradable "
                "(sc_book=%s sp_book=%s) — unwinding wings, retry next signal",
                self.instrument, fresh_sc, fresh_sp,
            )
            self.om.place_order(lc_sym, "SELL", qty, price=books["lc"].bid)
            self.om.place_order(lp_sym, "SELL", qty, price=books["lp"].bid)
            return False
        tick = settings.PRICE_TICK
        offset = settings.IC_SHORT_LIMIT_OFFSET_TICKS * tick
        drift_tol = settings.IC_SHORT_LIMIT_DRIFT_TOL
        sc_limit = round((fresh_sc.bid - drift_tol + offset) / tick) * tick
        sp_limit = round((fresh_sp.bid - drift_tol + offset) / tick) * tick

        # Feasibility: given wings already filled, can the shorts at these limits
        # still clear min_credit?
        projected_net_credit_unit = sc_limit + sp_limit - lc_fill - lp_fill
        if projected_net_credit_unit < min_credit:
            logger.error(
                "IC %s Phase 3: projected credit %.2f < min_credit %.2f after wings filled "
                "at LC=%.2f LP=%.2f with SC_limit=%.2f SP_limit=%.2f — fallback=%s; unwinding wings",
                self.instrument, projected_net_credit_unit, min_credit,
                lc_fill, lp_fill, sc_limit, sp_limit, settings.IC_PHASE3_FALLBACK,
            )
            # 'refuse' is the only implemented fallback. 'widen' / 'accept'
            # are checklist options for future tuning — they reuse this unwind.
            self.om.place_order(lc_sym, "SELL", qty, price=books["lc"].bid)
            self.om.place_order(lp_sym, "SELL", qty, price=books["lp"].bid)
            return False

        # ── Phase 4: submit shorts as LMT ────────────────────────────
        # OrderPollingAbandoned here is more dangerous than Phase 1 — wings
        # are already filled, so abandoning leaves the wings on at broker
        # plus an indeterminate short. Persist stuck-legs including the
        # filled wings so the operator can flatten manually; main.py halts.
        sc_order = None
        sp_order = None
        try:
            sc_order = self.om.place_order(sc_sym, "SELL", qty, price_type="LMT", price=sc_limit)
            sp_order = self.om.place_order(sp_sym, "SELL", qty, price_type="LMT", price=sp_limit)
        except OrderPollingAbandoned as exc:
            stuck = [
                {"symbol": lc_sym, "side": "BUY", "qty": qty, "phase": "4_shorts_abandoned", "reason": "filled_wing_to_unwind", "fill_price": lc_fill},
                {"symbol": lp_sym, "side": "BUY", "qty": qty, "phase": "4_shorts_abandoned", "reason": "filled_wing_to_unwind", "fill_price": lp_fill},
            ]
            if sc_order is None:
                stuck.append({"symbol": sc_sym, "side": "SELL", "qty": qty, "phase": "4_shorts_abandoned", "reason": "polling_abandoned_pre_status", "limit_price": sc_limit})
            else:
                stuck.append({"symbol": sc_sym, "side": "SELL", "qty": qty, "phase": "4_shorts_abandoned", "reason": "polling_abandoned_after_sc_returned", "sc_status": sc_order.get("status"), "limit_price": sc_limit})
            stuck.append({"symbol": sp_sym, "side": "SELL", "qty": qty, "phase": "4_shorts_abandoned", "reason": "polling_abandoned", "limit_price": sp_limit})
            persist_stuck_legs(stuck)
            self._last_rollback_stuck_legs = stuck
            logger.critical(
                "IC %s Phase 4 OrderPollingAbandoned: wings filled + shorts in-flight. "
                "Stuck-legs persisted (4 legs) for manual reconcile. exc=%s",
                self.instrument, exc,
            )
            raise

        sc_filled = _filled(sc_order)
        sp_filled = _filled(sp_order)

        if not (sc_filled and sp_filled):
            # ── Phase 5b: unwind everything ─────────────
            # LIVE-05: short legs may PARTIAL-fill (CANCELED with fill_qty > 0).
            # Unwinding by `qty` in that case over-buys and leaves NET LONG
            # exposure on the short strike — worse than the original short.
            # Use the actual filled quantity for each short-side reversal.
            sc_fill_qty = int(sc_order.get("fill_qty", 0))
            sp_fill_qty = int(sp_order.get("fill_qty", 0))
            logger.error(
                "IC %s Phase 5b: short(s) did not fill cleanly (SC status=%s fill=%d/%d, "
                "SP status=%s fill=%d/%d); unwinding filled portions + both wings",
                self.instrument,
                sc_order.get("status"), sc_fill_qty, qty,
                sp_order.get("status"), sp_fill_qty, qty,
            )
            if sc_fill_qty > 0:
                self.om.place_order(sc_sym, "BUY", sc_fill_qty, price=float(sc_order["fill_price"]))
            if sp_fill_qty > 0:
                self.om.place_order(sp_sym, "BUY", sp_fill_qty, price=float(sp_order["fill_price"]))
            self.om.place_order(lc_sym, "SELL", qty, price=books["lc"].bid)
            self.om.place_order(lp_sym, "SELL", qty, price=books["lp"].bid)
            # Phase-5b unified policy. A SELL LMT canceled because LTP drifted
            # below the limit between QuoteBook fetch and order submission is
            # bid-drift noise, not stressed liquidity — both 2026-04-27 12:00
            # (harvest re-entry) and 2026-04-28 10:49 (fresh entry) showed the
            # same mechanism. Halting the day on a single such event cost
            # ~₹1,200 of unwind plus all forward expected value. Policy: every
            # partial-fill skips the cycle and increments the counter; only a
            # sustained streak (cap consecutive) halts, bounding the
            # unwind-slippage tail. Counter resets on a successful entry.
            #
            # Phase-1/4 OrderPollingAbandoned (broker-connectivity events,
            # different signal) still halt unconditionally — see legacy enter()
            # path. Unwind-order failures (stuck legs in the book) also still
            # halt via _drain_rollback_failures → escalate_rollback_failure.
            if sc_fill_qty > 0 or sp_fill_qty > 0:
                self._consecutive_partial_fails += 1
                cap = settings.IC_PARTIAL_FAIL_CAP
                partial_legs = [
                    {"symbol": sc_sym, "side": "SELL", "fill_qty": sc_fill_qty, "requested_qty": qty},
                    {"symbol": sp_sym, "side": "SELL", "fill_qty": sp_fill_qty, "requested_qty": qty},
                ]
                if self._consecutive_partial_fails >= cap:
                    logger.critical(
                        "IC %s Phase-5b partial-fill cap reached "
                        "(%d consecutive ≥ cap %d) — suspending adjustments for today.",
                        self.instrument,
                        self._consecutive_partial_fails, cap,
                    )
                    self._phase5b_suspended = True
                else:
                    logger.warning(
                        "IC %s Phase-5b partial-fill (%d/%d) — skipping this "
                        "cycle, will retry on next signal.",
                        self.instrument,
                        self._consecutive_partial_fails, cap,
                    )
            return False

        sc_fill = float(sc_order["fill_price"])
        sp_fill = float(sp_order["fill_price"])

        # ── Phase 5a: post-fill credit re-check (LIVE-02 absorbed here) ──
        actual_net_credit_unit = sc_fill + sp_fill - lc_fill - lp_fill
        if actual_net_credit_unit < min_credit:
            logger.error(
                "IC %s Phase 5a: post-fill credit %.2f < min_credit %.2f "
                "(SC=%.2f SP=%.2f LC=%.2f LP=%.2f); unwinding all 4 legs",
                self.instrument, actual_net_credit_unit, min_credit,
                sc_fill, sp_fill, lc_fill, lp_fill,
            )
            self.om.place_order(sc_sym, "BUY", qty, price=sc_fill)
            self.om.place_order(sp_sym, "BUY", qty, price=sp_fill)
            self.om.place_order(lc_sym, "SELL", qty, price=books["lc"].bid)
            self.om.place_order(lp_sym, "SELL", qty, price=books["lp"].bid)
            return False

        # Entry successful — construct IC_Position mirroring legacy enter().
        # Reset the consecutive partial-fail counter and suspension flag.
        self._consecutive_partial_fails = 0
        self._phase5b_suspended = False
        max_profit = actual_net_credit_unit * qty
        try:
            expiry_iso = datetime.strptime(str(expiry).strip(), "%d-%b-%Y").date().isoformat()
        except (ValueError, TypeError):
            expiry_iso = ""
        now = datetime.now()
        self._position = IC_Position(
            instrument=self.instrument,
            sc_sym=sc_sym, sp_sym=sp_sym, lc_sym=lc_sym, lp_sym=lp_sym,
            sc_strike=sc, sp_strike=sp, lc_strike=lc, lp_strike=lp,
            max_profit=max_profit, entry_credit=actual_net_credit_unit,
            lots=lots, entry_time=now.strftime("%H:%M:%S"),
            expiry_date=expiry_iso,
            entry_date=now.strftime("%Y-%m-%d"),
        )
        logger.info(
            "IC %s ENTERED (hedge-first): SC=%s SP=%s LC=%s LP=%s | Credit=%.2f | Lots=%d (LotSize=%d)",
            self.instrument, sc, sp, lc, lp, actual_net_credit_unit, lots, lot_size,
        )
        self._record_session_entry()
        return True

    # ── Monitor ─────────────────────────────────────────────────────────

    def monitor(self) -> Optional[Dict]:
        if not self.is_active():
            return None
        
        pos = self._position
        prices = {
            'sc': self.md.get_ltp(pos.sc_sym),
            'sp': self.md.get_ltp(pos.sp_sym),
            'lc': self.md.get_ltp(pos.lc_sym),
            'lp': self.md.get_ltp(pos.lp_sym)
        }

        if any(p <= 0 for p in prices.values()):
            return None

        current_premium = (prices['sc'] + prices['sp']) - (prices['lc'] + prices['lp'])
        pnl_unit = pos.entry_credit - current_premium
        
        # Get actual lot size from master via market data
        lot_size = self.md.get_lot_size(pos.sc_sym)
        total_pnl = pnl_unit * pos.lots * lot_size

        # 1. Update Peak P&L
        if total_pnl > pos.peak_pnl:
            pos.peak_pnl = total_pnl

        # 2. Check 1% Profit Harvest Cycle
        # agents.md: "Close immediately when unrealized profit reaches 1% of the maximum possible profit"
        harvest_trigger = pos.max_profit * settings.IC_HARVEST_PCT
        if total_pnl >= harvest_trigger:
            logger.info(f"IC {self.instrument} HARVEST: PnL {total_pnl:.2f} >= Trigger {harvest_trigger:.2f}")
            return self.exit("PROFIT_HARVEST", total_pnl)

        # 3. Adjustment Logic (Breach + Profit)
        # Roll tested side OTM and safe side closer if overall position in profit
        if total_pnl > 0:
            # Check for breach
            spot = self.md.get_ltp(settings.NIFTY_SPOT_KEY if self.instrument == 'NIFTY' else "NSE|Nifty Bank")
            breached = spot >= pos.sc_strike or spot <= pos.sp_strike
            if breached:
                logger.info(f"IC {self.instrument} ADJUSTING: Spot={spot} breached strike. Rolling to cost-neutral.")
                return self.exit("ADJUSTMENT_REQUIRED", total_pnl) # Close to re-enter with adjusted strikes

        return None

    def _calculate_current_pnl(self) -> float:
        """Calculate current unrealised P&L for the active position."""
        if not self._position:
            return 0.0
        
        pos = self._position
        prices = {
            'sc': self.md.get_ltp(pos.sc_sym),
            'sp': self.md.get_ltp(pos.sp_sym),
            'lc': self.md.get_ltp(pos.lc_sym),
            'lp': self.md.get_ltp(pos.lp_sym)
        }

        # If any LTP is missing/zero, we return 0.0 to avoid bad exits, 
        # though in a force_exit we might want to be more aggressive.
        if any(p <= 0 for p in prices.values()):
            return 0.0

        current_premium = (prices['sc'] + prices['sp']) - (prices['lc'] + prices['lp'])
        pnl_unit = pos.entry_credit - current_premium
        
        lot_size = self.md.get_lot_size(pos.sc_sym)
        return pnl_unit * pos.lots * lot_size

    def exit(self, reason: str, pnl: float = 0.0) -> Dict:
        pos = self._position
        # Get actual lot size from master via market data
        lot_size = self.md.get_lot_size(pos.sc_sym)
        qty = pos.lots * lot_size

        # Axiom 5: exit orders bypass tracker.add_position (track_position=False),
        # so we must explicitly call tracker.close_position per leg to unwind state.
        closing_legs = [
            (pos.sc_sym, "BUY"),
            (pos.sp_sym, "BUY"),
            (pos.lc_sym, "SELL"),
            (pos.lp_sym, "SELL"),
        ]
        tracker = getattr(self.om, "tracker", None)
        exit_stuck: List[Dict] = []
        for sym, side in closing_legs:
            order = self.om.place_order(sym, side, qty, track_position=False)
            fq = order.get("fill_qty", 0)
            if fq < qty:
                logger.error(
                    "IC %s EXIT INCOMPLETE: %s %s filled %d/%d (status=%s, reason=%s)",
                    self.instrument, side, sym, fq, qty,
                    order.get("status"), order.get("reason", ""),
                )
                exit_stuck.append({
                    "symbol": sym,
                    "original_side": self._opposite_side(side),
                    "intended_qty": qty,
                    "reversed_qty": fq,
                    "reason": "exit_leg_incomplete",
                })
                # Stop attempting further close legs — position state is ambiguous.
                break
            if tracker is not None:
                try:
                    tracker.close_position(sym, order.get("fill_price", 0.0))
                except Exception:
                    logger.exception("IC %s tracker unwind failed for exit of %s", self.instrument, sym)

        if exit_stuck:
            self._last_rollback_stuck_legs = exit_stuck
            persist_stuck_legs(exit_stuck)

        logger.info(f"IC {self.instrument} EXIT [{reason}]: PnL={pnl:.2f}")
        
        # Align with TradeLogger.TRADE_COLUMNS
        # Columns: trade_id, date, time_entry, time_exit, instrument, 
        # sc_strike, sp_strike, lc_strike, lp_strike, entry_credit, exit_price, 
        # gross_pnl, net_pnl, exit_reason, lots, peak_pnl, vix_entry, day_type, paper
        result = {
            "instrument": self.instrument,
            "entry_date": pos.entry_date,
            "time_entry": pos.entry_time,
            "time_exit": datetime.now().strftime("%H:%M:%S"),
            "sc_strike": pos.sc_strike,
            "sp_strike": pos.sp_strike,
            "lc_strike": pos.lc_strike,
            "lp_strike": pos.lp_strike,
            "entry_credit": round(pos.entry_credit, 2),
            "pnl": pnl,           # Used by PnLEngine
            "net_pnl": pnl,       # Used by TradeLogger
            "exit_reason": reason,
            "lots": pos.lots,
            "peak_pnl": round(pos.peak_pnl, 2),
        }
        self._position = None
        # SHAKEDOWN-03b: stamp the exit reason + IST date so the next enter()
        # call can detect whether it's a same-day harvest/adjustment re-entry.
        self._last_exit_reason = reason
        self._last_exit_date = datetime.now().date().isoformat()
        return result

    def force_exit(self) -> Optional[Dict]:
        if not self.is_active():
            return None
        pnl = self._calculate_current_pnl()
        return self.exit("FORCE_EXIT", pnl)

    def save_state(self) -> Optional[Dict]:
        # SHAKEDOWN-03a: counter persists alongside the position so a same-day
        # crash-restart cannot silently rebudget an exhausted entry cap.
        today = datetime.now().date().isoformat()
        has_counter_state = bool(
            settings.SHAKEDOWN_MODE
            and self._entries_today_date
            and self._entries_today_count > 0
        )
        # Incident 2026-04-27 12:32:46: with SHAKEDOWN_MODE=False the harvest
        # re-entry sentinel was never persisted, so the first entry attempt
        # after any restart looked like a fresh entry to the Phase-5b policy.
        # Result: a single bid-drift cancel halted the day even though the
        # morning had 4 successful PROFIT_HARVEST exits. Persist independently
        # of the SHAKEDOWN gate; the date check at restore protects against
        # stale-day carryover regardless of mode.
        has_reentry_state = bool(
            self._last_exit_reason
            and self._last_exit_date == today
        )
        if not self._position and not has_counter_state and not has_reentry_state:
            return None
        payload: Dict[str, Any] = {}
        if self._position:
            payload["position"] = self._position.to_dict()
        if has_counter_state:
            payload["entries_today_count"] = self._entries_today_count
            payload["entries_today_date"] = self._entries_today_date
        if has_reentry_state:
            payload["last_exit_reason"] = self._last_exit_reason
            payload["last_exit_date"] = self._last_exit_date
        return payload

    def restore_state(self, state: Optional[Dict]) -> None:
        if not state:
            self._position = None
            return
        pos_data = state.get("position")
        self._position = IC_Position.from_dict(pos_data) if pos_data else None
        if self._position:
            logger.info(f"Restored {self.instrument} active position: {self._position.sc_sym} ...")
        # SHAKEDOWN-03a/-03b: counter and re-entry sentinel restore independently.
        # Each is keyed on its own persisted date to handle the case where one
        # is set without the other (paper mode persists re-entry but not counter).
        today = datetime.now().date().isoformat()
        persisted_counter_date = state.get("entries_today_date", "")
        if persisted_counter_date == today:
            self._entries_today_date = persisted_counter_date
            self._entries_today_count = int(state.get("entries_today_count", 0))
        persisted_reentry_date = state.get("last_exit_date", "")
        if persisted_reentry_date == today:
            self._last_exit_reason = state.get("last_exit_reason", "")
            self._last_exit_date = persisted_reentry_date
        if settings.SHAKEDOWN_MODE and self._entries_today_count > 0:
            logger.info(
                "SHAKEDOWN: %s entry counter restored: count=%d/%d date=%s last_exit=%s",
                self.instrument, self._entries_today_count,
                settings.IC_MAX_ENTRIES_PER_SESSION_SHAKEDOWN, today,
                self._last_exit_reason or "(none)",
            )
        elif self._last_exit_reason:
            logger.info(
                "%s re-entry sentinel restored: last_exit=%s date=%s",
                self.instrument, self._last_exit_reason, self._last_exit_date,
            )
