# NIFTY INTRADAY TRADING SYSTEM
> **Broker:** Shoonya (Finvasia) | **Instruments:** Nifty Options · BankNifty Options · Nifty Futures | **Capital:** ₹10–20L | **Target:** ₹5,000–10,000/day

# 1. How to Use This Document With Cursor

This is a single self-contained specification. Open Cursor, attach this document, and paste Section 2 as your first prompt. Then reference section numbers as you build each module. The document is ordered to match implementation sequence — build in the order presented.

| Step | Action | Section |
| --- | --- | --- |
| 1 | Paste master prompt into Cursor chat | Section 2 |
| 2 | Show Cursor your existing file structure | Section 3 |
| 3 | Build config/settings.py first | Section 4 |
| 4 | Build core modules one file at a time | Sections 5–12 |
| 5 | Build paper trading layer | Sections 13–16 |
| 6 | Build dashboards | Sections 17–18 |
| 7 | Update main.py last | Section 19 |
| 8 | Run go-live evaluator until all 15 criteria green | Section 20 |

> ⚠️  Do not skip Section 4 (settings.py). Every parameter must be externalised before any strategy code is written. Cursor will hardcode values if settings.py does not exist first.

---

# 2. Master Prompt — Paste This Into Cursor First

Copy everything inside the code block below and paste it as your first message to Cursor:

```
You are a senior Python quant developer. I have an existing Shoonya
(Finvasia) broker integration with these working modules:
  - auth.py             : login and token management
  - market_data.py      : live LTP feed and OHLCV data
  - order_manager.py    : place_order(), get_order_status()
  - position_tracker.py : open position management
  - options_chain.py    : options chain with greeks

DO NOT modify these existing files. Build everything new in separate modules.

YOUR TASK: Build a complete intraday trading system on top of this existing
foundation. The system trades Nifty Options, BankNifty Options, and Nifty
Futures from 10:00 AM to 2:15 PM IST every trading day.

FIRST ACTION: Audit existing codebase for any old strategy or signal logic.
Comment it out with header:
  # DEPRECATED — replaced by new strategy engine [date]
Do not delete any files.

THEN: Build the complete system as defined in this specification document.
Implement one module at a time. After each module, confirm it is importable
and unit-testable before moving to the next.

KEY CONSTRAINTS:
  - Single flag PAPER_TRADE_MODE in settings.py controls live vs paper
  - All parameters in settings.py — no hardcoded values anywhere else
  - All exceptions must log with full context — no silent failures
  - Hard close ALL positions at 14:15 IST — no exceptions
  - One strategy active at a time — no concurrent positions
  - Daily loss limit halts all trading for the day when hit
  - Never place real orders when PAPER_TRADE_MODE = True
```

---

# 3. Complete Folder Structure

```
trading_system/
├── existing/                      ← YOUR EXISTING FILES — DO NOT TOUCH
│   ├── auth.py
│   ├── market_data.py
│   ├── order_manager.py
│   ├── position_tracker.py
│   └── options_chain.py
│
├── config/
│   └── settings.py                ← BUILD FIRST
│
├── core/                          ← BUILD IN ORDER
│   ├── regime_filter.py           (1) VIX regime + routing
│   ├── day_classifier.py          (2) RANGING vs TRENDING at 10:30 AM
│   ├── signal_engine.py           (3) VWAP, RSI, PCR, max pain
│   ├── strategy_a.py              (4) Short Strangle
│   ├── strategy_b.py              (5) Directional Spread
│   ├── strategy_c.py              (6) Futures Scalp
│   ├── strategy_d.py              (7) Wide Iron Condor
│   ├── strategy_e.py              (8) Deep ITM Directional
│   ├── risk_manager.py            (9) Sizing, daily limits
│   ├── daily_target.py            (10) Dynamic revenue target
│   └── trade_logger.py            (11) CSV + log writer
│
├── paper/                         ← PAPER TRADING LAYER
│   ├── paper_order_manager.py
│   ├── paper_position_tracker.py
│   ├── paper_pnl_engine.py
│   └── go_live_evaluator.py
│
├── dashboard/
│   ├── terminal_dashboard.py      ← rich live console
│   └── web_dashboard.py           ← Flask at localhost:5000
│       └── templates/
│           └── dashboard.html
│
├── data/                          ← auto-created at runtime
│   ├── paper_trades.csv
│   ├── paper_signals.log
│   └── paper_summary.json
│
└── main.py                        ← BUILD LAST
```

---

# 4. config/settings.py — Complete Parameter File

> Build this file first. Every other module imports from here. No magic numbers anywhere else.

