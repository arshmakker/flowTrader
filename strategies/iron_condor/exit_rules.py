"""
Exit rules configuration for Iron Condor strategy
"""

# Trailing PnL lock (Iron Condor): first lock at ₹300, then trail ₹200 below current PnL
MIN_PNL_LOCK_INR = 300  # First lock when PnL >= ₹300
PNL_TRAIL_DISTANCE_INR = 200  # Lock trails at (current_pnl - 200); exit when PnL < lock

# Profit target: exit at 1% of margin used (primary target)
PROFIT_TARGET_MARGIN_PCT = 0.01  # 1% of margin

# Profit target: exit at 50-60% of max profit (fallback/legacy)
PROFIT_TARGET_PCT = (0.50, 0.60)

# Stop loss: exit if loss exceeds 1.2x of max loss
STOP_LOSS_MULTIPLIER = 1.2

# Mandatory exit: close position if days to expiry <= 1
MANDATORY_EXIT_DTE = 1

# Mandatory exit: close position at 14:30 on expiry day
MANDATORY_EXIT_TIME = "14:30"



