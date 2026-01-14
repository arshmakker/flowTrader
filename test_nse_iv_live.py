#!/usr/bin/env python3
"""
Test NSE IV fetching when market is closed
"""

import logging
from nse_iv_fetcher import get_nse_iv, fetch_nse_option_chain, extract_iv_from_nse_data
import json

# Setup logging
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

print("=" * 70)
print("Testing NSE IV Fetch (Market Closed)")
print("=" * 70)
print()

# Test 1: Fetch option chain
print("Test 1: Fetching NSE option chain...")
print("-" * 70)
nse_data = fetch_nse_option_chain('NIFTY', retry_count=3)

if nse_data:
    print("✅ Successfully fetched NSE option chain")
    print()
    
    # Show structure
    records = nse_data.get('records', {})
    expiry_dates = records.get('expiryDates', [])
    spot_price = records.get('underlyingValue', 0)
    data_count = len(records.get('data', []))
    
    print(f"   Spot Price: {spot_price}")
    print(f"   Available Expiries: {len(expiry_dates)}")
    if expiry_dates:
        print(f"   Nearest Expiry: {expiry_dates[0]}")
        print(f"   All Expiries: {expiry_dates[:5]}...")  # Show first 5
    print(f"   Total Option Records: {data_count}")
    print()
    
    # Test 2: Extract IV
    print("Test 2: Extracting ATM IV from NSE data...")
    print("-" * 70)
    iv = extract_iv_from_nse_data(nse_data)
    if iv:
        print(f"✅ Successfully extracted ATM IV: {iv:.2f}%")
    else:
        print("❌ Failed to extract IV from NSE data")
        print("   This might be because:")
        print("   - Market is closed (no live IV data)")
        print("   - Data structure is different")
        print("   - IV field is missing/empty")
    print()
    
    # Test 3: Check data structure
    print("Test 3: Inspecting data structure...")
    print("-" * 70)
    if records.get('data'):
        sample = records['data'][0]
        print("Sample option record structure:")
        print(f"   Keys: {list(sample.keys())}")
        
        # Check if CE/PE have IV
        if 'CE' in sample:
            ce_data = sample['CE']
            print(f"   CE keys: {list(ce_data.keys())}")
            if 'impliedVolatility' in ce_data:
                print(f"   CE IV value: {ce_data.get('impliedVolatility')}")
            else:
                print("   ⚠️  'impliedVolatility' not found in CE data")
        
        if 'PE' in sample:
            pe_data = sample['PE']
            print(f"   PE keys: {list(pe_data.keys())}")
            if 'impliedVolatility' in pe_data:
                print(f"   PE IV value: {pe_data.get('impliedVolatility')}")
            else:
                print("   ⚠️  'impliedVolatility' not found in PE data")
    print()
    
    # Test 4: Direct IV fetch
    print("Test 4: Direct IV fetch (convenience function)...")
    print("-" * 70)
    iv_direct = get_nse_iv('NIFTY')
    if iv_direct:
        print(f"✅ Successfully fetched IV directly: {iv_direct:.2f}%")
    else:
        print("❌ Failed to fetch IV directly")
    print()
    
else:
    print("❌ Failed to fetch NSE option chain")
    print()
    print("Possible reasons:")
    print("  1. NSE anti-bot protection is blocking requests")
    print("  2. Network connectivity issues")
    print("  3. NSE API is down or changed")
    print("  4. Market is closed and API returns empty/invalid data")
    print()

print("=" * 70)
print("Test Complete")
print("=" * 70)