```python
# ══ MODE ══════════════════════════════════════════════════════════
PAPER_TRADE_MODE       = True       # Flip to False to go live

# ══ INSTRUMENTS ════════════════════════════════════════════════════
NIFTY_SYMBOL           = 'NIFTY'
BANKNIFTY_SYMBOL       = 'BANKNIFTY'
NIFTY_LOT_SIZE         = 25
BANKNIFTY_LOT_SIZE     = 15
NIFTY_STRIKE_STEP      = 50
BANKNIFTY_STRIKE_STEP  = 100

# ══ SESSION ═════════════════════════════════════════════════════════
TRADE_START            = '10:00'
CLASSIFY_TIME          = '10:30'    # Day classification runs at this time
TRADE_END              = '14:15'    # Hard close ALL positions
SIGNAL_RECHECK_SEC     = 60

# ══ VIX REGIMES ══════════════════════════════════════════════════════
VIX_CALM               = 13.0
VIX_NORMAL_HIGH        = 17.0
VIX_DANGER             = 20.0

# ══ DAY CLASSIFICATION ════════════════════════════════════════════════
TREND_MOVE_THRESHOLD   = 0.015      # 1.5% from open = trending
VWAP_TREND_DISTANCE    = 0.003      # 0.3% from VWAP = trending

# ══ SIGNAL PARAMETERS ════════════════════════════════════════════════
RSI_PERIOD             = 14
RSI_BULL_THRESH        = 52
RSI_BEAR_THRESH        = 48
RSI_OVERBOUGHT         = 65
RSI_OVERSOLD           = 35
VWAP_BAND_PCT          = 0.002      # ±0.2% = ranging
PCR_BULL               = 1.0
PCR_BEAR               = 0.8
MAX_PAIN_DISTANCE      = 150

# ══ STRATEGY A — SHORT STRANGLE ═════════════════════════════════════
SA_OTM_PCT             = 0.010
SA_TARGET_PCT          = 0.45
SA_STOP_MULT           = 2.0
SA_MAX_LOTS            = 2

# ══ STRATEGY B — DIRECTIONAL SPREAD ════════════════════════════════
SB_OTM_PCT             = 0.015
SB_TARGET_PCT          = 0.70
SB_STOP_DEBIT_PCT      = 0.40
SB_MAX_SPREADS         = 3

# ══ STRATEGY C — FUTURES SCALP ══════════════════════════════════════
SC_TARGET_PTS          = 50
SC_STOP_PTS            = 28
SC_MAX_LOTS            = 1

# ══ STRATEGY D — WIDE IRON CONDOR ═══════════════════════════════════
SD_OTM_PCT_NORMAL      = 0.010
SD_OTM_PCT_ELEVATED    = 0.020
SD_OTM_PCT_HIGH        = 0.025
SD_WING_PCT            = 0.015
SD_TARGET_PCT          = 0.30
SD_STOP_PCT            = 0.80
SD_MAX_LOTS            = 2
SD_ENTRY_START         = '10:15'
SD_ENTRY_END           = '11:30'
SD_VIX_STABLE_MINS     = 45
SD_VIX_STABLE_BAND     = 1.5        # Max VIX range in stability window

# ══ STRATEGY E — DEEP ITM DIRECTIONAL ════════════════════════════════
SE_DELTA_TARGET        = 0.70
SE_TARGET_PCT          = 0.50
SE_STOP_PCT            = 0.40
SE_MAX_LOTS            = 1
SE_MIN_TREND_MOVE      = 0.015
SE_ENTRY_DEADLINE      = '10:45'

# ══ RISK MANAGEMENT ══════════════════════════════════════════════════
CAPITAL                = 1000000    # ₹10L base — adjust to actual
MAX_RISK_PCT_TRADE     = 0.015      # 1.5% per trade
LOSS_LIMIT_CALM        = 20000
LOSS_LIMIT_NORMAL      = 15000
LOSS_LIMIT_ELEVATED    = 10000
LOSS_LIMIT_HIGH_VIX    = 7500
MONTHLY_DD_LIMIT       = 60000

# ══ DAILY TARGETS BY REGIME ══════════════════════════════════════════
TARGET_CALM            = 8000
TARGET_NORMAL          = 6000
TARGET_ELEVATED        = 4000
TARGET_HIGH_VIX        = 2500

# ══ SHOONYA SYMBOL FORMATS ═══════════════════════════════════════════
# Futures:  'NFO|NIFTY25JANFUT'
# Options:  'NFO|NIFTY25JAN24000CE'
# VIX:      'NSE|India VIX'
# Spot:     'NSE|Nifty 50'
```

---

# 5. core/regime_filter.py

The first gate in the daily loop. Returns a routing dict that tells main.py which strategies are allowed, forbidden, what size to use, and what the daily revenue target is. Never halts trading — always routes to some strategy.

```python
class RegimeFilter:
def get_vix(self) -> float:
# Fetch India VIX live from Shoonya: symbol 'NSE|India VIX'
# Cache for 60 seconds to avoid excess API calls
def get_regime(self) -> str:
v = self.get_vix()
if v < VIX_CALM:         return 'CALM'
elif v < VIX_NORMAL_HIGH: return 'NORMAL'
elif v < VIX_DANGER:     return 'ELEVATED'
else:                    return 'DANGER'
def size_multiplier(self) -> float:
return {'CALM': 1.0, 'NORMAL': 0.5, 'ELEVATED': 0.3, 'DANGER': 0.25}
.get(self.get_regime(), 0.25)
def daily_loss_limit(self) -> float:
return {'CALM': LOSS_LIMIT_CALM, 'NORMAL': LOSS_LIMIT_NORMAL,
'ELEVATED': LOSS_LIMIT_ELEVATED, 'DANGER': LOSS_LIMIT_HIGH_VIX}
.get(self.get_regime(), LOSS_LIMIT_HIGH_VIX)
def get_routing(self, day_type: str) -> dict:
regime = self.get_regime()
mult   = self.size_multiplier()
routes = {
('CALM',     'RANGING')      : dict(primary=['A'], secondary=['B','C'],  forbidden=['D','E'], target=TARGET_CALM,     mult=mult),
('CALM',     'TRENDING_UP')  : dict(primary=['B'], secondary=['C'],      forbidden=['D','E'], target=TARGET_CALM,     mult=mult),
('CALM',     'TRENDING_DOWN'): dict(primary=['B'], secondary=['C'],      forbidden=['D','E'], target=TARGET_CALM,     mult=mult),
('NORMAL',   'RANGING')      : dict(primary=['A'], secondary=['B'],      forbidden=['C','D','E'], target=TARGET_NORMAL,  mult=mult),
('NORMAL',   'TRENDING_UP')  : dict(primary=['B'], secondary=[],        forbidden=['A','D','E'], target=TARGET_NORMAL,  mult=mult),
('NORMAL',   'TRENDING_DOWN'): dict(primary=['B'], secondary=[],        forbidden=['A','D','E'], target=TARGET_NORMAL,  mult=mult),
('ELEVATED', 'RANGING')      : dict(primary=['D'], secondary=[],        forbidden=['A','B','C','E'], target=TARGET_ELEVATED, mult=mult),
('ELEVATED', 'TRENDING_UP')  : dict(primary=['E'], secondary=['D'],     forbidden=['A','B','C'], target=TARGET_ELEVATED, mult=mult),
('ELEVATED', 'TRENDING_DOWN'): dict(primary=['E'], secondary=['D'],     forbidden=['A','B','C'], target=TARGET_ELEVATED, mult=mult),
('DANGER',   'RANGING')      : dict(primary=['D'], secondary=[],        forbidden=['A','B','C','E'], target=TARGET_HIGH_VIX, mult=mult),
('DANGER',   'TRENDING_UP')  : dict(primary=['E'], secondary=['D'],     forbidden=['A','B','C'], target=TARGET_HIGH_VIX, mult=mult),
('DANGER',   'TRENDING_DOWN'): dict(primary=['E'], secondary=['D'],     forbidden=['A','B','C'], target=TARGET_HIGH_VIX, mult=mult),
}
return routes.get((regime, day_type),
dict(primary=['D'], secondary=[], forbidden=['A','B','C','E'],
target=TARGET_HIGH_VIX, mult=0.25))
```

