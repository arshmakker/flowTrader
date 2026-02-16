import math

from strategies.iron_condor.position_sizer import calculate_lots
from strategies.iron_condor.config import MAX_PER_TRADE_RISK


def test_per_lot_sizing():
    """
    Ensure sizing uses max_loss per LOT (max_loss_per_share * lot_size).
    This guards against the historical bug where a per-share max_loss was
    passed directly to the position sizer, producing huge lot counts.
    """
    max_loss_per_share = 40.0
    lot_size = 65
    per_lot = max_loss_per_share * lot_size
    expected = math.floor(MAX_PER_TRADE_RISK / per_lot)

    # calculate_lots expects a per-lot max_loss value
    assert calculate_lots(per_lot) == expected

    # Sanity: passing the per-share value should NOT equal the per-lot result
    wrong = calculate_lots(max_loss_per_share)
    assert wrong != expected

