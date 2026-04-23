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
from trading_system.live.live_order_manager import persist_stuck_legs

logger = logging.getLogger(__name__)

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

    def is_active(self) -> bool:
        return self._position is not None

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
        """Reverse whatever fill_qty actually filled after a partial-fill CANCELED.

        Records stuck legs and signals halt via _last_rollback_stuck_legs.
        LIVE-05: full hedge path to be added when LIVE-03 hedge logic lands.
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
        self._last_rollback_stuck_legs = stuck
        persist_stuck_legs(stuck)

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
        if getattr(settings, "IC_ENTRY_MODE", "sequential") == "hedge_first":
            return self.enter_hedge_first(spot, vix, sr_high, sr_low, sr_manager, expiry, lots)

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
        
        # Credit Rule: net_credit >= IC_MIN_CREDIT
        width = abs(lc - sc)
        if net_credit_unit < settings.IC_MIN_CREDIT:
            lot_size = self.md.get_lot_size(sc_sym)
            self._log_credit_rejection(
                expiry=expiry,
                spot=spot,
                vix=vix,
                strikes=(sc, sp, lc, lp),
                symbols=(sc_sym, sp_sym, lc_sym, lp_sym),
                prices=prices,
                net_credit_unit=net_credit_unit,
                min_credit=settings.IC_MIN_CREDIT,
                lots=lots,
                lot_size=lot_size,
            )
            logger.info(f"IC {self.instrument}: FAILED Credit Rule (Credit {net_credit_unit:.2f} < Min {settings.IC_MIN_CREDIT})")
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

        # Pre-entry credit sanity — use bid/ask mids instead of LTP (LIVE-06).
        # Shorts at bid (we sell into bid); wings at ask (we buy from ask).
        pre_entry_credit_unit = (
            books["sc"].bid + books["sp"].bid
            - books["lc"].ask - books["lp"].ask
        )
        if pre_entry_credit_unit < settings.IC_MIN_CREDIT:
            logger.info(
                "IC %s hedge-first: pre-entry book credit %.2f < IC_MIN_CREDIT %.2f "
                "(SC_bid=%.2f SP_bid=%.2f LC_ask=%.2f LP_ask=%.2f)",
                self.instrument, pre_entry_credit_unit, settings.IC_MIN_CREDIT,
                books["sc"].bid, books["sp"].bid, books["lc"].ask, books["lp"].ask,
            )
            return False

        # ── Phase 1: submit wings as MKT ─────────────────────────────
        lc_order = self.om.place_order(lc_sym, "BUY", qty)
        lp_order = self.om.place_order(lp_sym, "BUY", qty)

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
                self.om.place_order(lc_sym, "SELL", lc_qty_filled)
            if lp_qty_filled > 0:
                self.om.place_order(lp_sym, "SELL", lp_qty_filled)
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
        tick = settings.PRICE_TICK
        offset = settings.IC_SHORT_LIMIT_OFFSET_TICKS * tick
        sc_limit = round((books["sc"].bid + offset) / tick) * tick
        sp_limit = round((books["sp"].bid + offset) / tick) * tick

        # Feasibility: given wings already filled, can the shorts at these limits
        # still clear IC_MIN_CREDIT?
        projected_net_credit_unit = sc_limit + sp_limit - lc_fill - lp_fill
        if projected_net_credit_unit < settings.IC_MIN_CREDIT:
            logger.error(
                "IC %s Phase 3: projected credit %.2f < IC_MIN_CREDIT %.2f after wings filled "
                "at LC=%.2f LP=%.2f with SC_limit=%.2f SP_limit=%.2f — fallback=%s; unwinding wings",
                self.instrument, projected_net_credit_unit, settings.IC_MIN_CREDIT,
                lc_fill, lp_fill, sc_limit, sp_limit, settings.IC_PHASE3_FALLBACK,
            )
            # 'refuse' is the only implemented fallback. 'widen' / 'accept'
            # are checklist options for future tuning — they reuse this unwind.
            self.om.place_order(lc_sym, "SELL", qty)
            self.om.place_order(lp_sym, "SELL", qty)
            return False

        # ── Phase 4: submit shorts as LMT ────────────────────────────
        sc_order = self.om.place_order(sc_sym, "SELL", qty, price_type="LMT", price=sc_limit)
        sp_order = self.om.place_order(sp_sym, "SELL", qty, price_type="LMT", price=sp_limit)

        sc_filled = _filled(sc_order)
        sp_filled = _filled(sp_order)

        if not (sc_filled and sp_filled):
            # ── Phase 5b: unwind everything ─────────────
            logger.error(
                "IC %s Phase 5b: short(s) did not fill (SC=%s, SP=%s); unwinding "
                "any filled shorts + both wings",
                self.instrument, sc_order.get("status"), sp_order.get("status"),
            )
            if sc_filled:
                self.om.place_order(sc_sym, "BUY", qty)
            if sp_filled:
                self.om.place_order(sp_sym, "BUY", qty)
            self.om.place_order(lc_sym, "SELL", qty)
            self.om.place_order(lp_sym, "SELL", qty)
            return False

        sc_fill = float(sc_order["fill_price"])
        sp_fill = float(sp_order["fill_price"])

        # ── Phase 5a: post-fill credit re-check (LIVE-02 absorbed here) ──
        actual_net_credit_unit = sc_fill + sp_fill - lc_fill - lp_fill
        if actual_net_credit_unit < settings.IC_MIN_CREDIT:
            logger.error(
                "IC %s Phase 5a: post-fill credit %.2f < IC_MIN_CREDIT %.2f "
                "(SC=%.2f SP=%.2f LC=%.2f LP=%.2f); unwinding all 4 legs",
                self.instrument, actual_net_credit_unit, settings.IC_MIN_CREDIT,
                sc_fill, sp_fill, lc_fill, lp_fill,
            )
            self.om.place_order(sc_sym, "BUY", qty)
            self.om.place_order(sp_sym, "BUY", qty)
            self.om.place_order(lc_sym, "SELL", qty)
            self.om.place_order(lp_sym, "SELL", qty)
            return False

        # Entry successful — construct IC_Position mirroring legacy enter().
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
        return result

    def force_exit(self) -> Optional[Dict]:
        if not self.is_active():
            return None
        pnl = self._calculate_current_pnl()
        return self.exit("FORCE_EXIT", pnl)

    def save_state(self) -> Optional[Dict]:
        if self._position:
            return self._position.to_dict()
        return None

    def restore_state(self, state: Optional[Dict]) -> None:
        if state:
            self._position = IC_Position.from_dict(state)
            logger.info(f"Restored {self.instrument} active position: {self._position.sc_sym} ...")
        else:
            self._position = None