---

# 6. core/day_classifier.py

Runs once at CLASSIFY_TIME (10:30 AM). Classifies the day as RANGING, TRENDING_UP, or TRENDING_DOWN based on price move from open and distance from VWAP. Classification is locked for the day — cannot be overridden.

```python
from dataclasses import dataclass
@dataclass
class DayClassification:
day_type: str           # 'RANGING' | 'TRENDING_UP' | 'TRENDING_DOWN'
confidence: str         # 'HIGH' | 'MEDIUM' | 'LOW'
open_price: float
current_price: float
move_pct: float
vwap_distance_pct: float
classified_at: str
class DayClassifier:
def __init__(self, market_data, signal_engine):
self.md  = market_data
self.se  = signal_engine
self._result = None
def classify(self) -> DayClassification:
if self._result: return self._result   # locked once set
open_px  = self.md.get_open_price('NIFTY')
current  = self.md.get_ltp('NSE|Nifty 50')
move_pct = (current - open_px) / open_px
vwap     = self.se.compute_vwap_value()
vwap_dist = abs(current - vwap) / vwap
abs_move  = abs(move_pct)
if abs_move >= TREND_MOVE_THRESHOLD and vwap_dist >= VWAP_TREND_DISTANCE:
day_type   = 'TRENDING_UP' if move_pct > 0 else 'TRENDING_DOWN'
confidence = 'HIGH' if abs_move > 0.02 else 'MEDIUM'
elif abs_move >= TREND_MOVE_THRESHOLD:
day_type, confidence = 'RANGING', 'MEDIUM'  # big move but near VWAP
else:
day_type, confidence = 'RANGING', 'HIGH'
self._result = DayClassification(
day_type=day_type, confidence=confidence,
open_price=open_px, current_price=current,
move_pct=round(move_pct*100, 2),
vwap_distance_pct=round(vwap_dist*100, 2),
classified_at=datetime.now().strftime('%H:%M:%S')
)
return self._result
def reset(self): self._result = None   # call at start of each day
```

---

# 7. core/signal_engine.py

Computes four independent signals. Returns a SignalResult with a consensus vote and confidence score (0–4). VWAP and RSI recompute every 60 seconds. PCR every 5 minutes. Max pain once at market open.

```python
from dataclasses import dataclass
@dataclass
class SignalResult:
vwap_bias: str        # 'BULL' | 'BEAR' | 'RANGE'
rsi_signal: str       # 'BULL' | 'BEAR' | 'NEUTRAL' | 'OB' | 'OS'
pcr_signal: str       # 'BULL' | 'BEAR' | 'NEUTRAL'
max_pain: float
max_pain_signal: str  # 'BULL' | 'BEAR' | 'NEUTRAL'
consensus: str        # 'BULL' | 'BEAR' | 'RANGE' — majority vote
confidence: int       # 0–4 signals in agreement
class SignalEngine:
def compute_vwap(self, ohlcv_df) -> str:
# VWAP = sum(((H+L+C)/3) * V) / sum(V), reset daily at 09:15
# If price > VWAP * (1 + VWAP_BAND_PCT): return 'BULL'
# If price < VWAP * (1 - VWAP_BAND_PCT): return 'BEAR'
# Else: return 'RANGE'
def compute_vwap_value(self, ohlcv_df) -> float:
# Returns raw VWAP float (used by DayClassifier)
def compute_rsi(self, close_series) -> str:
# Wilder RSI on 15-min candles, period = RSI_PERIOD
# Cross above 50 from below (prev < 50, now >= RSI_BULL_THRESH): BULL
# Cross below 50 from above (prev > 50, now <= RSI_BEAR_THRESH): BEAR
# >= RSI_OVERBOUGHT: OB  |  <= RSI_OVERSOLD: OS  |  Else: NEUTRAL
def compute_pcr(self, chain_data) -> str:
# Sum all Put OI / Sum all Call OI across all strikes
# > PCR_BULL: BULL  |  < PCR_BEAR: BEAR  |  else: NEUTRAL
def compute_max_pain(self, chain_data) -> float:
# For each strike S: pain = sum over all strikes K of:
#   Call holders: max(S - K, 0) * call_OI[K]
#   Put holders:  max(K - S, 0) * put_OI[K]
# Max pain = strike with minimum total pain
def get_signals(self) -> SignalResult:
# Run all four, count agreements for consensus
# confidence = number of signals pointing same direction
# consensus  = direction with most votes (BULL/BEAR/RANGE)
```

---

# 8. core/strategy_a.py — Short Strangle

| Field | Value |
| --- | --- |
| Condition | Regime CALM or NORMAL + consensus RANGE |
| Structure | Sell SA_OTM_PCT OTM Call + Sell SA_OTM_PCT OTM Put |
| Entry time | 10:00–13:00 only |
| Target | Close when combined P&L >= SA_TARGET_PCT × premium collected |
| Stop | Close when loss >= SA_STOP_MULT × premium collected |
| Hard exit | 14:15 IST — all legs closed simultaneously |
| Size | min(SA_MAX_LOTS, risk_manager.allowed_lots()) × size_multiplier |

```python
def get_strikes(self, spot, instrument='NIFTY'):
step = NIFTY_STRIKE_STEP if instrument == 'NIFTY' else BANKNIFTY_STRIKE_STEP
call_strike = round(spot * (1 + SA_OTM_PCT) / step) * step
put_strike  = round(spot * (1 - SA_OTM_PCT) / step) * step
return call_strike, put_strike
def monitor(self):
# Fetch current LTP for both legs
current_combined = call_ltp + put_ltp
pnl = self.premium_received - current_combined
if pnl >= self.premium_received * SA_TARGET_PCT: self.exit('TARGET_HIT')
if pnl <= -self.premium_received * SA_STOP_MULT: self.exit('STOP_HIT')
# EXTRA: if any short strike tested (price within 50pts): check delta
# If delta of short option > 0.40: consider early exit
```

