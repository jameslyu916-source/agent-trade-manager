#!/bin/bash
# TradeManager 監督腳本 — launchd 每 10 分鐘觸發一次
# 確保 bot 在 10:00-19:00 之間運行，19:00 後關閉
# 電腦從睡眠喚醒後 launchd 會立即觸發（間隔已過），自動補啟動

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PID_FILE="$PROJECT_DIR/scripts/.trademanager.pid"
LOG_FILE="$PROJECT_DIR/logs/launchd.log"
CURRENT_HOUR=$(date +%H | sed 's/^0//')
DAY_OF_WEEK=$(date +%u)  # 1=Mon ... 5=Fri, 6=Sat, 7=Sun

# launchd 環境精簡，確保能找到 python3.11
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"

# ── 查找運行中的 start.py ──
find_running_pid() {
    if [ -f "$PID_FILE" ]; then
        local pid=$(cat "$PID_FILE")
        if ps -p "$pid" > /dev/null 2>&1; then
            echo "$pid"
            return
        fi
    fi
    pgrep -f "python.*start\.py" 2>/dev/null | head -1
}

RUNNING=$(find_running_pid)

# ── 暫停機制：若暫停檔存在且未過期（30 分鐘），跳過啟動 ──
PAUSE_FILE="$PROJECT_DIR/scripts/.trademanager.pause"
if [ -f "$PAUSE_FILE" ]; then
    PAUSE_TIME=$(cat "$PAUSE_FILE" 2>/dev/null)
    NOW=$(date +%s)
    if [ -n "$PAUSE_TIME" ] && [ $((NOW - PAUSE_TIME)) -lt 1800 ]; then
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] 暫停模式（剩餘 $((30 - (NOW - PAUSE_TIME) / 60)) 分鐘），跳過" >> "$LOG_FILE"
        exit 0
    else
        rm -f "$PAUSE_FILE"
    fi
fi

# ── 決策邏輯 ──
if [ "$CURRENT_HOUR" -ge 10 ] && [ "$CURRENT_HOUR" -lt 19 ] && [ "$DAY_OF_WEEK" -le 5 ]; then
    # 運行窗口內（10:00-18:59）
    if [ -z "$RUNNING" ]; then
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] 運行窗口內且未啟動 → 正在啟動" >> "$LOG_FILE"
        # 先清理可能殘留的 Chrome 孤兒程序
        pkill -f "Google Chrome for Testing" 2>/dev/null
        sleep 2
        cd "$PROJECT_DIR"
        nohup /opt/homebrew/bin/python3.11 start.py >> "$LOG_FILE" 2>&1 &
        echo $! > "$PID_FILE"
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] TradeManager 已啟動 (PID: $!)" >> "$LOG_FILE"
    fi
else
    # 運行窗口外（19:00-09:59）
    if [ -n "$RUNNING" ]; then
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] 運行窗口外但仍運行中 → 正在關閉" >> "$LOG_FILE"
        kill -2 "$RUNNING" 2>/dev/null
        for i in $(seq 1 30); do
            if ! ps -p "$RUNNING" > /dev/null 2>&1; then break; fi
            sleep 1
        done
        if ps -p "$RUNNING" > /dev/null 2>&1; then
            kill -9 "$RUNNING" 2>/dev/null
        fi
        pkill -f "Google Chrome for Testing" 2>/dev/null
        pkill -f "uvicorn backend.main" 2>/dev/null
        pkill -f "node.*wa_bot" 2>/dev/null
        rm -f "$PID_FILE"
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] TradeManager 已關閉" >> "$LOG_FILE"
    fi
fi
