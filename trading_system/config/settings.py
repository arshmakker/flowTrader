"""
All system parameters live here (agents.md).

Do not hardcode values outside this file.
"""

# ══ MODE ══════════════════════════════════════════════════════════
PAPER_TRADE_MODE = True  # Flip to False to go live

# ══ INSTRUMENTS ════════════════════════════════════════════════════
# Restrict which instruments may enter trades. Empty list = all instruments active.
ACTIVE_INSTRUMENTS: list = ["NIFTY"]
NIFTY_SYMBOL = "NIFTY"
BANKNIFTY_SYMBOL = "BANKNIFTY"
NIFTY_LOT_SIZE = 65  # updated for 2026
BANKNIFTY_LOT_SIZE = 30  # updated for 2026
NIFTY_STRIKE_STEP = 50
BANKNIFTY_STRIKE_STEP = 100

# ══ SHOONYA TOKENS & SYMBOLS ══════════════════════════════════════
NIFTY_SPOT_TOKEN = "26000"
INDIA_VIX_TOKEN = "26017"
NIFTY_SPOT_KEY = "NSE|Nifty 50"  # LTP lookup key
BANKNIFTY_SPOT_KEY = "NSE|Nifty Bank"  # BUG-08: per-instrument classification
INDIA_VIX_KEY = "NSE|India VIX"
NIFTY_SPOT_EXCHANGE = "NSE"

# ══ SESSION ═════════════════════════════════════════════════════════
CLASSIFY_TIME = "10:30"
TRADE_END = "15:10"  # Hard close ALL positions (expiring and non-expiring)
TRADE_END_EXPIRY = "15:00"  # Hard close positions expiring today (30 min before expiry-day settlement squeeze)
# Entries after this time are blocked. Combined with hard TRADE_END close at 15:10,
# this ensures no unmanaged overnight exposure. Historical data: overnight carries lose
# money (0% win, avg -₹9.4k loss). 14:30 leaves 40 min for monitoring/harvest.
ENTRY_CUTOFF = "14:30"
SIGNAL_RECHECK_SEC = 30

# ══ IRON CONDOR PARAMETERS ══════════════════════════════════════════
IC_LOT_SIZE = 10  # 10 lots per instrument for greater absolute profit
IC_VIX_MAX = 30.0
IC_VIX_STABLE_MINS = 8  # LIVE-28: 15 → 8 min; opening-hour VIX swings sit outside the 8-min window
IC_VIX_STABLE_BAND = 1.5
IC_DTE_THRESHOLD = 3  # Roll to next week if current weekly < 3 DTE
# Per-instrument S/R buffer. BANKNIFTY median overnight gap is 325 pts, p85 is 803 pts;
# 400 pts covers the typical gap and places SP at P53900 which fills at <0.3% slippage.
IC_SR_BUFFER_BY_INSTRUMENT = {"NIFTY": 50, "BANKNIFTY": 400}
IC_HARVEST_PCT = 0.01  # 1% of max profit for harvest and re-entry (NIFTY default)
# Per-instrument harvest threshold, calibrated against the LIVE-12 cost stack.
# BANKNIFTY 8-leg round-trip fees ≈ ₹760 on 10 lots; break-even ratio ≈ 11.8%.
# A flat 1% threshold triggers harvests that are negative net of fees on BANKNIFTY.
IC_HARVEST_PCT_BY_INSTRUMENT = {"NIFTY": 0.02, "BANKNIFTY": 0.13}
IC_STOP_LOSS_MULT = 3.0  # 3x max profit stop-loss
IC_HARD_STOP_CONFIRM_TICKS = 2  # require 2 consecutive valid breaches before halt
# Per-instrument minimum net credit per lot. Calibrated against the LIVE-12
# cost stack (see docs/calibration_2026_04_26.md):
#   NIFTY     wide-IC break-even ≈ ₹17.38 → 18 leaves ₹0.62 margin
#   BANKNIFTY wide-IC break-even ≈ ₹23.92 → 25 leaves ₹1.08 margin
IC_MIN_CREDIT_BY_INSTRUMENT = {"NIFTY": 18.0, "BANKNIFTY": 30.0}
# LIVE-29: cap how far the S/R buffer can push a short strike from current spot.
# A wide 20-day range (e.g. BANKNIFTY 51100–57477 = 6377 pts) forces strikes into
# illiquid far-OTM territory where credit collapses and re-entry is impossible.
# When the S/R-adjusted strike exceeds this cap, the strike is clamped and a
# WARNING is logged so the operator can see when the rule fires.
IC_SR_CAP_OTM_FROM_SPOT = {"NIFTY": 400, "BANKNIFTY": 1000}
# Backtest (86 days): BANKNIFTY breach rate is 56% at 300pt OTM, drops to 10.7% at 700pt.
# S/R buffer can compress strikes below this floor — enforce it after all S/R adjustments.
IC_MIN_OTM_BANKNIFTY = 700
# LIVE-30: on VIX < 14 (quiet market) NIFTY IC credit is structurally below the
# ₹18 fee break-even — market delivers ₹3–9 regardless of strike selection.
# Skip NIFTY entry explicitly (avoid 500+ wasted quote-API calls per day) and log
# a single diagnostic per cycle instead of 125 credit-floor rejections.
IC_NIFTY_MIN_VIX = 14.0

