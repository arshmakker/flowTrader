"""LIVE-10: pre-entry margin estimation for iron condors.

A defined-risk IC has a bounded max loss: ``wing_width - net_credit`` per
lot. SPAN+Exposure margin at NSE is scenario-driven and always lower than
this bound (the worst SPAN scenario can't lose more than the geometric
max loss), so using max-loss as the margin upper bound is conservative.

We multiply by a buffer (default 1.2×) to absorb intraday SPAN drift —
the broker can re-price margin between entry and close without changing
the position. Lower the buffer with care; at 1.0× a volatile tick after
leg-1 fill can push the remaining leg into insufficient-margin rejection.

This function is pure so it can be unit-tested without a broker. The
adapter that actually queries Shoonya's ``get_limits`` lives on
``LiveOrderManager.get_available_margin``.
"""

from __future__ import annotations


def estimate_ic_required_margin(
    wing_width: float,
    lot_size: int,
    lots: int,
    net_credit_unit: float,
    buffer: float = 1.2,
) -> float:
    """Upper bound on margin needed to hold the 4-leg IC through a session.

    Args:
        wing_width: Points between a short strike and its paired wing.
        lot_size: Contract lot size (NIFTY 65, BANKNIFTY 30 as of 2026-04).
        lots: Number of lots for the trade.
        net_credit_unit: Per-lot credit collected (short premium - wing premium).
        buffer: Multiplier for intraday SPAN drift (default 1.2).

    Returns:
        Rupee margin requirement, already multiplied by the buffer.
        Clamps max_loss at 0 if credit exceeds width (unusual but possible
        in stressed books) — caller still has to pass the credit-floor check
        separately.
    """
    max_loss_per_lot = max(0.0, wing_width - net_credit_unit)
    base = max_loss_per_lot * lot_size * lots
    return base * buffer
