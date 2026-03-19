import pandas as pd
from datetime import datetime

def get_optimized_symbols(sm):
    """
    Get symbols only for the nearest two expiries to reduce data collection load.
    """
    # Use symbol_manager to get all index derivatives
    derivatives = sm.get_all_index_derivatives(max_expiries_per_index=2)
    
    # We can also add spot prices for Nifty/BankNifty if needed
    spot_symbols = []
    for index in ['NIFTY', 'BANKNIFTY']:
        token_info = sm.get_token_info(index, exchange='NSE')
        if token_info:
            spot_symbols.append({
                'symbol': token_info['symbol'],
                'token': token_info['token'],
                'exchange': 'NSE',
                'instrument': 'INDEX'
            })
            
    return derivatives + spot_symbols
