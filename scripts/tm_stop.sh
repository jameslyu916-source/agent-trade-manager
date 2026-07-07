#!/bin/bash
# TradeManager 優雅關閉腳本（供 launchd 排程調用）
# 確保所有服務正常關閉，Chrome 有足夠時間退出

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PID_FILE="$PROJECT_DIR/scripts/.trademanager.pid"
LOG_FILE="$PROJECT_DIR/logs/launchd.log"

echo "[$(date '+%Y-%m-%d %H:%M:%S')] 正在關閉 TradeManager..." >> "$LOG_FILE"

# 從 PID 檔案找到 start.py 主進程
MAIN_PID=""
if [ -f "$PID_FILE" ]; then
    PID=$(cat "$PID_FILE")
    if ps -p "$PID" > /dev/null 2>&1; then
        MAIN_PID="$PID"
    fi
fi

# fallback: 用 pgrep 搜尋
if [ -z "$MAIN_PID" ]; then
    MAIN_PID=$(pgrep -f "python.*start\.py" 2>/dev/null | head -1)
fi

if [ -z "$MAIN_PID" ]; then
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] start.py 未運行" >> "$LOG_FILE"
else
    # 發送 SIGINT（觸發 gracefulShutdown）
    kill -2 "$MAIN_PID" 2>/dev/null

    # 等待最多 30 秒讓服務正常退出
    for i in $(seq 1 30); do
        if ! ps -p "$MAIN_PID" > /dev/null 2>&1; then
            echo "[$(date '+%Y-%m-%d %H:%M:%S')] start.py 已優雅退出" >> "$LOG_FILE"
            break
        fi
        sleep 1
    done

    # 仍未退出則強制終止
    if ps -p "$MAIN_PID" > /dev/null 2>&1; then
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] 強制終止 start.py" >> "$LOG_FILE"
        kill -9 "$MAIN_PID" 2>/dev/null
    fi
fi

# 等待 Chrome 完全退出（確保 session 資料完整寫入）
for i in $(seq 1 10); do
    sleep 1
    CHROME_COUNT=$(pgrep -f "Google Chrome for Testing" 2>/dev/null | wc -l | tr -d ' ')
    if [ "$CHROME_COUNT" -eq 0 ]; then
        break
    fi
done

# 殘留清理
pkill -f "Google Chrome for Testing" 2>/dev/null
pkill -f "uvicorn backend.main" 2>/dev/null
pkill -f "node.*wa_bot" 2>/dev/null

# 清除 PID 檔案
rm -f "$PID_FILE"

echo "[$(date '+%Y-%m-%d %H:%M:%S')] TradeManager 已關閉" >> "$LOG_FILE"
