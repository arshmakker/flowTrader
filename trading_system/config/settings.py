"""
All system parameters live here (agent.md §4).

Do not hardcode values outside this file.
"""

# ══ MODE ══════════════════════════════════════════════════════════
PAPER_TRADE_MODE = True  # Flip to False to go live

# ══ INSTRUMENTS ════════════════════════════════════════════════════
NIFTY_SYMBOL = "NIFTY"
BANKNIFTY_SYMBOL = "BANKNIFTY"
NIFTY_LOT_SIZE = 65  # fallback; overwritten at startup from NFO.csv
BANKNIFTY_LOT_SIZE = 30  # fallback; overwritten at startup from NFO.csv
NIFTY_STRIKE_STEP = 50
BANKNIFTY_STRIKE_STEP = 100

# ══ SHOONYA TOKENS & SYMBOLS ══════════════════════════════════════
NIFTY_SPOT_TOKEN = "26000"
INDIA_VIX_TOKEN = "26017"
NIFTY_SPOT_KEY = "NSE|Nifty 50"        # LTP lookup key
INDIA_VIX_KEY = "NSE|India VIX"
NIFTY_SPOT_EXCHANGE = "NSE"

# ══ SESSION ═════════════════════════════════════════════════════════
TRADE_START = "10:00"
CLASSIFY_TIME = "10:30"
TRADE_END = "14:15"  # Hard close ALL positions
SIGNAL_RECHECK_SEC = 60
PRE_MARKET_SLEEP_SEC = 30
MONITORING_SLEEP_SEC = 60
PNL_LOG_INTERVAL_SEC = 300  # periodic P&L summary every 5 minutes
VIX_HISTORY_WINDOW_SEC = 3600  # keep last 60 min of VIX readings

# ══ STRATEGY ENTRY WINDOWS ═══════════════════════════════════════════
SA_ENTRY_START = "10:00"
SA_ENTRY_END = "13:00"
SB_ENTRY_START = "10:30"
SB_ENTRY_END = "13:00"

# ══ VIX REGIMES ══════════════════════════════════════════════════════
VIX_CALM = 13.0
VIX_NORMAL_HIGH = 17.0
VIX_DANGER = 20.0

# ══ DAY CLASSIFICATION ════════════════════════════════════════════════
TREND_MOVE_THRESHOLD = 0.015  # 1.5% from open = trending
VWAP_TREND_DISTANCE = 0.003  # 0.3% from VWAP = trending
TREND_HIGH_CONFIDENCE = 0.02  # >2% move = HIGH confidence trending

# ══ SIGNAL PARAMETERS ════════════════════════════════════════════════
RSI_PERIOD = 14
RSI_BULL_THRESH = 52
RSI_BEAR_THRESH = 48
RSI_OVERBOUGHT = 65
RSI_OVERSOLD = 35
VWAP_BAND_PCT = 0.002  # ±0.2% = ranging
PCR_BULL = 1.0
PCR_BEAR = 0.8
MAX_PAIN_DISTANCE = 150

# ══ STRATEGY A — SHORT STRANGLE ═════════════════════════════════════
SA_OTM_PCT = 0.010
SA_TARGET_PCT = 0.45
SA_STOP_MULT = 2.0
SA_MAX_LOTS = 2

# ══ STRATEGY B — DIRECTIONAL SPREAD ════════════════════════════════
SB_OTM_PCT = 0.015
SB_TARGET_PCT = 0.70
SB_STOP_DEBIT_PCT = 0.40
SB_MAX_SPREADS = 3

# ══ STRATEGY C — FUTURES SCALP ══════════════════════════════════════
SC_TARGET_PTS = 50
SC_STOP_PTS = 28
SC_MAX_LOTS = 1

