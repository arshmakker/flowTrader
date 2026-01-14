# NSE IV Fetcher

This module attempts to fetch Implied Volatility (IV) data directly from NSE's website, which is faster and more accurate than calculating IV from option prices.

## Status

⚠️ **Currently Limited by NSE Anti-Bot Protection**

NSE has implemented strong anti-bot protection that may block automated requests. The system is designed to gracefully fall back to calculating IV from option prices if NSE fetch fails.

## How It Works

1. **Primary Method**: Fetches IV directly from NSE's option chain API
   - Endpoint: `https://www.nseindia.com/api/option-chain-indices`
   - Faster and more accurate than calculation
   - May be blocked by NSE's anti-bot protection

2. **Fallback Method**: Calculates IV from option prices using Black-Scholes
   - Used when NSE fetch fails
   - Slower but always available
   - Already implemented and working

## Integration

The system automatically tries NSE IV first, then falls back to calculation:

```python
from technical_indicators import calculate_atm_iv

# Automatically tries NSE IV, falls back to calculation
iv = calculate_atm_iv(
    option_chain_df, 
    spot_price, 
    days_to_expiry,
    expiry_date_str='25-JAN-2024',  # Optional, for NSE fetch
    use_nse_iv=True  # Set to False to skip NSE and use calculation only
)
```

## Troubleshooting

If NSE IV fetch consistently fails:

1. **Check Network**: Ensure you can access `https://www.nseindia.com` from your network
2. **Check Logs**: Look for "NSE IV fetch failed" messages in logs
3. **Fallback**: The system will automatically use calculated IV
4. **Disable NSE**: Set `use_nse_iv=False` to skip NSE attempts entirely

## Alternative Solutions

If NSE API access is consistently blocked, consider:

1. **Use Calculated IV**: Already implemented and working (slower but reliable)
2. **Proxy/VPN**: May help bypass some restrictions (use responsibly)
3. **Browser Automation**: Use Selenium/Playwright for more realistic requests (more complex)
4. **Third-party APIs**: Some data providers offer NSE IV data (may require subscription)

## Current Implementation

The system is configured to:
- ✅ Try NSE IV first (fastest)
- ✅ Automatically fall back to calculated IV (reliable)
- ✅ Log which method was used
- ✅ Continue working even if NSE is unavailable

## Testing

Run the test script to check NSE connectivity:

```bash
python3 test_nse_iv.py
```

If it fails, the system will still work using calculated IV.

