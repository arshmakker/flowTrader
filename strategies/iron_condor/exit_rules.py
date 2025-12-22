"""
Exit rules configuration for Iron Condor strategy
"""

# Profit target: exit at 50-60% of max profit
PROFIT_TARGET_PCT = (0.50, 0.60)

# Stop loss: exit if loss exceeds 1.2x of max loss
STOP_LOSS_MULTIPLIER = 1.2

# Mandatory exit: close position if days to expiry <= 1
MANDATORY_EXIT_DTE = 1

# Mandatory exit: close position at 14:30 on expiry day
MANDATORY_EXIT_TIME = "14:30"



