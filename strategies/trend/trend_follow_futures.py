"""
Trend Following Futures Strategy

Directional trend following strategy for NIFTY futures:
- LONG if price > EMA(50) > EMA(100)
- SHORT if price < EMA(50) < EMA(100)

Risk Management:
- Max position: 1 lot
- Risk per trade ≤ 0.5% of capital
- Initial SL = 1.5 × ATR(14)
- Trailing SL = 2 × ATR (Chandelier)

Exit Rules:
- Stop loss hit
- EMA structure breaks
- Regime != TREND_CONTINUATION
"""

import logging
from datetime import datetime
from typing import Dict, Optional
from .config import (
    MAX_RISK_PCT_OF_CAPITAL,
    MAX_POSITION_SIZE,
    INITIAL_STOP_LOSS_ATR_MULTIPLIER,
    TRAILING_STOP_LOSS_ATR_MULTIPLIER,
    EMA_FAST_PERIOD,
    EMA_SLOW_PERIOD,
    EXIT_ON_REGIME_CHANGE,
    EXIT_ON_EMA_BREAK
)

logger = logging.getLogger(__name__)


def get_nifty_futures_price(api, symbol_manager) -> Optional[float]:
    """
    Get current NIFTY futures price.
    
    Args:
        api: ShoonyaApiPy instance
        symbol_manager: SymbolManager instance
    
    Returns:
        float: Current futures price or None if error
    """
    try:
        # Get active NIFTY futures
        futures = symbol_manager.get_index_futures()
        nifty_future = next((f for f in futures if f.get('index_name') == 'NIFTY'), None)
        
        if not nifty_future:
            logger.warning("No NIFTY future found")
            return None
        
        # Get quote
        quote = api.get_quotes(exchange='NFO', token=nifty_future['token'])
        if quote and 'lp' in quote:
            futures_price = float(quote['lp'])
            logger.debug(f"NIFTY futures price: {futures_price}")
            return futures_price
        else:
            logger.warning("Could not get NIFTY futures quote")
            return None
            
    except Exception as e:
        logger.error(f"Error getting NIFTY futures price: {str(e)}")
        return None


