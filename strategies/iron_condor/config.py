"""
Configuration constants for Iron Condor strategy
"""

# Market eligibility thresholds
IV_PERCENTILE_MIN = 50  # Expanded from 55 to allow more opportunities
IV_PERCENTILE_MAX = 100  # Increased from 90 to 100 to allow all high IV conditions
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
# NET_CREDIT_MIN rationale:
# Iron Condor requires 4 option orders (2 short + 2 long legs)
# Transaction costs per trade:
#   - Brokerage: ₹5 × 4 orders = ₹20
#   - GST on brokerage (18%): ₹3.60
#   - STT (0.1% on sell premium): ~₹0.10-0.15
#   - Transaction charges (0.03503% on premium): ~₹0.05-0.10
#   - GST on transaction charges (18%): ~₹0.01-0.02
#   - Stamp duty (0.003% on buy premium): ~₹0.001-0.002
# Total costs: ~₹23-24 per trade
# 
# Minimum credit threshold considerations:
#   - Must cover full transaction costs: ₹24
#   - Should provide profit buffer for viable trades
#   - Adjusted to ₹30 to allow trades in current low-IV market conditions
#     while still maintaining cost coverage + small profit buffer
#   - Can be increased to ₹50-70 in high-IV environments for better margins
NET_CREDIT_MIN = 30.0  # ₹ per lot (covers transaction costs + small profit buffer)
NET_CREDIT_MAX = 110.0  # ₹ per lot
MAX_LOSS_PER_LOT_MAX = 1500.0  # ₹ per lot
MIN_REWARD_TO_RISK = 0.9

# Position sizing parameters
MAX_PER_TRADE_RISK = 30000.0  # ₹
CAPITAL_ALLOCATED = 1000000.0  # ₹10L
MARGIN_BUFFER_PCT = 0.30  # 30%

# Target instrument
TARGET_INSTRUMENT = "NIFTY"
INSTRUMENT_TYPE = "WEEKLY"



