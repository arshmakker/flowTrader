#!/usr/bin/env python3
"""
Test script for NSE IV fetcher
"""

import logging
from nse_iv_fetcher import get_nse_iv, fetch_nse_option_chain, extract_iv_from_nse_data

# Setup logging
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

print("=" * 70)
print("Testing NSE IV Fetcher")
print("=" * 70)
print()

# Test 1: Fetch option chain
print("Test 1: Fetching NSE option chain...")
nse_data = fetch_nse_option_chain('NIFTY')
if nse_data:
    print("✅ Successfully fetched NSE option chain")
    print(f"   Expiry dates available: {len(nse_data.get('records', {}).get('expiryDates', []))}")
    if nse_data.get('records', {}).get('expiryDates'):
        print(f"   Nearest expiry: {nse_data['records']['expiryDates'][0]}")
else:
    print("❌ Failed to fetch NSE option chain")
    exit(1)

print()

# Test 2: Extract IV
print("Test 2: Extracting ATM IV from NSE data...")
iv = extract_iv_from_nse_data(nse_data)
if iv:
    print(f"✅ Successfully extracted ATM IV: {iv:.2f}%")
else:
    print("❌ Failed to extract IV from NSE data")

print()

# Test 3: Direct IV fetch
print("Test 3: Direct IV fetch (convenience function)...")
iv_direct = get_nse_iv('NIFTY')
if iv_direct:
    print(f"✅ Successfully fetched IV directly: {iv_direct:.2f}%")
else:
    print("❌ Failed to fetch IV directly")

print()
print("=" * 70)
print("Test Complete")
print("=" * 70)