# ══ LIVE-25: HEDGE-FIRST ENTRY SEQUENCING ════════════════════════════
# 'hedge_first' (default on this branch) routes IronCondorStrategy.enter()
# through enter_hedge_first — wings as MKT, then shorts as LMT priced off
# the actual wing fills. Converts the worst-case entry failure from unbounded
# naked short to bounded premium-at-risk. 'sequential' is the legacy path,
# retained for regression testing and operator fallback.
IC_ENTRY_MODE = "hedge_first"
#
# Wings (LC+LP) go as MKT first; shorts (SC+SP) as LMT priced off actual
# wing fills. Converts the worst-case entry failure from unbounded naked
# short to bounded premium-at-risk.
#
# Ticks above the top-of-book bid for the short-leg limit price. 0 = at bid
# (safest, highest probability of fill); 1-2 ticks adds credit at the cost of
# more Phase 5b aborts. After proving-period data, this is tunable upward.
IC_SHORT_LIMIT_OFFSET_TICKS = 0
# How far below the Phase-3 re-fetched bid to set the SC/SP SELL LMT price.
# The Phase-3→4 latency window (~50-200ms live) lets the bid drift down before
# the order reaches the broker. 2026-04-29 data: 4 cancels with gaps of
# 0.15, 0.15, 0.50, 2.00 pts. 0.50 absorbs the first three; the 2.00-pt event
# is a genuine stress move that should still cancel.
IC_SHORT_LIMIT_DRIFT_TOL = 0.50
# Phase 3 fallback when wing fills are so expensive that computed short-leg
# limits cannot achieve IC_MIN_CREDIT: 'refuse' aborts + unwinds wings; 'widen'
# re-runs strike calc with wider width; 'accept' lowers IC_MIN_CREDIT for this
# entry. Defaults to 'refuse' — cleanest, no hidden credit degradation.
IC_PHASE3_FALLBACK = "refuse"

# ══ TRADING CALENDAR (IST) ═══════════════════════════════════════════
# Used by strategy runner gates (data collection / strategy loop) and
# by data-quality guards to avoid treating holiday data as a trading day.
# Weekends are handled separately; list only non-weekend holidays here.
TRADING_HOLIDAYS_IST = {
    "2026-01-15",  # Municipal Corporation Election - Maharashtra
    "2026-01-26",  # Republic Day
    "2026-03-03",  # Holi
    "2026-03-26",  # Shri Ram Navami
    "2026-03-31",  # Shri Mahavir Jayanti
    "2026-04-03",  # Good Friday
    "2026-04-14",  # Dr. Baba Saheb Ambedkar Jayanti
    "2026-05-01",  # Maharashtra Day
    "2026-05-28",  # Bakri Id
    "2026-06-26",  # Muharram
    "2026-09-14",  # Ganesh Chaturthi
    "2026-10-02",  # Mahatma Gandhi Jayanti
    "2026-10-20",  # Dussehra
    "2026-11-10",  # Diwali-Balipratipada
    "2026-11-24",  # Prakash Gurpurb Sri Guru Nanak Dev
    "2026-12-25",  # Christmas
}

# LIVE-18: NSE pre-open session. Quotes flow here but orders queue until
# regular session opens at 09:15 — entries placed during pre-open get odd
# fills (auction matching, not continuous). Refuse entry in this window.
PRE_OPEN_START_IST = "09:00"
PRE_OPEN_END_IST = "09:15"

# LIVE-18: Muhurat sessions — days where the *regular* session is closed but
# a short trading window is open (e.g. Diwali evening). Populate from the
# NSE annual muhurat circular. Shape: list of {date, open, close} in IST.
# If present, the listed date becomes tradable ONLY within [open, close] —
# outside that window the same date is non-tradable even if the holiday
# list does not include it. Leave empty until the official circular lands.
MUHURAT_SESSIONS: list = []  # e.g. [{"date": "2026-11-08", "open": "18:15", "close": "19:15"}]


