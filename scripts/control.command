#!/bin/bash
# TradeManager 控制台 — 統一管理啟動/關閉/暫停/恢復/掃碼
# 雙擊即可開啟，取代舊有的多個 .command 檔案

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PID_FILE="$PROJECT_DIR/scripts/.trademanager.pid"
PAUSE_FILE="$PROJECT_DIR/scripts/.trademanager.pause"
LOG_FILE="$PROJECT_DIR/logs/launchd.log"

# ── 顏色 ──
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
CYAN='\033[0;36m'
RED='\033[0;31m'
BOLD='\033[1m'
NC='\033[0m'

# ── 確保能找到 python3.11 / node（.command 的 PATH 很精簡） ──
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"

# ── 查找運行中的 start.py ──
find_running_pid() {
    if [ -f "$PID_FILE" ]; then
        local pid
        pid=$(cat "$PID_FILE")
        if ps -p "$pid" > /dev/null 2>&1; then
            echo "$pid"
            return
        fi
    fi
    pgrep -f "python.*start\.py" 2>/dev/null | head -1
}

# ── 服務是否在運行 ──
is_running() {
    [ -n "$(find_running_pid)" ]
}

# ── 暫停檔是否有效（30 分鐘內） ──
is_paused() {
    if [ -f "$PAUSE_FILE" ]; then
        local pause_time
        pause_time=$(cat "$PAUSE_FILE" 2>/dev/null)
        local now
        now=$(date +%s)
        if [ -n "$pause_time" ] && [ $((now - pause_time)) -lt 1800 ]; then
            return 0
        fi
    fi
    return 1
}

# ── 剩餘暫停分鐘數 ──
pause_remaining() {
    if [ -f "$PAUSE_FILE" ]; then
        local pause_time
        pause_time=$(cat "$PAUSE_FILE" 2>/dev/null)
        local now
        now=$(date +%s)
        local remaining=$(( 30 - (now - pause_time) / 60 ))
        if [ $remaining -gt 0 ]; then
            echo "$remaining"
            return
        fi
    fi
    echo "0"
}

# ── 是否在運行窗口內（10:00-18:59） ──
in_window() {
    local hour
    hour=$(date +%H | sed 's/^0//')
    [ "$hour" -ge 10 ] && [ "$hour" -lt 19 ]
}

# ── 關閉服務（可復用） ──
stop_service() {
    local pid
    pid=$(find_running_pid)

    if [ -z "$pid" ]; then
        echo "  服務未在運行"
        return 0
    fi

    echo "  發送關閉信號 (SIGINT) → PID: $pid ..."
    kill -2 "$pid" 2>/dev/null

    # 等最多 30 秒讓 gracefulShutdown 完成
    local stopped=0
    for i in $(seq 1 30); do
        if ! ps -p "$pid" > /dev/null 2>&1; then
            echo "  ✅ 服務已優雅關閉（耗時 ${i}s）"
            stopped=1
            break
        fi
        sleep 1
    done

    if [ "$stopped" -eq 0 ]; then
        echo "  ⚠️  逾時未退出，強制終止..."
        kill -9 "$pid" 2>/dev/null
        sleep 1
    fi

    # 等待 Chrome 退出（確保 session 完整寫入磁碟）
    echo "  等待 Chrome 退出..."
    for i in $(seq 1 10); do
        if ! pgrep -f "Google Chrome for Testing" > /dev/null 2>&1; then
            echo "  ✅ Chrome 已退出（耗時 ${i}s）"
            break
        fi
        sleep 1
    done

    # 強制清理可能殘留的程序
    pkill -f "Google Chrome for Testing" 2>/dev/null
    pkill -f "uvicorn backend.main" 2>/dev/null
    pkill -f "node.*wa_bot" 2>/dev/null

    rm -f "$PID_FILE"
    echo "  ✅ 清理完成"
}

# ── 顯示選單 ──
show_menu() {
    clear
    echo ""
    echo -e "${CYAN}══════════════════════════════════════════════════════════${NC}"
    echo -e "${CYAN}                    TradeManager 控制台                    ${NC}"
    echo -e "${CYAN}══════════════════════════════════════════════════════════${NC}"
    echo ""
    echo -e "  ${GREEN}${BOLD}1${NC}. 啟動服務       啟動後端、Telegram、WhatsApp Bot，前景顯示即時日誌"
    echo -e "  ${YELLOW}${BOLD}2${NC}. 關閉服務       優雅關閉所有服務，等待 Chrome 正常退出（不影響排程）"
    echo -e "  ${YELLOW}${BOLD}3${NC}. 暫停自動排程    先關閉服務，再暫停 supervisor 自動啟動（改代碼時用）"
    echo -e "  ${GREEN}${BOLD}4${NC}. 恢復自動排程    重新啟用 10:00-19:00 自動排程"
    echo -e "  ${CYAN}${BOLD}5${NC}. 掃碼登入        顯示 WhatsApp QR Code（首次設定或 session 損壞時用）"
    echo -e "  ${RED}${BOLD}6${NC}. 離開           退出控制台"
    echo ""

    # ── 狀態列 ──
    local running_pid
    running_pid=$(find_running_pid)
    if [ -n "$running_pid" ]; then
        echo -ne "  ${BOLD}狀態${NC}: ${GREEN}● 運行中${NC} (PID: $running_pid)"
    else
        echo -ne "  ${BOLD}狀態${NC}: ${RED}○ 未運行${NC}"
    fi

    echo -ne "  |  ${BOLD}排程${NC}: "
    if is_paused; then
        echo -ne "${YELLOW}已暫停（剩 $(pause_remaining) 分鐘）${NC}"
    else
        echo -ne "${GREEN}正常${NC}"
    fi

    echo -ne "  |  ${BOLD}窗口${NC}: "
    if in_window; then
        echo -e "${GREEN}10:00-19:00${NC}"
    else
        echo -e "${RED}非運行時段${NC}"
    fi

    echo -e "${CYAN}──────────────────────────────────────────────────────────${NC}"
    echo -e "  ${BOLD}最近日誌${NC}"

    if [ -f "$LOG_FILE" ]; then
        # 顯示最後 5 行非空日誌
        grep -v '^$' "$LOG_FILE" 2>/dev/null | tail -5 | while IFS= read -r line; do
            printf "  %s\n" "$line"
        done
    else
        echo "  （暫無日誌）"
    fi

    echo -e "${CYAN}══════════════════════════════════════════════════════════${NC}"
    echo ""
}

