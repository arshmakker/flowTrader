"""
Backtest that mimics the main trading flow (Convex + Iron Condor only).

Flow (aligned with main.py + strategy_runner.run_strategy_with_regime):
1. Load market data (spot, option chain, expiries) from market_data_YYYYMMDD.
2. Regime detection (TRENDING / SIDEWAYS) from 15m candles + daily_metrics.
3. Each check: try BOTH Convex and Iron Condor; accept proposals (one per strategy type).
4. Position tracking: Convex exits (regime, time 40%, ATR, re-compression, max loss, TSL);
   Iron Condor exits (trailing stop, stop loss, mandatory DTE).
5. Uses same data layout as other backtests: market_data_*/raw_data/options, raw_data/futures,
   daily_metrics.json when present.
"""

import pandas as pd
import numpy as np
import os
import json
import logging
import re
import glob
from datetime import datetime, date, timedelta
from typing import Dict, List, Optional, Tuple, Any

from strategies.convex.call_backspread import generate_nifty_call_backspread
from strategies.iron_condor import generate_iron_condor_trade
from technical_indicators import calculate_iv_percentile, calculate_atm_iv
from regime.regime_detector import RegimeDetector, classify_regime_from_indicators
from backtest_trend_following import load_daily_metrics
from technical_indicators import calculate_adx, calculate_ema
from strategies.iron_condor.position_tracker import (
    CONVEX_REGIME_CHANGE_CONFIRMATION_CHECKS,
    CONVEX_TSL_ACTIVATION_MTM_PCT,
    CONVEX_TSL_ACTIVATION_TIME_PCT,
    CONVEX_TSL_TRAIL_PCT,
    CONVEX_TSL_TRAIL_TIGHT_PCT,
    CONVEX_TSL_TIGHT_TIME_PCT,
    CONVEX_TSL_ATR_TIGHT_THRESHOLD,
    CONVEX_MAX_LOSS_MTM_PCT,
)
from strategies.iron_condor.exit_rules import (
    MIN_PNL_LOCK_INR,
    PNL_TRAIL_DISTANCE_INR,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("BacktestMainFlow")


def _get_date_object(d):
    if d is None:
        return datetime.now().date()
    if isinstance(d, datetime):
        return d.date()
    if isinstance(d, date):
        return d
    return datetime.now().date()


class MainFlowBacktester:
    """Backtest Convex + Iron Condor using the same flow as production."""

    def __init__(
        self,
        data_dir_pattern: str = "market_data_*",
        initial_capital: float = 100000.0,
        use_ic_lock_for_convex: bool = False,
    ):
        self.data_dir_pattern = data_dir_pattern
        self.initial_capital = initial_capital
        self.capital = initial_capital
        self.use_ic_lock_for_convex = use_ic_lock_for_convex  # Use ₹300/₹200 trailing lock for Convex (same as IC)
        self.trades: List[Dict] = []
        self.open_positions: List[Dict] = []
        self.regime_detector = RegimeDetector()
        self.lot_size = 50
        self.max_quote_staleness_minutes = 10
        self._historical_candles: List[Dict] = []
        self._india_vix_by_date: Dict[str, Optional[float]] = {}
        # Diagnostics: why bars did not produce a trade
        self._diag_no_eligible_expiry = 0
        self._diag_chain_empty = 0
        self._diag_no_proposal_both = 0
        self._diag_has_position_skip = 0

    def load_historical_data(self, date_str: str) -> Dict:
        """Load options and spot for a date (same layout as other backtests)."""
        data = {"options": {}, "spot_prices": {}, "timestamps": set()}
        date_pattern = f"market_data_{date_str}"
        data_dirs = glob.glob(date_pattern)
        if not data_dirs:
            return data
        data_dir = data_dirs[0]
        options_dir = os.path.join(data_dir, "raw_data", "options", "NIFTY")
        for option_type in ["ce", "pe"]:
            option_path = os.path.join(options_dir, option_type)
            if not os.path.exists(option_path):
                continue
            for csv_file in glob.glob(os.path.join(option_path, "*.csv")):
                try:
                    df = pd.read_csv(csv_file)
                    if "timestamp" in df.columns:
                        df["timestamp"] = pd.to_datetime(df["timestamp"])
                        symbol = os.path.basename(csv_file).replace(".csv", "").split("_")[0]
                        data["options"][symbol] = df
                        data["timestamps"].update(df["timestamp"].tolist())
                except Exception as e:
                    logger.debug("Error loading %s: %s", csv_file, e)
        futures_dir = os.path.join(data_dir, "raw_data", "futures")
        if os.path.exists(futures_dir):
            for csv_file in glob.glob(os.path.join(futures_dir, "NIFTY*.csv")):
                try:
                    df = pd.read_csv(csv_file)
                    if "timestamp" in df.columns and "ltp" in df.columns:
                        df["timestamp"] = pd.to_datetime(df["timestamp"])
                        for _, row in df.iterrows():
                            ts = row["timestamp"]
                            data["spot_prices"][ts] = float(row["ltp"])
                            data["timestamps"].add(ts)
                except Exception as e:
                    logger.debug("Error loading futures %s: %s", csv_file, e)
        data["timestamps"] = sorted(list(data["timestamps"]))
        return data

    def load_futures_data(self, date_str: str) -> pd.DataFrame:
        """Load NIFTY futures for 15m candles."""
        data_dirs = glob.glob(f"market_data_{date_str}")
        if not data_dirs:
            return pd.DataFrame()
        futures_dir = os.path.join(data_dirs[0], "raw_data", "futures")
        if not os.path.exists(futures_dir):
            return pd.DataFrame()
        all_data = []
        for csv_file in glob.glob(os.path.join(futures_dir, "NIFTY*.csv")):
            try:
                df = pd.read_csv(csv_file)
                if "timestamp" in df.columns and "ltp" in df.columns:
                    df["timestamp"] = pd.to_datetime(df["timestamp"])
                    df = df[df["ltp"] > 0]
                    all_data.append(df)
            except Exception as e:
                logger.debug("Error loading %s: %s", csv_file, e)
        if not all_data:
            return pd.DataFrame()
        return pd.concat(all_data, ignore_index=True).sort_values("timestamp")

    def aggregate_to_15min(self, tick_data: pd.DataFrame) -> pd.DataFrame:
        """Aggregate tick data into 15-minute candles."""
        if tick_data.empty:
            return pd.DataFrame()
        tick_data = tick_data.set_index("timestamp")
        candles = tick_data["ltp"].resample("15min").agg(
            {"open": "first", "high": "max", "low": "min", "close": "last"}
        ).reset_index()
        return candles.dropna()

    def get_expiries_from_data(self, data: Dict, current_date: date) -> List[date]:
        """Extract expiry dates from option data (>= current_date)."""
        expiries = set()
        for symbol, df in data["options"].items():
            if df.empty:
                continue
            if "expiry" in df.columns:
                try:
                    expiry_str = df.iloc[0]["expiry"]
                    exp_date = datetime.strptime(expiry_str, "%d-%b-%Y").date()
                    if exp_date >= current_date:
                        expiries.add(exp_date)
                    continue
                except Exception:
                    pass
            match = re.match(r"NIFTY(\d{2}[A-Z]{3}\d{2})[CP]", symbol)
            if match:
                try:
                    exp_date = datetime.strptime(match.group(1), "%d%b%y").date()
                    if exp_date >= current_date:
                        expiries.add(exp_date)
                except Exception:
                    pass
        return sorted(expiries)

    def build_option_chain_at_time(
        self,
        data: Dict,
        timestamp: datetime,
        expiry_date: date,
        spot_price: float,
    ) -> pd.DataFrame:
        """Build option chain at timestamp for given expiry (production-like columns)."""
        chain_data = []
        expiry_str = expiry_date.strftime("%d-%b-%Y").upper()
        staleness = timedelta(minutes=self.max_quote_staleness_minutes)
        for symbol, df in data["options"].items():
            if "expiry" in df.columns:
                if df[df["expiry"] == expiry_str].empty:
                    continue
            elif expiry_str not in symbol:
                continue
            df_filtered = df[
                (df["timestamp"] <= timestamp) & (df["timestamp"] >= timestamp - staleness)
            ]
            if df_filtered.empty:
                continue
            row = df_filtered.sort_values("timestamp").iloc[-1]
            try:
                strike = float(row.get("strike", 0))
                option_type = str(row.get("option_type", "")).upper()
                ltp = float(row.get("ltp", 0))
                bid = float(row.get("bid", 0))
                ask = float(row.get("ask", 0))
                if strike <= 0 or option_type not in ("CE", "PE"):
                    continue
                mid_price = (bid + ask) / 2 if bid > 0 and ask > 0 else ltp
                chain_data.append({
                    "strike": strike,
                    "option_type": option_type,
                    "ltp": ltp,
                    "bid": bid,
                    "ask": ask,
                    "mid_price": mid_price,
                    "oi": int(row.get("oi", 0)),
                    "volume": int(row.get("volume", 0)),
                    "lot_size": self.lot_size,
                })
            except Exception as e:
                logger.debug("Error processing %s: %s", symbol, e)
        if not chain_data:
            return pd.DataFrame()
        return pd.DataFrame(chain_data)

    def get_spot_price_at_time(self, data: Dict, timestamp: datetime) -> Optional[float]:
        """Spot at timestamp (from futures or None)."""
        eligible = [ts for ts in data["spot_prices"].keys() if ts <= timestamp]
        if not eligible:
            return None
        closest = max(eligible)
        if abs((timestamp - closest).total_seconds()) < 600:
            return data["spot_prices"][closest]
        return None

    def _atr_percentile_and_range(
        self, candles_df: pd.DataFrame, atr_period: int = 14, range_lookback: int = 20
    ) -> Tuple[Optional[float], Optional[float], Optional[float]]:
        """ATR percentile, last_range, rolling_avg_range."""
        if len(candles_df) < atr_period + 5 or "high" not in candles_df.columns:
            return None, None, None
        highs = candles_df["high"].tolist()
        lows = candles_df["low"].tolist()
        closes = candles_df["close"].tolist()
        atr_values = []
        for i in range(atr_period - 1, len(closes)):
            h = highs[max(0, i - atr_period + 1) : i + 1]
            l_ = lows[max(0, i - atr_period + 1) : i + 1]
            c = closes[max(0, i - atr_period) : i + 1]
            if len(h) >= atr_period and len(l_) >= atr_period and len(c) >= atr_period + 1:
                a = self.regime_detector.calculate_atr(h, l_, c, period=atr_period)
                if a is not None:
                    atr_values.append(a)
        if not atr_values:
            return None, None, None
        current_atr = atr_values[-1]
        atr_pct = (sum(1 for a in atr_values if a <= current_atr) / len(atr_values)) * 100.0
        last_range = float(highs[-1] - lows[-1]) if highs and lows else None
        n = min(range_lookback, len(highs), len(lows))
        rolling_avg = (
            sum(float(highs[-i - 1] - lows[-i - 1]) for i in range(n)) / n if n > 0 else None
        )
        return atr_pct, last_range, rolling_avg

    def _regime_from_candles(
        self,
        candles_df: pd.DataFrame,
        spot_price: float,
        iv_pct: Optional[float],
        india_vix: Optional[float],
    ) -> Tuple[str, bool]:
        """Production two-fork: TRENDING or SIDEWAYS; range_compressed."""
        if len(candles_df) < 100:
            return "SIDEWAYS", False
        adx = calculate_adx(
            candles_df["high"].tolist(),
            candles_df["low"].tolist(),
            candles_df["close"].tolist(),
            period=14,
        )
        atr_pct, last_range, rolling_avg_range = self._atr_percentile_and_range(
            candles_df, 14, 20
        )
        range_compressed = (
            last_range is not None
            and rolling_avg_range is not None
            and rolling_avg_range > 0
            and last_range < 0.6 * rolling_avg_range
        )
        closes = candles_df["close"].tolist()
        ema_50 = calculate_ema(closes, 50)
        ema_100 = calculate_ema(closes, 100)
        ema_direction = None
        if spot_price and ema_50 and ema_100:
            if spot_price > ema_50 > ema_100:
                ema_direction = "LONG"
            elif spot_price < ema_50 < ema_100:
                ema_direction = "SHORT"
        regime = classify_regime_from_indicators(
            iv_percentile=iv_pct or 50.0,
            adx_14=adx,
            atr_percentile=atr_pct,
            range_compressed=range_compressed,
            ema_direction=ema_direction,
            india_vix=india_vix,
        )
        return regime, range_compressed

    def build_market_state(
        self,
        option_chain: pd.DataFrame,
        spot_price: float,
        expiry_date: date,
        current_date: date,
        regime: str,
        range_state: str,
        iv_percentile: Optional[float],
        adx_14: Optional[float],
        atr_percentile: Optional[float],
        india_vix: Optional[float],
    ) -> Dict:
        """Single market_state for both strategies (IC eligibility + Convex metadata)."""
        days_to_expiry = (expiry_date - current_date).days
        if iv_percentile is None and not option_chain.empty:
            try:
                iv_percentile = calculate_iv_percentile(
                    option_chain, spot_price, days_to_expiry
                )
            except Exception:
                iv_percentile = 50.0
        if adx_14 is None:
            adx_14 = 18.0
        return {
            "spot_price": spot_price,
            "expiry": expiry_date.strftime("%Y-%m-%d"),
            "days_to_expiry": days_to_expiry,
            "iv_percentile": iv_percentile or 50.0,
            "adx_14": adx_14,
            "atr_percentile": atr_percentile,
            "regime": regime,
            "range_state": range_state,
            "india_vix": india_vix,
            "has_major_event": False,
            "instrument": "NIFTY",
            "instrument_type": "WEEKLY" if days_to_expiry <= 7 else "MONTHLY",
        }

    def _estimate_convex_mtm(self, position: Dict, spot_price: float) -> float:
        """Estimate Convex position MTM (simplified)."""
        entry_atm = position["entry_price_atm"]
        entry_otm = position["entry_price_otm"]
        strike_atm = position["strike_atm"]
        spot_move_pct = (spot_price - strike_atm) / strike_atm if strike_atm else 0
        if spot_move_pct > 0.01:
            exit_atm = entry_atm * 0.3
            exit_otm = entry_otm * (1 + spot_move_pct * 2)
        else:
            exit_atm = entry_atm * 0.5
            exit_otm = entry_otm * 0.7
        pnl_per_lot = (entry_atm - exit_atm) + 2 * (exit_otm - entry_otm)
        return pnl_per_lot * position["lots"] * self.lot_size

    def _check_convex_exit(
        self,
        position: Dict,
        current_regime: str,
        spot_price: float,
        days_to_expiry: int,
        entry_days: int,
        atr_pct: Optional[float],
        range_state: str,
        current_mtm: float,
    ) -> Tuple[bool, Optional[str]]:
        """Production-aligned Convex exit conditions."""
        regime_at_entry = position.get("regime_at_entry") or "TRENDING"
        entry_spot = position.get("entry_spot")
        entry_range_state = position.get("entry_range_state")
        entry_premium = abs(position.get("entry_credit") or position.get("net_debit") or 0)
        time_elapsed_pct = (
            (entry_days - days_to_expiry) / entry_days if entry_days and entry_days > 0 else 0.0
        )
        if not position.get("convex_tsl_active", False):
            entry_ok = regime_at_entry in ("TRENDING", "CONVEX")
            current_ok = current_regime in ("TRENDING", "CONVEX")
            if entry_ok and not current_ok:
                count = position.get("convex_regime_change_count", 0) + 1
                position["convex_regime_change_count"] = count
                if count >= CONVEX_REGIME_CHANGE_CONFIRMATION_CHECKS:
                    return True, "REGIME_CHANGED"
                return False, None
            if position.get("convex_regime_change_count", 0) > 0:
                position["convex_regime_change_count"] = 0
        if entry_days > 0 and time_elapsed_pct > 0.40:
            return True, "TIME_ELAPSED_40PCT"
        if (
            entry_days > 0
            and time_elapsed_pct >= 0.40
            and atr_pct is not None
            and atr_pct < 30
        ):
            return True, "NO_ATR_EXPANSION"
        if (
            entry_range_state == "COMPRESSED"
            and range_state == "COMPRESSED"
            and entry_spot
            and entry_spot > 0
        ):
            price_change_pct = abs(spot_price - entry_spot) / entry_spot
            if time_elapsed_pct > 0.30 and price_change_pct < 0.005:
                return True, "RE_COMPRESSION"
        if entry_premium > 0 and current_mtm <= -CONVEX_MAX_LOSS_MTM_PCT * entry_premium:
            return True, "CONVEX_MAX_LOSS"
        # Convex trailing exit: either IC-style ₹300/₹200 lock or production %-from-peak TSL
        if self.use_ic_lock_for_convex:
            lock = position.get("profit_locked_inr", 0)
            if current_mtm >= MIN_PNL_LOCK_INR:
                lock = (
                    MIN_PNL_LOCK_INR
                    if lock == 0
                    else max(lock, current_mtm - PNL_TRAIL_DISTANCE_INR)
                )
                position["profit_locked_inr"] = lock
            if lock > 0 and current_mtm < lock:
                return True, "trailing_stop_pnl"
            return False, None
        if "convex_peak_mtm" not in position:
            position["convex_peak_mtm"] = float(current_mtm)
        if not position.get("convex_tsl_active", False):
            mtm_pct = (current_mtm / entry_premium) if entry_premium > 0 else 0.0
            if (entry_premium > 0 and mtm_pct >= CONVEX_TSL_ACTIVATION_MTM_PCT) or time_elapsed_pct >= CONVEX_TSL_ACTIVATION_TIME_PCT:
                position["convex_tsl_active"] = True
        if position.get("convex_tsl_active", False):
            peak = max(position.get("convex_peak_mtm", current_mtm), current_mtm)
            position["convex_peak_mtm"] = float(peak)
            trail = CONVEX_TSL_TRAIL_PCT
            if time_elapsed_pct > CONVEX_TSL_TIGHT_TIME_PCT or (
                atr_pct is not None and atr_pct < CONVEX_TSL_ATR_TIGHT_THRESHOLD
            ):
                trail = CONVEX_TSL_TRAIL_TIGHT_PCT
            if current_mtm <= peak * (1.0 - trail):
                return True, "CONVEX_TSL_HIT"
        return False, None

    def _close_convex_position(
        self, position: Dict, spot_price: float, exit_time: datetime, reason: str
    ) -> None:
        """Close Convex position and record trade."""
        entry_atm = position["entry_price_atm"]
        entry_otm = position["entry_price_otm"]
        strike_atm = position["strike_atm"]
        spot_move_pct = (spot_price - strike_atm) / strike_atm if strike_atm else 0
        if spot_move_pct > 0.01:
            exit_atm, exit_otm = entry_atm * 0.3, entry_otm * (1 + spot_move_pct * 2)
        else:
            exit_atm, exit_otm = entry_atm * 0.5, entry_otm * 0.7
        pnl_per_lot = (entry_atm - exit_atm) + 2 * (exit_otm - entry_otm)
        total_pnl = pnl_per_lot * position["lots"] * self.lot_size
        self.trades.append({
            "strategy": "CALL_BACKSPREAD",
            "book": "CONVEX",
            "entry_time": position["entry_time"],
            "exit_time": exit_time,
            "exit_reason": reason,
            "final_pnl": total_pnl,
            "lots": position["lots"],
        })
        self.capital += total_pnl

    def _ic_position_value(self, position: Dict, current_prices: Dict) -> float:
        """Current value of Iron Condor position (for PnL)."""
        total = 0
        lot_size = position.get("lot_size", self.lot_size)
        for leg in position.get("legs", []):
            key = f"{leg['option_type']}{int(leg['strike'])}"
            price = current_prices.get(key, leg.get("price", 0))
            if leg["position"] == "SHORT":
                total += (leg["price"] - price) * position["lots"] * lot_size
            else:
                total += (price - leg["price"]) * position["lots"] * lot_size
        return total

    def _ic_trailing_stop_hit(self, position: Dict, current_pnl: float) -> bool:
        """Iron Condor trailing stop (₹300 lock, trail ₹200)."""
        MIN_PNL_LOCK_INR = 300
        PNL_TRAIL_DISTANCE_INR = 200
        lock = position.get("profit_locked_inr", 0)
        if current_pnl >= MIN_PNL_LOCK_INR:
            lock = max(lock, MIN_PNL_LOCK_INR if lock == 0 else current_pnl - PNL_TRAIL_DISTANCE_INR)
            position["profit_locked_inr"] = lock
        return lock > 0 and current_pnl < lock

    def _check_ic_exit(
        self,
        position: Dict,
        current_prices: Dict,
        current_time: datetime,
        expiry_date: date,
    ) -> Tuple[bool, str]:
        """Iron Condor exit: trailing stop, stop loss, DTE."""
        entry_credit = position.get("net_credit_total", 0)
        current_value = self._ic_position_value(position, current_prices)
        current_pnl = current_value - entry_credit
        if self._ic_trailing_stop_hit(position, current_pnl):
            return True, "trailing_stop_pnl"
        max_loss = position.get("max_loss", 0)
        if max_loss and current_pnl <= -max_loss * 1.2:
            return True, "stop_loss"
        dte = (expiry_date - current_time.date()).days
        if dte <= 1:
            return True, "mandatory_exit_dte"
        return False, ""

    def run_backtest(
        self,
        start_date: str,
        end_date: str,
        check_interval_minutes: int = 15,
    ) -> None:
        """Run backtest mimicking main flow: both strategies, same exits."""
        start = datetime.strptime(start_date, "%Y%m%d").date()
        end = datetime.strptime(end_date, "%Y%m%d").date()
        current_date = start
        check_interval = timedelta(minutes=check_interval_minutes)

        while current_date <= end:
            date_str = current_date.strftime("%Y%m%d")
            if current_date.weekday() >= 5:
                current_date += timedelta(days=1)
                continue
            logger.info("Processing %s", date_str)

            data = self.load_historical_data(date_str)
            futures_df = self.load_futures_data(date_str)
            if not futures_df.empty:
                day_candles = self.aggregate_to_15min(futures_df)
                if not day_candles.empty:
                    self._historical_candles.extend(day_candles.to_dict("records"))
            if len(self._historical_candles) > 100:
                self._historical_candles = self._historical_candles[-100:]

            if date_str not in self._india_vix_by_date:
                daily = load_daily_metrics(date_str)
                self._india_vix_by_date[date_str] = daily.get("india_vix") if daily else None
            india_vix = self._india_vix_by_date.get(date_str) or 14.0

            expiries = self.get_expiries_from_data(data, current_date)
            if not expiries:
                current_date += timedelta(days=1)
                continue
            # Use first expiry with DTE >= 3 (production: get_all_eligible_expiries filters by DAYS_TO_EXPIRY_MIN=3)
            # so we get many more valid entry bars instead of only 1–2 days per week
            min_dte = 3
            eligible = [e for e in expiries if (e - current_date).days >= min_dte]
            nearest_expiry = min(eligible) if eligible else min(expiries)
            days_to_expiry = (nearest_expiry - current_date).days
            if not eligible:
                self._diag_no_eligible_expiry += 1

            market_start = datetime.combine(current_date, datetime.min.time().replace(hour=9, minute=15))
            market_end = datetime.combine(current_date, datetime.min.time().replace(hour=15, minute=30))
            current_time = market_start

            while current_time <= market_end:
                spot_price = self.get_spot_price_at_time(data, current_time)
                if not spot_price:
                    current_time += check_interval
                    continue

                option_chain = self.build_option_chain_at_time(
                    data, current_time, nearest_expiry, spot_price
                )
                if option_chain.empty:
                    self._diag_chain_empty += 1
                    current_time += check_interval
                    continue

                try:
                    iv_pct = calculate_iv_percentile(
                        option_chain, spot_price, days_to_expiry
                    )
                except Exception:
                    iv_pct = 50.0

                candles_up_to_now = [
                    c for c in self._historical_candles
                    if c.get("timestamp") <= current_time
                ]
                if len(candles_up_to_now) >= 100:
                    recent_df = pd.DataFrame(candles_up_to_now[-100:])
                    regime, range_compressed = self._regime_from_candles(
                        recent_df, spot_price, iv_pct, india_vix
                    )
                    atr_pct, _, _ = self._atr_percentile_and_range(recent_df, 14, 20)
                    adx = calculate_adx(
                        recent_df["high"].tolist(),
                        recent_df["low"].tolist(),
                        recent_df["close"].tolist(),
                        14,
                    )
                else:
                    regime, range_compressed = "SIDEWAYS", False
                    atr_pct, adx = None, 18.0

                range_state = "COMPRESSED" if range_compressed else "NORMAL"

                market_state = self.build_market_state(
                    option_chain,
                    spot_price,
                    nearest_expiry,
                    current_date,
                    regime,
                    range_state,
                    iv_pct,
                    adx,
                    atr_pct,
                    india_vix,
                )
                if "current_iv" not in market_state:
                    try:
                        market_state["current_iv"] = calculate_atm_iv(
                            option_chain, spot_price, days_to_expiry
                        )
                    except Exception:
                        pass

                # --- Exit checks (production-aligned) ---
                for position in self.open_positions[:]:
                    book = position.get("book", "")
                    if book == "CONVEX":
                        exp_date = position.get("expiry_date") or current_date
                        exp_date = _get_date_object(exp_date)
                        dte = (exp_date - current_time.date()).days
                        entry_days = position.get("entry_days_to_expiry", 7)
                        mtm = self._estimate_convex_mtm(position, spot_price)
                        should_exit, reason = self._check_convex_exit(
                            position,
                            regime,
                            spot_price,
                            dte,
                            entry_days,
                            atr_pct,
                            range_state,
                            mtm,
                        )
                        if should_exit and reason:
                            self._close_convex_position(
                                position, spot_price, current_time, reason
                            )
                            self.open_positions.remove(position)
                    elif book == "INCOME":
                        exp_date = position.get("expiry")
                        if exp_date:
                            if isinstance(exp_date, str):
                                exp_date = datetime.strptime(exp_date, "%Y-%m-%d").date()
                            else:
                                exp_date = _get_date_object(exp_date)
                        else:
                            exp_date = nearest_expiry
                        current_prices = {}
                        for leg in position.get("legs", []):
                            key = f"{leg['option_type']}{int(leg['strike'])}"
                            leg_df = option_chain[
                                (option_chain["option_type"] == leg["option_type"])
                                & (option_chain["strike"] == int(leg["strike"]))
                            ]
                            current_prices[key] = (
                                leg_df.iloc[0]["mid_price"] if not leg_df.empty else leg.get("price", 0)
                            )
                        should_exit, reason = self._check_ic_exit(
                            position, current_prices, current_time, exp_date
                        )
                        if should_exit:
                            entry_credit = position.get("net_credit_total", 0)
                            exit_value = self._ic_position_value(position, current_prices)
                            final_pnl = exit_value - entry_credit
                            self.trades.append({
                                "strategy": position.get("strategy", "IRON_CONDOR"),
                                "book": "INCOME",
                                "entry_time": position.get("entry_time"),
                                "exit_time": current_time,
                                "exit_reason": reason,
                                "final_pnl": final_pnl,
                                "lots": position.get("lots", 0),
                            })
                            self.capital += final_pnl
                            self.open_positions.remove(position)

                # --- Entry: try both strategies (mimic run_strategy_with_regime) ---
                has_convex = any(p.get("book") == "CONVEX" for p in self.open_positions)
                has_ic = any(p.get("book") == "INCOME" for p in self.open_positions)
                if has_convex and has_ic:
                    self._diag_has_position_skip += 1

                proposals = {}
                if not has_convex:
                    try:
                        convex_prop = generate_nifty_call_backspread(
                            market_state, option_chain, self.capital
                        )
                        if convex_prop:
                            proposals["CALL_BACKSPREAD"] = convex_prop
                    except Exception as e:
                        logger.debug("Convex generator error: %s", e)
                if not has_ic:
                    try:
                        ic_prop = generate_iron_condor_trade(
                            market_state, option_chain, api=None, symbol_manager=None
                        )
                        if ic_prop:
                            proposals["IRON_CONDOR"] = ic_prop
                    except Exception as e:
                        logger.debug("Iron Condor generator error: %s", e)

                if not proposals and (not has_convex or not has_ic):
                    self._diag_no_proposal_both += 1

                for name, prop in proposals.items():
                    if name == "CALL_BACKSPREAD":
                        position = {
                            "book": "CONVEX",
                            "entry_time": current_time,
                            "entry_price_atm": prop["legs"][0]["price"],
                            "entry_price_otm": prop["legs"][1]["price"],
                            "strike_atm": prop["legs"][0]["strike"],
                            "strike_otm": prop["legs"][1]["strike"],
                            "lots": prop["lots"],
                            "net_debit": prop.get("net_debit_total", 0),
                            "regime_at_entry": market_state["regime"],
                            "entry_spot": spot_price,
                            "entry_days_to_expiry": market_state["days_to_expiry"],
                            "entry_range_state": range_state,
                            "entry_credit": abs(prop.get("net_debit_total", 0)),
                            "expiry_date": nearest_expiry,
                            "convex_regime_change_count": 0,
                            "convex_tsl_active": False,
                            "profit_locked_inr": 0,
                        }
                        self.open_positions.append(position)
                        logger.info(
                            "Entered Convex: ATM=%s, OTM=%s, Debit=%.2f",
                            position["strike_atm"], position["strike_otm"], position["net_debit"],
                        )
                    else:
                        prop["entry_time"] = current_time
                        prop["entry_spot"] = spot_price
                        prop["book"] = "INCOME"
                        prop["profit_locked_inr"] = 0
                        prop["lot_size"] = self.lot_size
                        self.open_positions.append(prop)
                        logger.info(
                            "Entered Iron Condor: %s lots, Credit=%.2f",
                            prop["lots"], prop.get("net_credit_total", 0),
                        )

                current_time += check_interval

            current_date += timedelta(days=1)

        # Close remaining at end
        for position in self.open_positions[:]:
            spot = position.get("entry_spot") or position.get("strike_atm", 24000)
            if position.get("book") == "CONVEX":
                self._close_convex_position(
                    position, spot, datetime.now(), "end_of_backtest"
                )
            else:
                entry_credit = position.get("net_credit_total", 0)
                self.trades.append({
                    "strategy": position.get("strategy", "IRON_CONDOR"),
                    "book": "INCOME",
                    "entry_time": position.get("entry_time"),
                    "exit_time": datetime.now(),
                    "exit_reason": "end_of_backtest",
                    "final_pnl": -position.get("max_loss", 0),
                    "lots": position.get("lots", 0),
                })
                self.capital -= position.get("max_loss", 0)
            self.open_positions.remove(position)
        self.open_positions.clear()

    def generate_report(self) -> Dict[str, Any]:
        """Summary report by strategy and overall."""
        if not self.trades:
            return {
                "total_trades": 0,
                "by_strategy": {},
                "total_pnl": 0.0,
                "initial_capital": self.initial_capital,
                "final_capital": self.capital,
                "trades": [],
                "diagnostics": {
                    "no_eligible_expiry_bars": getattr(self, "_diag_no_eligible_expiry", 0),
                    "chain_empty_bars": getattr(self, "_diag_chain_empty", 0),
                    "no_proposal_both_bars": getattr(self, "_diag_no_proposal_both", 0),
                    "has_position_skip_bars": getattr(self, "_diag_has_position_skip", 0),
                },
            }
        by_strategy: Dict[str, List[Dict]] = {}
        for t in self.trades:
            key = t.get("strategy", t.get("book", "UNKNOWN"))
            by_strategy.setdefault(key, []).append(t)
        total_pnl = sum(t["final_pnl"] for t in self.trades)
        report = {
            "use_ic_lock_for_convex": getattr(self, "use_ic_lock_for_convex", False),
            "total_trades": len(self.trades),
            "by_strategy": {
                k: {
                    "count": len(v),
                    "pnl": sum(x["final_pnl"] for x in v),
                    "trades": v,
                }
                for k, v in by_strategy.items()
            },
            "total_pnl": total_pnl,
            "initial_capital": self.initial_capital,
            "final_capital": self.capital,
            "total_return_pct": ((self.capital - self.initial_capital) / self.initial_capital) * 100,
            "trades": self.trades,
            "diagnostics": {
                "no_eligible_expiry_bars": getattr(self, "_diag_no_eligible_expiry", 0),
                "chain_empty_bars": getattr(self, "_diag_chain_empty", 0),
                "no_proposal_both_bars": getattr(self, "_diag_no_proposal_both", 0),
                "has_position_skip_bars": getattr(self, "_diag_has_position_skip", 0),
            },
        }
        return report


def get_available_date_range() -> Tuple[Optional[str], Optional[str]]:
    """Return (start, end) from market_data_* dirs."""
    dirs = glob.glob("market_data_[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]")
    dates = sorted([d.replace("market_data_", "") for d in dirs if len(d.replace("market_data_", "")) == 8])
    if not dates:
        return None, None
    return dates[0], dates[-1]


def main() -> Dict[str, Any]:
    """Run main-flow backtest on available or given date range."""
    import sys
    use_ic_lock = "--ic-lock-convex" in sys.argv
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    start_date = args[0] if len(args) >= 1 else None
    end_date = args[1] if len(args) >= 2 else None
    if not start_date or not end_date:
        start_date, end_date = get_available_date_range()
    if not start_date or not end_date:
        start_date, end_date = "20251222", "20260116"
        logger.warning("No market_data_* dirs; using default range %s–%s", start_date, end_date)

    backtester = MainFlowBacktester(
        initial_capital=100000.0,
        use_ic_lock_for_convex=use_ic_lock,
    )
    backtester.run_backtest(start_date, end_date, check_interval_minutes=15)
    report = backtester.generate_report()

    print("\n" + "=" * 60)
    print("MAIN FLOW BACKTEST (Convex + Iron Condor)")
    if backtester.use_ic_lock_for_convex:
        print("Convex exit: IC-style trailing lock (₹300 lock, trail ₹200)")
    else:
        print("Convex exit: production %-from-peak TSL")
    print("=" * 60)
    print("Total Trades:", report["total_trades"])
    print("Total P&L: ₹{:.2f}".format(report["total_pnl"]))
    print("Initial Capital: ₹{:.2f}".format(report["initial_capital"]))
    print("Final Capital: ₹{:.2f}".format(report["final_capital"]))
    print("Return: {:.2f}%".format(report.get("total_return_pct", 0)))
    for strat, data in report.get("by_strategy", {}).items():
        print("  {}: {} trades, P&L ₹{:.2f}".format(strat, data["count"], data["pnl"]))
    diag = report.get("diagnostics", {})
    if diag:
        print("\nDiagnostics (why fewer trades):")
        print("  Days with no expiry with DTE>=3: {}".format(diag.get("no_eligible_expiry_bars", 0)))
        print("  Bars with empty option chain: {}".format(diag.get("chain_empty_bars", 0)))
        print("  Bars with no proposal (both strategies): {}".format(diag.get("no_proposal_both_bars", 0)))
        print("  Bars skipped (already had both positions): {}".format(diag.get("has_position_skip_bars", 0)))
    print("=" * 60)

    out_file = "backtest_main_flow_report_{}.json".format(
        datetime.now().strftime("%Y%m%d_%H%M%S")
    )
    with open(out_file, "w") as f:
        json.dump(report, f, indent=2, default=str)
    print("Report saved to", out_file)
    return report


if __name__ == "__main__":
    main()