# ══ VIX TIERS & OTM DISTANCES ══════════════════════════════════════
# VIX < 14: 150-200 OTM, 50 width
VIX_LOW_LIMIT = 14.0
VIX_LOW_OTM = 150
VIX_LOW_WIDTH = 50

# VIX 14-20: 200-250 OTM, 100 width
VIX_NORMAL_LIMIT = 20.0
VIX_NORMAL_OTM = 200
VIX_NORMAL_WIDTH = 100

# VIX > 20: 300+ OTM, 150 width
VIX_HIGH_OTM = 300
VIX_HIGH_WIDTH = 150

# ══ DAY CLASSIFICATION ════════════════════════════════════════════════
TREND_MOVE_THRESHOLD = 0.015  # 1.5% from open = trending
VWAP_TREND_DISTANCE = 0.003  # 0.3% from VWAP = trending
TREND_HIGH_CONFIDENCE = 0.02  # >2% move => HIGH confidence trending


# ══ PAPER TRADING SIMULATION ═════════════════════════════════════════
PRICE_TICK = 0.05
SLIPPAGE_PCT = 0.0005
SLIPPAGE_MIN_ABS = 1.50
SLIPPAGE_OTM_THRESHOLD = 50.0
SLIPPAGE_OTM_MIN_ABS = 3.00
# Quote sanity guard to avoid corrupt option fills from bad ticks/token mixups.
PAPER_OPTION_LTP_MIN = 0.05
PAPER_OPTION_LTP_MAX = 5000.0
# LIVE-12: Full Indian F&O options cost stack. Previously only STT (stale at
# 0.05%, three Budget-revisions behind) and a flat ₹5 brokerage were applied —
# exchange txn / SEBI / stamp / GST were missing, under-stating paper costs by
# ~₹60-80 per IC leg and making LIVE-08 reconciliation unusable.
#
# Rates verified 2026-04-24 against Shoonya FAQ (brokerage) + Zerodha's charges
# page (exch/sebi/stamp/gst base) + Budget 2026 circular (STT hike to 0.15%
# effective 2026-04-01). See trading_system/core/fees.py for the calculation.
FEES_NIFTY_OPT = {
    "brokerage_per_order": 5.0,  # Shoonya flat ₹5 per executed order
    "stt_sell_pct": 0.0015,  # 0.15% on SELL premium (Budget 2026)
    "stt_exercise_pct": 0.0015,  # 0.15% on intrinsic × qty on ITM exercise
    "exch_txn_pct": 0.0000255,  # NSE F&O options = ₹25.5/crore (corrected from 0.03553% typo)
    "sebi_pct": 0.000001,  # ₹10/crore = 0.0001% of turnover, both sides
    "stamp_buy_pct": 0.00003,  # 0.003% on BUY premium only
    "gst_pct": 0.18,  # 18% of (brokerage + exch_txn + sebi)
}

# ══ LIVE ORDER POLLING ════════════════════════════════════════════════
# Seconds between each poll of single_order_history while awaiting fill.
POLL_INTERVAL_SEC = 1
# Consecutive API errors before raising OrderPollingAbandoned and halting.
MAX_POLL_ERRORS = 10
# Shoonya returns OPEN indefinitely for broker-rejected orders (RED:RULE
# collateral shortfall) even though the app shows REJECTED immediately.
# After this many seconds, attempt cancel and treat as REJECTED.
MAX_POLL_WAIT_SEC = 45

# ══ SAFETY CONTROLS ═══════════════════════════════════════════════════
# LIVE-22: Absolute daily loss ceiling in rupees.
DAILY_MAX_LOSS = 50_000
# LIVE-19: Create this file from any terminal to trigger a clean emergency stop.
HALT_FILE = "data/HALT"
# LIVE-20: PID lock file preventing duplicate process instances.
PID_FILE = "data/regimetrader.pid"
# Solo-laptop reality: macOS sleep / SIGSTOP can leave a PID alive but frozen.
# If pnl_snapshot.json is older than this when a fresh start tries to acquire
# the lock, the existing process is presumed unresponsive and we overwrite.
PID_FRESHNESS_TIMEOUT_SEC = 600

