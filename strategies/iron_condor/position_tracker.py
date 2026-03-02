"""
Position Tracker for Iron Condor trades
Tracks open positions and monitors profit targets.
Supports syncing OPEN positions from broker at startup (broker is source of truth).
"""

import json
import os
import re
import logging
import uuid
from datetime import datetime
from typing import Dict, List, Optional, Any

logger = logging.getLogger(__name__)

# Require N consecutive regime != CONVEX before exiting Convex position on regime change (reduces whipsaw)
CONVEX_REGIME_CHANGE_CONFIRMATION_CHECKS = 3

# Convex trailing stop loss (MTM-based)
CONVEX_TSL_ACTIVATION_MTM_PCT = 0.05   # Activate TSL when mtm >= +5% of entry premium
CONVEX_TSL_ACTIVATION_TIME_PCT = 0.25  # Or when time elapsed >= 25% of expiry
CONVEX_TSL_TRAIL_PCT = 0.15            # Base trailing drawdown 15% from peak
CONVEX_TSL_TRAIL_TIGHT_PCT = 0.10      # Tighten to 10% when time > 40% or ATR% < 30
CONVEX_TSL_TIGHT_TIME_PCT = 0.40
CONVEX_TSL_ATR_TIGHT_THRESHOLD = 30
CONVEX_MAX_LOSS_MTM_PCT = 0.30         # Absolute exit if mtm <= -30% of entry premium