def get_ema_structure(api, symbol_manager, spot_price) -> Optional[Dict]:
    """
    Get EMA structure for trend detection.
    
    Args:
        api: ShoonyaApiPy instance
        symbol_manager: SymbolManager instance
        spot_price: Current spot price (for fallback)
    
    Returns:
        Dictionary with:
        {
            'ema_50': float,
            'ema_100': float,
            'current_price': float,
            'direction': 'LONG' | 'SHORT' | None
        }
    """
    try:
        from technical_indicators import get_15min_candle_data, calculate_ema
        
        # #region agent log
        import json
        try:
            with open('/Users/arshdeep/git/ironcondor/.cursor/debug.log', 'a') as f:
                f.write(json.dumps({"location":"trend_follow_futures.py:93","message":"Calling get_15min_candle_data for EMA","data":{"has_api":api is not None,"has_symbol_manager":symbol_manager is not None,"spot_price":spot_price},"timestamp":int(datetime.now().timestamp()*1000),"sessionId":"debug-session","runId":"check-trades","hypothesisId":"E1"})+"\n")
        except: pass
        # #endregion
        
        # Get 15-minute candle data (same as regime detector uses)
        # This provides enough candles (100+) for EMA(100) calculation
        closes = get_15min_candle_data(api, symbol_manager, 'Nifty 50', lookback_hours=30)
        
        # #region agent log
        try:
            with open('/Users/arshdeep/git/ironcondor/.cursor/debug.log', 'a') as f:
                f.write(json.dumps({"location":"trend_follow_futures.py:99","message":"15-minute candle data result","data":{"closes_is_none":closes is None,"closes_len":len(closes) if closes else 0,"has_sufficient_data":closes is not None and len(closes) >= 100},"timestamp":int(datetime.now().timestamp()*1000),"sessionId":"debug-session","runId":"check-trades","hypothesisId":"E2"})+"\n")
        except: pass
        # #endregion
        
        if not closes or len(closes) < 100:
            logger.warning(f"Insufficient 15-minute candle data for EMA calculation: {len(closes) if closes else 0} candles (need 100+)")
            return None
        
        # Calculate EMAs
        ema_50 = calculate_ema(closes, period=EMA_FAST_PERIOD)
        ema_100 = calculate_ema(closes, period=EMA_SLOW_PERIOD)
        current_price = closes[-1] if closes else spot_price
        
        # #region agent log
        try:
            with open('/Users/arshdeep/git/ironcondor/.cursor/debug.log', 'a') as f:
                f.write(json.dumps({"location":"trend_follow_futures.py:108","message":"EMA calculation results","data":{"ema_50":ema_50,"ema_100":ema_100,"current_price":current_price,"has_all_values":ema_50 is not None and ema_100 is not None and current_price is not None},"timestamp":int(datetime.now().timestamp()*1000),"sessionId":"debug-session","runId":"check-trades","hypothesisId":"E3"})+"\n")
        except: pass
        # #endregion
        
        if not ema_50 or not ema_100 or not current_price:
            logger.warning("Could not calculate EMAs")
            return None
        
        # Determine direction
        direction = None
        long_structure = current_price > ema_50 > ema_100
        short_structure = current_price < ema_50 < ema_100
        
        # #region agent log
        try:
            with open('/Users/arshdeep/git/ironcondor/.cursor/debug.log', 'a') as f:
                f.write(json.dumps({"location":"trend_follow_futures.py:120","message":"EMA structure check","data":{"long_structure":long_structure,"short_structure":short_structure,"price_ema50":current_price > ema_50,"ema50_ema100":ema_50 > ema_100,"price":current_price,"ema50":ema_50,"ema100":ema_100},"timestamp":int(datetime.now().timestamp()*1000),"sessionId":"debug-session","runId":"check-trades","hypothesisId":"E4"})+"\n")
        except: pass
        # #endregion
        
        if long_structure:
            direction = 'LONG'
        elif short_structure:
            direction = 'SHORT'
        
        return {
            'ema_50': ema_50,
            'ema_100': ema_100,
            'current_price': current_price,
            'direction': direction
        }
        
    except Exception as e:
        logger.error(f"Error getting EMA structure: {str(e)}")
        return None


