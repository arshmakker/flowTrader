"""
Convex Call Backspread Strategy

Structure:
- Sell 1 ATM Call
- Buy 2 OTM Calls (~ +1% strike)
- Same weekly expiry

Rules:
- Net debit ≤ 0.25% of spot value
- Max loss per trade ≤ 10% of total capital
"""

import pandas as pd
import numpy as np
import logging
import json
import uuid
from typing import Dict, Optional
from datetime import datetime

logger = logging.getLogger(__name__)
DEBUG_LOG = '/Users/arshdeep/git/regimetrader/.cursor/debug.log'

# Configuration
MAX_NET_DEBIT_PCT = 0.0025  # 0.25% of spot value
MAX_LOSS_PCT_OF_CAPITAL = 0.10  # 10% of total capital (₹1L for ₹10L capital)
from strategies.size_config import MIN_LOTS, MAX_LOTS, clamp_lots
OTM_CALL_DISTANCE_PCT = 0.01  # ~1% above ATM for OTM calls
MIN_DAYS_TO_EXPIRY = 2  # Don't enter if expiry within 2 days (OTM calls need time)
# Convex-only cap (order quantity = leg_qty × lots × lot_size; 2 lots → long 260, short 130 with lot_size 65)
CONVEX_MAX_LOTS = 2

