"""
Neutral-Safe Calendar Strategy

ATM Call Calendar:
- BUY 1 × ATM CALL (next monthly expiry)
- SELL 1 × ATM CALL (current weekly expiry)
- Same strike

Purpose:
- Capture short-term theta mismatch
- Absorb uncertainty in NEUTRAL regime
- Avoid regime-transition losses
"""

import pandas as pd
import numpy as np
import logging
from datetime import datetime, date
from typing import Dict, Optional, List
from .config import (
    ENABLE_NEUTRAL_CALENDAR,
    MAX_NET_DEBIT_PCT_OF_CAPITAL,
    IV_PERCENTILE_MIN,
    IV_PERCENTILE_MAX,
    ADX_MIN,
    ADX_MAX,
    MIN_DAYS_TO_EXPIRY_SHORT
)

logger = logging.getLogger(__name__)
from strategies.size_config import clamp_lots, MIN_LOTS, MAX_LOTS


def generate_neutral_call_calendar(market_state: Dict, option_chain_weekly: pd.DataFrame,
                                   option_chain_monthly: pd.DataFrame, capital: float = 1000000.0,
                                   symbol_manager=None) -> Optional[Dict]:
    """
    Generate Neutral Call Calendar trade proposal.
    
    Entry conditions (ALL required):
    - regime == "NEUTRAL"
    - sub_state == "NEUTRAL_ACTIVE"
    - iv_percentile BETWEEN 40 AND 60
    - adx BETWEEN 18 AND 25
    - atr NOT expanding
    - no major event in next 48 hours
    - no other active positions
    - ENABLE_NEUTRAL_CALENDAR == True
    
    Args:
        market_state: Market state dictionary with:
            - spot_price: float
            - expiry: str (YYYY-MM-DD) for weekly expiry
            - days_to_expiry: int (for weekly)
            - iv_percentile: float
            - adx_14: float
            - regime: str
            - sub_state: str
        option_chain_weekly: DataFrame with weekly expiry option chain
        option_chain_monthly: DataFrame with monthly expiry option chain
        capital: Total capital allocated (default: ₹10L)
        symbol_manager: SymbolManager instance (for lot size)
    
    Returns:
        Trade proposal dictionary or None if rejected:
        {
            "strategy": "ATM_CALL_CALENDAR",
            "book": "NEUTRAL",
            "regime_at_entry": "NEUTRAL",
            "sub_state": "NEUTRAL_ACTIVE",
            "legs": [
                {
                    "position": "SHORT",
                    "option_type": "CE",
                    "strike": float,
                    "price": float,
                    "expiry": "YYYY-MM-DD",  # weekly
                    ...
                },
                {
                    "position": "LONG",
                    "option_type": "CE",
                    "strike": float,
                    "price": float,
                    "expiry": "YYYY-MM-DD",  # monthly
                    ...
                }
            ],
            "max_loss": float,
            "net_debit": float,
            "expiry_short": "YYYY-MM-DD",
            "expiry_long": "YYYY-MM-DD",
            "spot_price": float
        }
    """
    try:
        # Check if calendar is enabled
        if not ENABLE_NEUTRAL_CALENDAR:
            logger.debug("Neutral calendar strategy is disabled")
            return None
        
        # Validate market state
        spot_price = market_state.get('spot_price')
        regime = market_state.get('regime', 'NEUTRAL')
        sub_state = market_state.get('sub_state')
        iv_percentile = market_state.get('iv_percentile')
        adx_14 = market_state.get('adx_14')
        expiry_weekly = market_state.get('expiry')
        days_to_expiry_weekly = market_state.get('days_to_expiry')
        
        if spot_price is None or spot_price <= 0:
            logger.debug("Invalid or missing spot_price")
            return None
        
        # Entry condition 1: Regime must be NEUTRAL
        if regime != "NEUTRAL":
            logger.debug(f"Calendar rejected: regime is {regime}, not NEUTRAL")
            return None
        
        # Entry condition 2: Sub-state must be NEUTRAL_ACTIVE
        if sub_state != "NEUTRAL_ACTIVE":
            logger.debug(f"Calendar rejected: sub_state is {sub_state}, not NEUTRAL_ACTIVE")
            return None
        
        # Entry condition 3: IV percentile between 40-60
        if iv_percentile is None or not (IV_PERCENTILE_MIN <= iv_percentile <= IV_PERCENTILE_MAX):
            logger.debug(f"Calendar rejected: IV percentile {iv_percentile} not in range [{IV_PERCENTILE_MIN}, {IV_PERCENTILE_MAX}]")
            return None
        
        # Entry condition 4: ADX between 18-25
        if adx_14 is None or not (ADX_MIN <= adx_14 <= ADX_MAX):
            logger.debug(f"Calendar rejected: ADX {adx_14} not in range [{ADX_MIN}, {ADX_MAX}]")
            return None
        
        # Entry condition 5: ATR not expanding (check range_state)
        range_state = market_state.get('range_state', 'NORMAL')
        if range_state == "EXPANDING":
            logger.debug("Calendar rejected: ATR expanding")
            return None
        
        # Entry condition 6: Short leg must have sufficient days to expiry
        if days_to_expiry_weekly is not None and days_to_expiry_weekly <= MIN_DAYS_TO_EXPIRY_SHORT:
            logger.info(
                f"Calendar rejected: Short leg expires in {days_to_expiry_weekly} days "
                f"(minimum: {MIN_DAYS_TO_EXPIRY_SHORT + 1} days)"
            )
            return None
        
        # Entry condition 7: No major events (placeholder - assume false for now)
        has_major_event = market_state.get('has_major_event', False)
        if has_major_event:
            logger.debug("Calendar rejected: Major event in next 48 hours")
            return None
        
        # Validate option chains
        if option_chain_weekly.empty or option_chain_monthly.empty:
            logger.debug("Calendar rejected: Empty option chains")
            return None
        
        # Filter calls only
        calls_weekly = option_chain_weekly[option_chain_weekly['option_type'].str.upper() == 'CE'].copy()
        calls_monthly = option_chain_monthly[option_chain_monthly['option_type'].str.upper() == 'CE'].copy()
        
        if calls_weekly.empty or calls_monthly.empty:
            logger.debug("Calendar rejected: No call options in chains")
            return None
        
        # Find ATM call for weekly expiry (short leg)
        calls_weekly['distance_from_spot'] = abs(calls_weekly['strike'] - spot_price)
        atm_weekly = calls_weekly.loc[calls_weekly['distance_from_spot'].idxmin()].copy()
        
        if atm_weekly.empty:
            logger.debug("Calendar rejected: Could not find ATM call for weekly expiry")
            return None
        
        atm_strike = float(atm_weekly['strike'])
        
        # Find same strike call for monthly expiry (long leg)
        calls_monthly_at_strike = calls_monthly[calls_monthly['strike'] == atm_strike].copy()
        
        if calls_monthly_at_strike.empty:
            logger.debug(f"Calendar rejected: No monthly call at strike {atm_strike}")
            return None
        
        atm_monthly = calls_monthly_at_strike.iloc[0].copy()
        
        # Get prices
        weekly_price = atm_weekly.get('mid_price', atm_weekly.get('ltp', 0))
        monthly_price = atm_monthly.get('mid_price', atm_monthly.get('ltp', 0))
        
        if weekly_price <= 0 or monthly_price <= 0:
            logger.debug(f"Calendar rejected: Invalid prices (weekly={weekly_price}, monthly={monthly_price})")
            return None
        
        # Calculate net debit (buy monthly, sell weekly)
        net_debit_per_lot = monthly_price - weekly_price
        
        if net_debit_per_lot <= 0:
            logger.debug(f"Calendar rejected: Net debit {net_debit_per_lot:.2f} is not positive (should be debit)")
            return None
        
        # Get lot size
        lot_size = option_chain_weekly.iloc[0].get('lot_size', 50)
        if symbol_manager is not None:
            try:
                nifty_options = symbol_manager.nse_fo[
                    (symbol_manager.nse_fo['symbol'] == 'NIFTY') &
                    (symbol_manager.nse_fo['instrument'] == 'OPTIDX')
                ]
                if not nifty_options.empty:
                    lot_size = int(nifty_options.iloc[0]['lotsize'])
            except Exception as e:
                logger.debug(f"Error getting lot size from symbol_manager: {e}")
        
        # Risk validation: Net debit ≤ 0.30% of capital
        max_allowed_debit = capital * MAX_NET_DEBIT_PCT_OF_CAPITAL
        max_allowed_debit_per_lot = max_allowed_debit / lot_size
        
        if net_debit_per_lot > max_allowed_debit_per_lot:
            logger.debug(
                f"Calendar rejected: Net debit {net_debit_per_lot:.2f} exceeds max "
                f"{max_allowed_debit_per_lot:.2f} (0.30% of capital)"
            )
            return None
        
        # Calculate number of lots (1 lot only, as per requirements)
        lots = 1
        # Enforce central sizing policy
        clamped = clamp_lots(lots)
        if clamped == 0:
            logger.info("Calendar sizing resulted in 0 lots (invalid)")
            return None
        if clamped != lots:
            lots = clamped

        # Calculate total net debit
        net_debit_total = net_debit_per_lot * lots * lot_size
        
        # Max loss = net debit (no additional risk)
        max_loss = net_debit_total
        
        # Get expiry dates
        expiry_weekly_str = expiry_weekly
        expiry_monthly_str = market_state.get('expiry_monthly')  # Should be passed in market_state
        
        # If monthly expiry not in market_state, try to get from option chain
        if not expiry_monthly_str:
            # Try to extract from option chain metadata or use a placeholder
            expiry_monthly_str = "TBD"  # Will need to be set properly
        
        # Build trade proposal
        trade_proposal = {
            "strategy": "ATM_CALL_CALENDAR",
            "book": "NEUTRAL",
            "regime_at_entry": "NEUTRAL",
            "sub_state": "NEUTRAL_ACTIVE",
            "expiry_short": expiry_weekly_str,
            "expiry_long": expiry_monthly_str,
            "legs": [
                {
                    "position": "SHORT",
                    "option_type": "CE",
                    "strike": atm_strike,
                    "price": float(weekly_price),
                    "expiry": expiry_weekly_str,
                    "quantity": 1,
                    "delta": float(atm_weekly.get('delta', 0)),
                    "ltp": float(atm_weekly.get('ltp', weekly_price)),
                    "bid": float(atm_weekly.get('bid', weekly_price)),
                    "ask": float(atm_weekly.get('ask', weekly_price)),
                    "oi": int(atm_weekly.get('oi', 0)),
                    "volume": int(atm_weekly.get('volume', 0))
                },
                {
                    "position": "LONG",
                    "option_type": "CE",
                    "strike": atm_strike,
                    "price": float(monthly_price),
                    "expiry": expiry_monthly_str,
                    "quantity": 1,
                    "delta": float(atm_monthly.get('delta', 0)),
                    "ltp": float(atm_monthly.get('ltp', monthly_price)),
                    "bid": float(atm_monthly.get('bid', monthly_price)),
                    "ask": float(atm_monthly.get('ask', monthly_price)),
                    "oi": int(atm_monthly.get('oi', 0)),
                    "volume": int(atm_monthly.get('volume', 0))
                }
            ],
            "lots": lots,
            "lot_size": lot_size,
            "net_debit": float(net_debit_per_lot),
            "net_debit_total": float(net_debit_total),
            "max_loss": float(max_loss),
            "spot_price": float(spot_price),
            "days_to_expiry_short": days_to_expiry_weekly,
            "days_to_expiry_long": market_state.get('days_to_expiry_monthly', days_to_expiry_weekly + 7),
            "entry_iv_percentile": iv_percentile,  # Store for exit checks
            "generated_at": datetime.now().isoformat()
        }
        
        logger.info(
            f"Neutral Calendar proposal: {lots} lot(s), "
            f"debit=₹{net_debit_per_lot:.2f}/lot, max_loss=₹{max_loss:.2f}"
        )
        
        return trade_proposal
        
    except Exception as e:
        logger.error(f"Error generating Neutral Calendar: {str(e)}", exc_info=True)
        return None