def calculate_position_size(futures_price: float, atr: float, capital: float, 
                           lot_size: int = 50) -> Dict:
    """
    Calculate position size based on risk limits.
    
    Args:
        futures_price: Current futures price
        atr: ATR(14) value
        capital: Total capital
        lot_size: Lot size for NIFTY futures (default: 50)
    
    Returns:
        Dictionary with position details:
        {
            'lots': int,
            'quantity': int,
            'risk_amount': float,
            'stop_loss_price': float,
            'risk_per_share': float
        }
    """
    try:
        # #region agent log
        import json
        try:
            with open('/Users/arshdeep/git/ironcondor/.cursor/debug.log', 'a') as f:
                f.write(json.dumps({"location":"trend_follow_futures.py:182","message":"calculate_position_size entry","data":{"futures_price":futures_price,"atr":atr,"capital":capital,"lot_size":lot_size,"INITIAL_STOP_LOSS_ATR_MULTIPLIER":INITIAL_STOP_LOSS_ATR_MULTIPLIER,"MAX_RISK_PCT_OF_CAPITAL":MAX_RISK_PCT_OF_CAPITAL,"MAX_POSITION_SIZE":MAX_POSITION_SIZE},"timestamp":int(datetime.now().timestamp()*1000),"sessionId":"debug-session","runId":"position-size-debug","hypothesisId":"H1,H2,H4"})+"\n")
        except: pass
        # #endregion
        
        # Calculate initial stop loss
        stop_loss_atr = atr * INITIAL_STOP_LOSS_ATR_MULTIPLIER
        risk_per_share = stop_loss_atr
        
        # #region agent log
        try:
            with open('/Users/arshdeep/git/ironcondor/.cursor/debug.log', 'a') as f:
                f.write(json.dumps({"location":"trend_follow_futures.py:190","message":"Risk per share calculated","data":{"stop_loss_atr":stop_loss_atr,"risk_per_share":risk_per_share},"timestamp":int(datetime.now().timestamp()*1000),"sessionId":"debug-session","runId":"position-size-debug","hypothesisId":"H2"})+"\n")
        except: pass
        # #endregion
        
        # Maximum risk per trade
        max_risk_amount = capital * MAX_RISK_PCT_OF_CAPITAL
        
        # #region agent log
        try:
            with open('/Users/arshdeep/git/ironcondor/.cursor/debug.log', 'a') as f:
                f.write(json.dumps({"location":"trend_follow_futures.py:196","message":"Max risk amount calculated","data":{"max_risk_amount":max_risk_amount,"capital":capital,"MAX_RISK_PCT_OF_CAPITAL":MAX_RISK_PCT_OF_CAPITAL},"timestamp":int(datetime.now().timestamp()*1000),"sessionId":"debug-session","runId":"position-size-debug","hypothesisId":"H2,H4"})+"\n")
        except: pass
        # #endregion
        
        # Calculate maximum quantity based on risk
        max_quantity_by_risk_raw = max_risk_amount / risk_per_share if risk_per_share > 0 else 0
        max_quantity_by_risk = int(max_quantity_by_risk_raw)
        
        # #region agent log
        try:
            with open('/Users/arshdeep/git/ironcondor/.cursor/debug.log', 'a') as f:
                f.write(json.dumps({"location":"trend_follow_futures.py:203","message":"Max quantity by risk calculated","data":{"max_quantity_by_risk_raw":max_quantity_by_risk_raw,"max_quantity_by_risk":max_quantity_by_risk,"max_risk_amount":max_risk_amount,"risk_per_share":risk_per_share},"timestamp":int(datetime.now().timestamp()*1000),"sessionId":"debug-session","runId":"position-size-debug","hypothesisId":"H1,H2,H5"})+"\n")
        except: pass
        # #endregion
        
        # Limit to max position size
        max_position_size_limit = MAX_POSITION_SIZE * lot_size
        max_quantity = min(max_quantity_by_risk, max_position_size_limit)
        
        # #region agent log
        try:
            with open('/Users/arshdeep/git/ironcondor/.cursor/debug.log', 'a') as f:
                f.write(json.dumps({"location":"trend_follow_futures.py:210","message":"Max quantity after limit","data":{"max_quantity":max_quantity,"max_quantity_by_risk":max_quantity_by_risk,"max_position_size_limit":max_position_size_limit,"MAX_POSITION_SIZE":MAX_POSITION_SIZE,"lot_size":lot_size},"timestamp":int(datetime.now().timestamp()*1000),"sessionId":"debug-session","runId":"position-size-debug","hypothesisId":"H1,H3"})+"\n")
        except: pass
        # #endregion
        
        # Round down to lot size
        lots = max_quantity // lot_size
        quantity = lots * lot_size
        
        # #region agent log
        try:
            with open('/Users/arshdeep/git/ironcondor/.cursor/debug.log', 'a') as f:
                f.write(json.dumps({"location":"trend_follow_futures.py:217","message":"Lots and quantity calculated","data":{"lots":lots,"quantity":quantity,"max_quantity":max_quantity,"lot_size":lot_size},"timestamp":int(datetime.now().timestamp()*1000),"sessionId":"debug-session","runId":"position-size-debug","hypothesisId":"H1"})+"\n")
        except: pass
        # #endregion
        
        if quantity == 0:
            logger.warning(f"Position size is 0: risk_per_share={risk_per_share:.2f}, max_risk={max_risk_amount:.2f}")
            # #region agent log
            try:
                with open('/Users/arshdeep/git/ironcondor/.cursor/debug.log', 'a') as f:
                    f.write(json.dumps({"location":"trend_follow_futures.py:223","message":"Position size is 0 - returning zero","data":{"risk_per_share":risk_per_share,"max_risk_amount":max_risk_amount,"max_quantity_by_risk":max_quantity_by_risk,"max_quantity":max_quantity,"lots":lots,"quantity":quantity},"timestamp":int(datetime.now().timestamp()*1000),"sessionId":"debug-session","runId":"position-size-debug","hypothesisId":"H1,H2,H3"})+"\n")
            except: pass
            # #endregion
            return {
                'lots': 0,
                'quantity': 0,
                'risk_amount': 0,
                'stop_loss_price': 0,
                'risk_per_share': risk_per_share
            }
        
        # Calculate actual risk
        actual_risk_amount = quantity * risk_per_share
        
        # Calculate stop loss price (will be set based on direction in entry)
        stop_loss_price = 0  # Will be set in generate_trend_follow_trade
        
        # #region agent log
        try:
            with open('/Users/arshdeep/git/ironcondor/.cursor/debug.log', 'a') as f:
                f.write(json.dumps({"location":"trend_follow_futures.py:241","message":"Position size calculated successfully","data":{"lots":lots,"quantity":quantity,"actual_risk_amount":actual_risk_amount,"risk_per_share":risk_per_share},"timestamp":int(datetime.now().timestamp()*1000),"sessionId":"debug-session","runId":"position-size-debug","hypothesisId":"H1"})+"\n")
        except: pass
        # #endregion
        
        return {
            'lots': lots,
            'quantity': quantity,
            'risk_amount': actual_risk_amount,
            'stop_loss_price': stop_loss_price,
            'risk_per_share': risk_per_share
        }
        
    except Exception as e:
        logger.error(f"Error calculating position size: {str(e)}")
        # #region agent log
        try:
            with open('/Users/arshdeep/git/ironcondor/.cursor/debug.log', 'a') as f:
                f.write(json.dumps({"location":"trend_follow_futures.py:252","message":"Exception in calculate_position_size","data":{"error":str(e)},"timestamp":int(datetime.now().timestamp()*1000),"sessionId":"debug-session","runId":"position-size-debug","hypothesisId":"H1"})+"\n")
        except: pass
        # #endregion
        return {
            'lots': 0,
            'quantity': 0,
            'risk_amount': 0,
            'stop_loss_price': 0,
            'risk_per_share': 0
        }