# ── 全域 Ctrl+C 處理（選單狀態下不退出） ──
trap_menu() {
    echo ""
    echo -e "  ${YELLOW}請選 6 離開控制台，Ctrl+C 不會退出${NC}"
    echo ""
    echo -n "  按 Enter 繼續..."
}

# ── 主循環 ──
while true; do
    # 選單狀態下的 Ctrl+C 不會退出
    trap 'trap_menu; read -r; continue' INT

    show_menu
    echo -n "  請輸入選項 [1-6]: "
    read -r choice

    case "$choice" in
        1)
            # ── 啟動服務 ──
            if is_running; then
                echo ""
                echo -e "  ${YELLOW}⚠️  服務已在運行中 (PID: $(find_running_pid))${NC}"
                echo -n "  按 Enter 返回選單..."
                read -r
                continue
            fi

            echo ""
            echo "  清理殘留 Chrome 程序..."
            pkill -f "Google Chrome for Testing" 2>/dev/null
            sleep 2

            echo "  正在啟動 TradeManager..."
            echo ""

            cd "$PROJECT_DIR" || exit 1

            # 子程序模式：前景顯示日誌，Ctrl+C 可優雅關閉
            /opt/homebrew/bin/python3.11 start.py &
            CHILD_PID=$!

            # Ctrl+C → 轉發 SIGINT 給 start.py → 等 gracefulShutdown → 返回選單
            trap 'kill -2 $CHILD_PID 2>/dev/null; wait $CHILD_PID 2>/dev/null' INT

            wait $CHILD_PID 2>/dev/null

            # 恢復選單層級的 trap
            trap - INT

            echo ""
            echo -n "  按 Enter 返回選單..."
            read -r
            ;;

        2)
            # ── 關閉服務 ──
            echo ""
            stop_service
            echo ""
            echo -n "  按 Enter 返回選單..."
            read -r
            ;;

        3)
            # ── 暫停自動排程 ──
            echo ""
            if is_running; then
                echo "  先關閉服務..."
                stop_service
            else
                echo "  服務未在運行"
            fi

            date +%s > "$PAUSE_FILE"
            echo ""
            echo -e "  ${GREEN}✅ 自動排程已暫停（30 分鐘後自動恢復）${NC}"
            echo ""
            echo -n "  按 Enter 返回選單..."
            read -r
            ;;

        4)
            # ── 恢復自動排程 ──
            echo ""
            rm -f "$PAUSE_FILE"
            echo -e "  ${GREEN}✅ 自動排程已恢復${NC}"
            echo "  supervisor 將在 10 分鐘內自動檢查並啟動服務"
            echo ""
            echo -n "  按 Enter 返回選單..."
            read -r
            ;;

        5)
            # ── 掃碼登入 ──
            echo ""
            echo -e "  ${YELLOW}⚠️  掃碼登入僅在以下情況使用：${NC}"
            echo "  • 首次設定 WhatsApp Bot"
            echo "  • session 損壞需重新掃碼（日誌連續 3 次 authenticated timeout）"
            echo ""
            echo -n "  確認繼續？(y/N): "
            read -r confirm
            if [ "$confirm" != "y" ] && [ "$confirm" != "Y" ]; then
                echo "  已取消"
                sleep 1
                continue
            fi

            echo ""
            echo "  清理殘留 Chrome..."
            pkill -f "Google Chrome for Testing" 2>/dev/null
            sleep 2

            echo "  啟動 WhatsApp 掃碼（請在終端機中查看 QR Code）..."
            echo ""

            cd "$PROJECT_DIR" || exit 1

            node wa_bot/wa_bot.js &
            CHILD_PID=$!

            trap 'kill -2 $CHILD_PID 2>/dev/null; wait $CHILD_PID 2>/dev/null' INT

            wait $CHILD_PID 2>/dev/null

            trap - INT

            echo ""
            echo -n "  按 Enter 返回選單..."
            read -r
            ;;

        6)
            # ── 離開 ──
            echo ""
            echo "  再見！"
            exit 0
            ;;

        *)
            echo ""
            echo -e "  ${YELLOW}⚠️  請輸入 1-6${NC}"
            sleep 1
            ;;
    esac
done
