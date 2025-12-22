"""
Example usage of Iron Condor strategy module

This demonstrates how to use the strategy with mock data.
In production, you would pass real option chain data from your data collector.
"""

import pandas as pd
import sys
import os

# Add parent directory to path for imports
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../..')))

from strategies.iron_condor.strategy import generate_iron_condor_trade


def example_usage():
    """Example of how to use the Iron Condor strategy"""
    
    # Example market state (you would get this from your market data)
    market_state = {
        'iv_percentile': 70.0,      # IV percentile between 55-85
        'days_to_expiry': 4,         # Days to expiry between 3-6
        'adx_14': 18.0,              # ADX < 22
        'has_major_event': False,    # No major events
        'instrument': 'NIFTY',       # Must be NIFTY
        'instrument_type': 'WEEKLY', # Must be WEEKLY
        'spot_price': 20000.0,       # Current spot price
        'expiry': '2024-01-15'       # Expiry date
    }
    
    # Example option chain (you would get this from your API/data collector)
    # This is a simplified example - real chains would have more strikes
    option_chain_data = []
    
    # Create call options
    for i in range(30):
        strike = 19800 + (i * 50)
        delta = 0.60 - (i * 0.02) if strike <= 20000 else 0.20 - ((strike - 20000) / 50 * 0.02)
        option_chain_data.append({
            'strike': strike,
            'option_type': 'CE',
            'ltp': max(5, 150 - abs(strike - 20000) * 0.5),
            'bid': max(4, 149 - abs(strike - 20000) * 0.5),
            'ask': max(6, 151 - abs(strike - 20000) * 0.5),
            'delta': delta,
            'oi': 1000 + abs(strike - 20000),
            'volume': 500 + abs(strike - 20000) // 2
        })
    
    # Create put options
    for i in range(30):
        strike = 20200 - (i * 50)
        delta = -0.60 + (i * 0.02) if strike >= 20000 else -0.20 + ((20000 - strike) / 50 * 0.02)
        option_chain_data.append({
            'strike': strike,
            'option_type': 'PE',
            'ltp': max(5, 150 - abs(strike - 20000) * 0.5),
            'bid': max(4, 149 - abs(strike - 20000) * 0.5),
            'ask': max(6, 151 - abs(strike - 20000) * 0.5),
            'delta': delta,
            'oi': 1000 + abs(strike - 20000),
            'volume': 500 + abs(strike - 20000) // 2
        })
    
    option_chain = pd.DataFrame(option_chain_data)
    
    # Generate trade proposal
    trade_proposal = generate_iron_condor_trade(market_state, option_chain)
    
    if trade_proposal:
        print("✅ Iron Condor trade proposal generated!")
        print(f"\nStrategy: {trade_proposal['strategy']}")
        print(f"Expiry: {trade_proposal['expiry']}")
        print(f"Lots: {trade_proposal['lots']}")
        print(f"\nLegs:")
        for leg in trade_proposal['legs']:
            print(f"  {leg['position']} {leg['option_type']} @ {leg['strike']} (Price: ₹{leg['price']:.2f})")
        print(f"\nNet Credit: ₹{trade_proposal['net_credit']:.2f} per lot")
        print(f"Total Credit: ₹{trade_proposal['net_credit_total']:.2f}")
        print(f"Max Loss: ₹{trade_proposal['max_loss']:.2f}")
        print(f"Max Profit: ₹{trade_proposal['max_profit']:.2f}")
        print(f"Reward-to-Risk: {trade_proposal['reward_to_risk']:.2f}")
    else:
        print("❌ No trade proposal generated (market conditions not suitable)")


if __name__ == '__main__':
    example_usage()