def generate_trend_follow_trade(market_state: Dict, capital: float = 1000000.0,
                                api=None, symbol_manager=None) -> Optional[Dict]:
    """
    Generate Trend Following Futures trade proposal.
    
    Entry conditions (ALL required):
    - regime == "TREND_CONTINUATION"
    - EMA structure aligned (price > EMA50 > EMA100 for LONG, or price < EMA50 < EMA100 for SHORT)
    - Risk per trade ≤ 0.5% of capital
    - Max position: 1 lot
    - No other active positions
    
    Args:
        market_state: Market state dictionary with:
            - spot_price: float
            - regime: str (must be "TREND_CONTINUATION")
            - adx_14: float
            - atr: float
        capital: Total capital allocated (default: ₹10L)
        api: ShoonyaApiPy instance
        symbol_manager: SymbolManager instance
    
    Returns:
        Trade proposal dictionary or None if rejected:
        {
            "strategy": "TREND_FOLLOW_FUTURE",
            "book": "TREND",
            "regime_at_entry": "TREND_CONTINUATION",
            "direction": "LONG" | "SHORT",
            "instrument": "NIFTY_FUTURE",
            "entry_price": float,
            "quantity": int,
            "lots": int,
            "stop_loss_price": float,
            "initial_stop_loss_atr": float,
            "trailing_stop_loss_atr": float,
            "risk_amount": float,
            "risk_pct_of_capital": float,
            "ema_50": float,
            "ema_100": float,
            "spot_price": float,
            "generated_at": str (ISO timestamp)
        }
    """
    try:
        # #region agent log
        import json
        try:
            with open('/Users/arshdeep/git/ironcondor/.cursor/debug.log', 'a') as f:
                f.write(json.dumps({"location":"trend_follow_futures.py:248","message":"generate_trend_follow_trade called","data":{"regime":market_state.get('regime'),"spot_price":market_state.get('spot_price'),"atr":market_state.get('atr'),"adx":market_state.get('adx_14')},"timestamp":int(datetime.now().timestamp()*1000),"sessionId":"debug-session","runId":"check-trades","hypothesisId":"T0"})+"\n")
        except: pass
        # #endregion
        
        # Check if regime is TREND_CONTINUATION
        regime = market_state.get('regime', 'NEUTRAL')
        if regime != "TREND_CONTINUATION":
            # #region agent log
            try:
                with open('/Users/arshdeep/git/ironcondor/.cursor/debug.log', 'a') as f:
                    f.write(json.dumps({"location":"trend_follow_futures.py:252","message":"Regime check failed","data":{"regime":regime,"expected":"TREND_CONTINUATION"},"timestamp":int(datetime.now().timestamp()*1000),"sessionId":"debug-session","runId":"check-trades","hypothesisId":"T0a"})+"\n")
            except: pass
            # #endregion
            logger.debug(f"Trend strategy rejected: regime is {regime}, not TREND_CONTINUATION")
            return None
        
        # Validate required inputs
        spot_price = market_state.get('spot_price')
        atr = market_state.get('atr')
        
        if spot_price is None or spot_price <= 0:
            logger.debug("Invalid or missing spot_price")
            return None
        
        if atr is None or atr <= 0:
            logger.debug("Invalid or missing ATR")
            return None
        
        if not api or not symbol_manager:
            logger.debug("API or symbol_manager not provided")
            return None
        
        # Get futures price
        # #region agent log
        try:
            with open('/Users/arshdeep/git/ironcondor/.cursor/debug.log', 'a') as f:
                f.write(json.dumps({"location":"trend_follow_futures.py:272","message":"Getting futures price","data":{"has_api":api is not None,"has_symbol_manager":symbol_manager is not None},"timestamp":int(datetime.now().timestamp()*1000),"sessionId":"debug-session","runId":"check-trades","hypothesisId":"T4"})+"\n")
        except: pass
        # #endregion
        
        futures_price = get_nifty_futures_price(api, symbol_manager)
        
        # #region agent log
        try:
            with open('/Users/arshdeep/git/ironcondor/.cursor/debug.log', 'a') as f:
                f.write(json.dumps({"location":"trend_follow_futures.py:275","message":"Futures price result","data":{"futures_price":futures_price,"has_price":futures_price is not None and futures_price > 0},"timestamp":int(datetime.now().timestamp()*1000),"sessionId":"debug-session","runId":"check-trades","hypothesisId":"T5"})+"\n")
        except: pass
        # #endregion
        
        if not futures_price:
            logger.debug("Could not get NIFTY futures price")
            return None
        
        # Get EMA structure
        # #region agent log
        import json
        try:
            with open('/Users/arshdeep/git/ironcondor/.cursor/debug.log', 'a') as f:
                f.write(json.dumps({"location":"trend_follow_futures.py:278","message":"Calling get_ema_structure","data":{"has_api":api is not None,"has_symbol_manager":symbol_manager is not None,"spot_price":spot_price},"timestamp":int(datetime.now().timestamp()*1000),"sessionId":"debug-session","runId":"check-trades","hypothesisId":"T1"})+"\n")
        except: pass
        # #endregion
        
        ema_structure = get_ema_structure(api, symbol_manager, spot_price)
        
        # #region agent log
        try:
            with open('/Users/arshdeep/git/ironcondor/.cursor/debug.log', 'a') as f:
                f.write(json.dumps({"location":"trend_follow_futures.py:282","message":"EMA structure result","data":{"ema_structure_is_none":ema_structure is None,"has_direction":ema_structure.get('direction') if ema_structure else None,"ema_50":ema_structure.get('ema_50') if ema_structure else None,"ema_100":ema_structure.get('ema_100') if ema_structure else None},"timestamp":int(datetime.now().timestamp()*1000),"sessionId":"debug-session","runId":"check-trades","hypothesisId":"T2"})+"\n")
        except: pass
        # #endregion
        
        if not ema_structure:
            logger.debug("Could not get EMA structure")
            return None
        
        direction = ema_structure.get('direction')
        
        # #region agent log
        try:
            with open('/Users/arshdeep/git/ironcondor/.cursor/debug.log', 'a') as f:
                f.write(json.dumps({"location":"trend_follow_futures.py:290","message":"Direction check","data":{"direction":direction,"has_direction":direction is not None},"timestamp":int(datetime.now().timestamp()*1000),"sessionId":"debug-session","runId":"check-trades","hypothesisId":"T3"})+"\n")
        except: pass
        # #endregion
        
        if not direction:
            logger.debug("EMA structure not aligned (no clear trend direction)")
            return None
        
        # Calculate position size
        lot_size = 50  # NIFTY futures lot size
        
        # #region agent log
        try:
            with open('/Users/arshdeep/git/ironcondor/.cursor/debug.log', 'a') as f:
                f.write(json.dumps({"location":"trend_follow_futures.py:290","message":"Calculating position size","data":{"futures_price":futures_price,"atr":atr,"capital":capital,"lot_size":lot_size},"timestamp":int(datetime.now().timestamp()*1000),"sessionId":"debug-session","runId":"check-trades","hypothesisId":"T6"})+"\n")
        except: pass
        # #endregion
        
        position_info = calculate_position_size(futures_price, atr, capital, lot_size)
        
        # #region agent log
        try:
            with open('/Users/arshdeep/git/ironcondor/.cursor/debug.log', 'a') as f:
                f.write(json.dumps({"location":"trend_follow_futures.py:295","message":"Position size result","data":{"quantity":position_info.get('quantity'),"lots":position_info.get('lots'),"risk_amount":position_info.get('risk_amount')},"timestamp":int(datetime.now().timestamp()*1000),"sessionId":"debug-session","runId":"check-trades","hypothesisId":"T7"})+"\n")
        except: pass
        # #endregion
        
        if position_info['quantity'] == 0:
            logger.debug("Position size is 0 (risk limits too tight)")
            return None
        
        # Calculate stop loss price based on direction
        stop_loss_atr = atr * INITIAL_STOP_LOSS_ATR_MULTIPLIER
        if direction == 'LONG':
            stop_loss_price = futures_price - stop_loss_atr
        else:  # SHORT
            stop_loss_price = futures_price + stop_loss_atr
        
        # Calculate risk metrics
        risk_amount = position_info['risk_amount']
        risk_pct_of_capital = (risk_amount / capital) * 100 if capital > 0 else 0
        
        # Build trade proposal
        trade_proposal = {
            "strategy": "TREND_FOLLOW_FUTURE",
            "book": "TREND",
            "regime_at_entry": "TREND_CONTINUATION",
            "direction": direction,
            "instrument": "NIFTY_FUTURE",
            "entry_price": futures_price,
            "quantity": position_info['quantity'],
            "lots": position_info['lots'],
            "stop_loss_price": stop_loss_price,
            "initial_stop_loss_atr": stop_loss_atr,
            "trailing_stop_loss_atr": atr * TRAILING_STOP_LOSS_ATR_MULTIPLIER,
            "risk_amount": risk_amount,
            "risk_pct_of_capital": risk_pct_of_capital,
            "ema_50": ema_structure['ema_50'],
            "ema_100": ema_structure['ema_100'],
            "spot_price": spot_price,
            "atr": atr,
            "adx": market_state.get('adx_14'),
            "generated_at": datetime.now().isoformat()
        }
        
        logger.info(
            f"Trend follow trade generated: {direction} {position_info['lots']} lots @ {futures_price:.2f}, "
            f"SL @ {stop_loss_price:.2f}, Risk: ₹{risk_amount:.2f} ({risk_pct_of_capital:.2f}%)"
        )
        
        return trade_proposal
        
    except Exception as e:
        logger.error(f"Error generating trend follow trade: {str(e)}", exc_info=True)
        return None
