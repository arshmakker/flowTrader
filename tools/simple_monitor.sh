#!/bin/bash
# Simple monitor - no clearing, just append to file
LOG_FILE="logs/ic_system_$(date +%Y%m%d).log"
SNAPSHOT="data/pnl_snapshot.json"

while true; do
    echo "========== $(date '+%Y-%m-%d %H:%M:%S') =========="

    # Process status
    PID=$(pgrep -f "python.*main.py" | head -1)
    if [ -n "$PID" ]; then
        echo "✓ Process RUNNING (PID: $PID)"
        PS_OUT=$(ps -p $PID -o %cpu,%mem,etime | tail -1)
        echo "  CPU/MEM: $PS_OUT"
    else
        echo "✗ Process STOPPED"
    fi

    # P&L Summary
    if [ -f "$SNAPSHOT" ]; then
        echo ""
        echo "P&L SNAPSHOT:"
        python3 -c "
import json
with open('$SNAPSHOT') as f:
    s = json.load(f)
print(f\"  Total: ₹{s.get('total_pnl', 0):.2f} | Realised: ₹{s.get('realised_pnl', 0):.2f} | Unrealised: ₹{s.get('unrealised_pnl', 0):.2f}\")
print(f\"  Trades: {s.get('total_trades', 0)} | Win Rate: {s.get('win_rate_pct', 0):.1f}%\")
daily = s.get('daily', {})
if daily:
    print(f\"  Daily P&L: ₹{daily.get('realised_pnl', 0):.2f}\")
"
    fi

    # Recent issues (last 5 rejections)
    if [ -f "$LOG_FILE" ]; then
        echo ""
        echo "RECENT ISSUES (last 5):"
        grep -E "(REJECT|WARNING.*refuse|untradable)" "$LOG_FILE" | tail -5 | sed 's/^/  /'
    fi

    # Last 3 log lines
    echo ""
    echo "LAST LOG ENTRIES:"
    if [ -f "$LOG_FILE" ]; then
        tail -3 "$LOG_FILE" | sed 's/^/  /'
    fi

    echo ""
    echo "========================================"
    echo "Next update in 30s... (Ctrl+C to stop)"
    echo ""
    sleep 30
done
