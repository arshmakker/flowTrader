#!/usr/bin/env python3
"""
View trade proposals in a readable format
"""

import json
import glob
from datetime import datetime

def format_trade(trade_data):
    """Format a trade proposal for display"""
    print("=" * 80)
    print(f"Trade Generated: {trade_data['generated_at']}")
    print(f"Strategy: {trade_data['strategy']}")
    print(f"Expiry: {trade_data['expiry']} ({trade_data.get('days_to_expiry', 'N/A')} days)")
    print(f"Spot Price: ₹{trade_data['spot_price']:.2f}")
    print()
    
    print("LEGS:")
    for leg in trade_data['legs']:
        pos = leg['position']
        opt_type = leg['option_type']
        strike = leg['strike']
        price = leg['price']
        print(f"  {pos:5s} {opt_type:2s} @ {strike:7.0f} = ₹{price:7.2f}")
    print()
    
    print("PAYOFF:")
    print(f"  Net Credit:        ₹{trade_data['net_credit']:.2f} per lot")
    print(f"  Net Credit Total:  ₹{trade_data['net_credit_total']:.2f}")
    print(f"  Max Loss:          ₹{trade_data['max_loss_per_lot']:.2f} per lot")
    print(f"  Max Loss Total:    ₹{trade_data['max_loss']:.2f}")
    print(f"  Max Profit:        ₹{trade_data['max_profit']:.2f}")
    print(f"  Reward-to-Risk:    {trade_data['reward_to_risk']:.2f}")
    print(f"  Probability of Profit: {trade_data.get('probability_of_profit', 'N/A')}%")
    print(f"  Lots:              {trade_data['lots']}")
    print()
    
    print("EXIT RULES:")
    exit_rules = trade_data.get('exit_rules', {})
    print(f"  Profit Target:     {exit_rules.get('profit_target_pct', 'N/A')}")
    print(f"  Stop Loss:         {exit_rules.get('stop_loss_multiplier', 'N/A')}x")
    print(f"  Mandatory Exit:    {exit_rules.get('mandatory_exit_dte', 'N/A')} DTE or {exit_rules.get('mandatory_exit_time', 'N/A')}")
    print()

def main():
    # Find all trade proposal files
    files = sorted(glob.glob('trade_proposals/iron_condor_*.json'))
    
    if not files:
        print("No trade proposals found in trade_proposals/ directory")
        return
    
    print(f"\n📊 Found {len(files)} Trade Proposal(s)\n")
    
    # Show all trades
    for i, filename in enumerate(files, 1):
        try:
            with open(filename, 'r') as f:
                trade_data = json.load(f)
            
            print(f"\n{'='*80}")
            print(f"TRADE #{i} of {len(files)}")
            print(f"{'='*80}")
            format_trade(trade_data)
            
        except Exception as e:
            print(f"Error reading {filename}: {e}")
            continue
    
    print("\n" + "=" * 80)
    print(f"Total: {len(files)} trade proposal(s)")
    print("=" * 80)

if __name__ == "__main__":
    main()