---

# 9. core/strategy_b.py — Directional Spread

| Field | Value |
| --- | --- |
| Condition | Regime CALM or NORMAL + consensus BULL or BEAR + confidence >= 3 |
| Structure | Bull: Buy ATM CE + Sell SB_OTM_PCT CE  |  Bear: Buy ATM PE + Sell SB_OTM_PCT PE |
| Entry time | 10:30–13:00 only (avoid first 30 mins) |
| Target | SB_TARGET_PCT of max profit (spread width − debit paid) |
| Stop | SB_STOP_DEBIT_PCT loss of debit paid |
| Hard exit | 14:15 IST |
| Size | min(SB_MAX_SPREADS, allowed) × size_multiplier |

```python
def enter(self, direction: str, spot: float, expiry: str):
step = NIFTY_STRIKE_STEP
atm_strike  = round(spot / step) * step
if direction == 'BULL':
buy_strike  = atm_strike
sell_strike = round(spot * (1 + SB_OTM_PCT) / step) * step
opt_type = 'CE'
else:
buy_strike  = atm_strike
sell_strike = round(spot * (1 - SB_OTM_PCT) / step) * step
opt_type = 'PE'
# Place BUY buy_strike opt_type + SELL sell_strike opt_type
# debit_paid = buy_ltp - sell_ltp
# max_profit = (sell_strike - buy_strike) - debit_paid  [for bull]
def monitor(self):
current_value = buy_ltp - sell_ltp
pnl = current_value - self.debit_paid
if pnl >= self.max_profit * SB_TARGET_PCT:          self.exit('TARGET_HIT')
if current_value <= self.debit_paid * (1 - SB_STOP_DEBIT_PCT): self.exit('STOP_HIT')
```

---

# 10. core/strategy_c.py — Futures Scalp

| Field | Value |
| --- | --- |
| Condition | Regime CALM only + confidence == 4 (ALL signals agree) + NOT expiry day + no other active strategy |
| Structure | 1 lot Nifty Futures, direction per consensus |
| Target | SC_TARGET_PTS points from entry |
| Stop | SC_STOP_PTS points from entry — HARD, no exceptions |
| Hard exit | 14:15 IST |
| Size | SC_MAX_LOTS = 1 only |

```python
def is_expiry_day(self) -> bool:
# NSE weekly expiry = every Thursday
# Check if today is Thursday (weekday() == 3)
# For monthly expiry: last Thursday of month
return datetime.today().weekday() == 3
def should_enter(self, regime, signals, active_strategies) -> bool:
return (regime == 'CALM'
and signals.confidence == 4
and not self.is_expiry_day()
and not any(active_strategies.values())
and not self.is_active())
```

---

# 11. core/strategy_d.py — Wide Iron Condor

Primary strategy for elevated/high VIX ranging days. Uses wider strikes calibrated to VIX level. Requires VIX stabilisation before entry. Lower profit target compensates for higher premium collected.

| Field | Value |
| --- | --- |
| Condition | VIX >= 17 + day_type RANGING + VIX stable for SD_VIX_STABLE_MINS + time in SD_ENTRY_START–SD_ENTRY_END |
| Structure | Sell OTM CE + Sell OTM PE (width per VIX) + Buy wing CE + Buy wing PE |
| OTM width | VIX 17–20: SD_OTM_PCT_ELEVATED (2%)  |  VIX > 20: SD_OTM_PCT_HIGH (2.5%) |
| Wings | SD_WING_PCT (1.5%) beyond short strikes — always buy wings, no naked legs |
| Target | SD_TARGET_PCT (30%) of net premium collected |
| Stop | SD_STOP_PCT (80%) of net premium collected |
| Hard exit | 14:15 IST — close all 4 legs simultaneously |
| Size | SD_MAX_LOTS × size_multiplier (0.25–0.30 on high VIX days) |

```python
def _get_otm_pct(self, vix: float) -> float:
if vix < VIX_NORMAL_HIGH: return SD_OTM_PCT_NORMAL
elif vix < VIX_DANGER:   return SD_OTM_PCT_ELEVATED
else:                    return SD_OTM_PCT_HIGH
def _vix_is_stable(self, vix_history: list) -> bool:
# vix_history = [(timestamp, vix_value), ...] last SD_VIX_STABLE_MINS
if len(vix_history) < 5: return False
recent = [v for _, v in vix_history[-10:]]
return (max(recent) - min(recent)) <= SD_VIX_STABLE_BAND
def get_strikes(self, spot: float, vix: float, step: int):
otm  = self._get_otm_pct(vix)
wing = otm + SD_WING_PCT
sc   = round(spot * (1 + otm)  / step) * step   # short call
sp   = round(spot * (1 - otm)  / step) * step   # short put
lc   = round(spot * (1 + wing) / step) * step   # long call
lp   = round(spot * (1 - wing) / step) * step   # long put
return sc, sp, lc, lp
def monitor(self):
current_value = (sc_ltp + sp_ltp) - (lc_ltp + lp_ltp)
pnl = self.net_premium - current_value
if pnl >= self.net_premium * SD_TARGET_PCT: self.exit('TARGET_HIT')
if pnl <= -self.net_premium * SD_STOP_PCT:  self.exit('STOP_HIT')
# EXTRA: if price within 50pts of either short strike:
# fetch delta of short option. If abs(delta) > 0.40: exit('STRIKE_TESTED')
```

---

# 12. core/strategy_e.py — Deep ITM Directional

Used on high VIX trending days only. Buys a deep ITM option with delta ≥ 0.70 for near-futures participation with a hard-capped maximum loss. Must enter before SE_ENTRY_DEADLINE or fall back to Strategy D.

> ⚠️  Strategy E has the lowest win rate (~45-50%). Never increase lot size to recover E losses. The payoff on winning trades compensates — trust the math.

