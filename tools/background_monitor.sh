#!/bin/bash
# Background monitor that logs to a file
LOG_FILE="logs/ic_system_$(date +%Y%m%d).log"
SNAPSHOT="data/pnl_snapshot.json"
OUTPUT_FILE="data/monitor_live.log"

while true; do
    echo "========== $(date '+%Y-%m-%d %H:%M:%S') ==========" > "$OUTPUT_FILE"

    # Process status
    PID=$(pgrep -f "python.*main.py" | head -1)
    if [ -n "$PID" ]; then
        echo "✓ Process RUNNING (PID: $PID)" >> "$OUTPUT_FILE"
        PS_OUT=$(ps -p $PID -o %cpu,%mem,etime 2>/dev/null | tail -1)
        echo "  CPU/MEM: $PS_OUT" >> "$OUTPUT_FILE"
    else
        echo "✗ Process STOPPED" >> "$OUTPUT_FILE"
    fi

    # P&L Summary
    if [ -f "$SNAPSHOT" ]; then
        echo "" >> "$OUTPUT_FILE"
        echo "P&L SNAPSHOT:" >> "$OUTPUT_FILE"
        python3 -c "
import json
with open('$SNAPSHOT') as f:
    s = json.load(f)
print(f'  Total: ₹{s.get(\"total_pnl\",0):.2f} | Realised: ₹{s.get(\"realised_pnl\",0):.2f} | Unrealised: ₹{s.get(\"unrealised_pnl\",0):.2f}', file=__import__('sys').stdout)
print(f'  Trades: {s.get(\"total_trades\",0)} | Win Rate: {s.get(\"win_rate_pct\",0):.1f}%', file=__import__('sys').stdout)
daily = s.get('daily', {})
if daily:
    print(f'  Daily P&L: ₹{daily.get(\"realised_pnl\",0):.2f}', file=__import__('sys').stdout)
" >> "$OUTPUT_FILE" 2>&1
    fi

    # Recent issues
    if [ -f "$LOG_FILE" ]; then
        echo "" >> "$OUTPUT_FILE"
        echo "RECENT ISSUES (last 5):" >> "$OUTPUT_FILE"
        grep -E "(REJECT|WARNING.*refuse|untradable)" "$LOG_FILE" 2>/dev/null | tail -5 >> "$OUTPUT_FILE"
    fi

    # Last 3 log lines
    echo "" >> "$OUTPUT_FILE"
    echo "LAST LOG ENTRIES:" >> "$OUTPUT_FILE"
    if [ -f "$LOG_FILE" ]; then
        tail -3 "$LOG_FILE" >> "$OUTPUT_FILE" 2>/dev/null
    fi

    sleep 30
done