# ══ SHAKEDOWN MODE (proving-period controls) ══════════════════════════
# Tighter caps applied during the first ~10 trading days of live operation
# while LIVE-21 reconciliation builds a verifiable track record. Operator
# flips SHAKEDOWN_MODE to True before the first live session and to False
# after the reconciliation gate clears. No effect in paper mode.
SHAKEDOWN_MODE = True
# Daily loss ceiling used in place of DAILY_MAX_LOSS while SHAKEDOWN_MODE=True.
# Sized for the proving-period blast radius (1 IC × 10 lots, 50pt wing
# ≈ ₹32k max loss on NIFTY). ₹10k absorbs the harvest-cycle slippage budget
# without funding a full max-loss event.
DAILY_MAX_LOSS_SHAKEDOWN = 10_000
# Cap on new entries per (instrument, IST date) while SHAKEDOWN_MODE=True.
# 1 = at most one IC per instrument per day, including harvest re-entries.
# Triggers Axiom 3 non-participation (refuse new entries) once hit.
IC_MAX_ENTRIES_PER_SESSION_SHAKEDOWN = 1
# Cap on consecutive Phase-5b partial-fills (fresh OR re-entry) before the
# session halts. Bid drift between QuoteBook fetch and order submission can
# cancel a SELL LMT when LTP moves below the limit — normal microstructure
# noise, not a "stressed liquidity" signal. Single events skip the cycle and
# retry on the next signal; only a sustained streak (cap consecutive) halts,
# bounding the unwind-slippage tail (~₹1,200/cycle observed 2026-04-27 and
# 2026-04-28). Counter resets on a successful entry.
IC_PARTIAL_FAIL_CAP = 3
# Minutes before a Phase-5b-suspended instrument is allowed one probe re-entry.
# Bounds worst-case slippage: each failed probe costs ~₹1,200; the cool-off
# limits probe frequency. Counter resets to 0 before each probe, so 3 new
# consecutive partials are needed to re-suspend.
IC_PHASE5B_COOLOFF_MINS = 30
# LIVE handshake: live mode refuses to start unless this file exists. Paper
# mode ignores the gate. Operator creates with `touch data/LIVE_ACK` after
# blessing the run; contents are not parsed, presence is the signal. Blocks
# the failure mode where PAPER_TRADE_MODE is flipped to False without a
# deliberate operator decision (config drift, bad rebase, accidental edit).
LIVE_ACK_FILE = "data/LIVE_ACK"

# LIVE-13: NSE per-order freeze quantity for index F&O. Breaching these triggers
# an exchange-side rejection mid-entry which cascades into rollback (LIVE-03).
# Refuse upfront instead. Values current as of Apr 2026; verify against NSE's
# market-wide position limits circular when lot-size changes occur.
FREEZE_QTY_NIFTY = 1800
FREEZE_QTY_BANKNIFTY = 900


# ══ LIVE-23: OPERATOR ALERTS ═════════════════════════════════════════
# Enabled 2026-04-24 after the end-to-end ntfy smoke test passed. If
# cred.yml's ALERTS_NTFY_TOPIC_URL is missing or empty, build_channel
# degrades gracefully to LogAlertChannel with a warning — no startup
# crash, just log-only alerts — so this stays safe in CI and fresh
# clones that don't have cred.yml populated.
ALERTS_ENABLED = True
# 'ntfy' | 'log' | 'null'. 'log' writes to the process logger only; 'ntfy'
# ships to the topic URL configured in cred.yml.
ALERTS_CHANNEL = "ntfy"

# ══ CACHE TTLs ═══════════════════════════════════════════════════════
LTP_CACHE_SEC = 2.0
VIX_CACHE_SEC = 60.0
OHLCV_CACHE_SEC = 60.0

# ══ GO-LIVE EVALUATOR THRESHOLDS (IC SPECIFIC) ═══════════════════════
GL_MIN_TRADES = 20
GL_OVERALL_WR = 60.0
GL_MIN_TSL_EXITS = 5
GL_WIN_LOSS_RATIO = 1.3
GL_MIN_DAYS = 10
GL_MAX_DD_PCT = 5.0
GL_MAX_PLAUSIBLE_WR = 90.0
GL_PLAUSIBLE_WR_MIN_TRADES = 30
# LIVE-21: reconciliation gate — N days of engine-vs-broker reconciliation
# reports with every matched leg within the pinned drift threshold, and no
# unmatched legs on either side. Until that bar is met, verdict caps at
# KEEP PAPER TRADING regardless of paper-side numbers.
GL_MIN_RECONCILED_DAYS = 10
GL_RECONCILED_PRICE_DRIFT_PCT = 0.02

# ══ DATA PATHS ═══════════════════════════════════════════════════════
DATA_DIR = "data"
RECONCILIATION_DIR = "data/reconciliation"

# ══ LOGGING ══════════════════════════════════════════════════════════
LOG_DIR = "logs"
LOG_LEVEL = "INFO"
LOG_MAX_BYTES = 10 * 1024 * 1024
LOG_BACKUP_COUNT = 5
LOG_FORMAT = "%(asctime)s [%(name)s] %(levelname)s — %(message)s"
