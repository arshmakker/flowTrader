"""
Configuration constants for Iron Condor strategy
"""

# Market eligibility thresholds
IV_PERCENTILE_MIN = 55
IV_PERCENTILE_MAX = 85
DAYS_TO_EXPIRY_MIN = 3
DAYS_TO_EXPIRY_MAX = 30  # Expanded to include monthly expiries (was 6)
ADX_THRESHOLD = 22

# Strike selection parameters
SHORT_CALL_DELTA_MIN = 0.15
SHORT_CALL_DELTA_MAX = 0.20
SHORT_PUT_DELTA_MIN = -0.20
SHORT_PUT_DELTA_MAX = -0.15

# Fallback: distance from spot (if delta unavailable)
SPOT_DISTANCE_MIN_PCT = 0.8
SPOT_DISTANCE_MAX_PCT = 1.2

# Wing width (hedge distance)
WING_WIDTH_MIN = 100
WING_WIDTH_MAX = 150

# Payoff validation thresholds
NET_CREDIT_MIN = 70.0  # ₹ per lot
NET_CREDIT_MAX = 110.0  # ₹ per lot
MAX_LOSS_PER_LOT_MAX = 1500.0  # ₹ per lot
MIN_REWARD_TO_RISK = 2.0

# Position sizing parameters
MAX_PER_TRADE_RISK = 30000.0  # ₹
CAPITAL_ALLOCATED = 1000000.0  # ₹10L
MARGIN_BUFFER_PCT = 0.30  # 30%

# Target instrument
TARGET_INSTRUMENT = "NIFTY"
INSTRUMENT_TYPE = "WEEKLY"



