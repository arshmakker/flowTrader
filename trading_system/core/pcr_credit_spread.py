"""
PCR Contrarian Credit Spread strategy.

Entry: PCR < 0.7 → BEAR_CALL (sell call 100pts above ATM, buy call 300pts above ATM)
       PCR > 1.3 → BULL_PUT  (sell put  100pts below ATM, buy put  300pts below ATM)
Exit:  Thursday/Tuesday 14:45 expiry-day close, OR stop if MTM loss > 2× entry credit.
"""

import logging
from dataclasses import asdict, dataclass
from datetime import date, datetime, time
from typing import Any, Dict, Optional

import pytz

from trading_system.config import settings

log = logging.getLogger(__name__)
IST = pytz.timezone("Asia/Kolkata")


@dataclass
class PCS_Position:
    instrument: str
    signal: str  # "BULL_PUT" | "BEAR_CALL"
    short_strike: int
    long_strike: int
    opt_type: str  # "PE" | "CE"
    short_sym: str
    long_sym: str
    entry_credit: float
    lots: int
    lot_size: int
    expiry: str  # ISO date string "YYYY-MM-DD"
    entry_time: str  # ISO datetime string
    entry_pcr: float


class PCRCreditSpreadStrategy:
    def __init__(self, order_manager: Any, market_data: Any, instrument: str = "NIFTY"):
        self.om = order_manager
        self.md = market_data
        self.instrument = instrument
        self.pos: Optional[PCS_Position] = None

    # ── Public interface ──────────────────────────────────────────────────────

    def is_active(self) -> bool:
        return self.pos is not None

    def enter(self, spot: float, pcr: Optional[float], expiry: date, lots: int) -> bool:
        """Attempt entry. Returns True if both legs filled, False otherwise."""
        if self.pos is not None:
            log.debug("enter() called but already active — skipping")
            return False

        if pcr is None:
            log.info("[%s] PCR unavailable — skipping entry", self.instrument)
            return False

        if settings.PCS_NO_PCR_FILTER:
            signal = "BEAR_CALL"
            opt_type = "CE"
            log.info("[%s] PCR %.3f (no-filter mode) — BEAR_CALL", self.instrument, pcr)
        elif pcr < settings.PCS_PCR_BEAR:
            signal = "BEAR_CALL"
            opt_type = "CE"
        elif pcr > settings.PCS_PCR_BULL:
            signal = "BULL_PUT"
            opt_type = "PE"
        else:
            log.info(
                "[%s] PCR %.3f neutral (%.2f–%.2f) — no entry",
                self.instrument,
                pcr,
                settings.PCS_PCR_BEAR,
                settings.PCS_PCR_BULL,
            )
            return False

        atm = int(round(spot / 50) * 50)
        if signal == "BULL_PUT":
            short_strike = atm - settings.PCS_SHORT_OTM_PTS
            long_strike = atm - settings.PCS_LONG_OTM_PTS
        else:
            short_strike = atm + settings.PCS_SHORT_OTM_PTS
            long_strike = atm + settings.PCS_LONG_OTM_PTS

        expiry_str = expiry.strftime("%d%b%y").upper()  # Shoonya format e.g. "15MAY25"
        short_sym = self.om.build_option_symbol(self.instrument, expiry_str, short_strike, opt_type)
        long_sym = self.om.build_option_symbol(self.instrument, expiry_str, long_strike, opt_type)

        short_ltp = self.md.get_ltp(short_sym)
        long_ltp = self.md.get_ltp(long_sym)

        if not short_ltp or not long_ltp:
            log.warning("[%s] LTP unavailable for spread legs — skipping entry", self.instrument)
            return False

        entry_credit = short_ltp - long_ltp
        if entry_credit < settings.PCS_MIN_CREDIT:
            log.info(
                "[%s] Credit %.1f pts below minimum %.1f — skipping entry",
                self.instrument,
                entry_credit,
                settings.PCS_MIN_CREDIT,
            )
            return False

        lot_size = self.md.get_lot_size(self.instrument)
        qty = lots * lot_size

        # Leg 1: sell the short strike
        sell_result = self.om.place_order(
            tradingsymbol=short_sym,
            buy_or_sell="S",
            quantity=qty,
            price_type="LMT",
            price=short_ltp,
        )
        if sell_result.get("status") != "COMPLETE":
            log.warning("[%s] Short leg SELL failed: %s", self.instrument, sell_result.get("status"))
            return False

        # Leg 2: buy the long strike (hedge)
        buy_result = self.om.place_order(
            tradingsymbol=long_sym,
            buy_or_sell="B",
            quantity=qty,
            price_type="LMT",
            price=long_ltp,
        )
        if buy_result.get("status") != "COMPLETE":
            log.warning("[%s] Long leg BUY failed — rolling back short leg", self.instrument)
            self.om.place_order(
                tradingsymbol=short_sym,
                buy_or_sell="B",
                quantity=qty,
                price_type="LMT",
                price=sell_result["fill_price"],
            )
            return False

        actual_credit = sell_result["fill_price"] - buy_result["fill_price"]
        self.pos = PCS_Position(
            instrument=self.instrument,
            signal=signal,
            short_strike=short_strike,
            long_strike=long_strike,
            opt_type=opt_type,
            short_sym=short_sym,
            long_sym=long_sym,
            entry_credit=actual_credit,
            lots=lots,
            lot_size=lot_size,
            expiry=expiry.isoformat(),
            entry_time=datetime.now(IST).isoformat(),
            entry_pcr=pcr,
        )
        log.info(
            "[%s] Entered %s | ATM=%d short=%d long=%d credit=%.1fpts lots=%d",
            self.instrument,
            signal,
            atm,
            short_strike,
            long_strike,
            actual_credit,
            lots,
        )
        return True

    def monitor(self) -> Optional[Dict]:
        """
        Check for stop-loss or expiry-day exit.
        Returns exit dict if action is needed, None otherwise.
        """
        if self.pos is None:
            return None

        now_ist = datetime.now(IST)
        today = now_ist.date()
        expiry_date = date.fromisoformat(self.pos.expiry)

        # Expiry-day force-exit
        exit_time = time(*[int(x) for x in settings.PCS_EXIT_TIME.split(":")])
        if today == expiry_date and now_ist.time() >= exit_time:
            log.info("[%s] Expiry day exit at %s", self.instrument, settings.PCS_EXIT_TIME)
            return self._build_exit("EXPIRY_CLOSE")

        # LTP freshness check
        short_ltp, short_age = self.md.get_ltp_with_age(self.pos.short_sym)
        long_ltp, long_age = self.md.get_ltp_with_age(self.pos.long_sym)
        if short_age > settings.IC_FRESH_LTP_MAX_AGE_SEC or long_age > settings.IC_FRESH_LTP_MAX_AGE_SEC:
            log.debug("[%s] Stale LTP — skipping monitor tick", self.instrument)
            return None

        if not short_ltp or not long_ltp:
            return None

        current_spread = short_ltp - long_ltp
        mtm_loss = current_spread - self.pos.entry_credit
        stop_threshold = settings.PCS_STOP_MULT * self.pos.entry_credit

        if mtm_loss > stop_threshold:
            log.warning(
                "[%s] STOP: MTM loss %.1f pts > threshold %.1f pts — exiting",
                self.instrument,
                mtm_loss,
                stop_threshold,
            )
            return self._build_exit("PCS_STOP", exit_spread=current_spread)

        return None

    def force_exit(self, reason: str = "FORCE_EXIT") -> Optional[Dict]:
        """Immediate close regardless of profit/loss."""
        if self.pos is None:
            return None
        return self._execute_exit(reason)

    # ── Persistence ───────────────────────────────────────────────────────────

    def save_state(self) -> Dict:
        return {"pos": asdict(self.pos) if self.pos else None}

    def restore_state(self, state: Dict) -> None:
        pos_dict = state.get("pos")
        if pos_dict:
            self.pos = PCS_Position(**pos_dict)
            log.info("[%s] Restored position: %s signal=%s", self.instrument, self.pos.short_sym, self.pos.signal)
        else:
            self.pos = None

    # ── Internal ──────────────────────────────────────────────────────────────

    def _build_exit(self, reason: str, exit_spread: Optional[float] = None) -> Dict:
        """Return an exit instruction dict without executing orders."""
        return {"action": "exit", "reason": reason, "exit_spread": exit_spread, "strategy": self}

    def _execute_exit(self, reason: str) -> Dict:
        if self.pos is None:
            return {}

        pos = self.pos
        qty = pos.lots * pos.lot_size

        # Buy back short (close the sold leg)
        close_short = self.om.place_order(
            tradingsymbol=pos.short_sym,
            buy_or_sell="B",
            quantity=qty,
            price_type="MKT",
            price=0.0,
        )
        # Sell the long hedge (close the bought leg)
        close_long = self.om.place_order(
            tradingsymbol=pos.long_sym,
            buy_or_sell="S",
            quantity=qty,
            price_type="MKT",
            price=0.0,
        )

        exit_short_price = close_short.get("fill_price", 0.0)
        exit_long_price = close_long.get("fill_price", 0.0)
        exit_spread = exit_short_price - exit_long_price
        pnl_pts = pos.entry_credit - exit_spread
        gross_pnl = pnl_pts * pos.lots * pos.lot_size

        record = {
            "instrument": pos.instrument,
            "signal": pos.signal,
            "short_strike": pos.short_strike,
            "long_strike": pos.long_strike,
            "entry_credit": pos.entry_credit,
            "exit_spread": exit_spread,
            "pnl_pts": pnl_pts,
            "gross_pnl": gross_pnl,
            "lots": pos.lots,
            "entry_time": pos.entry_time,
            "exit_time": datetime.now(IST).isoformat(),
            "reason": reason,
            "entry_pcr": pos.entry_pcr,
        }
        log.info(
            "[%s] Exit %s | credit=%.1f exit_spread=%.1f pnl=%.1fpts ₹%.0f",
            pos.instrument,
            reason,
            pos.entry_credit,
            exit_spread,
            pnl_pts,
            gross_pnl,
        )
        self.pos = None
        return record