# ══ STRATEGY D — WIDE IRON CONDOR ═══════════════════════════════════
SD_OTM_PCT_NORMAL = 0.010
SD_OTM_PCT_ELEVATED = 0.020
SD_OTM_PCT_HIGH = 0.025
SD_WING_PCT = 0.015
SD_TARGET_PCT = 0.30
SD_STOP_PCT = 0.80
SD_MAX_LOTS = 2
SD_ENTRY_START = "10:15"
SD_ENTRY_END = "11:30"
SD_VIX_STABLE_MINS = 45
SD_VIX_STABLE_BAND = 1.5

# ══ STRATEGY E — DEEP ITM DIRECTIONAL ════════════════════════════════
SE_DELTA_TARGET = 0.70
SE_DELTA_FILTER = 0.65  # minimum |delta| for candidate filter
SE_TARGET_PCT = 0.50
SE_STOP_PCT = 0.40
SE_MAX_LOTS = 1
SE_MIN_TREND_MOVE = 0.015
SE_ENTRY_DEADLINE = "10:45"
SE_DELTA_SCALE = 10       # moneyness-to-delta scaling factor
SE_DELTA_CLAMP_LOW = 0.05
SE_DELTA_CLAMP_HIGH = 0.95

# ══ RISK MANAGEMENT ══════════════════════════════════════════════════
CAPITAL = 1_000_000  # ₹10L base — adjust to actual
MAX_RISK_PCT_TRADE = 0.015  # 1.5% per trade
LOSS_LIMIT_CALM = 20_000
LOSS_LIMIT_NORMAL = 15_000
LOSS_LIMIT_ELEVATED = 10_000
LOSS_LIMIT_HIGH_VIX = 7_500
MONTHLY_DD_LIMIT = 60_000
MONTHLY_DD_SIZE_CUT = 0.5  # halve lot size when monthly DD limit hit

# ══ DAILY TARGETS BY REGIME ══════════════════════════════════════════
TARGET_CALM = 8_000
TARGET_NORMAL = 6_000
TARGET_ELEVATED = 4_000
TARGET_HIGH_VIX = 2_500

# ══ PAPER TRADING SIMULATION ═════════════════════════════════════════
PRICE_TICK = 0.05         # NSE F&O tick size — all buy/sell prices in multiples of 0.05
SLIPPAGE_PCT = 0.0005     # 0.05% slippage per order (ATM / futures)
SLIPPAGE_MIN_ABS = 0.25   # minimum slippage ₹0.25 per unit (tick-level floor for illiquid options)
SLIPPAGE_OTM_THRESHOLD = 50.0  # options priced below this get wider slippage
STT_OPTIONS_SELL = 0.0005  # 0.05% sell-side STT on options
STT_FUTURES = 0.0001       # 0.01% both-side STT on futures
BROKERAGE_PER_ORDER = 5.0  # ₹5 per order (Finvasia/Shoonya)

# ══ CACHE TTLs ═══════════════════════════════════════════════════════
LTP_CACHE_SEC = 2.0
VIX_CACHE_SEC = 60.0
OHLCV_CACHE_SEC = 60.0

# ══ GO-LIVE EVALUATOR THRESHOLDS ═════════════════════════════════════
GL_MIN_TRADES = 20
GL_OVERALL_WR = 60.0
GL_STRAT_A_WR = 58.0
GL_STRAT_B_WR = 45.0
GL_STRAT_C_WR = 48.0
GL_STRAT_D_WR = 45.0
GL_WIN_LOSS_RATIO = 1.3
GL_MIN_DAYS = 5
GL_WR_TREND_FLOOR = 4     # min wins in last 10 trades
GL_LATE_BUFFER_MIN = 1     # minutes after TRADE_END to flag as late

# ══ DATA PATHS ═══════════════════════════════════════════════════════
DATA_DIR = "data"
WEB_DASHBOARD_PORT = 5050

# ══ LOGGING ══════════════════════════════════════════════════════════
LOG_DIR = "logs"
LOG_LEVEL = "INFO"
LOG_MAX_BYTES = 10 * 1024 * 1024  # 10 MB per log file
LOG_BACKUP_COUNT = 5              # keep 5 rotated backups
LOG_FORMAT = "%(asctime)s [%(name)s] %(levelname)s — %(message)s"

