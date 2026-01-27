"""
Convex Call Backspread Strategy

Structure:
- Sell 1 ATM Call
- Buy 2 OTM Calls (~ +1% strike)
- Same weekly expiry

Rules:
- Net debit ≤ 0.25% of spot value
- Max loss per trade ≤ 1% of total capital
"""

import pandas as pd
import numpy as np
import logging
from typing import Dict, Optional
from datetime import datetime

logger = logging.getLogger(__name__)

# Configuration
MAX_NET_DEBIT_PCT = 0.0025  # 0.25% of spot value
MAX_LOSS_PCT_OF_CAPITAL = 0.01  # 1% of total capital
OTM_CALL_DISTANCE_PCT = 0.01  # ~1% above ATM for OTM calls
MIN_DAYS_TO_EXPIRY = 2  # Don't enter if expiry within 2 days (OTM calls need time)


def generate_nifty_call_backspread(market_state: Dict, option_chain: pd.DataFrame, 
                                   capital: float = 1000000.0) -> Optional[Dict]:
    """
    Generate NIFTY Call Backspread trade proposal.
    
    Args:
        market_state: Market state dictionary with:
            - spot_price: float
            - expiry: str (YYYY-MM-DD)
            - days_to_expiry: int
        option_chain: DataFrame with option chain data
        capital: Total capital allocated (default: ₹10L)
    
    Returns:
        Trade proposal dictionary or None if rejected:
        {
            "strategy": "CALL_BACKSPREAD",
            "book": "CONVEX",
            "regime_at_entry": "CONVEX",
            "expiry": "YYYY-MM-DD",
            "legs": [
                {
                    "position": "SHORT",
                    "option_type": "CE",
                    "strike": float,
                    "price": float,
                    ...
                },
                {
                    "position": "LONG",
                    "option_type": "CE",
                    "strike": float,
                    "price": float,
                    "quantity": 2,  # Buy 2 contracts
                    ...
                }
            ],
            "max_loss": float,
            "spot_price": float,
            "net_debit": float,
            "lots": int
        }
    """
    try:
        spot_price = market_state.get('spot_price')
        expiry = market_state.get('expiry')
        days_to_expiry = market_state.get('days_to_expiry')
        
        if spot_price is None or spot_price <= 0:
            logger.error("Invalid or missing spot_price in market_state")
            return None
        
        if expiry is None:
            logger.error("Missing expiry in market_state")
            return None
        
        # Entry guard: Don't enter if too close to expiry
        # OTM calls need time for the asymmetric payoff to work
        if days_to_expiry is not None and days_to_expiry <= MIN_DAYS_TO_EXPIRY:
            logger.info(
                f"Call Backspread rejected: Expires in {days_to_expiry} days "
                f"(minimum: {MIN_DAYS_TO_EXPIRY + 1} days). OTM calls need time value."
            )
            return None
        
        if option_chain.empty:
            logger.warning("Empty option chain provided")
            return None
        
        # Filter calls only
        calls = option_chain[option_chain['option_type'].str.upper() == 'CE'].copy()
        if calls.empty:
            logger.warning("No call options in chain")
            return None
        
        # Sort by strike
        calls = calls.sort_values('strike')
        
        # Find ATM call (closest to spot)
        calls['distance_from_spot'] = abs(calls['strike'] - spot_price)
        atm_call = calls.loc[calls['distance_from_spot'].idxmin()].copy()
        
        if atm_call.empty:
            logger.warning("Could not find ATM call")
            return None
        
        # Find OTM calls (~1% above ATM strike)
        target_otm_strike = spot_price * (1 + OTM_CALL_DISTANCE_PCT)
        calls['distance_from_target'] = abs(calls['strike'] - target_otm_strike)
        
        # Get the closest OTM call
        otm_call = calls.loc[calls['distance_from_target'].idxmin()].copy()
        
        if otm_call.empty:
            logger.warning("Could not find OTM call")
            return None
        
        # Validate strikes
        if otm_call['strike'] <= atm_call['strike']:
            logger.warning(f"OTM call strike {otm_call['strike']} not above ATM {atm_call['strike']}")
            return None
        
        # Get prices (use mid_price if available, else ltp)
        atm_price = atm_call.get('mid_price', atm_call.get('ltp', 0))
        otm_price = otm_call.get('mid_price', otm_call.get('ltp', 0))
        
        if atm_price <= 0 or otm_price <= 0:
            logger.warning(f"Invalid prices: ATM={atm_price}, OTM={otm_price}")
            return None
        
        # Calculate net debit
        # Sell 1 ATM, Buy 2 OTM
        net_debit = (2 * otm_price) - atm_price
        
        # Validate net debit (must be ≤ 0.25% of spot value)
        max_debit = spot_price * MAX_NET_DEBIT_PCT
        if net_debit > max_debit:
            logger.info(f"Net debit {net_debit:.2f} exceeds max {max_debit:.2f} (0.25% of spot)")
            return None
        
        if net_debit <= 0:
            logger.info(f"Net debit {net_debit:.2f} is not a debit (should be positive)")
            return None
        
        # Calculate max loss
        # Max loss occurs if price expires at or below ATM strike
        # Loss = Net debit paid
        max_loss_per_lot = net_debit
        
        # Validate max loss (must be ≤ 1% of capital)
        max_allowed_loss = capital * MAX_LOSS_PCT_OF_CAPITAL
        if max_loss_per_lot > max_allowed_loss:
            logger.info(f"Max loss {max_loss_per_lot:.2f} exceeds {max_allowed_loss:.2f} (1% of capital)")
            return None
        
        # Calculate lot size
        lot_size = option_chain.iloc[0].get('lot_size', 50)
        
        # Calculate number of lots based on max loss constraint
        lots = int(max_allowed_loss / (max_loss_per_lot * lot_size))
        if lots <= 0:
            logger.info("Position sizing resulted in 0 lots")
            return None
        
        # Build trade proposal
        trade_proposal = {
            "strategy": "CALL_BACKSPREAD",
            "book": "CONVEX",
            "regime_at_entry": "CONVEX",
            "expiry": expiry,
            "legs": [
                {
                    "position": "SHORT",
                    "option_type": "CE",
                    "strike": float(atm_call['strike']),
                    "price": float(atm_price),
                    "quantity": 1,  # Sell 1
                    "delta": float(atm_call.get('delta', 0)),
                    "ltp": float(atm_call.get('ltp', atm_price)),
                    "bid": float(atm_call.get('bid', atm_price)),
                    "ask": float(atm_call.get('ask', atm_price)),
                    "oi": int(atm_call.get('oi', 0)),
                    "volume": int(atm_call.get('volume', 0))
                },
                {
                    "position": "LONG",
                    "option_type": "CE",
                    "strike": float(otm_call['strike']),
                    "price": float(otm_price),
                    "quantity": 2,  # Buy 2
                    "delta": float(otm_call.get('delta', 0)),
                    "ltp": float(otm_call.get('ltp', otm_price)),
                    "bid": float(otm_call.get('bid', otm_price)),
                    "ask": float(otm_call.get('ask', otm_price)),
                    "oi": int(otm_call.get('oi', 0)),
                    "volume": int(otm_call.get('volume', 0))
                }
            ],
            "max_loss": float(max_loss_per_lot * lots * lot_size),
            "max_loss_per_lot": float(max_loss_per_lot),
            "spot_price": float(spot_price),
            "net_debit": float(net_debit),
            "net_debit_total": float(net_debit * lots * lot_size),
            "lots": lots,
            "lot_size": lot_size,
            "days_to_expiry": days_to_expiry,
            "generated_at": datetime.now().isoformat()
        }
        
        logger.info(
            f"Call Backspread proposal: {lots} lots, "
            f"debit=₹{net_debit:.2f}/lot, max_loss=₹{max_loss_per_lot:.2f}/lot"
        )
        
        return trade_proposal
        
    except Exception as e:
        logger.error(f"Error generating Call Backspread: {str(e)}", exc_info=True)
        return None
