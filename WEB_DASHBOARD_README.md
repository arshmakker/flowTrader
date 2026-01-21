# Web Dashboard - Quick Start

A simple web dashboard to monitor your trading system from your mobile device.

## Quick Start

### 1. Install Flask (if not already installed)

```bash
pip install flask
```

Or add to requirements.txt (already added):
```bash
pip install -r requirements.txt
```

### 2. Run the Dashboard

```bash
python web_dashboard.py
```

You'll see:
```
==================================================
Trading System Web Dashboard
==================================================
Starting server...
Access from your mobile:
  http://<your-computer-ip>:5000
  or
  http://localhost:5000 (on same device)
==================================================
```

### 3. Access from Mobile

**On the same WiFi network:**
1. Find your computer's IP address:
   - Mac: `ifconfig | grep "inet " | grep -v 127.0.0.1`
   - Windows: `ipconfig` (look for IPv4 Address)
   - Linux: `hostname -I`

2. On your mobile browser, go to:
   ```
   http://YOUR_COMPUTER_IP:5000
   ```
   Example: `http://192.168.1.100:5000`

**On the same device:**
- Just go to: `http://localhost:5000`

## Features

- ✅ View active positions
- ✅ See position details (entry price, quantity, stop loss)
- ✅ View recent logs
- ✅ Auto-refresh every 30 seconds
- ✅ Mobile-friendly design
- ✅ Simple and lightweight

## What It Shows

1. **Active Positions**
   - Strategy name
   - Direction (LONG/SHORT)
   - Entry price
   - Quantity
   - Stop loss
   - Entry time

2. **Recent Logs**
   - Last 20 log entries
   - Auto-updates

3. **System Status**
   - Last update time
   - Active indicator

## Troubleshooting

**Can't access from mobile?**
- Make sure both devices are on the same WiFi network
- Check your computer's firewall (may need to allow port 5000)
- Verify the IP address is correct

**Dashboard shows "No open positions"?**
- Make sure `active_positions.json` exists
- Check that positions have `"status": "OPEN"`

**Logs not showing?**
- Make sure `logs/` directory exists
- Check that today's log file exists: `logs/trading_system_YYYYMMDD.log`

## Next Steps

Once you have a working dashboard, you can:
1. Decide where to host it (Raspberry Pi, VPS, etc.)
2. Add more features (P&L calculation, charts, etc.)
3. Add authentication if needed
4. Deploy to a cloud service

For now, just run it locally and test it out!