def _to_json_serializable(obj: Any) -> Any:
    """Convert numpy/pandas scalar types to native Python for JSON serialization."""
    try:
        import numpy as np
        if isinstance(obj, (np.integer,)):
            return int(obj)
        if isinstance(obj, (np.floating,)):
            return float(obj)
        if isinstance(obj, (np.bool_,)):
            return bool(obj)
        if isinstance(obj, np.ndarray):
            return obj.tolist()
    except ImportError:
        pass
    if isinstance(obj, dict):
        return {k: _to_json_serializable(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_to_json_serializable(v) for v in obj]
    return obj


class IronCondorPositionTracker:
    """Track and monitor open Iron Condor positions"""
    
    def __init__(self, trade_proposals_dir='trade_proposals', 
                 active_positions_file='active_positions.json'):
        self.trade_proposals_dir = trade_proposals_dir
        self.active_positions_file = active_positions_file
        self.active_positions = self._load_active_positions()
    
    def _load_active_positions(self) -> List[Dict]:
        """Load active positions from file"""
        try:
            if os.path.exists(self.active_positions_file):
                with open(self.active_positions_file, 'r') as f:
                    return json.load(f)
        except Exception as e:
            logger.error(f"Error loading active positions: {e}")
        return []
    
    def _save_active_positions(self):
        """Save active positions to file"""
        try:
            serializable = _to_json_serializable(self.active_positions)
            with open(self.active_positions_file, 'w') as f:
                json.dump(serializable, f, indent=2)
        except Exception as e:
            logger.error(f"Error saving active positions: {e}")

    @staticmethod
    def _parse_nfo_tsym(tsym: str) -> Optional[Dict]:
        """Parse NFO option symbol e.g. NIFTY02MAR26C25550 -> expiry_key, strike, option_type."""
        if not tsym or not isinstance(tsym, str):
            return None
        # NIFTY + DDMMMYY + C/P + strike
        m = re.match(r"NIFTY(\d{2})([A-Z]{3})(\d{2})([CP])(\d+)", tsym.strip().upper())
        if not m:
            return None
        dd, mmm, yy, cp, strike = m.groups()
        expiry_key = f"{dd}{mmm}{yy}"
        option_type = "CE" if cp == "C" else "PE"
        try:
            strike_int = int(strike)
        except ValueError:
            return None
        return {"expiry_key": expiry_key, "strike": strike_int, "option_type": option_type, "tradingsymbol": tsym.strip()}

    @staticmethod
    def _effective_open_qty(row: Dict) -> int:
        """
        Open quantity for a broker position row. Prefer openqty (quantity that remains open);
        fall back to netqty. Rows with openqty=0 are closed (squared off) and should not be counted.
        """
        try:
            oq = row.get("openqty")
            if oq is not None and str(oq).strip() != "":
                return int(float(oq))
            nq = row.get("netqty", 0) or row.get("qty", 0) or 0
            return int(float(nq))
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _build_positions_from_broker_nfo(nfo_rows: List[Dict]) -> List[Dict]:
        """Build tracker position(s) from broker NFO position rows. Groups by expiry."""
        from collections import defaultdict
        # netqty: positive = long, negative = short
        by_expiry: Dict[str, List[Dict]] = defaultdict(list)
        for row in nfo_rows:
            tsym = row.get("tsym") or row.get("tradingsymbol") or ""
            parsed = IronCondorPositionTracker._parse_nfo_tsym(tsym)
            if not parsed:
                continue
            netqty = IronCondorPositionTracker._effective_open_qty(row)
            if netqty == 0:
                continue
            by_expiry[parsed["expiry_key"]].append({
                "tradingsymbol": parsed["tradingsymbol"],
                "strike": float(parsed["strike"]),
                "option_type": parsed["option_type"],
                "quantity": 2 if netqty > 0 else 1,
                "position": "LONG" if netqty > 0 else "SHORT",
                "netqty": netqty,
            })
        positions = []
        lot_size = 65
        for expiry_key, legs in by_expiry.items():
            if not legs:
                continue
            # Convex: short = 1*lots*65, long = 2*lots*65; infer lots from smallest leg
            min_qty = min(abs(leg["netqty"]) for leg in legs)
            lots = max(1, min_qty // lot_size) if lot_size else 1
            legs_for_tracker = []
            for leg in legs:
                legs_for_tracker.append({
                    "tradingsymbol": leg["tradingsymbol"],
                    "strike": leg["strike"],
                    "option_type": leg["option_type"],
                    "quantity": 2 if leg["position"] == "LONG" else 1,
                    "position": leg["position"],
                    "price": 0.0,
                })
            # Best-effort expiry date (DDMMMYY e.g. 02MAR26 -> YYYY-MM-DD)
            expiry_date = expiry_key
            try:
                dd, mmm, yy = expiry_key[:2], expiry_key[2:5], expiry_key[5:7]
                dt = datetime.strptime(f"{dd}{mmm}20{yy}", "%d%b%Y")
                expiry_date = dt.strftime("%Y-%m-%d")
            except Exception:
                pass
            trade_id = f"broker_sync_{expiry_key}_{uuid.uuid4().hex[:8]}"
            position = {
                "trade_id": trade_id,
                "proposal_id": f"broker_sync_{expiry_key}",
                "entry_time": datetime.now().isoformat(),
                "lots": lots,
                "lot_size": lot_size,
                "entry_spot": None,
                "strategy": "CALL_BACKSPREAD",
                "book": "CONVEX",
                "regime_at_entry": "SYNCED",
                "status": "OPEN",
                "expiry": expiry_date,
                "legs": legs_for_tracker,
                "entry_credit": 0,
                "margin_used": None,
                "profit_target_margin": None,
                "profit_target_inr": None,
                "max_loss": 0,
                "days_to_expiry": None,
                "days_to_expiry_short": None,
                "entry_range_state": None,
                "entry_iv_percentile": None,
                "entry_prices": {},
                "profit_locked_inr": 0,
                "convex_tsl_active": False,
                "convex_peak_mtm": 0.0,
            }
            positions.append(position)
        return positions

    def sync_from_broker(self, api: Any, positions_raw: Any = None) -> None:
        """
        Sync OPEN positions from broker. Broker is source of truth.
        Called at startup (main.py) and periodically every POSITION_CHECK_INTERVAL (60s) during market hours.
        - Keeps all CLOSED positions (history).
        - Replaces OPEN list: if broker has no NFO positions, clear OPEN; else set OPEN from broker.
        If positions_raw is provided (e.g. from a shared get_positions() in the same interval), no API call is made.
        """
        if positions_raw is None:
            logger.info("sync_from_broker: fetching broker positions from broker for sync...")
            try:
                raw = api.get_positions()
            except Exception as e:
                logger.warning("sync_from_broker: get_positions failed: %s", e)
                return
        else:
            raw = positions_raw
        if not raw:
            raw = []
        rows = raw if isinstance(raw, list) else [raw]
        nfo = [
            p for p in rows
            if (p.get("exch") or p.get("exchange") or "").strip().upper() == "NFO"
        ]
        # Only consider positions with open qty (openqty or netqty); closed/squared-off rows have openqty=0.
        nfo_open = [p for p in nfo if self._effective_open_qty(p) != 0]
        closed = [p for p in self.active_positions if p.get("status") == "CLOSED"]
        open_before = len([p for p in self.active_positions if p.get("status") == "OPEN"])
        if not nfo_open:
            self.active_positions = closed
            self._save_active_positions()
            if open_before > 0:
                logger.info(
                    "sync_from_broker: cleared %d local OPEN position(s) (broker has no open NFO positions)",
                    open_before,
                )
            else:
                logger.info("sync_from_broker: broker has no open NFO positions; local OPEN list already empty")
            return
        open_from_broker = self._build_positions_from_broker_nfo(nfo_open)
        self.active_positions = closed + open_from_broker
        self._save_active_positions()
        logger.info(
            "sync_from_broker: broker has %d open NFO leg(s); tracker OPEN set to %d position(s) (was %d)",
            len(nfo_open), len(open_from_broker), open_before,
        )

    def check_position_imbalance(self, api: Any, positions_raw: Any = None) -> Dict[str, Any]:
        """
        Check broker NFO positions for Convex imbalance (e.g. orphan leg, wrong long:short ratio).
        Convex expects 2 legs per expiry: 1 short (1×lots×65) + 1 long (2×lots×65), so |long| = 2×|short|.
        Returns dict: imbalanced (bool), details (list), recommended_actions (list).
        If positions_raw is provided (e.g. from a shared get_positions() in the same interval), no API call is made.
        """
        result = {"imbalanced": False, "details": [], "broker_legs": [], "recommended_actions": []}
        if positions_raw is None:
            logger.info("check_position_imbalance: fetching broker positions for imbalance check...")
            try:
                raw = api.get_positions()
            except Exception as e:
                logger.warning("check_position_imbalance: get_positions failed: %s", e)
                result["details"].append(f"Could not fetch broker positions: {e}")
                result["recommended_actions"].append("Retry later or check API connectivity.")
                return result
        else:
            raw = positions_raw
        if not raw:
            raw = []
        rows = raw if isinstance(raw, list) else [raw]
        nfo = [
            p for p in rows
            if (p.get("exch") or p.get("exchange") or "").strip().upper() == "NFO"
        ]
        result["broker_legs"] = [
            {
                "tsym": p.get("tsym") or p.get("tradingsymbol"),
                "netqty": p.get("netqty") or p.get("qty"),
                "openqty": p.get("openqty"),
            }
            for p in nfo
        ]
        if not nfo:
            logger.info("check_position_imbalance: broker has no NFO positions; nothing to check")
            return result
        from collections import defaultdict
        by_expiry: Dict[str, List[Dict]] = defaultdict(list)
        for row in nfo:
            tsym = row.get("tsym") or row.get("tradingsymbol") or ""
            parsed = self._parse_nfo_tsym(tsym)
            if not parsed:
                result["details"].append(f"Could not parse symbol: {tsym}")
                continue
            netqty = self._effective_open_qty(row)
            if netqty == 0:
                continue
            by_expiry[parsed["expiry_key"]].append({
                "tradingsymbol": parsed["tradingsymbol"],
                "strike": parsed["strike"],
                "option_type": parsed["option_type"],
                "netqty": netqty,
                "position": "LONG" if netqty > 0 else "SHORT",
            })
        for expiry_key, legs in by_expiry.items():
            if len(legs) != 2:
                result["imbalanced"] = True
                result["details"].append(
                    f"Expiry {expiry_key}: expected 2 legs (1 short + 1 long), found {len(legs)} leg(s)."
                )
                if len(legs) == 1:
                    leg = legs[0]
                    side = "LONG" if leg["netqty"] > 0 else "SHORT"
                    qty = abs(leg["netqty"])
                    result["recommended_actions"].append(
                        f"Orphan leg: {leg['tradingsymbol']} {side} {qty}. "
                        f"To flatten: place MKT {'SELL' if leg['netqty'] > 0 else 'BUY'} {qty} for {leg['tradingsymbol']} (product MIS)."
                    )
                else:
                    result["recommended_actions"].append(
                        f"Expiry {expiry_key}: {len(legs)} legs — flatten each leg with MKT orders (BUY to cover short, SELL to close long) or run close_convex_position if tracker has this position."
                    )
                continue
            long_legs = [l for l in legs if l["netqty"] > 0]
            short_legs = [l for l in legs if l["netqty"] < 0]
            if len(long_legs) != 1 or len(short_legs) != 1:
                result["imbalanced"] = True
                result["details"].append(
                    f"Expiry {expiry_key}: expected 1 long and 1 short leg, found {len(long_legs)} long and {len(short_legs)} short."
                )
                result["recommended_actions"].append(
                    f"Flatten all legs with MKT orders (BUY to cover shorts, SELL to close longs) or sync tracker and use close_convex_position."
                )
                continue
            long_qty = abs(long_legs[0]["netqty"])
            short_qty = abs(short_legs[0]["netqty"])
            # Convex: long = 2*lots*65, short = 1*lots*65 => long/short = 2
            if short_qty == 0:
                result["imbalanced"] = True
                result["details"].append(f"Expiry {expiry_key}: short qty is 0.")
                continue
            ratio = long_qty / short_qty
            if ratio < 1.8 or ratio > 2.2:
                result["imbalanced"] = True
                result["details"].append(
                    f"Expiry {expiry_key}: long:short ratio is {ratio:.2f} (expected 2:1). Long qty={long_qty}, short qty={short_qty}."
                )
                result["recommended_actions"].append(
                    f"Ratio mismatch: close the larger leg first with MKT, then close the other; or place MKT orders to flatten both legs independently."
                )
        # When imbalanced, list of orders to flatten (only legs with open qty) for auto-resolve with small profit.
        result["flatten_orders"] = []
        for p in nfo:
            netqty = self._effective_open_qty(p)
            if netqty == 0:
                continue
            tsym = (p.get("tsym") or p.get("tradingsymbol") or "").strip()
            if not tsym:
                continue
            result["flatten_orders"].append({
                "tradingsymbol": tsym,
                "side": "S" if netqty > 0 else "B",
                "quantity": abs(netqty),
            })
        return result

    def add_position(self, trade_proposal: Dict):
        """Add a new position to track"""
        import uuid
        
        strategy = trade_proposal.get('strategy', 'UNKNOWN').upper()
        is_futures_strategy = 'FUTURE' in strategy or trade_proposal.get('instrument', '').upper() == 'NIFTY_FUTURE'
        
        # Use proposal_id if available, otherwise generate a unique ID
        # proposal_id groups all legs of a single proposal together
        proposal_id = trade_proposal.get('proposal_id', None)
        if not proposal_id:
            proposal_id = f"{datetime.now().isoformat()}_{uuid.uuid4().hex[:8]}"
        
        # Generate unique trade_id for this specific position (handles multi-leg strategies)
        trade_id = f"{proposal_id}_{uuid.uuid4().hex[:8]}"
        
        # Build position based on strategy type
        position = {
            'trade_id': trade_id,
            'proposal_id': proposal_id,  # Group all legs of a proposal together
            'entry_time': datetime.now().isoformat(),
            'lots': trade_proposal.get('lots', 0),
            'lot_size': trade_proposal.get('lot_size', 50),
            'entry_spot': trade_proposal.get('spot_price'),
            'strategy': trade_proposal.get('strategy', 'UNKNOWN'),
            'book': trade_proposal.get('book', 'UNKNOWN'),
            'regime_at_entry': trade_proposal.get('regime_at_entry', 'UNKNOWN'),
            'status': 'OPEN'
        }
        
        if is_futures_strategy:
            # Futures-specific fields
            # Parse expiry if it's a string (ISO format) or use directly if it's already a date
            expiry = trade_proposal.get('expiry', None)
            if expiry and isinstance(expiry, str):
                try:
                    expiry = datetime.fromisoformat(expiry).date()
                except:
                    pass
            
            position.update({
                'symbol': trade_proposal.get('instrument', 'NIFTY_FUTURE'),  # Add symbol field from instrument
                'expiry': expiry.isoformat() if expiry and hasattr(expiry, 'isoformat') else (expiry if expiry else None),  # Store expiry date
                'days_to_expiry': trade_proposal.get('days_to_expiry', None),  # Days to expiry at entry
                'legs': [],  # Futures don't have legs
                'entry_credit': 0,  # Futures don't have credit/debit
                'margin_used': trade_proposal.get('margin_used'),
                'profit_target_margin': trade_proposal.get('profit_target_margin'),
                'max_loss': trade_proposal.get('risk_amount', 0),  # Use risk_amount as max_loss for futures
                'entry_price': trade_proposal.get('entry_price'),
                'stop_loss_price': trade_proposal.get('stop_loss_price'),
                'current_stop_price': trade_proposal.get('stop_loss_price'),  # Initialize with initial stop, will be updated for trailing
                'direction': trade_proposal.get('direction'),
                'quantity': trade_proposal.get('quantity'),
                'days_to_expiry': trade_proposal.get('days_to_expiry'),
                'days_to_expiry_short': None,
                'entry_range_state': None,
                'entry_iv_percentile': None,
                'entry_prices': {}
            })
        else:
            # Options-specific fields
            position.update({
                'expiry': trade_proposal.get('expiry'),
                'legs': trade_proposal.get('legs', []),
                'entry_credit': trade_proposal.get('net_credit_total', trade_proposal.get('net_debit_total', 0)),
                'margin_used': trade_proposal.get('margin_used'),
                'profit_target_margin': trade_proposal.get('profit_target_margin'),
                'profit_target_inr': trade_proposal.get('profit_target_inr'),  # Convex: 1% of capital
                'max_loss': trade_proposal.get('max_loss', 0),
                'days_to_expiry': trade_proposal.get('days_to_expiry'),
                'days_to_expiry_short': trade_proposal.get('days_to_expiry_short'),
                'entry_range_state': None,  # Will be set from regime_info if available
                'entry_iv_percentile': trade_proposal.get('entry_iv_percentile'),
                'entry_prices': {leg['option_type'] + str(int(leg['strike'])): leg['price'] for leg in trade_proposal.get('legs', [])},
                'profit_locked_inr': 0,  # Trailing lock: first 300, then trail 200 below current PnL
            })
            # Convex-only trailing stop state (MTM-based)
            if position.get('book') == 'CONVEX' or 'BACKSPREAD' in strategy:
                position['convex_tsl_active'] = False
                position['convex_peak_mtm'] = 0.0  # Entry MTM at open
        
        self.active_positions.append(position)
        self._save_active_positions()
        logger.info(f"Added position to tracker: {position['trade_id']} ({strategy})")
    
    def get_active_positions(self) -> List[Dict]:
        """Get all active positions"""
        return [p for p in self.active_positions if p.get('status') == 'OPEN']
    
    def calculate_current_pnl(self, position: Dict, current_prices: Dict) -> float:
        """
        Calculate current P&L for a position
        
        Args:
            position: Position dictionary
            current_prices: Dict of {option_key: current_price}
                          e.g., {'CE26200': 50.5, 'PE25800': 45.2, ...}
        
        Returns:
            Current P&L in ₹
        """
        entry_credit = position['entry_credit']
        lots = position['lots']
        lot_size = position.get('lot_size', 50)
        
        current_value = 0.0
        
        for leg in position['legs']:
            strike = int(leg['strike'])
            option_type = leg['option_type']
            entry_price = leg['price']
            quantity = leg.get('quantity', 1)  # Support different quantities (e.g., 2 for convex backspread)
            
            # Get current price
            option_key = f"{option_type}{strike}"
            current_price = current_prices.get(option_key, entry_price)
            
            # Calculate value change
            if leg['position'] == 'SHORT':
                # Short: profit when price decreases
                # Value = (entry_price - current_price) × lots × lot_size × quantity
                value = (entry_price - current_price) * lots * lot_size * quantity
            else:  # LONG
                # Long: profit when price increases
                # Value = (current_price - entry_price) × lots × lot_size × quantity
                value = (current_price - entry_price) * lots * lot_size * quantity
            
            current_value += value
        
        # P&L calculation depends on strategy type
        strategy = position.get('strategy', '').upper()
        if 'BACKSPREAD' in strategy or position.get('book') == 'CONVEX':
            # For convex backspread: current_value already is total P&L (entry credit + mark-to-market).
            # entry_credit is stored as net_debit (negative when we received credit); do not add it again.
            pnl = current_value
        else:
            # For Iron Condor: entry_credit is positive (we received it)
            # P&L = entry_credit - current_value
            pnl = entry_credit - current_value
        
        return pnl

    def update_trailing_lock_and_check(self, position: Dict, current_pnl: float) -> bool:
        """
        Update trailing PnL lock for Iron Condor: first lock at ₹300, then trail ₹200 below current PnL.
        Updates position['profit_locked_inr'] in place and persists. Call after calculating current_pnl.

        Returns:
            True if trailing stop hit (current_pnl < profit_locked_inr), else False.
        """
        from strategies.iron_condor.exit_rules import MIN_PNL_LOCK_INR, PNL_TRAIL_DISTANCE_INR
        current_lock = position.get('profit_locked_inr', 0)
        if current_pnl >= MIN_PNL_LOCK_INR:
            if current_lock == 0:
                new_lock = MIN_PNL_LOCK_INR
            else:
                new_lock = max(current_lock, current_pnl - PNL_TRAIL_DISTANCE_INR)
            position['profit_locked_inr'] = new_lock
            current_lock = new_lock
        if current_lock > 0 and current_pnl < current_lock:
            return True
        if current_pnl >= MIN_PNL_LOCK_INR:
            self._save_active_positions()
        return False

    def check_profit_target(self, position: Dict, current_pnl: float) -> bool:
        """Profit-target exit disabled: we use only TSL (trailing stop) for Convex and Iron Condor."""
        return False
    
    def check_convex_exit_conditions(self, position: Dict, current_regime: str, 
                                     current_spot: float, entry_spot: float,
                                     days_to_expiry: int, entry_days_to_expiry: int,
                                     current_atr_percentile: float = None,
                                     entry_range_state: str = None,
                                     current_range_state: str = None,
                                     current_mtm: float = None):
        """
        Check mandatory exit conditions for Convex Backspread strategy
        
        MANDATORY exits:
        - Exit when regime flips from TRENDING (or CONVEX) to SIDEWAYS, after confirmation (REGIME_CHANGED)
        - Exit if no ATR expansion within 40% of expiry time
        - Exit if time elapsed > 40% of expiry duration
        - Exit if price re-enters compression range after entry
        - (MTM-based) Exit if current_mtm <= -30% of entry premium (CONVEX_MAX_LOSS)
        - (MTM-based) Trailing stop: activate at +20% mtm or 25% time; exit on drawdown from peak (CONVEX_TSL_HIT)
        
        Args:
            position: Position dictionary
            current_regime: Current detected regime
            current_spot: Current spot price
            entry_spot: Entry spot price
            days_to_expiry: Current days to expiry
            entry_days_to_expiry: Days to expiry at entry
            current_atr_percentile: Current ATR percentile (optional)
            entry_range_state: Range state at entry (optional)
            current_range_state: Current range state (optional)
            current_mtm: Current mark-to-market PnL (required for TSL / max-loss checks)
        
        Returns:
            Tuple of (should_exit: bool, exit_reason: str)
        """
        try:
            strategy = position.get('strategy', '').upper()
            if 'BACKSPREAD' not in strategy and position.get('book') != 'CONVEX':
                # Not a convex position, use standard exit logic
                return False, None
            
            # Exit condition 1: Regime changed from regime at entry (with confirmation to reduce whipsaw)
            # Skip regime-change exit once TSL is active: let TSL or max loss handle exit (reduces regime-change losses)
            # Two-fork: TRENDING = convex-friendly, SIDEWAYS = not; treat TRENDING and CONVEX as equivalent for entry regime
            if not position.get('convex_tsl_active', False):
                regime_at_entry = position.get('regime_at_entry') or 'CONVEX'
                entry_is_trending = regime_at_entry in ('TRENDING', 'CONVEX')
                current_is_trending = current_regime in ('TRENDING', 'CONVEX')
                if entry_is_trending and not current_is_trending:
                    count = position.get('convex_regime_change_count', 0) + 1
                    position['convex_regime_change_count'] = count
                    self._save_active_positions()
                    if count >= CONVEX_REGIME_CHANGE_CONFIRMATION_CHECKS:
                        return True, "REGIME_CHANGED"
                    return False, None
                else:
                    # Still in trending (or entry was sideways); reset regime-change count
                    if position.get('convex_regime_change_count', 0) > 0:
                        position['convex_regime_change_count'] = 0
                        self._save_active_positions()

            # Exit condition 2: Time elapsed > 40% of expiry duration
            if (entry_days_to_expiry is not None and days_to_expiry is not None
                    and entry_days_to_expiry > 0):
                time_elapsed_pct = (entry_days_to_expiry - days_to_expiry) / entry_days_to_expiry
                if time_elapsed_pct > 0.40:
                    return True, "TIME_ELAPSED_40PCT"
            
            # Exit condition 3: No ATR expansion within 40% of expiry time
            # Check if we're past 40% of time and ATR hasn't expanded
            if (entry_days_to_expiry is not None and days_to_expiry is not None
                    and entry_days_to_expiry > 0):
                time_elapsed_pct = (entry_days_to_expiry - days_to_expiry) / entry_days_to_expiry
                if time_elapsed_pct >= 0.40:
                    # Check if ATR has expanded (percentile should be higher)
                    if current_atr_percentile is not None:
                        # If ATR percentile is still low (< 30), no expansion occurred
                        if current_atr_percentile < 30:
                            return True, "NO_ATR_EXPANSION"
            
            # Exit condition 4: Price re-entered compression range
            # If range was COMPRESSED at entry and is still COMPRESSED, check if price moved back
            if entry_range_state == "COMPRESSED" and current_range_state == "COMPRESSED":
                # Calculate price movement from entry
                if entry_spot and entry_spot != 0:
                    price_change_pct = abs(current_spot - entry_spot) / entry_spot
                    # If price moved significantly but range is still compressed, might indicate re-compression
                    if (entry_days_to_expiry is not None and days_to_expiry is not None
                            and entry_days_to_expiry > 0):
                        time_elapsed_pct = (entry_days_to_expiry - days_to_expiry) / entry_days_to_expiry
                        if time_elapsed_pct > 0.30 and price_change_pct < 0.005:  # Less than 0.5% movement
                            return True, "RE_COMPRESSION"
            
            # Time elapsed for TSL / absolute protection (reuse in this block)
            if (entry_days_to_expiry is not None and days_to_expiry is not None
                    and entry_days_to_expiry > 0):
                time_elapsed_pct = (entry_days_to_expiry - days_to_expiry) / entry_days_to_expiry
            else:
                time_elapsed_pct = 0.0
            
            # Absolute protection (fail-safe): exit if mtm <= -30% of entry premium (regardless of TSL state)
            if current_mtm is not None:
                entry_premium = abs(position.get('entry_credit') or 0)
                if entry_premium > 0 and current_mtm <= -CONVEX_MAX_LOSS_MTM_PCT * entry_premium:
                    return True, "CONVEX_MAX_LOSS"
                
                # Initialize peak MTM if not set (entry MTM = 0 at open)
                if 'convex_peak_mtm' not in position:
                    position['convex_peak_mtm'] = float(current_mtm)
                    self._save_active_positions()
                
                # Activation gate: activate TSL if mtm >= +20% of entry premium OR time >= 25%
                if not position.get('convex_tsl_active', False):
                    mtm_pct = (current_mtm / entry_premium) if entry_premium > 0 else 0.0
                    if (entry_premium > 0 and mtm_pct >= CONVEX_TSL_ACTIVATION_MTM_PCT) or time_elapsed_pct >= CONVEX_TSL_ACTIVATION_TIME_PCT:
                        position['convex_tsl_active'] = True
                        self._save_active_positions()
                        logger.info(
                            "Unrealized MTM: TSL activated trade_id=%s mtm=₹%.2f mtm_pct=%.1f%% time_elapsed_pct=%.1f%%",
                            position.get("trade_id", "?"), current_mtm, mtm_pct * 100, time_elapsed_pct * 100,
                        )
                
                # Trailing stop: once active, track peak and exit on drawdown
                if position.get('convex_tsl_active', False):
                    old_peak = position.get('convex_peak_mtm', current_mtm)
                    peak = max(old_peak, current_mtm)
                    position['convex_peak_mtm'] = float(peak)
                    if peak != old_peak:
                        self._save_active_positions()
                    trailing_pct = CONVEX_TSL_TRAIL_PCT
                    if time_elapsed_pct > CONVEX_TSL_TIGHT_TIME_PCT or (current_atr_percentile is not None and current_atr_percentile < CONVEX_TSL_ATR_TIGHT_THRESHOLD):
                        trailing_pct = CONVEX_TSL_TRAIL_TIGHT_PCT
                    # Log unrealized MTM and trail level (for debugging / audit)
                    logger.info(
                        "Unrealized MTM (TSL): trade_id=%s mtm=₹%.2f peak=₹%.2f trail_pct=%.0f%% threshold=₹%.2f",
                        position.get("trade_id", "?"),
                        current_mtm,
                        peak,
                        trailing_pct * 100,
                        peak * (1.0 - trailing_pct),
                    )
                    if current_mtm <= peak * (1.0 - trailing_pct):
                        return True, "CONVEX_TSL_HIT"
            
            return False, None
            
        except Exception as e:
            logger.error(f"Error checking convex exit conditions: {str(e)}")
            return False, None
    
    def check_calendar_exit_conditions(self, position: Dict, current_regime: str,
                                      current_spot: float, entry_spot: float,
                                      days_to_expiry_short: int, entry_days_to_expiry_short: int,
                                      current_prices: Dict, entry_prices: Dict,
                                      current_iv_percentile: float = None,
                                      entry_iv_percentile: float = None) -> tuple:
        """
        Check mandatory exit conditions for Calendar strategy.
        
        Calendar strategy removed - convex-only mode. Always returns False.
        
        Args:
            position: Position dictionary
            current_regime: Current detected regime
            current_spot: Current spot price
            entry_spot: Entry spot price
            days_to_expiry_short: Current days to short expiry
            entry_days_to_expiry_short: Days to short expiry at entry
            current_prices: Current option prices dict
            entry_prices: Entry option prices dict
            current_iv_percentile: Current IV percentile (optional)
            entry_iv_percentile: Entry IV percentile (optional)
        
        Returns:
            Tuple of (should_exit: bool, exit_reason: str)
        """
        # Calendar strategy removed - convex-only mode
        return False, None
    
    def close_position(self, position: Dict, exit_reason: str, final_pnl: float):
        """Close a position and log performance by regime"""
        position['status'] = 'CLOSED'
        position['exit_time'] = datetime.now().isoformat()
        position['exit_reason'] = exit_reason
        position['final_pnl'] = final_pnl
        self._save_active_positions()
        
        # Log performance by regime
        self._log_performance_by_regime(position, final_pnl)
        
        logger.info(
            f"Closed position {position['trade_id']}: {exit_reason}, "
            f"P&L=₹{final_pnl:.2f}"
        )
    
    def _log_performance_by_regime(self, position: Dict, final_pnl: float):
        """Log trade performance by regime to performance_by_regime.json"""
        try:
            performance_file = 'performance_by_regime.json'
            
            # Load existing performance data
            performance_data = []
            if os.path.exists(performance_file):
                with open(performance_file, 'r') as f:
                    performance_data = json.load(f)
            
            # Create performance entry
            performance_entry = {
                "strategy": position.get('strategy', 'UNKNOWN'),
                "book": position.get('book', 'UNKNOWN'),
                "regime_at_entry": position.get('regime_at_entry', 'UNKNOWN'),
                "entry_time": position.get('entry_time', ''),
                "exit_time": position.get('exit_time', datetime.now().isoformat()),
                "pnl": final_pnl,
                "max_loss": position.get('max_loss', 0),
                "lots": position.get('lots', 0),
                "trade_id": position.get('trade_id', '')
            }
            
            performance_data.append(performance_entry)
            
            # Save to file
            with open(performance_file, 'w') as f:
                json.dump(performance_data, f, indent=2)
            
            logger.debug(f"Logged performance by regime: {performance_entry}")
            
        except Exception as e:
            logger.error(f"Error logging performance by regime: {e}")