| Field | Value |
| --- | --- |
| Condition | VIX >= 17 + day_type TRENDING + confidence HIGH or MEDIUM + time <= SE_ENTRY_DEADLINE (10:45) |
| Structure | Buy 1 Deep ITM CE (delta ≥ 0.70) if TRENDING_UP  |  Buy 1 Deep ITM PE if TRENDING_DOWN |
| Strike | Find strike in options chain where abs(delta) is closest to SE_DELTA_TARGET (0.70) |
| Target | SE_TARGET_PCT (50%) gain on premium paid |
| Stop | SE_STOP_PCT (40%) loss on premium paid |
| Hard exit | 14:15 IST  |  Also exit immediately if day_type reversal detected |
| Size | SE_MAX_LOTS = 1 only — never scale up |
| Fallback | If entry deadline missed or no suitable strike found → fall back to Strategy D |

```python
def find_deep_itm_strike(self, spot, direction, chain) -> dict | None:
opt_type = 'CE' if direction == 'UP' else 'PE'
# CE: ITM strikes are below spot
# PE: ITM strikes are above spot
candidates = [
o for o in chain
if o['type'] == opt_type
and abs(o['delta']) >= 0.65
and (o['strike'] < spot if opt_type == 'CE' else o['strike'] > spot)
]
if not candidates: return None
return min(candidates, key=lambda o: abs(abs(o['delta']) - SE_DELTA_TARGET))
def should_enter(self, regime, day_class, now) -> bool:
return (regime.get_vix() >= VIX_NORMAL_HIGH
and day_class.day_type in ['TRENDING_UP', 'TRENDING_DOWN']
and day_class.confidence in ['HIGH', 'MEDIUM']
and now <= time(*map(int, SE_ENTRY_DEADLINE.split(':')))
and not self.is_active())
def monitor(self):
ltp = self.md.get_ltp(self.option_symbol)
if ltp >= self.target_price: self.exit('TARGET_HIT')
if ltp <= self.stop_price:   self.exit('STOP_HIT')
# Direction reversal check: re-fetch day_class
# If day_type changed from entry direction → exit('DIRECTION_REVERSAL')
```

---

# 13. core/risk_manager.py

```python
class RiskManager:
def __init__(self):
self.daily_pnl      = 0.0
self.monthly_pnl    = 0.0
self.trades_today   = 0
self.halted         = False
def update_pnl(self, pnl: float, regime_filter):
self.daily_pnl   += pnl
self.monthly_pnl += pnl
limit = regime_filter.daily_loss_limit()
if self.daily_pnl <= -limit:
self.halted = True
log.critical(f'DAILY LOSS LIMIT ₹{limit:,.0f} HIT — HALTED')
def can_trade(self) -> bool:
return not self.halted
def allowed_lots(self, strategy: str, size_mult: float) -> int:
base = {'A': SA_MAX_LOTS, 'B': SB_MAX_SPREADS,
'C': SC_MAX_LOTS, 'D': SD_MAX_LOTS, 'E': SE_MAX_LOTS}[strategy]
if self.monthly_pnl <= -MONTHLY_DD_LIMIT:
size_mult = size_mult * 0.5   # halve size if monthly limit hit
return max(1, int(base * size_mult))
def reset_daily(self):
self.daily_pnl  = 0.0
self.trades_today = 0
self.halted     = False
```

---

# 14. core/daily_target.py

```python
class DailyTarget:
def __init__(self):
self._target = 0
self._hit    = False
def set(self, amount: float):
self._target, self._hit = amount, False
def is_hit(self, current_pnl: float) -> bool:
if not self._hit and current_pnl >= self._target:
self._hit = True
log.info(f'Daily target ₹{self._target:,.0f} reached — no new entries')
return self._hit
def progress(self, pnl: float) -> str:
pct = min(pnl / self._target * 100, 100) if self._target else 0
return f'₹{pnl:,.0f} / ₹{self._target:,.0f}  ({pct:.0f}%)'
def reset(self): self._target = 0; self._hit = False
```

---

# 15. core/trade_logger.py

Writes two output files. Every completed trade gets one CSV row. Every signal evaluation gets one log line. These files are the source of truth for the go-live evaluator and web dashboard.

```python
# paper_trades.csv columns (all strategies):
COLUMNS = [
'trade_id', 'date', 'time_entry', 'time_exit', 'strategy',
'instrument', 'direction', 'strike_1', 'strike_2',
'strike_3', 'strike_4',     # D uses all 4
'entry_price', 'exit_price', 'gross_pnl', 'costs', 'net_pnl',
'exit_reason',               # TARGET_HIT|STOP_HIT|HARD_EXIT|RISK_LIMIT|DIRECTION_REVERSAL|STRIKE_TESTED
'duration_mins', 'lots',
'vix_entry', 'regime_entry', 'day_type',
'vwap_bias', 'rsi_signal', 'pcr_signal', 'max_pain',
'signal_confidence',         # 0-4
'daily_target', 'target_hit_today',
'paper'                      # True in paper mode
]
# paper_signals.log format (append-only):
# [10:31:42] VIX=12.4 CALM | DAY=RANGING HIGH | ROUTE=A,B
#            VWAP=RANGE RSI=NEUTRAL PCR=BULL CONF=2 | ACTION=STRATEGY_A_ENTER
```

---

# 16. paper/ — Paper Trading Layer

All four modules have identical interfaces to their real counterparts. Strategies call them without knowing which mode is active. The PAPER_TRADE_MODE flag in main.py controls which gets injected.

## 16.1  paper_order_manager.py

