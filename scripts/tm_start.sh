#!/bin/bash
# TradeManager 背景啟動腳本（供 launchd 排程調用）
# 若已在運行則跳過，否則在背景啟動所有服務

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PID_FILE="$PROJECT_DIR/scripts/.trademanager.pid"

# 檢查是否已在運行
if [ -f "$PID_FILE" ]; then
    PID=$(cat "$PID_FILE")
    if ps -p "$PID" > /dev/null 2>&1; then
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] TradeManager 已在運行 (PID: $PID)，跳過啟動"
        exit 0
    fi
fi

# 啟動服務（背景執行，輸出寫入日誌）
cd "$PROJECT_DIR"
nohup /opt/homebrew/bin/python3.11 start.py >> logs/launchd.log 2>&1 &
echo $! > "$PID_FILE"
echo "[$(date '+%Y-%m-%d %H:%M:%S')] TradeManager 已啟動 (PID: $!)"
