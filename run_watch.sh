#!/bin/bash
# Watch loop: run main.py, restart on failure, until 2:40 PM IST

END_TIME="14:40"
LOG_FILE="logs/watchloop_$(date +%Y%m%d).log"

echo "=== Watch loop started at $(date) ===" | tee -a "$LOG_FILE"
echo "Running until $END_TIME. Log: $LOG_FILE"

RUN_COUNT=0

while true; do
    CURRENT_TIME=$(date +%H:%M)
    if [[ "$CURRENT_TIME" > "$END_TIME" || "$CURRENT_TIME" == "$END_TIME" ]]; then
        echo "=== Reached $END_TIME. Stopping watch loop. ===" | tee -a "$LOG_FILE"
        break
    fi

    RUN_COUNT=$((RUN_COUNT + 1))
    echo "[$(date '+%H:%M:%S')] Launch #${RUN_COUNT}: python main.py" | tee -a "$LOG_FILE"

    python main.py 2>&1 | tee -a "$LOG_FILE"
    EXIT_CODE=${PIPESTATUS[0]}

    if [ $EXIT_CODE -eq 0 ]; then
        echo "[$(date '+%H:%M:%S')] Process exited cleanly (code 0)." | tee -a "$LOG_FILE"
    else
        echo "[$(date '+%H:%M:%S')] ERROR: Process exited with code $EXIT_CODE. Restarting in 10s..." | tee -a "$LOG_FILE"
        sleep 10
    fi
done

echo "=== Watch loop ended at $(date) ===" | tee -a "$LOG_FILE"