```python
class PaperOrderManager:
# Identical interface to existing order_manager.py
# Fills at live LTP with realistic simulation:
def place_order(self, buy_or_sell, tradingsymbol, quantity,
price_type='MKT', price=0.0) -> dict:
ltp = self.market_data.get_ltp(tradingsymbol)
# Slippage: MKT BUY +0.05%, MKT SELL -0.05%
slip = ltp * 0.0005
fill = (ltp + slip) if buy_or_sell == 'B' else (ltp - slip)
# Costs: STT (0.05% sell-side options, 0.01% futures both sides)
#        Brokerage: flat ₹20 per order
stt  = self._calc_stt(tradingsymbol, buy_or_sell, fill, quantity)
return { 'order_id': str(self._next_id()), 'symbol': tradingsymbol,
'side': buy_or_sell, 'quantity': quantity,
'fill_price': round(fill, 2), 'stt': round(stt, 2),
'brokerage': 20.0, 'status': 'COMPLETE',
'timestamp': datetime.now().isoformat(), 'paper': True }
```

## 16.2  paper_position_tracker.py

```python
class PaperPositionTracker:
# Tracks open positions in memory
# add_position(order) — builds position from filled order
# close_position(symbol, exit_price) -> float (net P&L after costs)
# get_unrealised_pnl(market_data) -> float (mark-to-market)
# get_open_positions() -> list[dict]
```

## 16.3  paper_pnl_engine.py

```python
class PaperPnLEngine:
# Tracks realised P&L, win/loss counts per strategy
# Writes data/paper_summary.json every time a trade closes
# summary.json schema:
# { timestamp, realised_pnl, unrealised_pnl, total_pnl,
#   total_trades, winning_trades, win_rate_pct,
#   strategy_stats: { A: {trades, total_pnl, win_rate},
#                     B: ..., C: ..., D: ..., E: ... } }
```

---

# 17. dashboard/terminal_dashboard.py

Uses the rich library. Runs in a daemon thread. Reads paper_summary.json every 5 seconds — never computes P&L itself.

```
# pip install rich
# 4-panel layout, refreshes every 5 seconds:
#
# ┌─── HEADER ┐
# │  PAPER TRADING   VIX: 12.4 [CALM]   10:42:15   Mode: PAPER │
# ├─── TODAY ──────────┬── WIN RATE ────┬── OPEN POSITION ─────┤
# │  Total:  ₹+4,250   │  67.3%         │  Strategy A          │
# │  Realised: ₹+3,150 │  8/12 trades   │  Short Strangle      │
# │  Unrealised: ₹+1,100│ Target: >60%  │  Unrl P&L: ₹+820    │
# ├─── STRATEGY BREAKDOWN ─────────────────────────────────────┤
# │  A: 5 trades  ₹+3,100  80% win                             │
# │  B: 4 trades  ₹+900    60% win                             │
# │  C: 2 trades  ₹-750    50% win                             │
# │  D: 1 trade   ₹+0      --                                  │
# │  E: 0 trades  --       --                                  │
# ├─── LAST 5 SIGNALS ─────────────────────────────────────────┤
# │  10:41 CALM RANGE CONF=2 → STRAT A ACTIVE                  │
# └────────────┘
# Colours:
# P&L: green if > 0, red if < 0
# Win rate: green > 60%, yellow 50-60%, red < 50%
# Regime badge: green CALM, yellow NORMAL/ELEVATED, red DANGER
# Blinking dot next to active position name
```

---

# 18. dashboard/web_dashboard.py — localhost:5000

Flask app. Single HTML page. Dark theme. No build step — plain HTML + Chart.js CDN. Reads paper_trades.csv and paper_summary.json. Auto-updates every 10 seconds via JS fetch.

| Route | Data Source | Update Frequency |
| --- | --- | --- |
| / | Renders dashboard.html | Full page load |
| /api/summary | data/paper_summary.json | JS polls every 5s |
| /api/trades | data/paper_trades.csv (last 50 rows) | JS polls every 30s |
| /api/signals | data/paper_signals.log (last 100 lines) | JS polls every 10s |
| /api/golive | GoLiveEvaluator.evaluate() | JS polls every 60s |

Page sections (top to bottom): header bar with VIX + regime + mode, KPI cards row (total P&L / win rate / trades today / daily loss used), strategy breakdown table, equity curve (Chart.js line chart — cumulative P&L), trade log table (last 20 trades, colour coded), signal log (last 20 lines, monospace), go-live checklist panel.

```
# Colour scheme:
# background: #0a0a0f  |  surface: #12121a  |  border: #2a2a40
# accent: #2E4BCC       |  profit: #00ff9d   |  loss: #ff3d5a
# warn: #ffb800         |  muted: #6b6b8a
# Chart.js CDN: https://cdn.jsdelivr.net/npm/chart.js
# No React, no npm, no build step — pure HTML/CSS/JS only
```

---

# 19. main.py — Complete Orchestrator

> ⚠️  Build main.py last. Every module it imports must already be tested individually before wiring together here.

