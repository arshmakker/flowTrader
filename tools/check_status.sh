#!/bin/bash
# Quick status check - run this anytime to see current state
LOG_FILE="logs/ic_system_$(date +%Y%m%d).log"
SNAPSHOT="data/pnl_snapshot.json"

echo "========== $(date '+%Y-%m-%d %H:%M:%S') =========="
echo ""

# Process status
PID=$(pgrep -f "python.*main.py" | head -1)
if [ -n "$PID" ]; then
    echo "✓ RUNNING (PID: $PID)"
    ps -p $PID -o %cpu,%mem,etime 2>/dev/null | tail -1
else
    echo "✗ STOPPED"
fi

echo ""
echo "P&L:"
if [ -f "$SNAPSHOT" ]; then
    python3 -c "import json; s=json.load(open('$SNAPSHOT')); print(f'  Total: ₹{s.get(\"total_pnl\",0):.2f} | Realised: ₹{s.get(\"realised_pnl\",0):.2f} | Trades: {s.get(\"total_trades\",0)}')"
fi

echo ""
echo "Last 5 issues:"
if [ -f "$LOG_FILE" ]; then
    grep -E "(REJECT|WARNING.*refuse|untradable)" "$LOG_FILE" 2>/dev/null | tail -5 | sed 's/^/  /'
fi

echo ""
echo "Last 3 log lines:"
if [ -f "$LOG_FILE" ]; then
    tail -3 "$LOG_FILE" 2>/dev/null | sed 's/^/  /'
fi
