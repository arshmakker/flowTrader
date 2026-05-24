 T-15 min before 09:15 IST

  Step 1 — start regimetrader FIRST (it owns the OAuth refresh)
  cd /Users/arshdeep/git/regimetrader
  python main.py
  Let it complete OAuth login and write the fresh Access_token to cred.yml. Watch for:
  OAuth login successful
  Then kill it (Ctrl-C) — you just needed it to refresh the token. Takes ~30 sec.

  Step 2 — start the proxy
  cd /Users/arshdeep/git/flowTrader
  python broker_proxy.py --cred-file ../regimetrader/cred.yml
  Watch for:
  Proxy ready — session valid uid=FA50394
  If you see Access_token stale — go back to Step 1.

  Step 3 — start regimetrader via proxy
  cd /Users/arshdeep/git/regimetrader
  BROKER_PROXY_URL=http://127.0.0.1:7890 python main.py
  Watch for:
  Using broker proxy at http://127.0.0.1:7890

  Step 4 — start flowTrader via proxy
  cd /Users/arshdeep/git/flowTrader
  BROKER_PROXY_URL=http://127.0.0.1:7890 python main.py
  Watch for:
  Using broker proxy at http://127.0.0.1:7890
  Then verify PCR flowing (no more get_option_chain returned non-Ok).

  ---
  What to watch for

  ┌───────────────────────────────────────────────┬──────────────────────────────────────────────┬──────────────────────────────────────────────────────────────────────────────────┐
  │                   Log line                    │                   Meaning                    │                                      Action                                      │
  ├───────────────────────────────────────────────┼──────────────────────────────────────────────┼──────────────────────────────────────────────────────────────────────────────────┤
  │ Proxy ready — session valid                   │ Proxy up, token good                         │ Proceed                                                                          │
  ├───────────────────────────────────────────────┼──────────────────────────────────────────────┼──────────────────────────────────────────────────────────────────────────────────┤
  │ Access_token stale                            │ Token expired overnight                      │ Re-run Step 1                                                                    │
  ├───────────────────────────────────────────────┼──────────────────────────────────────────────┼──────────────────────────────────────────────────────────────────────────────────┤
  │ get_option_chain jKey attempt rejected: ...   │ jKey auth; log will show actual broker error │ Paste error here                                                                 │
  ├───────────────────────────────────────────────┼──────────────────────────────────────────────┼──────────────────────────────────────────────────────────────────────────────────┤
  │ get_option_chain Bearer attempt rejected: ... │ Bearer also failing                          │ Paste error                                                                      │
  ├───────────────────────────────────────────────┼──────────────────────────────────────────────┼──────────────────────────────────────────────────────────────────────────────────┤
  │ PCR for NIFTY...                              │ PCR working                                  │ All good                                                                         │
  ├───────────────────────────────────────────────┼──────────────────────────────────────────────┼──────────────────────────────────────────────────────────────────────────────────┤
  │ Broker proxy session expired (mid-session)    │ Proxy token expired intraday                 │ Restart proxy (Step 2 only — other systems keep running if token refreshes fast) │
  └───────────────────────────────────────────────┴──────────────────────────────────────────────┴──────────────────────────────────────────────────────────────────────────────────┘

  ---
  regimetrader open positions

  NIFTY + BANKNIFTY ICs from today (trading_date: 2026-