```python
import threading, time, sys
from datetime import datetime, time as dtime
from config.settings import *
from core.regime_filter import RegimeFilter
from core.day_classifier import DayClassifier
from core.signal_engine import SignalEngine
from core.strategy_a import StrategyA
from core.strategy_b import StrategyB
from core.strategy_c import StrategyC
from core.strategy_d import StrategyD
from core.strategy_e import StrategyE
from core.risk_manager import RiskManager
from core.daily_target import DailyTarget
from core.trade_logger import TradeLogger
from paper.go_live_evaluator import GoLiveEvaluator
if PAPER_TRADE_MODE:
from paper.paper_order_manager import PaperOrderManager as OrderMgr
from paper.paper_position_tracker import PaperPositionTracker as PosMgr
from paper.paper_pnl_engine import PaperPnLEngine as PnLEngine
from dashboard.terminal_dashboard import TerminalDashboard
from dashboard.web_dashboard import WebDashboard
else:
from existing.order_manager import OrderManager as OrderMgr
from existing.position_tracker import PositionTracker as PosMgr
print('[LIVE MODE] Real orders will be placed.')
print('Type YES to confirm: ', end='')
if input().strip() != 'YES': sys.exit(0)
def run():
# ── Init ────
from existing.auth import ShoonyaAuth
from existing.market_data import MarketData
auth     = ShoonyaAuth(); auth.login()
md       = MarketData(auth)
order_mgr = OrderMgr(md) if PAPER_TRADE_MODE else OrderMgr()
pos_mgr  = PosMgr()
pnl      = PaperPnLEngine(pos_mgr, md, TradeLogger()) if PAPER_TRADE_MODE else None
regime   = RegimeFilter(md)
signals  = SignalEngine(md)
classifier = DayClassifier(md, signals)
risk     = RiskManager()
target   = DailyTarget()
logger   = TradeLogger()
evaluator = GoLiveEvaluator()
strats   = { 'A': StrategyA(order_mgr, pos_mgr, md, logger),
'B': StrategyB(order_mgr, pos_mgr, md, logger),
'C': StrategyC(order_mgr, pos_mgr, md, logger),
'D': StrategyD(order_mgr, pos_mgr, md, logger),
'E': StrategyE(order_mgr, pos_mgr, md, logger) }
if PAPER_TRADE_MODE:
threading.Thread(target=TerminalDashboard(pnl, logger).run, daemon=True).start()
threading.Thread(target=WebDashboard(pnl, logger, evaluator).run, daemon=True).start()
print('Paper trading started. Dashboard: http://localhost:5000')
routing, day_class = None, None
# ── Main loop ────────────────────────────────────────────────
while True:
now = datetime.now().time()
# Hard close
if now >= dtime(14, 15):
for s in strats.values():
if s.is_active(): s.exit('HARD_EXIT_TIME')
classifier.reset(); target.reset(); risk.reset_daily()
routing, day_class = None, None
print(f'Session closed. Daily P&L: {pnl.realised_pnl if pnl else "N/A"}')
time.sleep(3600)   # sleep till next day
continue
if now < dtime(10, 0): time.sleep(30); continue
# Day classification at 10:30
if now >= dtime(10, 30) and day_class is None:
day_class = classifier.classify()
routing   = regime.get_routing(day_class.day_type)
target.set(routing['target'])
risk.update_loss_limit(regime.daily_loss_limit())
logger.log_signal(f'DAY CLASSIFIED: {day_class.day_type} '
f'({day_class.confidence}) | ROUTE: {routing["primary"]}')
if day_class is None: time.sleep(30); continue
# Risk gate
if not risk.can_trade(): time.sleep(60); continue
# Daily target gate
if pnl and target.is_hit(pnl.realised_pnl):
for s in strats.values():
if s.is_active(): s.monitor()
time.sleep(60); continue
# Signals
sig  = signals.get_signals()
spot = md.get_ltp('NSE|Nifty 50')
logger.log_signal(regime, day_class, routing, sig)
# Monitor active
for s in strats.values():
if s.is_active(): s.monitor()
# Enter if nothing active
if not any(s.is_active() for s in strats.values()):
for key in routing['primary'] + routing['secondary']:
s = strats[key]
if key not in routing['forbidden']:
if s.should_enter(regime, sig, day_class, now):
s.enter(spot, expiry=md.get_nearest_expiry())
break
time.sleep(SIGNAL_RECHECK_SEC)
if __name__ == '__main__':
run()
```

---

# 20. paper/go_live_evaluator.py — All 15 Criteria

All 15 criteria must be green before flipping PAPER_TRADE_MODE = False. The web dashboard shows this as a live checklist with score (e.g. 11/15).

| # | Criterion | Threshold | Rationale |
| --- | --- | --- | --- |
| 1 | Minimum total trades | ≥ 20 | Statistical significance |
| 2 | Overall win rate | > 60% | Primary target |
| 3 | Strategy A win rate | > 58% | Core income strategy |
| 4 | Strategy B win rate | > 45% | Directional — lower bar |
| 5 | Strategy C win rate | > 48% | Positive EV confirmation |
| 6 | Strategy D win rate | > 45% | High VIX ranging |
| 7 | Strategy E attempted | ≥ 1 trade or no DANGER day occurred | Coverage check |
| 8 | Net realised P&L | > 0 after all costs | Must be profitable on paper |
| 9 | Average win > average loss | Ratio ≥ 1.3× | Positive expected value |
| 10 | No daily loss limit breaches | 0 breaches | Risk discipline proven |
| 11 | No positions past 14:15 | 0 violations | Hard exit rule proven |
| 12 | No routing violations | 0 A/B on DANGER days | System discipline |
| 13 | Daily target respected | 0 overtrading after target hit | Profit protection |
| 14 | Minimum paper trading days | ≥ 5 trading days | Cover at least one expiry |
| 15 | Win rate trend | Improving or stable over last 10 trades | Not deteriorating |

```python
class GoLiveEvaluator:
def evaluate(self, summary: dict, trades_df) -> dict:
c = {}
s = summary['strategy_stats']
c['min_trades']      = summary['total_trades'] >= 20
c['overall_wr']      = summary['win_rate_pct'] > 60.0
c['strat_a_wr']      = s['A']['win_rate'] > 58.0 if s['A']['trades'] > 0 else False
c['strat_b_wr']      = s['B']['win_rate'] > 45.0 if s['B']['trades'] > 0 else False
c['strat_c_wr']      = s['C']['win_rate'] > 48.0 if s['C']['trades'] > 0 else True
c['strat_d_wr']      = s['D']['win_rate'] > 45.0 if s['D']['trades'] > 0 else True
c['strat_e_covered'] = s['E']['trades'] > 0 or self._no_danger_day(trades_df)
c['net_pnl_pos']     = summary['realised_pnl'] > 0
c['ev_positive']     = self._win_loss_ratio(trades_df) >= 1.3
c['no_loss_breach']  = self._no_daily_breach(trades_df)
c['no_late_pos']     = self._no_late_positions(trades_df)
c['no_routing_viol'] = self._no_routing_violations(trades_df)
c['target_respected']= self._no_overtrade(trades_df)
c['min_days']        = self._count_days(trades_df) >= 5
c['wr_trend']        = self._wr_trend_ok(trades_df)
score = sum(c.values())
return { 'checks': c, 'score': score, 'total': 15,
'all_green': score == 15,
'verdict': 'GO LIVE ✓' if score == 15 else f'KEEP PAPER TRADING ({score}/15)' }
```

---

# 21. Shoonya API Reference

## 21.1  Symbol Formats