# Broker margin example (from rejection screenshot): 10 lots required total ~₹38.18L (shortfall ₹29.87L + available ₹8.31L).
# Used to estimate margin for other lot sizes (scale linearly).
CONVEX_MARGIN_EXAMPLE_LOTS = 10
CONVEX_MARGIN_EXAMPLE_INR = 38_18_065  # ~38.18 lakh for 10 lots (MIS)


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
        # #region agent log
        try:
            with open(DEBUG_LOG, 'a') as _f:
                _f.write(json.dumps({"location":"call_backspread.py:generate_nifty_call_backspread","message":"Convex generator entry","data":{"spot_price":spot_price,"expiry":expiry,"days_to_expiry":days_to_expiry,"min_dte":MIN_DAYS_TO_EXPIRY},"timestamp":int(datetime.now().timestamp()*1000),"sessionId":"debug-session","runId":"convex-debug","hypothesisId":"H4"})+"\n")
        except Exception: pass
        # #endregion
        if spot_price is None or spot_price <= 0:
            logger.error("Invalid or missing spot_price in market_state")
            # #region agent log
            try:
                with open(DEBUG_LOG, 'a') as _f:
                    _f.write(json.dumps({"location":"call_backspread.py:reject","message":"Convex reject","data":{"reason":"SPOT_INVALID","spot_price":spot_price},"timestamp":int(datetime.now().timestamp()*1000),"sessionId":"debug-session","runId":"convex-debug","hypothesisId":"H4"})+"\n")
            except Exception: pass
            # #endregion
            return None
        
        if expiry is None:
            logger.error("Missing expiry in market_state")
            # #region agent log
            try:
                with open(DEBUG_LOG, 'a') as _f:
                    _f.write(json.dumps({"location":"call_backspread.py:reject","message":"Convex reject","data":{"reason":"EXPIRY_MISSING"},"timestamp":int(datetime.now().timestamp()*1000),"sessionId":"debug-session","runId":"convex-debug","hypothesisId":"H4"})+"\n")
            except Exception: pass
            # #endregion
            return None
        
        # Entry guard: Don't enter if too close to expiry
        # OTM calls need time for the asymmetric payoff to work
        if days_to_expiry is not None and days_to_expiry <= MIN_DAYS_TO_EXPIRY:
            # #region agent log
            try:
                with open(DEBUG_LOG, 'a') as _f:
                    _f.write(json.dumps({"location":"call_backspread.py:reject","message":"Convex reject","data":{"reason":"DAYS_TO_EXPIRY_LE_2","days_to_expiry":days_to_expiry,"min_required":MIN_DAYS_TO_EXPIRY+1},"timestamp":int(datetime.now().timestamp()*1000),"sessionId":"debug-session","runId":"convex-debug","hypothesisId":"H2"})+"\n")
            except Exception: pass
            # #endregion
            logger.info(
                f"Call Backspread rejected: Expires in {days_to_expiry} days "
                f"(minimum: {MIN_DAYS_TO_EXPIRY + 1} days). OTM calls need time value."
            )
            return None
        
        if option_chain.empty:
            logger.warning("Empty option chain provided")
            # #region agent log
            try:
                with open(DEBUG_LOG, 'a') as _f:
                    _f.write(json.dumps({"location":"call_backspread.py:reject","message":"Convex reject","data":{"reason":"OPTION_CHAIN_EMPTY"},"timestamp":int(datetime.now().timestamp()*1000),"sessionId":"debug-session","runId":"convex-debug","hypothesisId":"H3"})+"\n")
            except Exception: pass
            # #endregion
            return None
        
        # Filter calls only
        calls = option_chain[option_chain['option_type'].str.upper() == 'CE'].copy()
        if calls.empty:
            logger.warning("No call options in chain")
            # #region agent log
            try:
                with open(DEBUG_LOG, 'a') as _f:
                    _f.write(json.dumps({"location":"call_backspread.py:reject","message":"Convex reject","data":{"reason":"NO_CALL_OPTIONS"},"timestamp":int(datetime.now().timestamp()*1000),"sessionId":"debug-session","runId":"convex-debug","hypothesisId":"H4"})+"\n")
            except Exception: pass
            # #endregion
            return None
        
        # Sort by strike
        calls = calls.sort_values('strike')
        
        # Find ATM call (closest to spot)
        calls['distance_from_spot'] = abs(calls['strike'] - spot_price)
        atm_call = calls.loc[calls['distance_from_spot'].idxmin()].copy()
        
        if atm_call.empty:
            logger.warning("Could not find ATM call")
            # #region agent log
            try:
                with open(DEBUG_LOG, 'a') as _f:
                    _f.write(json.dumps({"location":"call_backspread.py:reject","message":"Convex reject","data":{"reason":"ATM_CALL_NOT_FOUND"},"timestamp":int(datetime.now().timestamp()*1000),"sessionId":"debug-session","runId":"convex-debug","hypothesisId":"H4"})+"\n")
            except Exception: pass
            # #endregion
            return None
        
        # Find OTM calls (~1% above ATM strike)
        target_otm_strike = spot_price * (1 + OTM_CALL_DISTANCE_PCT)
        calls['distance_from_target'] = abs(calls['strike'] - target_otm_strike)
        
        # Get the closest OTM call
        otm_call = calls.loc[calls['distance_from_target'].idxmin()].copy()
        
        if otm_call.empty:
            logger.warning("Could not find OTM call")
            # #region agent log
            try:
                with open(DEBUG_LOG, 'a') as _f:
                    _f.write(json.dumps({"location":"call_backspread.py:reject","message":"Convex reject","data":{"reason":"OTM_CALL_NOT_FOUND"},"timestamp":int(datetime.now().timestamp()*1000),"sessionId":"debug-session","runId":"convex-debug","hypothesisId":"H4"})+"\n")
            except Exception: pass
            # #endregion
            return None
        
        # Validate strikes
        if otm_call['strike'] <= atm_call['strike']:
            logger.warning(f"OTM call strike {otm_call['strike']} not above ATM {atm_call['strike']}")
            # #region agent log
            try:
                with open(DEBUG_LOG, 'a') as _f:
                    _f.write(json.dumps({"location":"call_backspread.py:reject","message":"Convex reject","data":{"reason":"OTM_NOT_ABOVE_ATM","otm_strike":float(otm_call['strike']),"atm_strike":float(atm_call['strike'])},"timestamp":int(datetime.now().timestamp()*1000),"sessionId":"debug-session","runId":"convex-debug","hypothesisId":"H4"})+"\n")
            except Exception: pass
            # #endregion
            return None
        
        # Get prices (use mid_price if available, else ltp)
        atm_price = atm_call.get('mid_price', atm_call.get('ltp', 0))
        otm_price = otm_call.get('mid_price', otm_call.get('ltp', 0))
        
        if atm_price <= 0 or otm_price <= 0:
            logger.warning(f"Invalid prices: ATM={atm_price}, OTM={otm_price}")
            # #region agent log
            try:
                with open(DEBUG_LOG, 'a') as _f:
                    _f.write(json.dumps({"location":"call_backspread.py:reject","message":"Convex reject","data":{"reason":"INVALID_PRICES","atm_price":float(atm_price) if atm_price is not None else None,"otm_price":float(otm_price) if otm_price is not None else None},"timestamp":int(datetime.now().timestamp()*1000),"sessionId":"debug-session","runId":"convex-debug","hypothesisId":"H4"})+"\n")
            except Exception: pass
            # #endregion
            return None
        
        # Calculate net debit
        # Sell 1 ATM, Buy 2 OTM
        net_debit = (2 * otm_price) - atm_price
        
        # Validate net debit (must be ≤ 0.25% of spot value)
        max_debit = spot_price * MAX_NET_DEBIT_PCT
        # Allow debit (pay to open) or credit/zero (receive at open)
        if net_debit > max_debit:
            logger.info(f"Net debit {net_debit:.2f} exceeds max {max_debit:.2f} (0.25% of spot)")
            # #region agent log
            try:
                with open(DEBUG_LOG, 'a') as _f:
                    _f.write(json.dumps({"location":"call_backspread.py:reject","message":"Convex reject","data":{"reason":"NET_DEBIT_EXCEEDS_MAX","net_debit":net_debit,"max_debit":max_debit},"timestamp":int(datetime.now().timestamp()*1000),"sessionId":"debug-session","runId":"convex-debug","hypothesisId":"H4"})+"\n")
            except Exception: pass
            # #endregion
            return None
        
        # Max loss: when we pay a debit, loss at expiry at/below ATM = debit paid; when we receive credit, that scenario has no loss
        max_loss_per_lot = max(0.0, net_debit)
        max_allowed_loss = capital * MAX_LOSS_PCT_OF_CAPITAL
        if max_loss_per_lot > 0 and max_loss_per_lot > max_allowed_loss:
            logger.info(f"Max loss {max_loss_per_lot:.2f} exceeds {max_allowed_loss:.2f} (1% of capital)")
            # #region agent log
            try:
                with open(DEBUG_LOG, 'a') as _f:
                    _f.write(json.dumps({"location":"call_backspread.py:reject","message":"Convex reject","data":{"reason":"MAX_LOSS_EXCEEDS_CAPITAL","max_loss_per_lot":max_loss_per_lot,"max_allowed_loss":max_allowed_loss},"timestamp":int(datetime.now().timestamp()*1000),"sessionId":"debug-session","runId":"convex-debug","hypothesisId":"H4"})+"\n")
            except Exception: pass
            # #endregion
            return None
        
        # Calculate lot size
        lot_size = option_chain.iloc[0].get('lot_size', 50)
        # When net credit/zero: max_loss_per_lot is 0, use minimum lots. When debit: size by max loss constraint.
        if max_loss_per_lot <= 0:
            lots = MIN_LOTS
        else:
            lots = int(max_allowed_loss / (max_loss_per_lot * lot_size))
            # Ensure minimum lots
            if lots < MIN_LOTS:
                lots = MIN_LOTS

        # Enforce global caps via clamp_lots, then Convex cap
        clamped = clamp_lots(lots)
        if clamped == 0:
            logger.info("Position sizing resulted in 0 lots (invalid)")
            try:
                with open(DEBUG_LOG, 'a') as _f:
                    _f.write(json.dumps({"location":"call_backspread.py:reject","message":"Convex reject","data":{"reason":"LOTS_ZERO","lots":lots,"max_allowed_loss":max_allowed_loss,"max_loss_per_lot":max_loss_per_lot,"lot_size":lot_size},"timestamp":int(datetime.now().timestamp()*1000),"sessionId":"debug-session","runId":"convex-debug","hypothesisId":"H4"})+"\n")
            except Exception: pass
            return None
        if clamped != lots:
            lots = clamped
        lots = min(lots, CONVEX_MAX_LOTS)

        # Log lot/quantity calculation: long leg = 2 × lots × lot_size, short leg = 1 × lots × lot_size
        long_qty = 2 * lots * lot_size
        short_qty = 1 * lots * lot_size
        logger.info(
            "Convex lots: %d (cap %d). Order quantities: long = 2×%d×%d = %d, short = 1×%d×%d = %d",
            lots, CONVEX_MAX_LOTS, lots, lot_size, long_qty, lots, lot_size, short_qty
        )

        # Money: capital to enter (net debit total), max loss (worst-case risk), and estimated broker margin
        net_debit_total = net_debit * lots * lot_size
        max_loss_total = max_loss_per_lot * lots * lot_size
        # Margin estimate from broker example: 10 lots → ~₹38.18L required; scale linearly for current lots
        margin_est_inr = (lots / CONVEX_MARGIN_EXAMPLE_LOTS) * CONVEX_MARGIN_EXAMPLE_INR
        if net_debit_total >= 0:
            logger.info(
                "Convex money: capital to enter (net debit) = ₹%.2f (₹%.2f/lot × %d lots × %d). Max loss (risk) = ₹%.2f. Est. margin (broker) ≈ ₹%.2f",
                net_debit_total, net_debit, lots, lot_size, max_loss_total, margin_est_inr
            )
        else:
            logger.info(
                "Convex money: net credit on entry = ₹%.2f. Max loss (risk) = ₹%.2f. Est. margin (broker) ≈ ₹%.2f",
                -net_debit_total, max_loss_total, margin_est_inr
            )

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
                    "tradingsymbol": str(atm_call.get('tradingsymbol', '')),  # Exact NFO symbol for place_order
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
                    "tradingsymbol": str(otm_call.get('tradingsymbol', '')),  # Exact NFO symbol for place_order
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
            "max_loss": float(max_loss_total),
            "max_loss_per_lot": float(max_loss_per_lot),
            "spot_price": float(spot_price),
            "net_debit": float(net_debit),
            "net_debit_total": float(net_debit_total),
            "margin_estimate_inr": float(margin_est_inr),  # from broker example: 10 lots ~₹38.18L, scaled by lots/10
            "lots": lots,
            "lot_size": lot_size,
            "days_to_expiry": days_to_expiry,
            "generated_at": datetime.now().isoformat(),
            "proposal_id": f"{datetime.now().isoformat()}_{uuid.uuid4().hex[:8]}"
        }
        
        logger.info(
            f"Call Backspread proposal: {lots} lots, "
            f"debit=₹{net_debit:.2f}/lot, max_loss=₹{max_loss_per_lot:.2f}/lot"
        )
        # #region agent log
        try:
            with open(DEBUG_LOG, 'a') as _f:
                _f.write(json.dumps({"location":"call_backspread.py:success","message":"Convex proposal","data":{"lots":lots,"net_debit":net_debit},"timestamp":int(datetime.now().timestamp()*1000),"sessionId":"debug-session","runId":"convex-debug","hypothesisId":"H4"})+"\n")
        except Exception: pass
        # #endregion
        return trade_proposal
        
    except Exception as e:
        logger.error(f"Error generating Call Backspread: {str(e)}", exc_info=True)
        return None
