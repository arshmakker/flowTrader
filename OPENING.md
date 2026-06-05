# PCR Trader — Daily Startup

## Single Command

```bash
~/git/shoonya-auth/start.sh
```

This launches a tmux session with 3 windows:
- **Window 0 (proxy):** Broker session (auto-login if token is stale)
- **Window 1 (regime):** RegimeTrader logs
- **Window 2 (flow):** PCR Trader logs

## tmux Navigation

- `Ctrl-b 0` — proxy window (broker logs)
- `Ctrl-b 1` — regime window (regimetrader logs)
- `Ctrl-b 2` — flow window (PCR trader logs)
- `Ctrl-b d` — detach (all processes keep running in background)
- `Ctrl-b :kill-session -t trading` — kill entire session and all processes

## Mid-Session Token Expiry Recovery

If either bot logs `broker proxy health check failed`:

1. Press `Ctrl-b 0` to switch to proxy window
2. Press `Ctrl-c` to stop the proxy
3. `python ~/git/shoonya-auth/broker_proxy.py` — this auto re-logins, then restarts
4. Both bots reconnect automatically on next health check cycle (usually within 30s)

## What to Watch For

| Log line | Meaning | Action |
|----------|---------|--------|
| `✅ Proxy ready` | Proxy up and OAuth token valid | Proceed; regimetrader + PCR Trader will auto-start |
| `PCR for NIFTY` | PCR signal flowing live | Normal operation |
| `broker proxy health check failed` | Token expired mid-session | Ctrl-b 0 → Ctrl-c → restart proxy manually (see recovery above) |
| `MTM loss >2x credit` | Position hit stop loss | Automatic exit; monitor realized loss |
| `FORCE_EXIT` | Forced close (expiry-day 14:45 or EOD 15:10) | Session ending or hard stop reached |

---

## Architecture

- **shoonya-auth/** — Centralized OAuth + broker proxy (lives in `~/git/shoonya-auth/`)
- **regimetrader/** — Iron Condor strategy (optional; can run solo)
- **flowTrader/** — PCR Contrarian Credit Spread strategy (this repo)

Both strategies read credentials from `~/.shoonya/cred.yml` (shared, never committed).