```python
# Nifty Spot (for LTP + VWAP):  'NSE|Nifty 50'
# India VIX:                     'NSE|India VIX'
# Nifty Futures:                 'NFO|NIFTY25JANFUT'
# Nifty Options:                 'NFO|NIFTY25JAN24000CE'
#                                format: SYMBOLYYMONSTRIKECE/PE
# BankNifty Options:             'NFO|BANKNIFTY25JAN51000CE'
def round_strike(spot, instrument='NIFTY') -> int:
step = NIFTY_STRIKE_STEP if instrument == 'NIFTY' else BANKNIFTY_STRIKE_STEP
return int(round(spot / step) * step)
```

## 21.2  Order Placement

```python
api.place_order(
buy_or_sell  = 'B',          # 'B' buy or 'S' sell
product_type = 'M',          # 'M' = NRML for options/futures
exchange     = 'NFO',
tradingsymbol = symbol,
quantity     = lots * lot_size,
discloseqty  = 0,
price_type   = 'MKT',        # or 'LMT'
price        = 0,
trigger_price = None,
retention    = 'DAY',
remarks      = 'strategy_tag'
)
# ALWAYS verify order status after placement:
# If status != 'COMPLETE' within 5 seconds: log warning + retry once
```

## 21.3  Options Chain

```python
api.get_option_chain(
exchange      = 'NFO',
tradingsymbol = 'NIFTY',
strikeprice   = str(int(spot)),
count         = '10'          # strikes on each side
)
# Cache result for 60 seconds
# For PCR: sum(all put OI) / sum(all call OI)
# For max pain: see signal_engine.compute_max_pain()
```

## 21.4  Live Data

```python
# Subscribe for live ticks:
api.subscribe('NSE|Nifty 50')
api.subscribe('NSE|India VIX')
# Build 15-min OHLCV in memory from tick stream
# Maintain rolling window of last 50 candles for RSI
# VWAP resets daily at 09:15
# Reconnect on WebSocket disconnect — implement auto-reconnect with backoff
```

---

# 22. Explicit Exclusions — Tell Cursor Explicitly

> ⚠️  Tell Cursor: 'Do NOT build any of the following. If found in existing code, comment out with DEPRECATED header.'

- Any strategy that adds to a losing position (averaging down, martingale)
- Any overnight position — hard close at 14:15 is non-negotiable
- Any hardcoded strike, price, or VIX value — use settings.py only
- Any momentum chasing without all signal gates passing
- Any order placement when PAPER_TRADE_MODE = True — must route to PaperOrderManager
- Silent exception handling — every except block must log with full stack trace
- Any UI that reads live data itself — dashboards read from paper_summary.json only
- Any strategy running outside the 10:00–14:15 window
- Strategy C on expiry day — is_expiry_day() check is mandatory
- Strategies A or B when routing dict has them in forbidden list

---

# 23. Testing Checklist — Verify Before Going Live

| Test | Method | Pass Condition |
| --- | --- | --- |
| settings.py imports cleanly | python -c 'from config.settings import *' | No errors |
| Regime filter VIX levels | Mock VIX = 10, 15, 18, 25 | Returns CALM/NORMAL/ELEVATED/DANGER |
| Routing table complete | Call get_routing() for all 12 combinations | No KeyError, correct strategies returned |
| Day classifier at 10:30 | Mock move = 0.5%, 1.8%, 2.5% | Correct RANGING/TRENDING returned |
| Signal engine computes | Feed mock 15-min OHLCV + chain | All 4 signals return without error |
| Strategy A strike calculation | Spot = 22000, OTM = 1% | call = 22250, put = 21750 |
| Strategy D strike calculation | Spot = 22000, VIX = 22 (2.5% OTM) | sc=22550, sp=21450, lc=22900, lp=21100 |
| Strategy E delta selection | Mock chain with varying deltas | Returns strike closest to 0.70 |
| Paper order fill simulation | Place order in paper mode | Fills at LTP±slippage, costs deducted |
| Daily loss limit halt | Simulate loss of LOSS_LIMIT_CALM + 1 | trading_halted = True, no new orders |
| Daily target stops new entries | Simulate realised_pnl = TARGET_CALM | No new entries, existing monitored |
| Hard exit at 14:15 | Set clock to 14:16 | All positions closed immediately |
| Paper summary JSON updates | Close a paper trade | data/paper_summary.json written |
| Web dashboard loads | curl localhost:5000 | 200 OK, HTML returned |
| Go-live evaluator runs | Call evaluate() with mock data | Returns dict with 15 checks |

---

# 24. Go-Live Procedure

When GoLiveEvaluator returns score = 15/15, follow this exact sequence:

- Step 1: Run one full paper trading day after all 15 criteria are green. Confirm system is stable.
- Step 2: Change PAPER_TRADE_MODE = False in settings.py.
- Step 3: Reduce all MAX_LOTS by 50% for first 3 live days (SA_MAX_LOTS = 1 etc.).
- Step 4: Compare live P&L to paper P&L daily. If within 25% variance — scale to full size after day 3.
- Step 5: If live drawdown exceeds 3× typical paper drawdown in first week — pause and re-paper-trade.
```python
# The only line that changes when going live:
PAPER_TRADE_MODE = False   # settings.py
# Keep all paper modules in codebase permanently.
# Re-enable paper mode any time you change strategy logic.
```

System Summary

| Component | Files | Purpose |
| --- | --- | --- |
| Configuration | config/settings.py | Single source of all parameters |
| Regime + Routing | core/regime_filter.py | VIX → which strategy runs |
| Day Classification | core/day_classifier.py | RANGING vs TRENDING at 10:30 |
| Signals | core/signal_engine.py | VWAP + RSI + PCR + Max Pain |
| Strategies | core/strategy_a–e.py | 5 strategies covering all conditions |
| Risk | core/risk_manager.py + daily_target.py | Capital protection + profit lock |
| Paper Layer | paper/paper_*.py | Simulation on live market data |
| Dashboards | dashboard/terminal_dashboard.py + web_dashboard.py | Real-time monitoring |
| Go-Live Gate | paper/go_live_evaluator.py | 15 criteria — all must be green |
| Orchestrator | main.py | Wires everything together |

The edge is not in any single strategy.

It is in deploying the right strategy for the right condition — every single day.
