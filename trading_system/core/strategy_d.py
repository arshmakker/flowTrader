"""
Strategy D — Wide Iron Condor (agent.md §11).

Primary strategy for elevated / high VIX ranging days.
Wider strikes calibrated to VIX level; requires VIX stabilisation before entry.

Condition : VIX >= 17 + day RANGING + VIX stable for SD_VIX_STABLE_MINS +
            time in SD_ENTRY_START–SD_ENTRY_END
Structure : Sell OTM CE + Sell OTM PE (width per VIX) + Buy wing CE + Buy wing PE
Target    : SD_TARGET_PCT (30%) of net premium collected
Stop      : SD_STOP_PCT (80%) of net premium collected
Hard exit : 14:15 IST — all 4 legs simultaneously
Size      : SD_MAX_LOTS × size_multiplier
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import time
from typing import Any, Dict, List, Optional, Tuple

from trading_system.config import settings

logger = logging.getLogger(__name__)


@dataclass
class IronCondorPosition:
    short_call: float = 0.0
    short_put: float = 0.0
    long_call: float = 0.0
    long_put: float = 0.0
    sc_sym: str = ""
    sp_sym: str = ""
    lc_sym: str = ""
    lp_sym: str = ""
    net_premium: float = 0.0    # credit collected
    lots: int = 0
    entry_time: str = ""


class StrategyD:
    """Wide Iron Condor — sell volatility on elevated-VIX ranging days."""

    def __init__(self, order_manager: Any, market_data: Any):
        self.om = order_manager
        self.md = market_data
        self._position: Optional[IronCondorPosition] = None

    def is_active(self) -> bool:
        return self._position is not None

    # ── Helpers ─────────────────────────────────────────────────────────

    @staticmethod
    def _parse_time(s: str) -> time:
        h, m = s.split(":")
        return time(int(h), int(m))

    @staticmethod
    def _get_otm_pct(vix: float) -> float:
        if vix < settings.VIX_NORMAL_HIGH:
            return settings.SD_OTM_PCT_NORMAL
        if vix < settings.VIX_DANGER:
            return settings.SD_OTM_PCT_ELEVATED
        return settings.SD_OTM_PCT_HIGH

    @staticmethod
    def vix_is_stable(vix_history: List[Tuple[float, float]]) -> bool:
        """vix_history: [(monotonic_ts, vix_value), ...] covering last SD_VIX_STABLE_MINS."""
        if len(vix_history) < 5:
            return False
        recent = [v for _, v in vix_history[-10:]]
        return (max(recent) - min(recent)) <= settings.SD_VIX_STABLE_BAND

    @staticmethod
    def get_strikes(spot: float, vix: float, step: int = 50) -> Tuple[float, float, float, float]:
        """Returns (short_call, short_put, long_call, long_put)."""
        otm = StrategyD._get_otm_pct(vix)
        wing = otm + settings.SD_WING_PCT
        sc = round(spot * (1 + otm) / step) * step
        sp = round(spot * (1 - otm) / step) * step
        lc = round(spot * (1 + wing) / step) * step
        lp = round(spot * (1 - wing) / step) * step
        return sc, sp, lc, lp

    # ── Entry gate ──────────────────────────────────────────────────────

    def should_enter(
        self,
        vix: float,
        day_type: str,
        vix_history: List[Tuple[float, float]],
        now_time: time,
    ) -> bool:
        entry_start = self._parse_time(settings.SD_ENTRY_START)
        entry_end = self._parse_time(settings.SD_ENTRY_END)
        return (
            vix >= settings.VIX_NORMAL_HIGH
            and day_type == "RANGING"
            and self.vix_is_stable(vix_history)
            and entry_start <= now_time <= entry_end
            and not self.is_active()
        )

    def enter(
        self, spot: float, vix: float, lots: int, expiry: str, now_str: str
    ) -> Optional[Dict]:
        sc, sp, lc, lp = self.get_strikes(spot, vix)
        sc_sym = self.om.build_option_symbol("NIFTY", expiry, sc, "CE")
        sp_sym = self.om.build_option_symbol("NIFTY", expiry, sp, "PE")
        lc_sym = self.om.build_option_symbol("NIFTY", expiry, lc, "CE")
        lp_sym = self.om.build_option_symbol("NIFTY", expiry, lp, "PE")

        sc_ltp = self.md.get_ltp(sc_sym)
        sp_ltp = self.md.get_ltp(sp_sym)
        lc_ltp = self.md.get_ltp(lc_sym)
        lp_ltp = self.md.get_ltp(lp_sym)
        if any(p <= 0 for p in (sc_ltp, sp_ltp, lc_ltp, lp_ltp)):
            logger.warning("StrategyD: cannot get LTP for all legs; skipping entry")
            return None

        net_prem = (sc_ltp + sp_ltp) - (lc_ltp + lp_ltp)
        qty = lots * settings.NIFTY_LOT_SIZE

        self.om.place_order(sc_sym, "SELL", qty)
        self.om.place_order(sp_sym, "SELL", qty)
        self.om.place_order(lc_sym, "BUY", qty)
        self.om.place_order(lp_sym, "BUY", qty)

        self._position = IronCondorPosition(
            short_call=sc, short_put=sp, long_call=lc, long_put=lp,
            sc_sym=sc_sym, sp_sym=sp_sym, lc_sym=lc_sym, lp_sym=lp_sym,
            net_premium=net_prem, lots=lots, entry_time=now_str,
        )
        logger.info(
            "StratD ENTER IC: SC=%.0f SP=%.0f LC=%.0f LP=%.0f  prem=%.2f lots=%d",
            sc, sp, lc, lp, net_prem, lots,
        )
        return {"strategy": "D", "action": "ENTER", "net_premium": net_prem, "lots": lots}

    # ── Monitor / exit ──────────────────────────────────────────────────

    def monitor(self) -> Optional[Dict]:
        if not self.is_active():
            return None
        pos = self._position
        sc_ltp = self.md.get_ltp(pos.sc_sym)
        sp_ltp = self.md.get_ltp(pos.sp_sym)
        lc_ltp = self.md.get_ltp(pos.lc_sym)
        lp_ltp = self.md.get_ltp(pos.lp_sym)

        current_value = (sc_ltp + sp_ltp) - (lc_ltp + lp_ltp)
        pnl = pos.net_premium - current_value

        if pnl >= pos.net_premium * settings.SD_TARGET_PCT:
            return self.exit("TARGET_HIT", pnl)
        if pnl <= -pos.net_premium * settings.SD_STOP_PCT:
            return self.exit("STOP_HIT", pnl)
        return None

    def exit(self, reason: str, pnl: float = 0.0) -> Dict:
        pos = self._position
        qty = pos.lots * settings.NIFTY_LOT_SIZE
        self.om.place_order(pos.sc_sym, "BUY", qty)
        self.om.place_order(pos.sp_sym, "BUY", qty)
        self.om.place_order(pos.lc_sym, "SELL", qty)
        self.om.place_order(pos.lp_sym, "SELL", qty)
        logger.info("StratD EXIT [%s]: pnl=%.2f lots=%d", reason, pnl, pos.lots)
        result = {
            "strategy": "D",
            "action": "EXIT",
            "reason": reason,
            "pnl": pnl,
            "net_premium": pos.net_premium,
            "lots": pos.lots,
            "entry_time": pos.entry_time,
        }
        self._position = None
        return result

    def force_exit(self) -> Optional[Dict]:
        if not self.is_active():
            return None
        pos = self._position
        sc_ltp = self.md.get_ltp(pos.sc_sym)
        sp_ltp = self.md.get_ltp(pos.sp_sym)
        lc_ltp = self.md.get_ltp(pos.lc_sym)
        lp_ltp = self.md.get_ltp(pos.lp_sym)
        pnl = pos.net_premium - ((sc_ltp + sp_ltp) - (lc_ltp + lp_ltp))
        return self.exit("HARD_CLOSE", pnl)
