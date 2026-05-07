"""Tax and fee computation for Indian F&O options (LIVE-12).

Pure function used on both paper fills (PaperOrderManager / PaperPositionTracker)
and broker-side reconciliation (ops/reconcile.py). Rates are sourced from
``settings.FEES_NIFTY_OPT`` so a SEBI/NSE/broker revision is a one-line config
change rather than a code patch.

Derivation of rates (as of 2026-04-24, verified against Shoonya FAQ + Zerodha's
charges page + Budget 2026 STT circular):

  - brokerage:  ₹5 flat per executed order (Shoonya-specific)
  - STT:        0.15% on SELL premium, options (post-Budget 2026, eff. 2026-04-01)
  - exch_txn:   0.00255% of premium turnover, both sides (NSE F&O options = ₹25.5/crore)
  - sebi:       ₹10 / crore = 0.0001% of turnover, both sides
  - stamp:      0.003% on BUY premium turnover only (₹300 / crore)
  - gst:        18% of (brokerage + exch_txn + sebi)   ── NOT of STT or stamp

Side-asymmetry is load-bearing: STT fires only on SELLs, stamp only on BUYs.
The GST base deliberately excludes STT and stamp — a common implementation bug.
"""

from typing import Dict

from trading_system.config import settings


def compute_taxes_and_fees(symbol: str, side: str, price: float, qty: int) -> Dict[str, float]:
    """Return the full six-component cost breakdown for a single options leg.

    Returns a dict with keys ``brokerage, stt, exch_txn, sebi, stamp, gst, total``.
    All values are rupees rounded to paise. The ``total`` key is the sum of the
    other six — callers should use it rather than re-summing to avoid drift.
    """
    rates = settings.FEES_NIFTY_OPT
    turnover = price * qty
    is_sell = side in ("SELL", "S")

    brokerage = rates["brokerage_per_order"] if turnover > 0 else 0.0
    exch_txn = turnover * rates["exch_txn_pct"]
    sebi = turnover * rates["sebi_pct"]
    stt = turnover * rates["stt_sell_pct"] if is_sell else 0.0
    stamp = turnover * rates["stamp_buy_pct"] if not is_sell else 0.0
    gst = (brokerage + exch_txn + sebi) * rates["gst_pct"]

    brokerage_r = round(brokerage, 2)
    stt_r = round(stt, 2)
    exch_txn_r = round(exch_txn, 2)
    sebi_r = round(sebi, 2)
    stamp_r = round(stamp, 2)
    gst_r = round(gst, 2)
    total_r = round(brokerage_r + stt_r + exch_txn_r + sebi_r + stamp_r + gst_r, 2)

    return {
        "brokerage": brokerage_r,
        "stt": stt_r,
        "exch_txn": exch_txn_r,
        "sebi": sebi_r,
        "stamp": stamp_r,
        "gst": gst_r,
        "total": total_r,
    }
