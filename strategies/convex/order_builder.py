"""
Order Builder Utility for Convex Strategy

Provides functions to convert trade proposals/positions into broker orders,
with BUY orders prioritized before SELL orders to avoid margin issues.
"""

from typing import Dict, List, Optional
from datetime import datetime


def generate_nifty_symbol(strike: float, option_type: str, expiry: str) -> str:
    """
    Generate NIFTY option trading symbol.
    
    Format: NIFTY{YY}{MMM}{STRIKE}{CE/PE}
    Example: NIFTY26FEB25550CE
    
    Args:
        strike: Strike price (e.g., 25550)
        option_type: 'CE' or 'PE'
        expiry: Expiry date string (YYYY-MM-DD or DD-MON-YY)
    
    Returns:
        Trading symbol string
    """
    try:
        if '-' in expiry:
            if len(expiry) == 10:  # YYYY-MM-DD
                dt = datetime.strptime(expiry, '%Y-%m-%d')
            else:  # DD-MON-YY
                dt = datetime.strptime(expiry, '%d-%b-%y')
        else:
            dt = datetime.strptime(expiry, '%Y%m%d')
        
        yy = dt.strftime('%y')
        mon = dt.strftime('%b').upper()
        symbol = f"NIFTY{yy}{mon}{int(strike)}{option_type}"
        return symbol
    except Exception:
        return f"NIFTY{expiry}{int(strike)}{option_type}"


def build_convex_entry_orders(proposal: Dict, product_type: str = 'M') -> List[Dict]:
    """
    Build orders for Convex entry with BUY orders first.
    
    CRITICAL: BUY orders must be placed BEFORE SELL orders to avoid margin
    shortfall. SELL orders require margin, but BUY orders use available cash.
    
    Args:
        proposal: Trade proposal with legs
        product_type: 'M' for MIS, 'N' for NRML
    
    Returns:
        List of order dictionaries (BUY first, then SELL)
    """
    legs = proposal.get('legs', [])
    lots = proposal.get('lots', 1)
    lot_size = proposal.get('lot_size', 65)
    expiry = proposal.get('expiry')
    
    if not legs or not expiry:
        raise ValueError("Proposal missing legs or expiry")
    
    buy_orders = []
    sell_orders = []
    
    for leg in legs:
        position = leg.get('position', '').upper()
        option_type = leg.get('option_type', '').upper()
        strike = leg.get('strike')
        price = leg.get('price', leg.get('ltp', 0))
        
        if strike is None:
            continue
        
        symbol = generate_nifty_symbol(strike, option_type, expiry)
        
        leg_qty = leg.get('quantity', 1)
        quantity = leg_qty * lots * lot_size
        
        order = {
            'exchange': 'NSE',
            'tradingsymbol': symbol,
            'quantity': quantity,
            'price': float(price),
            'price_type': 'LMT',
        }
        
        if position == 'LONG':
            order['buy_or_sell'] = 'B'
            order['product_type'] = product_type
            buy_orders.append(order)
        else:
            order['buy_or_sell'] = 'S'
            order['product_type'] = product_type
            sell_orders.append(order)
    
    return buy_orders + sell_orders


def build_convex_exit_orders(position: Dict, exit_prices: Optional[Dict] = None, 
                             product_type: str = 'M') -> List[Dict]:
    """
    Build orders for Convex exit with BUY orders first.
    
    CRITICAL: BUY orders (to cover SHORT) must be placed BEFORE SELL orders
    (to close LONG) to avoid margin issues.
    
    Args:
        position: Position with legs
        exit_prices: Optional dict of {strike: price} for each leg
        product_type: 'M' for MIS, 'N' for NRML
    
    Returns:
        List of order dictionaries (BUY first, then SELL)
    """
    legs = position.get('legs', [])
    lots = position.get('lots', 1)
    lot_size = position.get('lot_size', 65)
    expiry = position.get('expiry')
    
    if not legs or not expiry:
        raise ValueError("Position missing legs or expiry")
    
    buy_orders = []
    sell_orders = []
    
    for leg in legs:
        position_type = leg.get('position', '').upper()
        option_type = leg.get('option_type', '').upper()
        strike = leg.get('strike')
        
        if strike is None:
            continue
        
        symbol = generate_nifty_symbol(strike, option_type, expiry)
        
        leg_qty = leg.get('quantity', 1)
        quantity = leg_qty * lots * lot_size
        
        if exit_prices and strike in exit_prices:
            price = exit_prices[strike]
        else:
            price = leg.get('price', leg.get('ltp', 0))
        
        order = {
            'exchange': 'NSE',
            'tradingsymbol': symbol,
            'quantity': quantity,
            'price': float(price),
            'price_type': 'MKT',
        }
        
        if position_type == 'SHORT':
            order['buy_or_sell'] = 'B'
            order['product_type'] = product_type
            buy_orders.append(order)
        else:
            order['buy_or_sell'] = 'S'
            order['product_type'] = product_type
            sell_orders.append(order)
    
    return buy_orders + sell_orders


def place_convex_trade(api, proposal: Dict, product_type: str = 'M') -> Dict:
    """
    Place Convex trade with BUY orders first.
    
    Args:
        api: ShoonyaApiPy instance
        proposal: Trade proposal
        product_type: 'M' for MIS, 'N' for NRML
    
    Returns:
        Result dict with success status and order details
    """
    orders = build_convex_entry_orders(proposal, product_type)
    
    print("=== Order Sequence (BUY first) ===")
    for i, order in enumerate(orders, 1):
        print(f"{i}. {order['buy_or_sell']} {order['quantity']} {order['tradingsymbol']} @ ₹{order['price']}")
    
    result = api.place_basket(orders)
    
    return {
        'success': True,
        'orders': orders,
        'result': result
    }


def close_convex_position(api, position: Dict, exit_prices: Optional[Dict] = None,
                          product_type: str = 'M') -> Dict:
    """
    Close Convex position with BUY orders first.
    
    Args:
        api: ShoonyaApiPy instance
        position: Position to close
        exit_prices: Optional dict of {strike: price}
        product_type: 'M' for MIS, 'N' for NRML
    
    Returns:
        Result dict with success status
    """
    orders = build_convex_exit_orders(position, exit_prices, product_type)
    
    print("=== Exit Order Sequence (BUY first) ===")
    for i, order in enumerate(orders, 1):
        print(f"{i}. {order['buy_or_sell']} {order['quantity']} {order['tradingsymbol']} @ ₹{order['price']}")
    
    result = api.place_basket(orders)
    
    return {
        'success': True,
        'orders': orders,
        'result': result
    }
