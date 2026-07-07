#!/bin/bash
# TradeManager 手動關閉（雙擊優雅停止所有服務）

echo "正在關閉 TradeManager..."

# 找到 start.py 主進程（支援 python3 / python3.11）
MAIN_PID=$(pgrep -f "python.*start\.py" 2>/dev/null | head -1)

if [ -z "$MAIN_PID" ]; then
    echo "start.py 未在運行"
    exit 0
fi

kill -2 "$MAIN_PID" 2>/dev/null
echo "已發送關閉信號，等待服務退出..."

for i in $(seq 1 30); do
    if ! ps -p "$MAIN_PID" > /dev/null 2>&1; then
        echo "TradeManager 已關閉"
        exit 0
    fi
    sleep 1
done

echo "強制終止中..."
kill -9 "$MAIN_PID" 2>/dev/null
echo "已關閉"
