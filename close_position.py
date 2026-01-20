#!/usr/bin/env python3
"""
Script to manually close an active futures position
Usage: python3 close_position.py <exit_price> [exit_reason]
"""

import json
import sys
from datetime import datetime
from strategies.iron_condor.position_tracker import IronCondorPositionTracker

def close_futures_position(exit_price=None, exit_reason="MANUAL_EXIT"):
    """Close the active futures position"""
    try:
        # Load position tracker
        position_tracker = IronCondorPositionTracker()
        
        # Get active positions
        active_positions = position_tracker.get_active_positions()
        
        # Find futures position
        futures_position = None
        for pos in active_positions:
            if pos.get('strategy', '').upper() == 'TREND_FOLLOW_FUTURE':
                futures_position = pos
                break
        
        if not futures_position:
            print("❌ No active futures position found")
            return False
        
        print(f"📊 Found position: {futures_position['trade_id']}")
        print(f"   Entry: ₹{futures_position['entry_price']:.2f}")
        print(f"   Direction: {futures_position['direction']}")
        print(f"   Quantity: {futures_position['quantity']}")
        print(f"   Symbol: {futures_position.get('symbol', 'N/A')}")
        
        # Get exit price
        if exit_price is None:
            # Try to get current price from API
            print("\n🔄 Attempting to get current price from API...")
            try:
                from api_helper import ShoonyaApiPy
                from symbol_manager import SymbolManager
                from strategies.trend.trend_follow_futures import get_nifty_futures_price
                import yaml
                
                # Load credentials
                with open('cred.yml', 'r') as f:
                    cred = yaml.safe_load(f)
                
                api = ShoonyaApiPy()
                factor2 = input("Enter 2FA code for API login: ").strip()
                
                login_status = api.login(
                    userid=cred['user'],
                    password=cred['pwd'],
                    twoFA=factor2,
                    vendor_code=cred['vc'],
                    api_secret=cred['apikey'],
                    imei=cred['imei']
                )
                
                if login_status:
                    symbol_manager = SymbolManager(api)
                    symbol_manager.load_symbol_files()
                    current_price = get_nifty_futures_price(api, symbol_manager)
                    if current_price:
                        exit_price = current_price
                        print(f"✅ Got current price from API: ₹{exit_price:.2f}")
                    else:
                        print("⚠️  Could not get current price from API")
                        exit_price = float(input("Enter exit price: ₹"))
                else:
                    print("⚠️  API login failed")
                    exit_price = float(input("Enter exit price: ₹"))
            except Exception as e:
                print(f"⚠️  Error getting price from API: {e}")
                exit_price = float(input("Enter exit price: ₹"))
        else:
            exit_price = float(exit_price)
        
        # Calculate P&L
        entry_price = futures_position['entry_price']
        quantity = futures_position['quantity']
        direction = futures_position['direction']
        
        if direction == 'LONG':
            pnl = (exit_price - entry_price) * quantity
        else:  # SHORT
            pnl = (entry_price - exit_price) * quantity
        
        print(f"\n💰 P&L Calculation:")
        print(f"   Entry Price: ₹{entry_price:.2f}")
        print(f"   Exit Price: ₹{exit_price:.2f}")
        print(f"   Quantity: {quantity}")
        print(f"   Direction: {direction}")
        print(f"   Final P&L: ₹{pnl:.2f}")
        
        # Close position
        position_tracker.close_position(
            futures_position,
            exit_reason,
            pnl
        )
        
        print(f"\n✅ Position closed successfully!")
        print(f"   Exit Reason: {exit_reason}")
        print(f"   Final P&L: ₹{pnl:.2f}")
        print(f"   Exit Time: {datetime.now().isoformat()}")
        
        return True
        
    except Exception as e:
        import traceback
        print(f"❌ Error closing position: {str(e)}")
        traceback.print_exc()
        return False

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python3 close_position.py <exit_price> [exit_reason]")
        print("Example: python3 close_position.py 25500 MANUAL_EXIT")
        sys.exit(1)
    
    exit_price = sys.argv[1]
    exit_reason = sys.argv[2] if len(sys.argv) > 2 else "MANUAL_EXIT"
    
    close_futures_position(exit_price, exit_reason)
